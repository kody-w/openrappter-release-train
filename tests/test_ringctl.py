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
    make_head,
    make_receipt,
    validate_applied,
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
            "intended_release_tag": "v2.0.0",
            "channel_version": "0.1.0-beta.11",
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
        ack_args.request_path = f"requests/alpha/{payload['sequence']:020d}-{payload['promotion_id']}.json"
        ack_args.current = str(current_path)
        ack_args.target_manifest_commit = target_commit
        ack_args.output = str(ack_path)
        ack_args.target_ring = "alpha"
        ack_args.target_repository = "kody-w/openrappter-alpha"
        acknowledge(ack_args)
        ack = json.loads(ack_path.read_text())
        self.assertEqual(ack["request_id"], payload["promotion_id"])
        self.assertEqual(ack["request_sha256"], digest(payload))

    def test_receiver_accepts_ancestral_base_and_null_identity_migration_only(self):
        target = self.work / "target-drift"
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir()
        subprocess.run(["git", "-C", str(target), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(target), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(target), "config", "user.name", "Ring Test"], check=True)
        (target / "manifest-marker").write_text("authority base")
        subprocess.run(["git", "-C", str(target), "add", "."], check=True)
        subprocess.run(["git", "-C", str(target), "commit", "-qm", "authority base"], check=True)
        base = subprocess.check_output(
            ["git", "-C", str(target), "rev-parse", "HEAD"], text=True
        ).strip()
        (target / "workflow-marker").write_text("unrelated workflow change")
        subprocess.run(["git", "-C", str(target), "add", "."], check=True)
        subprocess.run(["git", "-C", str(target), "commit", "-qm", "workflow change"], check=True)
        tip = subprocess.check_output(
            ["git", "-C", str(target), "rev-parse", "HEAD"], text=True
        ).strip()

        legacy = json.loads(json.dumps(self.previous))
        legacy.pop("intended_release_tag")
        legacy.pop("channel_version")
        payload = validate_promotion(
            self.source,
            "alpha",
            previous_target=legacy,
            checkout=self.repo,
            target_base_commit=base,
        )
        payload_path = self.work / "drift-payload.json"
        current_path = self.work / "drift-current.json"
        proposed_path = self.work / "drift-proposed.json"
        payload_path.write_text(json.dumps(payload))
        current = {**legacy, "intended_release_tag": None, "channel_version": None}
        current_path.write_text(json.dumps(current))
        args = Args()
        args.payload, args.current, args.output = map(
            str, (payload_path, current_path, proposed_path)
        )
        args.target_ring = "alpha"
        args.target_repository = "kody-w/openrappter-alpha"
        args.current_head = tip
        args.target_checkout = str(target)
        prepare(args)
        self.assertEqual(json.loads(proposed_path.read_text()), payload["target_manifest"])

        current_path.write_text(proposed_path.read_text())
        args.current_head = "f" * 40
        with self.assertRaisesRegex(ManifestError, "not an ancestor"):
            prepare(args)

        args.current_head = tip
        current_path.write_text(json.dumps(current))
        current["channel_version"] = "0.1.0-beta.11"
        current_path.write_text(json.dumps(current))
        with self.assertRaisesRegex(ManifestError, "manifest changed"):
            prepare(args)
        current["channel_version"] = None
        current_path.write_text(json.dumps(current))
        args.current_head = "f" * 40
        with self.assertRaisesRegex(ManifestError, "not an ancestor"):
            prepare(args)

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

    def test_applied_ack_and_monotonic_head_are_exact_and_idempotent(self):
        payload = self.plan()
        manifest = payload["target_manifest"]
        ack = {
            "schema": "openrappter-applied-request/v1",
            "request_id": payload["promotion_id"],
            "request_sequence": payload["sequence"],
            "request_sha256": digest(payload),
            "request_authority_commit": "1" * 40,
            "request_path": f"requests/alpha/{payload['sequence']:020d}-{payload['promotion_id']}.json",
            "target_repository": payload["target_repository"],
            "target_ring": "alpha",
            "target_manifest_sha256": digest(manifest),
            "target_manifest_commit": "2" * 40,
        }
        validate_applied(payload, ack, manifest)
        for field, value in (
            ("request_id", "f" * 64),
            ("request_sha256", "f" * 64),
            ("target_manifest_commit", "bad"),
        ):
            with self.assertRaises(ManifestError):
                validate_applied(payload, {**ack, field: value}, manifest)
        receipt_value = make_receipt(
            payload,
            target_manifest_commit=ack["target_manifest_commit"],
            emitted_at="2026-08-23T20:00:00Z",
        )
        kwargs = {
            "authority_commit": "3" * 40,
            "receipt_path": f"receipts/alpha/{payload['promotion_id']}.json",
            "receipt_sha256": digest(receipt_value),
        }
        head, changed = make_head(receipt_value, **kwargs)
        self.assertTrue(changed)
        self.assertEqual(head["sequence"], 1)
        replay, changed = make_head(receipt_value, existing=head, **kwargs)
        self.assertFalse(changed)
        self.assertEqual(replay, head)
        conflicting = {**receipt_value, "promotion_id": "9" * 64}
        with self.assertRaisesRegex(ManifestError, "previous receipt"):
            make_head(
                conflicting,
                authority_commit="4" * 40,
                receipt_path=f"receipts/alpha/{'9' * 64}.json",
                receipt_sha256=digest(conflicting),
                existing=head,
            )

    def test_finalize_validates_application_before_existing_receipt_success(self):
        workflow = (ROOT / ".github/workflows/finalize-promotion.yml").read_text()
        applied = workflow.index("ringctl.py validate-applied")
        receipt_lookup = workflow.index("contents/$receipt_path?ref=main")
        head_update = workflow.index('head_path="heads/$target_ring.json"')
        self.assertLess(applied, receipt_lookup)
        self.assertLess(receipt_lookup, head_update)


if __name__ == "__main__":
    unittest.main()
