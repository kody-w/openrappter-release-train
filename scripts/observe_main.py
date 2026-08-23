#!/usr/bin/env python3
"""Verify canonical main checks and construct one deterministic nightly request."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

from ringctl import (
    REPOS,
    digest,
    promotion_id_for_payload,
    validate_manifest,
    validate_payload,
)


class ObservationError(ValueError):
    pass


def required_checks(path: Path) -> list[str]:
    value = json.loads(path.read_text())
    if set(value) != {"schema", "checks"} or value["schema"] != "openrappter-required-main-checks/v1":
        raise ObservationError("required check policy is not closed")
    checks = value["checks"]
    if not isinstance(checks, list) or not checks or any(not isinstance(item, str) for item in checks):
        raise ObservationError("required check policy has no checks")
    return checks


def verify_green_head(check_runs: object, head: str, required: list[str]) -> None:
    if not isinstance(check_runs, dict) or not isinstance(check_runs.get("check_runs"), list):
        raise ObservationError("check-runs response is malformed")
    if len(head) != 40 or any(char not in "0123456789abcdef" for char in head):
        raise ObservationError("observed main head is not immutable 40-hex")
    rows = check_runs["check_runs"]
    for name in required:
        candidates = [
            row for row in rows
            if isinstance(row, dict) and row.get("name") == name and row.get("head_sha") == head
        ]
        if not candidates:
            raise ObservationError(f"required check {name!r} is missing for exact head {head}")
        latest = max(candidates, key=lambda row: int(row.get("id", 0)))
        if latest.get("status") != "completed":
            raise ObservationError(f"required check {name!r} is pending")
        if latest.get("conclusion") != "success":
            raise ObservationError(
                f"required check {name!r} concluded {latest.get('conclusion')!r}"
            )


def nightly_request_id(
    *,
    head: str,
    version: str,
    artifact_url: str,
    artifact_sha256: str,
    promoted_at: str,
    source_tag: str | None,
    published: bool,
) -> str:
    source_identity = {
        "repository": "kody-w/openrappter",
        "commit": head,
        "tag": source_tag,
        "version": version,
        "artifact_url": artifact_url,
        "install_url": artifact_url if published else None,
        "artifact_sha256": artifact_sha256,
        "artifact_provenance": "github-candidate-bundle-sha256",
        "promoted_at": promoted_at,
    }
    seed = {
        "from": None,
        "to": "nightly",
        "target_repository": REPOS["nightly"],
        "source_repository": source_identity["repository"],
        "source_commit": head,
        "source_tag": source_tag,
        "version": version,
        "artifact_url": artifact_url,
        "install_url": artifact_url if published else None,
        "artifact_sha256": artifact_sha256,
        "artifact_provenance": "github-candidate-bundle-sha256",
        "promoted_at": promoted_at,
        "predecessor_manifest_sha256": digest(source_identity),
    }
    return promotion_id_for_payload(seed)


def build_request(
    *,
    head: str,
    package_version: str,
    committed_at: str,
    artifact_url: str,
    artifact_sha256: str,
    previous_manifest: dict,
    target_base_commit: str,
    sequence: int,
    release_tag: str | None,
    candidate_kind: str,
) -> dict:
    validate_manifest(previous_manifest, expected_ring="nightly")
    dt.datetime.fromisoformat(committed_at.replace("Z", "+00:00"))
    version = package_version
    promotion_id = nightly_request_id(
        head=head,
        version=version,
        artifact_url=artifact_url,
        artifact_sha256=artifact_sha256,
        promoted_at=committed_at,
        source_tag=release_tag,
        published=candidate_kind == "release",
    )
    source_identity = {
        "repository": "kody-w/openrappter",
        "commit": head,
        "tag": release_tag,
        "version": version,
        "artifact_url": artifact_url,
        "install_url": artifact_url if candidate_kind == "release" else None,
        "artifact_sha256": artifact_sha256,
        "artifact_provenance": "github-candidate-bundle-sha256",
        "promoted_at": committed_at,
    }
    target_manifest = {
        "schema": "openrappter-ring/v1",
        "ring": "nightly",
        "source": {
            "repository": "kody-w/openrappter",
            "commit": head,
            "tag": release_tag,
        },
        "version": version,
        "artifact": {
            "url": artifact_url,
            "install_url": artifact_url if candidate_kind == "release" else None,
            "sha256": artifact_sha256,
            "provenance": "github-candidate-bundle-sha256",
        },
        "promoted_at": committed_at,
        "predecessor": None,
        "status": "published" if candidate_kind == "release" else "unpublished",
        "reason": None if candidate_kind == "release" else "Continuous untagged snapshot; never stable-publishable.",
        "receipt": None,
        "promotion_id": promotion_id,
    }
    request = {
        "schema": "openrappter-promotion/v1",
        "sequence": sequence,
        "promotion_id": promotion_id,
        "from": None,
        "to": "nightly",
        "target_repository": REPOS["nightly"],
        "target_base_commit": target_base_commit,
        "target_previous_manifest_sha256": digest(previous_manifest),
        "target_previous_source_commit": previous_manifest["source"]["commit"],
        "source_repository": "kody-w/openrappter",
        "source_commit": head,
        "source_tag": release_tag,
        "version": version,
        "artifact_url": artifact_url,
        "install_url": artifact_url if candidate_kind == "release" else None,
        "artifact_sha256": artifact_sha256,
        "artifact_provenance": "github-candidate-bundle-sha256",
        "promoted_at": committed_at,
        "predecessor_manifest_sha256": digest(source_identity),
        "target_manifest": target_manifest,
        "target_manifest_sha256": digest(target_manifest),
    }
    validate_payload(request)
    return request


def candidate_fields(provenance: dict, bundle_names: list[str], head: str, candidate_commit: str) -> tuple[str, str, str, str]:
    if (
        provenance.get("schema") != "openrappter-candidate-provenance/v1"
        or provenance.get("channel") != "candidate"
        or provenance.get("stable") is not False
        or provenance.get("source_commit") != head
        or provenance.get("candidate_kind") not in {"release", "snapshot"}
    ):
        raise ObservationError("candidate provenance rejected")
    bundles = [name for name in bundle_names if re.fullmatch(r"[0-9a-f]{64}\.tar\.gz", name)]
    if len(bundles) != 1:
        raise ObservationError("candidate store must contain exactly one bundle")
    sha = bundles[0][:-7]
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise ObservationError("candidate bundle SHA-256 is empty or malformed")
    kind = provenance["candidate_kind"]
    tag = provenance.get("release_tag")
    if kind == "release" and (not isinstance(tag, str) or tag != f"v{provenance['version']}"):
        raise ObservationError("release candidate tag/version mismatch")
    if kind == "snapshot" and tag is not None:
        raise ObservationError("continuous snapshot must be untagged")
    url = f"https://raw.githubusercontent.com/kody-w/openrappter/{candidate_commit}/candidates/{head}/{sha}.tar.gz"
    return provenance["version"], kind, tag or "-", url


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--checks", required=True)
    verify.add_argument("--head", required=True)
    verify.add_argument("--policy", required=True)
    build = sub.add_parser("build")
    for name in (
        "head", "package-version", "committed-at", "artifact-url", "artifact-sha256",
        "previous-manifest", "target-base-commit", "sequence", "release-tag",
        "candidate-kind", "output",
    ):
        build.add_argument(f"--{name}", required=True)
    candidate = sub.add_parser("candidate")
    for name in ("provenance", "bundle-list", "head", "candidate-commit"):
        candidate.add_argument(f"--{name}", required=True)
    args = parser.parse_args()
    try:
        if args.command == "verify":
            verify_green_head(
                json.loads(Path(args.checks).read_text()),
                args.head,
                required_checks(Path(args.policy)),
            )
        elif args.command == "candidate":
            fields = candidate_fields(
                json.loads(Path(args.provenance).read_text()),
                Path(args.bundle_list).read_text().splitlines(),
                args.head,
                args.candidate_commit,
            )
            print("\t".join(fields))
        else:
            request = build_request(
                head=args.head,
                package_version=args.package_version,
                committed_at=args.committed_at,
                artifact_url=args.artifact_url,
                artifact_sha256=args.artifact_sha256,
                previous_manifest=json.loads(Path(args.previous_manifest).read_text()),
                target_base_commit=args.target_base_commit,
                sequence=int(args.sequence),
                release_tag=args.release_tag or None,
                candidate_kind=args.candidate_kind,
            )
            Path(args.output).write_bytes(
                json.dumps(request, indent=2, sort_keys=True).encode() + b"\n"
            )
            print(request["promotion_id"])
    except (OSError, json.JSONDecodeError, ObservationError, ValueError) as exc:
        print(f"observe-main: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
