"""Small durable deduplication and delivery observation store for Phase 1.

This is deliberately not a retry queue. A claimed message stays claimed after
an interrupted run, because repeating an unacknowledged send can duplicate it.
"""

from datetime import datetime
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo


def now_shanghai():
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="milliseconds")


class GuardStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS inbound (
                platform TEXT NOT NULL,
                session TEXT NOT NULL,
                message_id TEXT NOT NULL,
                sender_id TEXT NOT NULL,
                text TEXT NOT NULL,
                chain_json TEXT NOT NULL,
                received_at TEXT NOT NULL,
                duplicate_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(platform, session, message_id)
            );
            CREATE TABLE IF NOT EXISTS outbound (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                session TEXT NOT NULL,
                message_id TEXT NOT NULL,
                text TEXT NOT NULL,
                chain_json TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('pending','sent','failed','unknown')),
                error_type TEXT,
                created_at TEXT NOT NULL,
                finished_at TEXT,
                FOREIGN KEY(platform, session, message_id)
                    REFERENCES inbound(platform, session, message_id)
            );
        """)

    def recover_interrupted(self):
        """Mark unconfirmed attempts unknown once, during plugin startup."""
        with self.db:
            count = self.db.execute(
                "UPDATE outbound SET state='unknown', error_type='Interrupted', "
                "finished_at=? WHERE state='pending'", (now_shanghai(),)
            ).rowcount
        return count

    def claim(self, platform, session, message_id, sender_id, text, chain):
        """Atomically claim an inbound identity before downstream handlers."""
        if not all(isinstance(value, str) and value for value in (platform, session, message_id)):
            raise ValueError("A nonempty platform, session and message_id are required")
        with self.db:
            inserted = self.db.execute(
                "INSERT OR IGNORE INTO inbound "
                "(platform,session,message_id,sender_id,text,chain_json,received_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (platform, session, message_id, str(sender_id), text,
                 json.dumps(chain, ensure_ascii=False), now_shanghai()),
            ).rowcount
            if not inserted:
                self.db.execute(
                    "UPDATE inbound SET duplicate_count=duplicate_count+1 "
                    "WHERE platform=? AND session=? AND message_id=?",
                    (platform, session, message_id),
                )
        return bool(inserted)

    def begin_outbound(self, key, text, chain):
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO outbound "
                "(platform,session,message_id,text,chain_json,state,created_at) "
                "VALUES (?,?,?,?,?,'pending',?)",
                (*key, text, json.dumps(chain, ensure_ascii=False), now_shanghai()),
            )
        return cursor.lastrowid

    def finish(self, attempt_id, state, error_type=None):
        if state not in {"sent", "failed", "unknown"}:
            raise ValueError("Delivery result must be sent, failed or unknown")
        with self.db:
            changed = self.db.execute(
                "UPDATE outbound SET state=?, error_type=?, finished_at=? "
                "WHERE id=? AND state='pending'",
                (state, error_type, now_shanghai(), attempt_id),
            ).rowcount
            if changed != 1:
                raise ValueError("Only an existing pending delivery can be finalized")

    def snapshot(self):
        return {
            "inbound": [dict(row) for row in self.db.execute(
                "SELECT * FROM inbound ORDER BY platform,session,message_id")],
            "outbound": [dict(row) for row in self.db.execute(
                "SELECT * FROM outbound ORDER BY id")],
        }

    def close(self):
        self.db.close()
