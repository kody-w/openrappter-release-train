#!/usr/bin/env python3
"""Machine enforcement for openrappter-release-constitution/v1."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import hashlib
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

from ringctl import ManifestError, digest, validate_manifest, validate_receipt

ORDER = ("nightly", "alpha", "canary", "beta")
AUTHORITY = "kody-w/openrappter-release-train"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
RELEASE_KEYS = {
    "schema", "mode", "source_commit", "source_tag", "version",
    "artifact_url", "install_url", "artifact_sha256",
    "artifact_provenance", "rollback_receipt",
}


class ConstitutionError(ValueError):
    pass


def load_policy(path: Path) -> dict:
    value = json.loads(path.read_text())
    expected = {
        "schema", "check_name", "authority_repository", "required_ring_order",
        "distribution_surfaces", "normal_release", "emergency_rollback",
        "additional_properties",
    }
    if set(value) != expected:
        raise ConstitutionError("constitution contract is not closed")
    if (
        value["schema"] != "openrappter-release-constitution/v1"
        or value["check_name"] != "Release Constitution"
        or value["authority_repository"] != AUTHORITY
        or value["required_ring_order"] != list(ORDER)
        or value["additional_properties"] is not False
    ):
        raise ConstitutionError("constitution identity/order changed")
    return value


def validate_release(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != RELEASE_KEYS:
        raise ConstitutionError("release identity is not closed")
    if value["schema"] != "openrappter-release/v1":
        raise ConstitutionError("unknown release identity schema")
    if value["mode"] not in {"normal", "rollback"}:
        raise ConstitutionError("release mode must be normal or rollback")
    if not isinstance(value["source_commit"], str) or not HEX40.fullmatch(value["source_commit"]):
        raise ConstitutionError("release source commit must be exact 40-hex")
    if not isinstance(value["artifact_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", value["artifact_sha256"]):
        raise ConstitutionError("release artifact checksum is malformed")
    if value["mode"] == "normal" and value["rollback_receipt"] is not None:
        raise ConstitutionError("normal release cannot carry rollback receipt")
    if value["mode"] == "rollback" and not isinstance(value["rollback_receipt"], dict):
        raise ConstitutionError("rollback requires its own immutable receipt")
    return value


def _identity(value: dict) -> tuple:
    return (
        value["source_commit"], value["source_tag"], value["version"],
        value["artifact_url"], value["install_url"], value["artifact_sha256"],
        value["artifact_provenance"],
    )


def validate_chain(
    release: object,
    chain: list[dict],
    policy: dict,
) -> None:
    value = validate_release(release)
    load_order = [item.get("ring") for item in chain]
    if load_order != list(ORDER):
        raise ConstitutionError(f"receipt order must be {' -> '.join(ORDER)}")
    expected_identity = _identity(value)
    previous_manifest: dict | None = None
    previous_time: dt.datetime | None = None
    seen_ids: set[str] = set()
    for ring, item in zip(ORDER, chain):
        if set(item) != {
            "ring", "authority_commit", "receipt_path", "receipt", "manifest",
        }:
            raise ConstitutionError(f"{ring} chain entry is not closed")
        authority_commit = item["authority_commit"]
        receipt_path = item["receipt_path"]
        if not isinstance(authority_commit, str) or not HEX40.fullmatch(authority_commit):
            raise ConstitutionError(f"{ring} receipt authority is mutable/untrusted")
        receipt = item["receipt"]
        manifest = item["manifest"]
        expected_path = f"receipts/{ring}/{receipt.get('promotion_id')}.json"
        if receipt_path != expected_path:
            raise ConstitutionError(f"{ring} receipt path is mutable/untrusted")
        try:
            validate_receipt(
                receipt,
                target_repository=f"kody-w/openrappter-{ring}",
                target_ring=ring,
                current_manifest=manifest,
                immutable_manifest=manifest,
            )
        except ManifestError as exc:
            raise ConstitutionError(str(exc)) from exc
        if receipt["receipt_kind"] != "promotion":
            raise ConstitutionError(f"{ring} is not finalized by a promotion receipt")
        if manifest["status"] != "published":
            raise ConstitutionError(f"{ring} is pending or disabled")
        if _identity({
            "source_commit": manifest["source"]["commit"],
            "source_tag": manifest["source"]["tag"],
            "version": manifest["version"],
            "artifact_url": manifest["artifact"]["url"],
            "install_url": manifest["artifact"]["install_url"],
            "artifact_sha256": manifest["artifact"]["sha256"],
            "artifact_provenance": manifest["artifact"]["provenance"],
        }) != expected_identity:
            raise ConstitutionError(f"{ring} release identity/checksum/version mismatch")
        if receipt["promotion_id"] in seen_ids:
            raise ConstitutionError("receipt promotion id was reused")
        seen_ids.add(receipt["promotion_id"])
        emitted = dt.datetime.fromisoformat(receipt["emitted_at"].replace("Z", "+00:00"))
        if previous_time and emitted < previous_time:
            raise ConstitutionError("receipt timestamps are out of order")
        if previous_manifest is not None and receipt["predecessor_manifest_sha256"] != digest(previous_manifest):
            raise ConstitutionError(f"{ring} does not descend from prior finalized ring")
        previous_manifest, previous_time = manifest, emitted
    if value["mode"] == "rollback":
        rollback = value["rollback_receipt"]
        expected = {
            "schema", "source_commit", "version", "artifact_sha256",
            "beta_receipt_id", "created_at",
        }
        if set(rollback) != expected or rollback["schema"] != "openrappter-rollback-receipt/v1":
            raise ConstitutionError("rollback receipt is not closed")
        if (
            rollback["source_commit"] != value["source_commit"]
            or rollback["version"] != value["version"]
            or rollback["artifact_sha256"] != value["artifact_sha256"]
            or rollback["beta_receipt_id"] != chain[-1]["receipt"]["promotion_id"]
        ):
            raise ConstitutionError("rollback artifact was not already fully receipted")


def fetch_authority_head(ring: str) -> dict:
    head = json.load(urllib.request.urlopen(
        f"https://raw.githubusercontent.com/{AUTHORITY}/main/heads/{ring}.json"
    ))
    expected = {
        "schema", "ring", "sequence", "promotion_id", "authority_commit",
        "receipt_path", "receipt_sha256", "target_repository",
        "target_manifest_commit", "target_manifest_sha256",
    }
    if (
        not isinstance(head, dict)
        or set(head) != expected
        or head["schema"] != "openrappter-ring-head/v1"
        or head["ring"] != ring
        or not isinstance(head["sequence"], int)
        or head["sequence"] < 1
        or not HEX40.fullmatch(head["authority_commit"])
        or head["receipt_path"] != f"receipts/{ring}/{head['promotion_id']}.json"
    ):
        raise ConstitutionError(f"{ring} authority head is malformed")
    return head


def fetch_chain(release: dict) -> list[dict]:
    chain = []
    for ring in ORDER:
        head = fetch_authority_head(ring)
        authority_commit, path = head["authority_commit"], head["receipt_path"]
        receipt = json.load(urllib.request.urlopen(
            f"https://raw.githubusercontent.com/{AUTHORITY}/{authority_commit}/{path}"
        ))
        if _identity(receipt) != _identity(release) or receipt.get("receipt_kind") != "promotion":
            raise ConstitutionError(f"latest finalized {ring} head does not match exact release")
        target_repo = head["target_repository"]
        manifest = json.load(urllib.request.urlopen(
            f"https://raw.githubusercontent.com/{target_repo}/"
            f"{head['target_manifest_commit']}/.ring/manifest.json"
        ))
        if (
            receipt["promotion_id"] != head["promotion_id"]
            or receipt["target_manifest_commit"] != head["target_manifest_commit"]
            or receipt["target_manifest_sha256"] != head["target_manifest_sha256"]
            or digest(receipt) != head["receipt_sha256"]
        ):
            raise ConstitutionError(f"{ring} authority head/receipt mismatch")
        chain.append({
            "ring": ring,
            "authority_commit": authority_commit,
            "receipt_path": path,
            "receipt": receipt,
            "manifest": manifest,
        })
    return chain


def discover_artifact(release: dict) -> dict:
    head = fetch_authority_head("beta")
    receipt = json.load(urllib.request.urlopen(
        f"https://raw.githubusercontent.com/{AUTHORITY}/{head['authority_commit']}/{head['receipt_path']}"
    ))
    if not (
        receipt.get("receipt_kind") == "promotion"
        and receipt.get("source_commit") == release["source_commit"]
        and receipt.get("source_tag") == release["source_tag"]
        and receipt.get("version") == release["version"]
        and digest(receipt) == head["receipt_sha256"]
    ):
        raise ConstitutionError("latest finalized beta artifact does not match release identity")
    result = dict(release)
    for target, source in (
        ("artifact_url", "artifact_url"),
        ("install_url", "install_url"),
        ("artifact_sha256", "artifact_sha256"),
        ("artifact_provenance", "artifact_provenance"),
    ):
        result[target] = receipt[source]
    return result


def verify_candidate_bytes(release: dict, local_dir: Path) -> None:
    if release["artifact_provenance"] != "github-candidate-bundle-sha256":
        raise ConstitutionError("finalized beta artifact is not an immutable candidate bundle")
    bundle = local_dir / ".constitution-candidate.tar.gz"
    extracted = local_dir / ".constitution-candidate"
    shutil.rmtree(extracted, ignore_errors=True)
    extracted.mkdir()
    try:
        with urllib.request.urlopen(release["artifact_url"]) as response:
            bundle.write_bytes(response.read())
        actual = hashlib.sha256(bundle.read_bytes()).hexdigest()
        if actual != release["artifact_sha256"]:
            raise ConstitutionError("candidate bundle digest mismatch")
        with tarfile.open(bundle, "r:gz") as archive:
            for member in archive.getmembers():
                resolved = (extracted / member.name).resolve()
                if extracted.resolve() not in resolved.parents and resolved != extracted.resolve():
                    raise ConstitutionError("candidate archive path traversal")
            archive.extractall(extracted, filter="data")
        provenance = json.loads((extracted / "provenance.json").read_text())
        matched = []
        for row in provenance["files"]:
            name = row["path"]
            candidate = extracted / name
            local = local_dir / name
            if not local.exists():
                continue
            matched.append(name)
            if hashlib.sha256(local.read_bytes()).hexdigest() != row["sha256"]:
                raise ConstitutionError(f"published artifact {name} differs from finalized candidate")
        if (
            not any(name.endswith(".tgz") for name in matched)
            or not any(name.endswith(".whl") for name in matched)
            or not any(name.endswith(".tar.gz") for name in matched)
        ):
            raise ConstitutionError("release job did not present npm, wheel, and sdist candidate bytes")
    finally:
        bundle.unlink(missing_ok=True)
        shutil.rmtree(extracted, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--chain")
    parser.add_argument("--remote", action="store_true")
    parser.add_argument("--discover-artifact", action="store_true")
    parser.add_argument("--local-artifacts-dir")
    args = parser.parse_args()
    try:
        policy = load_policy(Path(args.policy))
        release = json.loads(Path(args.release).read_text())
        if args.discover_artifact:
            release = discover_artifact(release)
        if args.local_artifacts_dir:
            verify_candidate_bytes(release, Path(args.local_artifacts_dir))
        chain = fetch_chain(release) if args.remote else json.loads(Path(args.chain).read_text())
        validate_chain(release, chain, policy)
        print("Release Constitution: exact finalized chain verified")
    except (OSError, json.JSONDecodeError, ConstitutionError, ManifestError) as exc:
        print(f"Release Constitution: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
