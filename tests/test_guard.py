"""Local SQLite contract tests; real AstrBot/OneBot tests run separately."""

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location(
    "guard_store", Path(__file__).resolve().parents[1] / "integrations/phase1_guard/store.py"
)
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


class GuardStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "guard.sqlite3"
        self.store = guard.GuardStore(self.path)
        self.key = ("offline", "offline:FriendMessage:10001", "synthetic-1")
        self.chain = [{"type": "text", "data": {"text": "中文🙂"}}]

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def claim(self, key=None):
        return self.store.claim(*(key or self.key), "10001", "中文🙂", self.chain)

    def test_duplicate_preserves_original_and_only_increments_observation(self):
        self.assertTrue(self.claim())
        self.assertFalse(self.store.claim(*self.key, "10001", "changed", []))
        rows = self.store.snapshot()["inbound"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["text"], "中文🙂")
        self.assertEqual(json.loads(rows[0]["chain_json"]), self.chain)
        self.assertEqual(rows[0]["duplicate_count"], 1)

    def test_dedup_survives_real_sqlite_reopen(self):
        self.assertTrue(self.claim())
        self.store.close()
        self.store = guard.GuardStore(self.path)
        self.assertFalse(self.claim())

    def test_same_message_id_in_another_session_or_platform_is_distinct(self):
        self.assertTrue(self.claim())
        self.assertTrue(self.claim(("offline", "offline:FriendMessage:10002", self.key[2])))
        self.assertTrue(self.claim(("second-adapter", self.key[1], self.key[2])))
        self.assertEqual(len(self.store.snapshot()["inbound"]), 3)

    def test_racing_claims_make_one_record(self):
        def worker(_):
            store = guard.GuardStore(self.path)
            try:
                return store.claim(*self.key, "10001", "中文🙂", self.chain)
            finally:
                store.close()
        with ThreadPoolExecutor(max_workers=8) as pool:
            accepted = list(pool.map(worker, range(16)))
        self.assertEqual(sum(accepted), 1)
        self.assertEqual(self.store.snapshot()["inbound"][0]["duplicate_count"], 15)

    def test_delivery_transitions_are_explicit_and_cannot_be_reclassified(self):
        self.claim()
        for state in ("sent", "failed", "unknown"):
            attempt = self.store.begin_outbound(self.key, "reply", self.chain)
            self.assertEqual(self.store.snapshot()["outbound"][-1]["state"], "pending")
            self.store.finish(attempt, state, None if state == "sent" else "SyntheticError")
            with self.assertRaises(ValueError):
                self.store.finish(attempt, "sent")
        self.assertEqual([r["state"] for r in self.store.snapshot()["outbound"]],
                         ["sent", "failed", "unknown"])

    def test_restart_marks_pending_unknown_and_never_retries(self):
        self.claim()
        self.store.begin_outbound(self.key, "reply", self.chain)
        self.store.close()
        self.store = guard.GuardStore(self.path)
        self.assertEqual(self.store.recover_interrupted(), 1)
        self.assertEqual(self.store.recover_interrupted(), 0)
        row = self.store.snapshot()["outbound"][0]
        self.assertEqual((row["state"], row["error_type"]), ("unknown", "Interrupted"))
        self.assertFalse(self.claim())

    def test_invalid_identity_and_orphan_reply_fail_closed(self):
        with self.assertRaises(ValueError):
            self.claim((self.key[0], self.key[1], ""))
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.begin_outbound(self.key, "reply", [])
        self.assertEqual(self.store.snapshot(), {"inbound": [], "outbound": []})

    def test_outbound_and_received_times_explicitly_use_shanghai_offset(self):
        self.claim()
        attempt = self.store.begin_outbound(self.key, "reply", self.chain)
        self.store.finish(attempt, "sent")
        rows = self.store.snapshot()
        self.assertTrue(rows["inbound"][0]["received_at"].endswith("+08:00"))
        self.assertTrue(rows["outbound"][0]["created_at"].endswith("+08:00"))
        self.assertTrue(rows["outbound"][0]["finished_at"].endswith("+08:00"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
