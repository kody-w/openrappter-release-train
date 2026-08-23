import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from request_queue import QueueError, next_applicable, select  # noqa: E402


class RequestQueueTests(unittest.TestCase):
    def index(self):
        return {
            "schema": "openrappter-request-index/v1",
            "ring": "beta",
            "base_sequence": 1,
            "next_sequence": 4,
            "entries": [
                {"sequence": 2, "request_id": "f" * 64, "path": "requests/beta/00000000000000000002-" + "f" * 64 + ".json"},
                {"sequence": 3, "request_id": "0" * 64, "path": "requests/beta/00000000000000000003-" + "0" * 64 + ".json"},
            ],
        }

    def test_low_hash_later_request_cannot_starve_earlier_sequence(self):
        first = select(self.index(), cursor=1)
        self.assertEqual(first["sequence"], 2)
        self.assertEqual(first["request_id"], "f" * 64)
        second = select(self.index(), cursor=2)
        self.assertEqual(second["sequence"], 3)
        self.assertEqual(second["request_id"], "0" * 64)

    def test_gap_and_skip_fail_closed(self):
        broken = self.index()
        broken["entries"][1]["sequence"] = 4
        with self.assertRaisesRegex(QueueError, "gap"):
            select(broken, cursor=2)
        with self.assertRaisesRegex(QueueError, "skips"):
            select(self.index(), cursor=1, requested=3)

    def test_acknowledged_replay_is_idempotent(self):
        self.assertIsNone(select(self.index(), cursor=2, requested=2))

    def test_n_plus_one_waits_until_n_is_finalized(self):
        self.assertIsNone(next_applicable(self.index(), finalized=1, applied=2))
        request = next_applicable(self.index(), finalized=2, applied=2)
        self.assertEqual(request["sequence"], 3)
        with self.assertRaisesRegex(QueueError, "trails"):
            next_applicable(self.index(), finalized=2, applied=1)


if __name__ == "__main__":
    unittest.main()
