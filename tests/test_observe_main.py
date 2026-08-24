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
    select_candidate,
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
            "source_tag": None,
            "channel_version": "0.1.0-beta.11",
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
            source_tag=None,
            published=True,
            intended_release_tag="v2.0.0",
            channel_version="0.1.0-beta.11",
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
            "intended_release_tag", "channel_version",
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
                "candidate_id": "v2.0.0",
                "source_tag": None,
                "intended_release_tag": "v2.0.0",
                "source_commit": self.head,
                "versions": {"npm": "2.0.0", "pypi": "2.0.0", "runtime": "2.0.0", "channel": "2.0.0"},
            }
            (work / "provenance.json").write_text(json.dumps(provenance))
            entry = {"id": "v2.0.0", "bundle_sha256": "c" * 64, "path": f"candidates/{self.head}/release/v2.0.0", "provenance_path": "unused", "source_date_epoch": 1}
            (work / "entry.json").write_text(json.dumps(entry))
            result = subprocess.run(
                [
                    sys.executable, str(ROOT / "scripts/observe_main.py"), "candidate",
                    "--provenance", str(work / "provenance.json"),
                    "--entry", str(work / "entry.json"),
                    "--head", self.head, "--candidate-commit", "b" * 40,
                ],
                text=True, capture_output=True, check=True,
            )
            fields = result.stdout.rstrip("\n").split("\t")
            self.assertEqual(len(fields), 6)
            self.assertEqual(fields[:3], ["2.0.0", "release", "v2.0.0"])
            self.assertRegex(fields[3], rf"/{'c' * 64}\.tar\.gz$")
            self.assertEqual(fields[4:], ["2.0.0", "-"])
        finally:
            __import__("shutil").rmtree(work, ignore_errors=True)

    def test_candidate_bundle_count_and_sha_fail_closed(self):
        provenance = {
            "schema": "openrappter-candidate-provenance/v1",
            "channel": "candidate",
            "stable": False,
            "candidate_kind": "snapshot",
            "candidate_id": "snapshot-1",
            "source_tag": None,
            "intended_release_tag": None,
            "source_commit": self.head,
            "versions": {"npm": "2.0.0", "pypi": "2.0.0", "runtime": "2.0.0", "channel": "snapshot"},
        }
        with self.assertRaisesRegex(ObservationError, "malformed"):
            candidate_fields(provenance, {"id": "snapshot-1", "bundle_sha256": "", "path": f"candidates/{self.head}/snapshot/snapshot-1"}, self.head, "b" * 40)

    def test_same_commit_snapshot_and_release_selection_is_explicit(self):
        index = {
            "schema": "openrappter-candidate-index/v1",
            "source_commit": self.head,
            "snapshots": [
                {"id": "s1", "source_date_epoch": 1},
                {"id": "s2", "source_date_epoch": 2},
            ],
            "releases": [{"id": "v0.1.0-beta.11", "source_date_epoch": 1}],
        }
        self.assertEqual(select_candidate(index, "snapshot", None)["id"], "s2")
        self.assertEqual(select_candidate(index, "snapshot", "s1")["id"], "s1")
        self.assertEqual(select_candidate(index, "release", "v0.1.0-beta.11")["id"], "v0.1.0-beta.11")
        with self.assertRaisesRegex(ObservationError, "not found"):
            select_candidate(index, "release", "missing")


if __name__ == "__main__":
    unittest.main()
