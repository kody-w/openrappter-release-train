import hashlib
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ringctl import digest  # noqa: E402


class CandidateWorkflowIntegrationTests(unittest.TestCase):
    def run_script(self, script: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / script), *args],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        )

    def test_real_bundle_observe_apply_ack_finalize_and_head(self):
        source = "a" * 40
        candidate_commit = "b" * 40
        target_base = "d" * 40
        target_commit = "e" * 40
        request_commit = "f" * 40
        candidate_id = "tag-djIuMC4w"
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            payload = work / "payload"
            payload.mkdir()
            for name, content in {
                "openrappter-2.0.0.tgz": b"npm bytes\n",
                "openrappter-2.0.0-py3-none-any.whl": b"wheel bytes\n",
                "openrappter-2.0.0.tar.gz": b"sdist bytes\n",
                "install.sh": b"#!/bin/sh\n",
                "install.ps1": b"Write-Output openrappter\n",
            }.items():
                (payload / name).write_bytes(content)
            bundle = work / "candidate.tar.gz"
            with tarfile.open(bundle, "w:gz") as archive:
                for artifact in sorted(payload.iterdir()):
                    archive.add(artifact, arcname=artifact.name)
            bundle_sha = hashlib.sha256(bundle.read_bytes()).hexdigest()

            provenance = {
                "schema": "openrappter-candidate-provenance/v1",
                "channel": "candidate",
                "stable": False,
                "candidate_kind": "release",
                "candidate_id": candidate_id,
                "source_commit": source,
                "source_tag": None,
                "intended_release_tag": "v2.0.0",
                "versions": {
                    "npm": "2.0.0",
                    "pypi": "2.0.0",
                    "runtime": "2.0.0",
                    "channel": "0.1.0-beta.11",
                },
            }
            entry = {
                "id": candidate_id,
                "bundle_sha256": bundle_sha,
                "path": f"candidates/{source}/release/{candidate_id}",
                "provenance_path": "provenance.json",
                "source_date_epoch": 1,
            }
            previous = {
                "schema": "openrappter-ring/v1",
                "ring": "nightly",
                "source": {
                    "repository": "kody-w/openrappter",
                    "commit": "9" * 40,
                    "tag": None,
                },
                "version": "1.9.0",
                "artifact": {
                    "url": f"https://github.com/kody-w/openrappter/archive/{'9' * 40}.tar.gz",
                    "install_url": None,
                    "sha256": "8" * 64,
                    "provenance": "github-commit-archive-sha256",
                },
                "promoted_at": "2026-08-22T20:00:00Z",
                "predecessor": None,
                "status": "unpublished",
                "reason": "Bootstrap base.",
                "receipt": None,
                "promotion_id": None,
                "intended_release_tag": None,
                "channel_version": None,
            }
            provenance_path = work / "provenance.json"
            entry_path = work / "entry.json"
            previous_path = work / "previous.json"
            provenance_path.write_text(json.dumps(provenance))
            entry_path.write_text(json.dumps(entry))
            previous_path.write_text(json.dumps(previous))

            candidate = self.run_script(
                "observe_main.py", "candidate",
                "--provenance", str(provenance_path),
                "--entry", str(entry_path),
                "--head", source,
                "--candidate-commit", candidate_commit,
            ).stdout.strip().split("\t")
            version, kind, intended_tag, artifact_url, channel_version, source_tag = candidate
            self.assertEqual(hashlib.sha256(bundle.read_bytes()).hexdigest(), bundle_sha)
            self.assertTrue(artifact_url.endswith(f"/release/{candidate_id}/{bundle_sha}.tar.gz"))

            request_path = work / "request.json"
            self.run_script(
                "observe_main.py", "build",
                "--head", source,
                "--package-version", version,
                "--committed-at", "2026-08-23T20:00:00Z",
                "--artifact-url", artifact_url,
                "--artifact-sha256", bundle_sha,
                "--previous-manifest", str(previous_path),
                "--target-base-commit", target_base,
                "--sequence", "1",
                "--release-tag", intended_tag,
                "--candidate-kind", kind,
                "--source-tag", "" if source_tag == "-" else source_tag,
                "--channel-version", channel_version,
                "--output", str(request_path),
            )
            request = json.loads(request_path.read_text())

            proposed_path = work / "proposed.json"
            self.run_script(
                "target_receiver.py", "prepare",
                "--payload", str(request_path),
                "--current", str(previous_path),
                "--output", str(proposed_path),
                "--target-ring", "nightly",
                "--target-repository", "kody-w/openrappter-nightly",
                "--current-head", target_base,
            )
            applied_path = work / "applied.json"
            request_storage_path = (
                f"requests/nightly/{request['sequence']:020d}-{request['promotion_id']}.json"
            )
            self.run_script(
                "target_receiver.py", "acknowledge",
                "--request", str(request_path),
                "--request-commit", request_commit,
                "--request-path", request_storage_path,
                "--current", str(proposed_path),
                "--target-manifest-commit", target_commit,
                "--output", str(applied_path),
                "--target-ring", "nightly",
                "--target-repository", "kody-w/openrappter-nightly",
            )
            verified = self.run_script(
                "ringctl.py", "validate-applied",
                "--request", str(request_path),
                "--applied", str(applied_path),
                "--manifest", str(proposed_path),
            )
            self.assertEqual(verified.stdout.strip(), target_commit)

            receipt_path = work / "receipt.json"
            self.run_script(
                "ringctl.py", "receipt", str(request_path),
                "--target-manifest-commit", target_commit,
                "--emitted-at", "2026-08-23T20:01:00Z",
                "--out", str(receipt_path),
            )
            receipt = json.loads(receipt_path.read_text())
            authority_receipt_path = f"receipts/nightly/{request['promotion_id']}.json"
            head_path = work / "head.json"
            self.run_script(
                "ringctl.py", "head",
                "--receipt", str(receipt_path),
                "--authority-commit", request_commit,
                "--receipt-path", authority_receipt_path,
                "--receipt-sha256", digest(receipt),
                "--out", str(head_path),
            )
            head = json.loads(head_path.read_text())
            self.assertEqual(head["sequence"], 1)
            self.assertEqual(head["target_manifest_sha256"], digest(json.loads(proposed_path.read_text())))
            self.assertEqual(receipt["artifact_sha256"], bundle_sha)


if __name__ == "__main__":
    unittest.main()
