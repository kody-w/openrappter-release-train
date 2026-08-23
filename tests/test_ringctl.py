import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from ringctl import (  # noqa: E402
    ManifestError,
    compare_semver,
    digest,
    make_receipt,
    validate_manifest,
    validate_promotion,
    validate_receipt,
)
from target_receiver import acknowledge, prepare  # noqa: E402


class Args:
    pass


class RingAuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = ROOT / "tests/.ring-e2e"
        shutil.rmtree(cls.work, ignore_errors=True)
        cls.work.mkdir()
        cls.repo = cls.work / "canonical"
        cls.repo.mkdir()
        subprocess.run(["git", "-C", str(cls.repo), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(cls.repo), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(cls.repo), "config", "user.name", "Ring Test"], check=True)
        (cls.repo / "source").write_text("previous")
        subprocess.run(["git", "-C", str(cls.repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(cls.repo), "commit", "-qm", "previous"], check=True)
        cls.previous_commit = subprocess.check_output(
            ["git", "-C", str(cls.repo), "rev-parse", "HEAD"], text=True
        ).strip()
        (cls.repo / "source").write_text("proposed")
        subprocess.run(["git", "-C", str(cls.repo), "commit", "-qam", "proposed"], check=True)
        cls.source_commit = subprocess.check_output(
            ["git", "-C", str(cls.repo), "rev-parse", "HEAD"], text=True
        ).strip()
        subprocess.run(["git", "-C", str(cls.repo), "tag", "v2.0.0"], check=True)
        subprocess.run([
            "git", "-C", str(cls.repo), "update-ref",
            "refs/remotes/origin/main", cls.source_commit,
        ], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work, ignore_errors=True)

    def setUp(self):
        self.source = {
            "schema": "openrappter-ring/v1",
            "ring": "nightly",
            "source": {
                "repository": "kody-w/openrappter",
                "commit": self.source_commit,
                "tag": "v2.0.0",
            },
            "version": "2.0.0",
            "artifact": {
                "url": "https://registry.npmjs.org/openrappter/-/openrappter-2.0.0.tgz",
                "install_url": "https://registry.npmjs.org/openrappter/-/openrappter-2.0.0.tgz",
                "sha256": "a" * 64,
                "provenance": "npm-registry-download-sha256",
            },
            "promoted_at": "2026-08-23T14:38:58Z",
            "predecessor": None,
            "status": "published",
            "reason": None,
            "receipt": None,
            "promotion_id": "b" * 64,
        }
        self.previous = {
            **self.source,
            "ring": "alpha",
            "source": {**self.source["source"], "commit": self.previous_commit, "tag": None},
            "version": "1.9.8",
            "artifact": {
                "url": f"https://github.com/kody-w/openrappter/archive/{self.previous_commit}.tar.gz",
                "install_url": None,
                "sha256": "c" * 64,
                "provenance": "github-commit-archive-sha256",
            },
            "predecessor": "nightly",
            "status": "disabled",
            "reason": "bootstrap target",
            "promotion_id": None,
        }
        self.base = "d" * 40

    def plan(self):
        return validate_promotion(
            self.source,
            "alpha",
            previous_target=self.previous,
            checkout=self.repo,
            target_base_commit=self.base,
        )

    def test_complete_semver_ordering(self):
        vectors = [
            ("1.9.8-beta.1", "1.9.8", -1),
            ("1.9.8-beta.2", "1.9.8-beta.10", -1),
            ("1.9.8-2", "1.9.8-beta", -1),
            ("1.9.8-beta.10", "1.9.8-beta.2", 1),
        ]
        for left, right, expected in vectors:
            self.assertEqual(compare_semver(left, right), expected)

    def test_plan_is_deterministic_and_closed(self):
        first = self.plan()
        second = self.plan()
        self.assertEqual(first, second)
        self.assertEqual(first["promotion_id"], first["target_manifest"]["promotion_id"])
        self.assertEqual(first["target_manifest_sha256"], digest(first["target_manifest"]))

    def test_provenance_and_ancestry_fail_closed(self):
        compromised = json.loads(json.dumps(self.source))
        compromised["artifact"]["url"] = "https://github.com/evil/openrappter/releases/download/v2.0.0/a.tgz"
        compromised["artifact"]["install_url"] = compromised["artifact"]["url"]
        compromised["artifact"]["provenance"] = "github-release-download-sha256"
        with self.assertRaisesRegex(ManifestError, "canonical npm|GitHub"):
            validate_manifest(compromised)
        backwards = json.loads(json.dumps(self.previous))
        backwards["source"]["commit"] = "e" * 40
        with self.assertRaises(ManifestError):
            validate_promotion(
                self.source, "alpha", previous_target=backwards,
                checkout=self.repo, target_base_commit=self.base,
            )

    def test_end_to_end_mutation_receipt_linkage_and_idempotent_replay(self):
        payload = self.plan()
        payload_path = self.work / "payload.json"
        current_path = self.work / "current.json"
        proposed_path = self.work / "proposed.json"
        output_path = self.work / "outputs"
        payload_path.write_text(json.dumps(payload))
        current_path.write_text(json.dumps(self.previous))
        args = Args()
        args.payload, args.current, args.output = map(str, (payload_path, current_path, proposed_path))
        args.target_ring, args.target_repository, args.current_head = (
            "alpha", "kody-w/openrappter-alpha", self.base,
        )
        old_output = os.environ.get("GITHUB_OUTPUT")
        os.environ["GITHUB_OUTPUT"] = str(output_path)
        try:
            prepare(args)
            applied = json.loads(proposed_path.read_text())
            self.assertEqual(applied, payload["target_manifest"])
            current_path.write_text(proposed_path.read_text())
            output_path.write_text("")
            prepare(args)
            self.assertIn("noop=true", output_path.read_text())
        finally:
            if old_output is None:
                os.environ.pop("GITHUB_OUTPUT", None)
            else:
                os.environ["GITHUB_OUTPUT"] = old_output

        target_commit = "f" * 40
        receipt_value = make_receipt(
            payload,
            target_manifest_commit=target_commit,
            emitted_at="2026-08-23T20:00:00Z",
        )
        validate_receipt(
            receipt_value,
            target_repository="kody-w/openrappter-alpha",
            target_ring="alpha",
            current_manifest=applied,
            immutable_manifest=applied,
        )
        ack_path = self.work / "applied.json"
        ack_args = Args()
        ack_args.request = str(payload_path)
        ack_args.request_commit = "1" * 40
        ack_args.request_path = f"requests/alpha/{payload['promotion_id']}.json"
        ack_args.current = str(current_path)
        ack_args.target_manifest_commit = target_commit
        ack_args.output = str(ack_path)
        ack_args.target_ring = "alpha"
        ack_args.target_repository = "kody-w/openrappter-alpha"
        acknowledge(ack_args)
        ack = json.loads(ack_path.read_text())
        self.assertEqual(ack["request_id"], payload["promotion_id"])
        self.assertEqual(ack["request_sha256"], digest(payload))

    def test_failed_acknowledgement_creates_no_receipt_and_compromise_is_rejected(self):
        payload = self.plan()
        with self.assertRaises(ManifestError):
            make_receipt(
                {**payload, "target_manifest": self.previous},
                target_manifest_commit="f" * 40,
                emitted_at="2026-08-23T20:00:00Z",
            )
        receipt_value = make_receipt(
            payload,
            target_manifest_commit="f" * 40,
            emitted_at="2026-08-23T20:00:00Z",
        )
        compromised = json.loads(json.dumps(payload["target_manifest"]))
        compromised["source"]["commit"] = self.previous_commit
        with self.assertRaises(ManifestError):
            validate_receipt(
                receipt_value,
                target_repository="kody-w/openrappter-alpha",
                target_ring="alpha",
                current_manifest=compromised,
                immutable_manifest=payload["target_manifest"],
            )

    def test_pull_workflows_use_only_repo_scoped_tokens(self):
        workflows = "\n".join(
            path.read_text() for path in (ROOT / ".github/workflows").glob("*.yml")
        )
        self.assertNotIn("RING_AUTHORITY_TOKEN", workflows)
        self.assertNotIn("repository_dispatch", workflows)
        self.assertIn("missing/mismatched target acknowledgement", workflows)
        self.assertIn("contents: write", workflows)


if __name__ == "__main__":
    unittest.main()
