#!/usr/bin/env python3
"""Fail-closed, deterministic authority for OpenRappter ring promotion."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

SCHEMA = "openrappter-ring/v1"
RINGS = ("nightly", "alpha", "canary", "beta", "stable")
REPOS = {
    "nightly": "kody-w/openrappter-nightly",
    "alpha": "kody-w/openrappter-alpha",
    "canary": "kody-w/openrappter-canary",
    "beta": "kody-w/openrappter-beta",
    "stable": "kody-w/openrappter",
}
TOP_KEYS = {
    "schema", "ring", "source", "version", "artifact", "promoted_at",
    "predecessor", "status", "reason", "receipt", "promotion_id",
}
SOURCE_KEYS = {"repository", "commit", "tag"}
ARTIFACT_KEYS = {"url", "install_url", "sha256", "provenance"}
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
PAYLOAD_KEYS = {
    "schema", "promotion_id", "from", "to", "target_repository",
    "target_base_commit", "target_previous_manifest_sha256",
    "target_previous_source_commit", "source_repository", "source_commit",
    "source_tag", "version", "artifact_url", "install_url",
    "artifact_sha256", "artifact_provenance", "promoted_at",
    "predecessor_manifest_sha256", "target_manifest",
    "target_manifest_sha256",
}


class ManifestError(ValueError):
    pass


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _closed(value: object, expected: set[str], label: str) -> dict:
    if not isinstance(value, dict):
        raise ManifestError(f"{label} must be an object")
    missing = expected - value.keys()
    extra = value.keys() - expected
    if missing or extra:
        raise ManifestError(
            f"{label} is not closed (missing={sorted(missing)}, extra={sorted(extra)})"
        )
    return value


def _timestamp(value: object, now: dt.datetime) -> str:
    if not isinstance(value, str):
        raise ManifestError("promoted_at must be an RFC3339 string")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ManifestError("promoted_at must be RFC3339") from exc
    if parsed.tzinfo is None or parsed > now + dt.timedelta(minutes=5):
        raise ManifestError("promoted_at is timezone-less or in the future")
    return value


def parse_semver(version: str) -> tuple[tuple[int, int, int], list[str] | None]:
    match = SEMVER.fullmatch(version)
    if not match:
        raise ManifestError(f"invalid SemVer: {version!r}")
    prerelease = match.group(4).split(".") if match.group(4) else None
    if prerelease and any(part.isdigit() and len(part) > 1 and part[0] == "0" for part in prerelease):
        raise ManifestError(f"invalid numeric SemVer identifier: {version!r}")
    return (
        (int(match.group(1)), int(match.group(2)), int(match.group(3))),
        prerelease,
    )


def compare_semver(left: str, right: str) -> int:
    a_core, a_pre = parse_semver(left)
    b_core, b_pre = parse_semver(right)
    if a_core != b_core:
        return -1 if a_core < b_core else 1
    if a_pre is None and b_pre is None:
        return 0
    if a_pre is None:
        return 1
    if b_pre is None:
        return -1
    for index in range(max(len(a_pre), len(b_pre))):
        if index >= len(a_pre):
            return -1
        if index >= len(b_pre):
            return 1
        left_id, right_id = a_pre[index], b_pre[index]
        if left_id == right_id:
            continue
        left_numeric, right_numeric = left_id.isdigit(), right_id.isdigit()
        if left_numeric and right_numeric:
            return -1 if int(left_id) < int(right_id) else 1
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return -1 if left_id < right_id else 1
    return 0


def validate_artifact(artifact: object, version: str, tag: str | None, status: str) -> dict:
    value = _closed(artifact, ARTIFACT_KEYS, "artifact")
    sha = value["sha256"]
    if not isinstance(sha, str) or not HEX64.fullmatch(sha):
        raise ManifestError("artifact.sha256 must be 64 lowercase hex characters")
    url, install_url, provenance = value["url"], value["install_url"], value["provenance"]
    if status != "published":
        if install_url is not None:
            raise ManifestError("non-published manifest must have install_url=null")
        expected = re.compile(
            r"https://github\.com/kody-w/openrappter/archive/[0-9a-f]{40}\.tar\.gz"
        )
        if provenance != "github-commit-archive-sha256" or not isinstance(url, str) or not expected.fullmatch(url):
            raise ManifestError("non-published artifact must be an exact canonical commit archive")
        return value
    npm_url = f"https://registry.npmjs.org/openrappter/-/openrappter-{version}.tgz"
    github_release = (
        re.compile(
            rf"https://github\.com/kody-w/openrappter/releases/download/"
            rf"{re.escape(tag or '')}/[0-9A-Za-z._-]+"
        )
        if tag else None
    )
    npm_ok = provenance == "npm-registry-download-sha256" and url == npm_url and install_url == npm_url
    release_ok = (
        provenance == "github-release-download-sha256"
        and isinstance(url, str)
        and isinstance(install_url, str)
        and url == install_url
        and github_release is not None
        and github_release.fullmatch(url)
    )
    if not (npm_ok or release_ok):
        raise ManifestError("published artifact URL is not bound to canonical npm version or GitHub release tag")
    return value


def validate_manifest(
    manifest: object,
    *,
    expected_ring: str | None = None,
    now: dt.datetime | None = None,
) -> dict:
    value = _closed(manifest, TOP_KEYS, "manifest")
    if value["schema"] != SCHEMA:
        raise ManifestError(f"schema must be {SCHEMA}")
    ring = value["ring"]
    if ring not in RINGS or (expected_ring and ring != expected_ring):
        raise ManifestError(f"unexpected ring: {ring!r}")
    source = _closed(value["source"], SOURCE_KEYS, "source")
    if source["repository"] != "kody-w/openrappter":
        raise ManifestError("source repository is not authorized")
    if not isinstance(source["commit"], str) or not HEX40.fullmatch(source["commit"]):
        raise ManifestError("source.commit must be 40 lowercase hex characters")
    tag = source["tag"]
    if tag is not None and (
        not isinstance(tag, str) or not re.fullmatch(r"v[0-9][0-9A-Za-z.+-]*", tag)
    ):
        raise ManifestError("source.tag is malformed")
    if not isinstance(value["version"], str):
        raise ManifestError("version must be a string")
    parse_semver(value["version"])
    status = value["status"]
    if status not in {"published", "unpublished", "disabled"}:
        raise ManifestError("unknown status")
    reason = value["reason"]
    if status == "published" and reason is not None:
        raise ManifestError("published manifests require reason=null")
    if status != "published" and (not isinstance(reason, str) or not reason.strip()):
        raise ManifestError("non-published manifests require an explicit reason")
    validate_artifact(value["artifact"], value["version"], tag, status)
    predecessor = None if ring == "nightly" else RINGS[RINGS.index(ring) - 1]
    if value["predecessor"] != predecessor:
        raise ManifestError(f"{ring} predecessor must be {predecessor!r}")
    _timestamp(value["promoted_at"], now or dt.datetime.now(dt.timezone.utc))
    if value["receipt"] is not None:
        raise ManifestError("receipt trust is carried by .ring/authority.json, not mutable manifest content")
    promotion_id = value["promotion_id"]
    if promotion_id is not None and (
        not isinstance(promotion_id, str) or not HEX64.fullmatch(promotion_id)
    ):
        raise ManifestError("promotion_id must be null or 64 lowercase hex characters")
    return value


def _git(checkout: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(checkout), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise ManifestError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def validate_promotion(
    source: dict,
    target_ring: str,
    *,
    previous_target: dict,
    checkout: Path,
    target_base_commit: str,
) -> dict:
    validate_manifest(source)
    validate_manifest(previous_target, expected_ring=target_ring)
    if source["status"] != "published":
        raise ManifestError("only a published predecessor can be promoted")
    expected = RINGS[RINGS.index(source["ring"]) + 1] if source["ring"] != "stable" else None
    if target_ring != expected:
        raise ManifestError(f"promotion must advance exactly one ring: {source['ring']} -> {expected}")
    if not HEX40.fullmatch(target_base_commit):
        raise ManifestError("target base commit must be immutable 40-hex")
    if compare_semver(source["version"], previous_target["version"]) < 0:
        raise ManifestError("promotion would downgrade the target ring")
    proposed_commit = source["source"]["commit"]
    previous_commit = previous_target["source"]["commit"]
    _git(checkout, "merge-base", "--is-ancestor", previous_commit, proposed_commit)
    _git(checkout, "merge-base", "--is-ancestor", proposed_commit, "origin/main")
    if source["source"]["tag"]:
        if _git(checkout, "rev-list", "-n", "1", source["source"]["tag"]) != proposed_commit:
            raise ManifestError("source tag does not resolve to source commit")
    seed = {
        "from": source["ring"],
        "to": target_ring,
        "target_repository": REPOS[target_ring],
        "target_base_commit": target_base_commit,
        "target_previous_manifest_sha256": digest(previous_target),
        "target_previous_source_commit": previous_commit,
        "source_repository": source["source"]["repository"],
        "source_commit": proposed_commit,
        "source_tag": source["source"]["tag"],
        "version": source["version"],
        "artifact_url": source["artifact"]["url"],
        "install_url": source["artifact"]["install_url"],
        "artifact_sha256": source["artifact"]["sha256"],
        "artifact_provenance": source["artifact"]["provenance"],
        "promoted_at": source["promoted_at"],
        "predecessor_manifest_sha256": digest(source),
    }
    promotion_id = digest({
        "from": seed["from"],
        "to": seed["to"],
        "target_repository": seed["target_repository"],
        "source_repository": seed["source_repository"],
        "source_commit": seed["source_commit"],
        "source_tag": seed["source_tag"],
        "version": seed["version"],
        "artifact_url": seed["artifact_url"],
        "install_url": seed["install_url"],
        "artifact_sha256": seed["artifact_sha256"],
        "artifact_provenance": seed["artifact_provenance"],
        "promoted_at": seed["promoted_at"],
        "predecessor_manifest_sha256": seed["predecessor_manifest_sha256"],
    })
    target_manifest = {
        "schema": SCHEMA,
        "ring": target_ring,
        "source": source["source"],
        "version": source["version"],
        "artifact": source["artifact"],
        "promoted_at": source["promoted_at"],
        "predecessor": source["ring"],
        "status": "published",
        "reason": None,
        "receipt": None,
        "promotion_id": promotion_id,
    }
    validate_manifest(target_manifest, expected_ring=target_ring)
    payload = {
        "schema": "openrappter-promotion/v1",
        "promotion_id": promotion_id,
        **seed,
        "target_manifest": target_manifest,
        "target_manifest_sha256": digest(target_manifest),
    }
    _closed(payload, PAYLOAD_KEYS, "promotion payload")
    return payload


def validate_confirmation(payload: dict, target_manifest: dict, target_manifest_commit: str) -> None:
    validate_payload(payload)
    validate_manifest(target_manifest, expected_ring=payload["to"])
    if not HEX40.fullmatch(target_manifest_commit):
        raise ManifestError("target manifest commit must be immutable 40-hex")
    if target_manifest != payload["target_manifest"]:
        raise ManifestError("target manifest identity differs from authorized payload")
    if digest(target_manifest) != payload["target_manifest_sha256"]:
        raise ManifestError("target manifest digest differs from authorized payload")


def promotion_id_for_payload(payload: dict) -> str:
    return digest({
        key: payload[key]
        for key in (
            "from", "to", "target_repository", "source_repository",
            "source_commit", "source_tag", "version", "artifact_url",
            "install_url", "artifact_sha256", "artifact_provenance",
            "promoted_at", "predecessor_manifest_sha256",
        )
    })


def validate_payload(payload: object) -> dict:
    value = _closed(payload, PAYLOAD_KEYS, "promotion payload")
    if value["schema"] != "openrappter-promotion/v1":
        raise ManifestError("unknown promotion payload schema")
    if value["to"] not in RINGS or value["target_repository"] != REPOS[value["to"]]:
        raise ManifestError("promotion target is not allowlisted")
    if value["from"] != (None if value["to"] == "nightly" else RINGS[RINGS.index(value["to"]) - 1]):
        raise ManifestError("promotion predecessor ring is invalid")
    for field in (
        "promotion_id", "target_previous_manifest_sha256",
        "predecessor_manifest_sha256", "artifact_sha256",
        "target_manifest_sha256",
    ):
        if not isinstance(value[field], str) or not HEX64.fullmatch(value[field]):
            raise ManifestError(f"{field} is malformed")
    for field in ("target_base_commit", "target_previous_source_commit", "source_commit"):
        if not isinstance(value[field], str) or not HEX40.fullmatch(value[field]):
            raise ManifestError(f"{field} is malformed")
    if promotion_id_for_payload(value) != value["promotion_id"]:
        raise ManifestError("promotion id does not match canonical payload")
    target = validate_manifest(value["target_manifest"], expected_ring=value["to"])
    if target["promotion_id"] != value["promotion_id"]:
        raise ManifestError("target manifest promotion id mismatch")
    if digest(target) != value["target_manifest_sha256"]:
        raise ManifestError("target manifest digest mismatch")
    expected_identity = (
        target["source"]["repository"],
        target["source"]["commit"],
        target["source"]["tag"],
        target["version"],
        target["artifact"]["url"],
        target["artifact"]["install_url"],
        target["artifact"]["sha256"],
        target["artifact"]["provenance"],
    )
    payload_identity = (
        value["source_repository"], value["source_commit"], value["source_tag"],
        value["version"], value["artifact_url"], value["install_url"],
        value["artifact_sha256"], value["artifact_provenance"],
    )
    if expected_identity != payload_identity:
        raise ManifestError("target manifest identity differs from payload")
    return value


RECEIPT_KEYS = {
    "schema", "promotion_id", "target_repository", "target_ring",
    "target_manifest_sha256", "target_manifest_commit", "source_repository",
    "source_commit", "source_tag", "version", "artifact_url", "install_url",
    "artifact_sha256", "artifact_provenance", "predecessor_manifest_sha256",
    "emitted_at", "receipt_kind",
}


def validate_receipt(
    receipt: object,
    *,
    target_repository: str,
    target_ring: str,
    current_manifest: dict,
    immutable_manifest: dict,
) -> dict:
    value = _closed(receipt, RECEIPT_KEYS, "receipt")
    if value["schema"] != "openrappter-promotion-receipt/v1":
        raise ManifestError("unknown receipt schema")
    if value["receipt_kind"] not in {"bootstrap", "promotion"}:
        raise ManifestError("unknown receipt kind")
    if value["target_repository"] != target_repository or value["target_ring"] != target_ring:
        raise ManifestError("receipt authorizes a different target")
    validate_manifest(current_manifest, expected_ring=target_ring)
    validate_manifest(immutable_manifest, expected_ring=target_ring)
    if current_manifest != immutable_manifest:
        raise ManifestError("mutable target manifest differs from receipt-bound commit")
    if value["target_manifest_sha256"] != digest(current_manifest):
        raise ManifestError("receipt manifest digest mismatch")
    if value["promotion_id"] != current_manifest["promotion_id"]:
        raise ManifestError("receipt promotion id mismatch")
    identity = (
        value["source_repository"], value["source_commit"], value["source_tag"],
        value["version"], value["artifact_url"], value["install_url"],
        value["artifact_sha256"], value["artifact_provenance"],
    )
    manifest_identity = (
        current_manifest["source"]["repository"], current_manifest["source"]["commit"],
        current_manifest["source"]["tag"], current_manifest["version"],
        current_manifest["artifact"]["url"], current_manifest["artifact"]["install_url"],
        current_manifest["artifact"]["sha256"], current_manifest["artifact"]["provenance"],
    )
    if identity != manifest_identity:
        raise ManifestError("receipt source/artifact identity mismatch")
    return value


def make_receipt(
    payload: dict,
    *,
    target_manifest_commit: str,
    emitted_at: str,
) -> dict:
    validate_confirmation(payload, payload["target_manifest"], target_manifest_commit)
    return {
        "schema": "openrappter-promotion-receipt/v1",
        "receipt_kind": "promotion",
        "promotion_id": payload["promotion_id"],
        "target_repository": payload["target_repository"],
        "target_ring": payload["to"],
        "target_manifest_sha256": payload["target_manifest_sha256"],
        "target_manifest_commit": target_manifest_commit,
        "source_repository": payload["source_repository"],
        "source_commit": payload["source_commit"],
        "source_tag": payload["source_tag"],
        "version": payload["version"],
        "artifact_url": payload["artifact_url"],
        "install_url": payload["install_url"],
        "artifact_sha256": payload["artifact_sha256"],
        "artifact_provenance": payload["artifact_provenance"],
        "predecessor_manifest_sha256": payload["predecessor_manifest_sha256"],
        "emitted_at": emitted_at,
    }


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("manifest")
    validate.add_argument("--ring", choices=RINGS)
    promote = sub.add_parser("promote")
    promote.add_argument("manifest")
    promote.add_argument("--to", required=True, choices=RINGS)
    promote.add_argument("--previous", required=True)
    promote.add_argument("--checkout", required=True)
    promote.add_argument("--target-base-commit", required=True)
    receipt = sub.add_parser("receipt")
    receipt.add_argument("payload")
    receipt.add_argument("--target-manifest-commit", required=True)
    receipt.add_argument("--emitted-at", required=True)
    receipt.add_argument("--out", required=True)
    args = parser.parse_args()
    try:
        if args.command == "validate":
            value = load(args.manifest)
            validate_manifest(value, expected_ring=args.ring)
            print(f"valid {value['ring']} manifest at {value['source']['commit']}")
        elif args.command == "promote":
            payload = validate_promotion(
                load(args.manifest),
                args.to,
                previous_target=load(args.previous),
                checkout=Path(args.checkout),
                target_base_commit=args.target_base_commit,
            )
            print(json.dumps(payload, sort_keys=True))
        else:
            output = Path(args.out)
            receipt_value = make_receipt(
                load(args.payload),
                target_manifest_commit=args.target_manifest_commit,
                emitted_at=args.emitted_at,
            )
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", encoding="utf-8") as handle:
                json.dump(receipt_value, handle, indent=2, sort_keys=True)
                handle.write("\n")
            print(output)
    except (OSError, json.JSONDecodeError, ManifestError, IndexError) as exc:
        print(f"ringctl: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
