import json
import sys
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from observe_main import (  # noqa: E402
    ObservationError,
    build_request,
    candidate_fields,
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
            "artifact_url": (
                f"https://raw.githubusercontent.com/kody-w/openrappter/"
                f"{'b' * 40}/candidates/{self.head}/{'c' * 64}.tar.gz"
            ),
            "artifact_sha256": "c" * 64,
            "previous_manifest": self.previous,
            "target_base_commit": "d" * 40,
            "sequence": 1,
            "release_tag": "v2.0.0",
            "candidate_kind": "release",
        }
        first = build_request(**kwargs)
        second = build_request(**kwargs)
        self.assertEqual(first, second)
        self.assertEqual(first["promotion_id"], first["target_manifest"]["promotion_id"])
        expected = nightly_request_id(
            head=self.head,
            version="2.0.0",
            artifact_url=kwargs["artifact_url"],
            artifact_sha256="c" * 64,
            promoted_at="2026-08-23T20:00:00Z",
            source_tag="v2.0.0",
            published=True,
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
            "sequence",
        })
        self.assertEqual(first["target_previous_manifest_sha256"], digest(self.previous))
        self.assertEqual(first["target_previous_source_commit"], self.previous["source"]["commit"])

    def test_workflow_rechecks_exact_head_before_writing(self):
        workflow = (ROOT / ".github/workflows/observe-main.yml").read_text()
        first_read = workflow.index('head="$(gh api repos/kody-w/openrappter/commits/main')
        exact_check = workflow.index('test "$(gh api repos/kody-w/openrappter/commits/main')
        write = workflow.index('git push origin HEAD:main')
        self.assertLess(first_read, exact_check)
        self.assertLess(exact_check, write)
        self.assertNotIn("repository_dispatch", workflow)
        self.assertNotIn("secrets.", workflow)
        self.assertIn("observe_main.py build", workflow)
        self.assertNotIn("client_payload", workflow)
        self.assertIn("heads/nightly.json", workflow)
        self.assertNotIn("openrappter-nightly/commits/main", workflow)

    def test_candidate_workflow_output_parses_as_one_tab_delimited_record(self):
        work = ROOT / "tests/.candidate-output"
        work.mkdir(exist_ok=True)
        try:
            provenance = {
                "schema": "openrappter-candidate-provenance/v1",
                "channel": "candidate",
                "stable": False,
                "candidate_kind": "release",
                "release_tag": "v2.0.0",
                "source_commit": self.head,
                "version": "2.0.0",
            }
            (work / "provenance.json").write_text(json.dumps(provenance))
            (work / "bundles.txt").write_text(f"{'c' * 64}.tar.gz\nprovenance.json\n")
            result = subprocess.run(
                [
                    sys.executable, str(ROOT / "scripts/observe_main.py"), "candidate",
                    "--provenance", str(work / "provenance.json"),
                    "--bundle-list", str(work / "bundles.txt"),
                    "--head", self.head, "--candidate-commit", "b" * 40,
                ],
                text=True, capture_output=True, check=True,
            )
            fields = result.stdout.rstrip("\n").split("\t")
            self.assertEqual(len(fields), 4)
            self.assertEqual(fields[:3], ["2.0.0", "release", "v2.0.0"])
            self.assertRegex(fields[3], rf"/{'c' * 64}\.tar\.gz$")
        finally:
            __import__("shutil").rmtree(work, ignore_errors=True)

    def test_candidate_bundle_count_and_sha_fail_closed(self):
        provenance = {
            "schema": "openrappter-candidate-provenance/v1",
            "channel": "candidate",
            "stable": False,
            "candidate_kind": "snapshot",
            "release_tag": None,
            "source_commit": self.head,
            "version": "2.0.0",
        }
        with self.assertRaisesRegex(ObservationError, "exactly one"):
            candidate_fields(provenance, [], self.head, "b" * 40)
        with self.assertRaisesRegex(ObservationError, "exactly one"):
            candidate_fields(
                provenance,
                [f"{'c' * 64}.tar.gz", f"{'d' * 64}.tar.gz"],
                self.head,
                "b" * 40,
            )


if __name__ == "__main__":
    unittest.main()
