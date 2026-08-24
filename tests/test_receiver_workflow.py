import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReceiverWorkflowContractTests(unittest.TestCase):
    def test_requested_sequence_uses_validated_string_workaround(self):
        workflow = (ROOT / ".github/workflows/apply-request.yml").read_text()
        self.assertIn("type: string", workflow)
        self.assertIn("default: '0'", workflow)
        self.assertIn(
            '[[ "$REQUESTED_SEQUENCE" =~ ^(0|[1-9][0-9]*)$ ]]',
            workflow,
        )
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("--target-checkout target", workflow)
        self.assertIn('if cursor_content="$(gh api ', workflow)
        rules_check = workflow.index('rules/branches/main')
        prepare = workflow.index("target_receiver.py prepare")
        self.assertLess(rules_check, prepare)
        self.assertIn('{"deletion", "non_fast_forward"}', workflow)
        self.assertIn(
            'blob="$(git -C target rev-parse HEAD:.ring/manifest.json)"',
            workflow,
        )
        self.assertIn("optional_content_sha()", workflow)
        self.assertNotIn("--jq .sha 2>/dev/null || true", workflow)


if __name__ == "__main__":
    unittest.main()
