import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ringctl import ManifestError, validate_manifest  # noqa: E402


class LegacyCandidatePathRegressionTests(unittest.TestCase):
    def test_legacy_flat_candidate_path_is_rejected(self):
        source = "a" * 40
        sha256 = "c" * 64
        legacy_url = (
            "https://raw.githubusercontent.com/kody-w/openrappter/"
            f"{'b' * 40}/candidates/{source}/{sha256}.tar.gz"
        )
        manifest = {
            "schema": "openrappter-ring/v1",
            "ring": "nightly",
            "source": {
                "repository": "kody-w/openrappter",
                "commit": source,
                "tag": None,
            },
            "version": "2.0.0",
            "artifact": {
                "url": legacy_url,
                "install_url": legacy_url,
                "sha256": sha256,
                "provenance": "github-candidate-bundle-sha256",
            },
            "promoted_at": "2026-08-23T20:00:00Z",
            "predecessor": None,
            "status": "published",
            "reason": None,
            "receipt": None,
            "promotion_id": "d" * 64,
            "intended_release_tag": "v2.0.0",
            "channel_version": "0.1.0-beta.11",
        }
        with self.assertRaisesRegex(ManifestError, "artifact URL"):
            validate_manifest(manifest, expected_ring="nightly")


if __name__ == "__main__":
    unittest.main()
