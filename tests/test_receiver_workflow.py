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


if __name__ == "__main__":
    unittest.main()
