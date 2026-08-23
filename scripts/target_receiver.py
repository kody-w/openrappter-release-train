#!/usr/bin/env python3
"""Target-side atomic promotion/receipt preparation used by the reusable workflow."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ringctl import (
    ManifestError,
    canonical_bytes,
    compare_semver,
    digest,
    validate_manifest,
    validate_payload,
    validate_receipt,
)


def write_output(**values: object) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        return
    with open(output, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={str(value).lower() if isinstance(value, bool) else value}\n")


def prepare(args: argparse.Namespace) -> None:
    payload = validate_payload(json.loads(Path(args.payload).read_text()))
    current = json.loads(Path(args.current).read_text())
    validate_manifest(current, expected_ring=args.target_ring)
    if payload["target_repository"] != args.target_repository or payload["to"] != args.target_ring:
        raise ManifestError("payload target differs from receiver")
    proposed = payload["target_manifest"]
    proposed_digest = digest(proposed)
    current_digest = digest(current)
    if current_digest == proposed_digest and current["promotion_id"] == payload["promotion_id"]:
        Path(args.output).write_text(json.dumps(proposed, indent=2, sort_keys=True) + "\n")
        write_output(
            noop=True,
            promotion_id=payload["promotion_id"],
            manifest_sha256=proposed_digest,
            previous_source_commit=current["source"]["commit"],
            proposed_source_commit=proposed["source"]["commit"],
            source_tag=proposed["source"]["tag"] or "",
            artifact_url=proposed["artifact"]["url"],
            artifact_sha256=proposed["artifact"]["sha256"],
        )
        return
    if args.current_head != payload["target_base_commit"]:
        raise ManifestError("target HEAD changed after authority validation")
    if current_digest != payload["target_previous_manifest_sha256"]:
        raise ManifestError("target manifest changed after authority validation")
    if current["source"]["commit"] != payload["target_previous_source_commit"]:
        raise ManifestError("target source ancestry base changed")
    if compare_semver(proposed["version"], current["version"]) < 0:
        raise ManifestError("target mutation would downgrade version")
    Path(args.output).write_text(json.dumps(proposed, indent=2, sort_keys=True) + "\n")
    write_output(
        noop=False,
        promotion_id=payload["promotion_id"],
        manifest_sha256=proposed_digest,
        previous_source_commit=current["source"]["commit"],
        proposed_source_commit=proposed["source"]["commit"],
        source_tag=proposed["source"]["tag"] or "",
        artifact_url=proposed["artifact"]["url"],
        artifact_sha256=proposed["artifact"]["sha256"],
    )


def acknowledge(args: argparse.Namespace) -> None:
    request = validate_payload(json.loads(Path(args.request).read_text()))
    current = json.loads(Path(args.current).read_text())
    validate_manifest(current, expected_ring=args.target_ring)
    if current != request["target_manifest"]:
        raise ManifestError("applied manifest differs from immutable request")
    if args.target_repository != request["target_repository"]:
        raise ManifestError("request target repository mismatch")
    if len(args.request_commit) != 40 or len(args.target_manifest_commit) != 40:
        raise ManifestError("request/target commit must be immutable 40-hex")
    ack = {
        "schema": "openrappter-applied-request/v1",
        "request_id": request["promotion_id"],
        "request_sha256": digest(request),
        "request_authority_commit": args.request_commit,
        "request_path": args.request_path,
        "target_repository": args.target_repository,
        "target_ring": args.target_ring,
        "target_manifest_sha256": digest(current),
        "target_manifest_commit": args.target_manifest_commit,
    }
    Path(args.output).write_text(json.dumps(ack, indent=2, sort_keys=True) + "\n")
    write_output(ack_sha256=digest(ack))


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    for name in ("payload", "current", "output", "target-ring", "target-repository", "current-head"):
        prepare_parser.add_argument(f"--{name}", required=True)
    receipt_parser = sub.add_parser("acknowledge")
    for name in (
        "request", "request-commit", "request-path", "current",
        "target-manifest-commit", "output", "target-ring", "target-repository",
    ):
        receipt_parser.add_argument(f"--{name}", required=True)
    args = parser.parse_args()
    try:
        prepare(args) if args.command == "prepare" else acknowledge(args)
    except (OSError, json.JSONDecodeError, ManifestError, ValueError) as exc:
        print(f"target-receiver: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
