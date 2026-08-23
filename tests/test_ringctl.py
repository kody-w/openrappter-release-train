import copy
import datetime as dt
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from ringctl import (  # noqa: E402
    ManifestError,
    validate_manifest,
    validate_promotion,
    write_receipt,
)


class RingCtlTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(
            (ROOT / "tests/fixtures/nightly.json").read_text()
        )
        self.now = dt.datetime(2026, 8, 23, 20, tzinfo=dt.timezone.utc)

    def test_valid_closed_manifest(self):
        validate_manifest(self.manifest, expected_ring="nightly", now=self.now)

    def test_rejects_unknown_fields(self):
        self.manifest["repository"] = "attacker/repo"
        with self.assertRaisesRegex(ManifestError, "not closed"):
            validate_manifest(self.manifest, now=self.now)

    def test_rejects_repository_and_url_injection(self):
        for field, value in (
            ("repository", "attacker/openrappter"),
            ("url", "https://evil.example/artifact.tgz"),
        ):
            candidate = copy.deepcopy(self.manifest)
            if field == "repository":
                candidate["source"][field] = value
            else:
                candidate["artifact"][field] = value
            with self.assertRaises(ManifestError):
                validate_manifest(candidate, now=self.now)

    def test_rejects_future_manifest(self):
        self.manifest["promoted_at"] = "2026-08-24T20:00:00Z"
        with self.assertRaisesRegex(ManifestError, "future"):
            validate_manifest(self.manifest, now=self.now)

    def test_nonpublished_requires_reason(self):
        self.manifest["reason"] = None
        with self.assertRaisesRegex(ManifestError, "explicit reason"):
            validate_manifest(self.manifest, now=self.now)

    def test_promotion_is_one_step_and_payload_is_closed(self):
        payload = validate_promotion(self.manifest, "alpha")
        self.assertEqual(payload["target_repository"], "kody-w/openrappter-alpha")
        self.assertEqual(payload["source_commit"], self.manifest["source"]["commit"])
        with self.assertRaisesRegex(ManifestError, "exactly one ring"):
            validate_promotion(self.manifest, "beta")

    def test_downgrade_is_rejected(self):
        previous = copy.deepcopy(self.manifest)
        previous["ring"] = "alpha"
        previous["predecessor"] = "nightly"
        previous["version"] = "2.0.0-alpha.1"
        with self.assertRaisesRegex(ManifestError, "downgrade"):
            validate_promotion(self.manifest, "alpha", previous_target=previous)

    def test_dispatch_workflow_carries_exact_identity_and_fails_without_secret(self):
        workflow = (ROOT / ".github/workflows/promote.yml").read_text()
        self.assertIn("if [ -z \"${RING_AUTHORITY_TOKEN:-}\" ]", workflow)
        for key in (
            "source_commit", "source_tag", "version", "artifact_url",
            "artifact_sha256", "predecessor_manifest_sha256",
        ):
            self.assertIn(f"client_payload[{key}]", workflow)

    def test_receipts_are_create_only(self):
        payload = validate_promotion(self.manifest, "alpha")
        destination = ROOT / "tests/.receipt-test.json"
        destination.unlink(missing_ok=True)
        try:
            write_receipt(payload, destination, emitted_at="2026-08-23T20:00:00Z")
            receipt = json.loads(destination.read_text())
            self.assertEqual(receipt["promotion"], payload)
            self.assertEqual(len(receipt["payload_sha256"]), 64)
            with self.assertRaises(FileExistsError):
                write_receipt(payload, destination)
        finally:
            destination.unlink(missing_ok=True)

    def test_nightly_dispatch_payload_is_exact_and_authorized(self):
        workflow = (ROOT / ".github/workflows/nightly-candidate.yml").read_text()
        self.assertIn("RING_AUTHORITY_TOKEN is required", workflow)
        self.assertIn("grep -Eq '^[0-9a-f]{40}$'", workflow)
        for key in (
            "source_commit", "version", "artifact_url",
            "artifact_sha256", "ci_run_id",
        ):
            self.assertIn(f"client_payload[{key}]", workflow)


if __name__ == "__main__":
    unittest.main()
