import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from release_gate import ConstitutionError, load_policy, validate_chain  # noqa: E402
from ringctl import digest  # noqa: E402


class ReleaseConstitutionTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_policy(ROOT / "contracts/release-constitution-v1.json")
        self.release = {
            "schema": "openrappter-release/v1",
            "mode": "normal",
            "source_commit": "a" * 40,
            "source_tag": "v2.0.0",
            "intended_release_tag": "v2.0.0",
            "channel_version": "0.1.0-beta.11",
            "version": "2.0.0",
            "artifact_url": (
                f"https://raw.githubusercontent.com/kody-w/openrappter/"
                f"{'c' * 40}/candidates/{'a' * 40}/{'b' * 64}.tar.gz"
            ),
            "install_url": (
                f"https://raw.githubusercontent.com/kody-w/openrappter/"
                f"{'c' * 40}/candidates/{'a' * 40}/{'b' * 64}.tar.gz"
            ),
            "artifact_sha256": "b" * 64,
            "artifact_provenance": "github-candidate-bundle-sha256",
            "rollback_receipt": None,
        }
        self.chain = []
        previous = None
        for index, ring in enumerate(("nightly", "alpha", "canary", "beta")):
            promotion_id = f"{index + 1:064x}"
            manifest = {
                "schema": "openrappter-ring/v1",
                "ring": ring,
                "source": {
                    "repository": "kody-w/openrappter",
                    "commit": self.release["source_commit"],
                    "tag": None,
                },
                "version": self.release["version"],
                "artifact": {
                    "url": self.release["artifact_url"],
                    "install_url": self.release["install_url"],
                    "sha256": self.release["artifact_sha256"],
                    "provenance": self.release["artifact_provenance"],
                },
                "promoted_at": f"2026-08-23T20:0{index}:00Z",
                "predecessor": None if ring == "nightly" else ("nightly", "alpha", "canary")[index - 1],
                "status": "published",
                "reason": None,
                "receipt": None,
                "promotion_id": promotion_id,
                "intended_release_tag": self.release["intended_release_tag"],
                "channel_version": self.release["channel_version"],
            }
            receipt = {
                "schema": "openrappter-promotion-receipt/v1",
                "receipt_kind": "promotion",
                "sequence": index + 1,
                "promotion_id": promotion_id,
                "target_repository": f"kody-w/openrappter-{ring}",
                "target_ring": ring,
                "target_manifest_sha256": digest(manifest),
                "target_manifest_commit": chr(99 + index) * 40,
                "source_repository": "kody-w/openrappter",
                "source_commit": self.release["source_commit"],
                "source_tag": None,
                "intended_release_tag": self.release["intended_release_tag"],
                "channel_version": self.release["channel_version"],
                "version": self.release["version"],
                "artifact_url": self.release["artifact_url"],
                "install_url": self.release["install_url"],
                "artifact_sha256": self.release["artifact_sha256"],
                "artifact_provenance": self.release["artifact_provenance"],
                "predecessor_manifest_sha256": digest(previous) if previous else "0" * 64,
                "emitted_at": f"2026-08-23T20:0{index}:30Z",
            }
            self.chain.append({
                "ring": ring,
                "authority_commit": f"{index + 10:040x}",
                "receipt_path": f"receipts/{ring}/{promotion_id}.json",
                "receipt": receipt,
                "manifest": manifest,
            })
            previous = manifest

    def test_exact_valid_chain(self):
        validate_chain(self.release, self.chain, self.policy)

    def test_skipped_alpha_and_receipt_order_fail(self):
        with self.assertRaisesRegex(ConstitutionError, "order"):
            validate_chain(self.release, [self.chain[0], *self.chain[2:]], self.policy)
        swapped = copy.deepcopy(self.chain)
        swapped[1], swapped[2] = swapped[2], swapped[1]
        with self.assertRaisesRegex(ConstitutionError, "order"):
            validate_chain(self.release, swapped, self.policy)

    def test_stale_beta_and_identity_mismatches_fail(self):
        for field, value in (
            ("source_commit", "f" * 40),
            ("version", "1.9.9"),
            ("artifact_sha256", "f" * 64),
        ):
            chain = copy.deepcopy(self.chain)
            if field == "source_commit":
                chain[-1]["manifest"]["source"]["commit"] = value
            elif field == "version":
                chain[-1]["manifest"]["version"] = value
            else:
                chain[-1]["manifest"]["artifact"]["sha256"] = value
            with self.assertRaises(ConstitutionError):
                validate_chain(self.release, chain, self.policy)

    def test_pending_or_disabled_ring_fails_closed(self):
        chain = copy.deepcopy(self.chain)
        chain[2]["manifest"]["status"] = "disabled"
        chain[2]["manifest"]["reason"] = "rollout pending"
        with self.assertRaises(ConstitutionError):
            validate_chain(self.release, chain, self.policy)

    def test_mutable_or_untrusted_receipt_path_fails(self):
        chain = copy.deepcopy(self.chain)
        chain[0]["authority_commit"] = "main"
        with self.assertRaisesRegex(ConstitutionError, "mutable"):
            validate_chain(self.release, chain, self.policy)
        chain = copy.deepcopy(self.chain)
        chain[0]["receipt_path"] = "requests/nightly/request.json"
        with self.assertRaisesRegex(ConstitutionError, "mutable"):
            validate_chain(self.release, chain, self.policy)

    def test_rollback_to_unreceipted_artifact_fails(self):
        release = copy.deepcopy(self.release)
        release["mode"] = "rollback"
        release["rollback_receipt"] = {
            "schema": "openrappter-rollback-receipt/v1",
            "source_commit": release["source_commit"],
            "version": release["version"],
            "artifact_sha256": release["artifact_sha256"],
            "beta_receipt_id": "f" * 64,
            "created_at": "2026-08-23T21:00:00Z",
        }
        with self.assertRaisesRegex(ConstitutionError, "not already fully receipted"):
            validate_chain(release, self.chain, self.policy)


if __name__ == "__main__":
    unittest.main()
