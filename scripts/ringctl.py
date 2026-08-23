#!/usr/bin/env python3
"""Fail-closed validation and promotion tooling for OpenRappter ring pointers."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

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
    "predecessor", "status", "reason", "receipt",
}
SOURCE_KEYS = {"repository", "commit", "tag"}
ARTIFACT_KEYS = {"url", "install_url", "sha256", "provenance"}
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
VERSION = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?$"
)
ALLOWED_ARTIFACT_HOSTS = {"github.com", "registry.npmjs.org"}
PROVENANCE = {
    "github-commit-archive-sha256",
    "npm-registry-download-sha256",
}


class ManifestError(ValueError):
    pass


def _closed(value: dict, expected: set[str], label: str) -> None:
    missing = expected - value.keys()
    extra = value.keys() - expected
    if missing or extra:
        raise ManifestError(
            f"{label} is not closed (missing={sorted(missing)}, extra={sorted(extra)})"
        )


def _timestamp(value: object, now: dt.datetime) -> dt.datetime:
    if not isinstance(value, str):
        raise ManifestError("promoted_at must be an RFC3339 string")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ManifestError("promoted_at must be RFC3339") from exc
    if parsed.tzinfo is None:
        raise ManifestError("promoted_at must include a timezone")
    if parsed > now + dt.timedelta(minutes=5):
        raise ManifestError("promoted_at is in the future")
    return parsed


def validate_manifest(
    manifest: object,
    *,
    expected_ring: str | None = None,
    now: dt.datetime | None = None,
) -> dict:
    if not isinstance(manifest, dict):
        raise ManifestError("manifest must be an object")
    _closed(manifest, TOP_KEYS, "manifest")
    if manifest["schema"] != SCHEMA:
        raise ManifestError(f"schema must be {SCHEMA}")
    ring = manifest["ring"]
    if ring not in RINGS:
        raise ManifestError("unknown ring")
    if expected_ring and ring != expected_ring:
        raise ManifestError(f"expected ring {expected_ring}, got {ring}")

    source = manifest["source"]
    if not isinstance(source, dict):
        raise ManifestError("source must be an object")
    _closed(source, SOURCE_KEYS, "source")
    if source["repository"] != "kody-w/openrappter":
        raise ManifestError("source repository is not authorized")
    if not isinstance(source["commit"], str) or not HEX40.fullmatch(source["commit"]):
        raise ManifestError("source.commit must be 40 lowercase hex characters")
    tag = source["tag"]
    if tag is not None and (
        not isinstance(tag, str) or not re.fullmatch(r"v[0-9][0-9A-Za-z.+-]*", tag)
    ):
        raise ManifestError("source.tag is malformed")

    if not isinstance(manifest["version"], str) or not VERSION.fullmatch(manifest["version"]):
        raise ManifestError("version is not strict semver")

    artifact = manifest["artifact"]
    if not isinstance(artifact, dict):
        raise ManifestError("artifact must be an object")
    _closed(artifact, ARTIFACT_KEYS, "artifact")
    for field in ("url", "install_url"):
        url = artifact[field]
        if field == "install_url" and url is None:
            continue
        if not isinstance(url, str) or urlparse(url).scheme != "https":
            raise ManifestError(f"artifact.{field} must be HTTPS")
        if urlparse(url).hostname not in ALLOWED_ARTIFACT_HOSTS:
            raise ManifestError(f"artifact.{field} host is not authorized")
    if not isinstance(artifact["sha256"], str) or not HEX64.fullmatch(artifact["sha256"]):
        raise ManifestError("artifact.sha256 must be 64 lowercase hex characters")
    if artifact["provenance"] not in PROVENANCE:
        raise ManifestError("unknown checksum provenance")

    status = manifest["status"]
    if status not in {"published", "unpublished", "disabled"}:
        raise ManifestError("unknown status")
    reason = manifest["reason"]
    if status == "published":
        if reason is not None:
            raise ManifestError("published manifests must have reason=null")
        if artifact["install_url"] is None:
            raise ManifestError("published manifests require install_url")
    elif not isinstance(reason, str) or not reason.strip():
        raise ManifestError("non-published manifests require an explicit reason")

    expected_predecessor = None if ring == "nightly" else RINGS[RINGS.index(ring) - 1]
    if manifest["predecessor"] != expected_predecessor:
        raise ManifestError(
            f"{ring} predecessor must be {expected_predecessor!r}"
        )
    _timestamp(
        manifest["promoted_at"],
        now or dt.datetime.now(dt.timezone.utc),
    )
    receipt = manifest["receipt"]
    if receipt is not None and (
        not isinstance(receipt, str)
        or not re.fullmatch(
            r"https://github\.com/kody-w/openrappter-release-train/blob/"
            r"[0-9a-f]{40}/receipts/.+\.json",
            receipt,
        )
    ):
        raise ManifestError("receipt must use an immutable release-train commit URL")
    return manifest


def semver_key(version: str) -> tuple:
    match = VERSION.fullmatch(version)
    if not match:
        raise ManifestError("version is not strict semver")
    major, minor, patch, prerelease, _build = match.groups()
    pre = ()
    if prerelease is not None:
        pre = tuple(
            (0, int(part)) if part.isdigit() else (1, part)
            for part in prerelease.split(".")
        )
    return (int(major), int(minor), int(patch), prerelease is None, pre)


def validate_promotion(
    source: dict,
    target_ring: str,
    *,
    previous_target: dict | None = None,
    checkout: Path | None = None,
) -> dict:
    validate_manifest(source)
    if source["status"] not in {"published", "unpublished"}:
        raise ManifestError("disabled source cannot be promoted")
    expected = RINGS[RINGS.index(source["ring"]) + 1] if source["ring"] != "stable" else None
    if target_ring != expected:
        raise ManifestError(
            f"promotion must advance exactly one ring: {source['ring']} -> {expected}"
        )
    if previous_target:
        validate_manifest(previous_target, expected_ring=target_ring)
        if semver_key(source["version"]) < semver_key(previous_target["version"]):
            raise ManifestError("promotion would downgrade the target ring")
    if checkout:
        commit = source["source"]["commit"]
        result = subprocess.run(
            ["git", "-C", str(checkout), "merge-base", "--is-ancestor", commit, "HEAD"],
            check=False,
        )
        if result.returncode:
            raise ManifestError("source commit is not an ancestor of vetted checkout HEAD")
        tag = source["source"]["tag"]
        if tag:
            resolved = subprocess.run(
                ["git", "-C", str(checkout), "rev-list", "-n", "1", tag],
                check=False,
                capture_output=True,
                text=True,
            )
            if resolved.returncode or resolved.stdout.strip() != commit:
                raise ManifestError("source tag does not resolve to source commit")
    return {
        "schema": "openrappter-promotion/v1",
        "from": source["ring"],
        "to": target_ring,
        "target_repository": REPOS[target_ring],
        "source_repository": source["source"]["repository"],
        "source_commit": source["source"]["commit"],
        "source_tag": source["source"]["tag"],
        "version": source["version"],
        "artifact_url": source["artifact"]["url"],
        "artifact_sha256": source["artifact"]["sha256"],
        "predecessor_manifest_sha256": hashlib.sha256(
            json.dumps(source, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def write_receipt(payload: dict, output: Path, *, emitted_at: str | None = None) -> Path:
    expected = {
        "schema", "from", "to", "target_repository", "source_repository",
        "source_commit", "source_tag", "version", "artifact_url",
        "artifact_sha256", "predecessor_manifest_sha256",
    }
    _closed(payload, expected, "promotion payload")
    if payload["schema"] != "openrappter-promotion/v1":
        raise ManifestError("unknown promotion payload schema")
    timestamp = emitted_at or dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    receipt = {
        "schema": "openrappter-promotion-receipt/v1",
        "emitted_at": timestamp,
        "payload_sha256": hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "promotion": payload,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(receipt, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("manifest")
    validate.add_argument("--ring", choices=RINGS)
    promote = sub.add_parser("promote")
    promote.add_argument("manifest")
    promote.add_argument("--to", required=True, choices=RINGS)
    promote.add_argument("--previous")
    promote.add_argument("--checkout")
    receipt = sub.add_parser("receipt")
    receipt.add_argument("payload")
    receipt.add_argument("--out", required=True)
    receipt.add_argument("--emitted-at")
    args = parser.parse_args()
    try:
        if args.command == "receipt":
            destination = write_receipt(
                load(args.payload),
                Path(args.out),
                emitted_at=args.emitted_at,
            )
            print(destination)
            return 0
        manifest = load(args.manifest)
        if args.command == "validate":
            validate_manifest(manifest, expected_ring=args.ring)
            print(f"valid {manifest['ring']} manifest at {manifest['source']['commit']}")
        else:
            payload = validate_promotion(
                manifest,
                args.to,
                previous_target=load(args.previous) if args.previous else None,
                checkout=Path(args.checkout) if args.checkout else None,
            )
            print(json.dumps(payload, sort_keys=True))
    except (OSError, json.JSONDecodeError, ManifestError, IndexError) as exc:
        print(f"ringctl: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
