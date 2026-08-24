import json
import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RULESET = ROOT / "rulesets" / "release-constitution.json"
HELPER = ROOT / "scripts" / "apply_release_constitution_ruleset.sh"


class ReleaseRulesetSetupTests(unittest.TestCase):
    def test_solo_maintainer_prs_require_the_gate_not_an_impossible_approval(self):
        value = json.loads(RULESET.read_text())
        pull_request = next(rule for rule in value["rules"] if rule["type"] == "pull_request")
        parameters = pull_request["parameters"]
        self.assertEqual(parameters["required_approving_review_count"], 0)
        self.assertFalse(parameters["require_code_owner_review"])
        self.assertFalse(parameters["require_extra_approval_for_unattributed_changes"])
        self.assertFalse(parameters["require_last_push_approval"])
        self.assertEqual(parameters["allowed_merge_methods"], ["merge"])
        status = next(
            rule for rule in value["rules"] if rule["type"] == "required_status_checks"
        )
        self.assertEqual(
            status["parameters"]["required_status_checks"],
            [{"context": "Release Constitution"}],
        )
        self.assertEqual(value["bypass_actors"], [])

    def test_helper_is_executable(self):
        self.assertTrue(HELPER.stat().st_mode & stat.S_IXUSR)

    def test_existing_ruleset_is_updated_not_duplicated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            log = root / "calls.log"
            fake = root / "gh"
            fake.write_text(
                textwrap.dedent(
                    f"""\
                    #!/usr/bin/env bash
                    set -euo pipefail
                    printf '%s\\n' "$*" >> {str(log)!r}
                    if [[ "$*" == *"repos/kody-w/openrappter/rulesets --jq"* ]]; then
                      printf '21261374\\n'
                    fi
                    """
                )
            )
            fake.chmod(0o700)
            environment = dict(os.environ)
            environment["PATH"] = f"{root}{os.pathsep}{environment['PATH']}"
            subprocess.run(
                ["bash", str(HELPER), "kody-w/openrappter"],
                check=True,
                cwd=ROOT,
                env=environment,
            )
            calls = log.read_text()
            self.assertIn(
                "repos/kody-w/openrappter/rulesets/21261374 --method PUT", calls
            )
            self.assertNotIn(
                "repos/kody-w/openrappter/rulesets --method POST", calls
            )


if __name__ == "__main__":
    unittest.main()
