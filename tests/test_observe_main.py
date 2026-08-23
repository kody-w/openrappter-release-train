import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from observe_main import (  # noqa: E402
    ObservationError,
    build_request,
    nightly_request_id,
    verify_green_head,
)
from ringctl import digest  # noqa: E402


class ObserveMainTests(unittest.TestCase):
    def setUp(self):
        self.head = "a" * 40
        self.required = ["TypeScript (Node 22)", "Python (3.12)"]
        self.checks = {
            "check_runs": [
                {
                    "id": index + 1,
                    "name": name,
                    "head_sha": self.head,
                    "status": "completed",
                    "conclusion": "success",
                }
                for index, name in enumerate(self.required)
            ]
        }
        self.previous = json.loads(
            (ROOT / "tests/fixtures/nightly.json").read_text()
        )

    def test_exact_green_head_is_accepted(self):
        verify_green_head(self.checks, self.head, self.required)

    def test_red_and_pending_main_are_refused(self):
        for status, conclusion, expected in (
            ("completed", "failure", "concluded"),
            ("in_progress", None, "pending"),
        ):
            checks = json.loads(json.dumps(self.checks))
            checks["check_runs"][0].update(status=status, conclusion=conclusion)
            with self.assertRaisesRegex(ObservationError, expected):
                verify_green_head(checks, self.head, self.required)

    def test_checks_from_another_commit_do_not_authorize_head(self):
        checks = json.loads(json.dumps(self.checks))
        checks["check_runs"][0]["head_sha"] = "b" * 40
        with self.assertRaisesRegex(ObservationError, "exact head"):
            verify_green_head(checks, self.head, self.required)

    def test_duplicate_green_commit_has_one_deterministic_request(self):
        kwargs = {
            "head": self.head,
            "package_version": "2.0.0",
            "committed_at": "2026-08-23T20:00:00Z",
            "artifact_sha256": "c" * 64,
            "previous_manifest": self.previous,
            "target_base_commit": "d" * 40,
        }
        first = build_request(**kwargs)
        second = build_request(**kwargs)
        self.assertEqual(first, second)
        self.assertEqual(first["promotion_id"], first["target_manifest"]["promotion_id"])
        expected = nightly_request_id(
            head=self.head,
            version="2.0.0-nightly.20260823+gaaaaaaaa",
            artifact_url=f"https://github.com/kody-w/openrappter/archive/{self.head}.tar.gz",
            artifact_sha256="c" * 64,
            promoted_at="2026-08-23T20:00:00Z",
        )
        self.assertEqual(first["promotion_id"], expected)
        self.assertIsNone(first["from"])
        self.assertEqual(set(first), {
            "schema", "promotion_id", "from", "to", "target_repository",
            "target_base_commit", "target_previous_manifest_sha256",
            "target_previous_source_commit", "source_repository",
            "source_commit", "source_tag", "version", "artifact_url",
            "install_url", "artifact_sha256", "artifact_provenance",
            "promoted_at", "predecessor_manifest_sha256", "target_manifest",
            "target_manifest_sha256",
        })
        self.assertEqual(first["target_previous_manifest_sha256"], digest(self.previous))
        self.assertEqual(first["target_previous_source_commit"], self.previous["source"]["commit"])

    def test_workflow_rechecks_exact_head_before_writing(self):
        workflow = (ROOT / ".github/workflows/observe-main.yml").read_text()
        first_read = workflow.index('head="$(gh api repos/kody-w/openrappter/commits/main')
        exact_check = workflow.index('test "$(gh api repos/kody-w/openrappter/commits/main')
        write = workflow.index('--method PUT')
        self.assertLess(first_read, exact_check)
        self.assertLess(exact_check, write)
        self.assertNotIn("repository_dispatch", workflow)
        self.assertNotIn("secrets.", workflow)
        self.assertIn("observe_main.py build", workflow)
        self.assertNotIn("client_payload", workflow)


if __name__ == "__main__":
    unittest.main()
