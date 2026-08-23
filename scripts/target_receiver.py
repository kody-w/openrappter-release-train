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


def receipt(args: argparse.Namespace) -> None:
    pointer_payload = json.loads(Path(args.payload).read_text())
    expected = {
        "authority_repository", "authority_commit", "receipt_path",
        "receipt_sha256", "promotion_id", "target_manifest_commit",
    }
    if not isinstance(pointer_payload, dict) or set(pointer_payload) != expected:
        raise ManifestError("receipt acknowledgement payload is not closed")
    if pointer_payload["authority_repository"] != "kody-w/openrappter-release-train":
        raise ManifestError("receipt authority repository is not allowlisted")
    for field in ("authority_commit", "target_manifest_commit"):
        value = pointer_payload[field]
        if not isinstance(value, str) or len(value) != 40 or any(c not in "0123456789abcdef" for c in value):
            raise ManifestError(f"{field} is not immutable 40-hex")
    receipt_value = json.loads(Path(args.receipt).read_text())
    if digest(receipt_value) != pointer_payload["receipt_sha256"]:
        raise ManifestError("immutable receipt checksum mismatch")
    current = json.loads(Path(args.current).read_text())
    immutable = json.loads(Path(args.immutable).read_text())
    validate_receipt(
        receipt_value,
        target_repository=args.target_repository,
        target_ring=args.target_ring,
        current_manifest=current,
        immutable_manifest=immutable,
    )
    if receipt_value["target_manifest_commit"] != pointer_payload["target_manifest_commit"]:
        raise ManifestError("receipt target manifest commit mismatch")
    if pointer_payload["promotion_id"] != receipt_value["promotion_id"]:
        raise ManifestError("receipt promotion id mismatch")
    authority = {
        "schema": "openrappter-ring-authority/v1",
        "authority_repository": pointer_payload["authority_repository"],
        "authority_commit": pointer_payload["authority_commit"],
        "receipt_path": pointer_payload["receipt_path"],
        "receipt_sha256": pointer_payload["receipt_sha256"],
        "promotion_id": pointer_payload["promotion_id"],
    }
    Path(args.output).write_text(json.dumps(authority, indent=2, sort_keys=True) + "\n")
    write_output(authority_sha256=digest(authority))


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    for name in ("payload", "current", "output", "target-ring", "target-repository", "current-head"):
        prepare_parser.add_argument(f"--{name}", required=True)
    receipt_parser = sub.add_parser("receipt")
    for name in (
        "payload", "receipt", "current", "immutable", "output",
        "target-ring", "target-repository",
    ):
        receipt_parser.add_argument(f"--{name}", required=True)
    args = parser.parse_args()
    try:
        prepare(args) if args.command == "prepare" else receipt(args)
    except (OSError, json.JSONDecodeError, ManifestError, ValueError) as exc:
        print(f"target-receiver: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
