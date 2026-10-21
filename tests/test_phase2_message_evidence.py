"""Synthetic-only aggregate evidence fixtures, including stale-match negatives."""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from integrations.phase1_guard.store import GuardStore

spec = importlib.util.spec_from_file_location(
    "phase2_message_evidence", PROJECT / "scripts/phase2-message-evidence.py"
)
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)
ADMIN = "10001"
PLATFORM = "napcat"
TEXT = "SYNTHETIC_PRIVATE_BODY"
NONCE = "fixture-context-731"


class MessageEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.state = Path(self.temporary.name)
        config = self.state / evidence.POLICY_PATH
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({"admin_id": ADMIN, "platform_id": PLATFORM}))
        config.chmod(0o600)
        self.guard_path = self.state / evidence.GUARD_PATH
        self.guard = GuardStore(self.guard_path)
        self.recorder_path = self.state / evidence.RECORDER_PATH
        self.recorder_path.parent.mkdir(parents=True)
        self.recorder = sqlite3.connect(self.recorder_path)
        self.recorder.execute(
            "CREATE TABLE messages(id INTEGER PRIMARY KEY, sender_id TEXT, message_chain TEXT, message_str TEXT)"
        )
        self.recorder.commit()

    def tearDown(self):
        self.guard.close()
        self.recorder.close()
        self.temporary.cleanup()

    def inbound(self, number, sender=ADMIN, chain=None):
        key = (PLATFORM, f"{PLATFORM}:FriendMessage:{sender}", f"fixture-message-{number}")
        self.guard.claim(*key, sender, TEXT, chain if chain is not None else [])
        return key

    def outbound(self, key, state="sent", text=TEXT):
        attempt = self.guard.begin_outbound(key, text, [{"type": "Plain", "text": text}])
        if state != "pending":
            self.guard.finish(attempt, state)
        return attempt

    def assert_private(self, report):
        encoded = json.dumps(report)
        for value in (ADMIN, TEXT, "fixture-message-", "private-media", NONCE):
            self.assertNotIn(value, encoded)

    def test_empty_databases_have_zero_counts_and_no_false_match(self):
        result = evidence.build_report(self.state, NONCE)
        self.assertEqual(result["status"], "INSPECTED")
        self.assertEqual(result["guard"]["inbound_count"], 0)
        self.assertEqual(result["guard"]["outbound_count"], 0)
        self.assertFalse(result["guard"]["outbound_match"])
        self.assertEqual(result["recorder"]["total_count"], 0)
        self.assert_private(result)

    def test_counts_states_images_and_private_output(self):
        self.outbound(self.inbound(1, chain=[{"type": "image", "data": {"file": "private-media"}}]))
        self.outbound(self.inbound(2), "pending")
        self.outbound(self.inbound(3), "failed")
        self.outbound(self.inbound(4), "unknown")
        self.inbound(5)
        self.inbound(6, sender="10002")
        self.recorder.executemany("INSERT INTO messages VALUES(?,?,?,?)", [
            (1, ADMIN, json.dumps([{"type": "Image", "file": "private-media"}]), TEXT),
            (2, "10002", "[]", TEXT),
        ])
        self.recorder.commit()
        result = evidence.build_report(self.state)
        guard = result["guard"]
        self.assertEqual(guard["inbound_count"], 6)
        self.assertEqual(guard["outbound_count"], 4)
        self.assertEqual(guard["unauthorized_sender_count"], 1)
        self.assertEqual(guard["outbound_state_counts"],
                         {"pending": 1, "sent": 1, "failed": 1, "unknown": 1, "other": 0})
        self.assertEqual(guard["outbound_missing_or_unfinalized_count"], 3)
        self.assertEqual(guard["inbound_imagechain_count"], 1)
        self.assertEqual(result["recorder"]["authorized_sender_count"], 1)
        self.assertEqual(result["recorder"]["image_entry_count"], 1)
        self.assertNotIn("outbound_match", guard)
        self.assert_private(result)

    def test_latest_inbound_without_reply_does_not_match_old_echo(self):
        self.outbound(self.inbound(1), text=NONCE)
        self.inbound(2)
        result = evidence.build_report(self.state, NONCE)
        self.assertFalse(result["guard"]["outbound_match"])

    def test_only_latest_finalized_sent_reply_can_match(self):
        self.outbound(self.inbound(1), text=NONCE)
        newest = self.inbound(2)
        attempt = self.outbound(newest, "pending", NONCE)
        self.assertFalse(evidence.build_report(self.state, NONCE)["guard"]["outbound_match"])
        self.guard.finish(attempt, "sent")
        result = evidence.build_report(self.state, NONCE)
        self.assertTrue(result["guard"]["outbound_match"])
        self.assert_private(result)

    def test_unauthorized_reply_does_not_satisfy_match(self):
        self.outbound(self.inbound(1), text="different fixture")
        self.outbound(self.inbound(2, sender="10002"), text=NONCE)
        result = evidence.build_report(self.state, NONCE)
        self.assertFalse(result["guard"]["outbound_match"])

    def test_malformed_and_nonobject_chain_items_are_counted_safely(self):
        self.inbound(1, chain=["private-media", 1, {"type": "Plain", "text": "image"}])
        self.inbound(2, chain=[{"type": "Image"}, {"type": "image"}])
        with self.guard.db:
            self.guard.db.execute("UPDATE inbound SET chain_json=? WHERE message_id=?",
                                  (TEXT, "fixture-message-1"))
        result = evidence.build_report(self.state)
        self.assertEqual(result["guard"]["inbound_imagechain_count"], 1)
        self.assertEqual(result["guard"]["malformed_inbound_chain_count"], 1)
        self.assert_private(result)

    def test_missing_database_does_not_create_empty_file(self):
        missing = self.state / "missing.sqlite3"
        with self.assertRaisesRegex(evidence.EvidenceError, "^DATABASE_MISSING$"):
            with evidence.read_database(missing):
                pass
        self.assertFalse(missing.exists())

    def test_database_connection_rejects_writes_and_sees_live_committed_wal(self):
        self.inbound(1)
        with evidence.read_database(self.guard_path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM inbound").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM inbound")
        self.assertEqual(self.guard.db.execute("SELECT COUNT(*) FROM inbound").fetchone()[0], 1)

    def test_database_symlink_is_rejected(self):
        link = self.state / "linked.sqlite3"
        link.symlink_to(self.guard_path)
        with self.assertRaisesRegex(evidence.EvidenceError, "^UNSAFE_DATABASE_PATH$"):
            with evidence.read_database(link):
                pass

    def test_identity_failure_does_not_read_databases_or_echo_private_path(self):
        config = self.state / evidence.POLICY_PATH
        config.write_text(TEXT)
        with patch.object(evidence, "guard_counts", side_effect=AssertionError("must not run")):
            result = evidence.build_report(self.state)
        self.assertEqual(result["status"], "UNAVAILABLE")
        self.assert_private(result)

    def test_database_error_is_fixed_code_without_exception_details(self):
        with patch.object(evidence, "guard_counts", side_effect=sqlite3.DatabaseError(TEXT)):
            result = evidence.build_report(self.state)
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["guard"]["reason"], "DATABASE_READ_FAILED")
        self.assert_private(result)

    def test_blank_text_assertion_is_rejected_instead_of_matching_every_reply(self):
        result = evidence.build_report(self.state, " ")
        self.assertEqual(result["reason"], "INVALID_TEXT_ASSERTION")
        self.assertNotIn("guard", result)

    def test_user_assertion_is_bound_parameter_not_sql(self):
        self.outbound(self.inbound(1), text=TEXT)
        result = evidence.build_report(self.state, "' OR 1=1 --")
        self.assertFalse(result["guard"]["outbound_match"])
        self.assertEqual(result["guard"]["inbound_count"], 1)


if __name__ == "__main__":
    unittest.main()
