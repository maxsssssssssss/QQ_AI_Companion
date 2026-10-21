#!/usr/bin/env python3
"""Read online message metadata without exporting identifiers or conversation text.

Each SQLite database is opened mode=ro with a separate read transaction. This
is an online observation, not a consistent multi-database backup or integrity
check. --contains-text is for a non-secret, controlled acceptance assertion;
it checks the latest authorized inbound's latest finalized, sent reply only.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time

DEFAULT_STATE = Path("/opt/qq-ai-companion/deployment/state")
GUARD_PATH = "data/plugin_data/phase1_guard/guard.sqlite3"
RECORDER_PATH = "data/plugin_data/astrbot_plugin_message_recorder/messages.db"
POLICY_PATH = "data/config/phase2_policy_config.json"


class EvidenceError(Exception):
    """Messages are fixed codes, never input, paths or database errors."""


def read_identity(state: Path) -> tuple[str, str]:
    descriptor = os.open(state / POLICY_PATH, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or \
                (os.geteuid() != 0 and info.st_uid != os.geteuid()):
            raise EvidenceError("UNSAFE_POLICY_FILE")
        content = stream.read(65537)
        if len(content) > 65536:
            raise EvidenceError("INVALID_POLICY_FILE")
    policy = json.loads(content)
    admin, platform = policy.get("admin_id"), policy.get("platform_id")
    if not isinstance(admin, str) or not re.fullmatch(r"[1-9][0-9]{4,14}", admin) or \
            not isinstance(platform, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", platform):
        raise EvidenceError("INVALID_POLICY_IDENTITY")
    return admin, platform


@contextmanager
def read_database(path: Path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise EvidenceError("DATABASE_MISSING") from None
    if not stat.S_ISREG(info.st_mode):
        raise EvidenceError("UNSAFE_DATABASE_PATH")
    # Do not use immutable=1: it can omit live WAL transactions.
    connection = sqlite3.connect(path.absolute().as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        deadline = time.monotonic() + 5
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        connection.execute("BEGIN")
        yield connection
    finally:
        connection.close()


def image_counts(connection, table: str, column: str) -> tuple[int, int]:
    # Identifiers are internal constants, never database content or CLI input.
    if (table, column) not in {("inbound", "chain_json"), ("messages", "message_chain")}:
        raise EvidenceError("UNEXPECTED_QUERY")
    # CASE prevents json_each/json_extract from receiving invalid JSON or
    # plain string array elements. No media URLs, paths or text leave SQLite.
    safe_array = f"""CASE WHEN json_valid({column})
        THEN CASE WHEN json_type({column})='array' THEN {column} ELSE '[]' END
        ELSE '[]' END"""
    row_count = connection.execute(f"""
        SELECT COUNT(*) FROM {table}
        WHERE EXISTS (
          SELECT 1 FROM json_each({safe_array}) AS component
          WHERE CASE WHEN component.type='object'
            THEN lower(json_extract(component.value, '$.type')) ELSE '' END = 'image'
        )
    """).fetchone()[0]
    malformed = connection.execute(f"""
        SELECT COUNT(*) FROM {table}
        WHERE {column} IS NOT NULL AND {column} != '' AND (
          CASE WHEN json_valid({column}) THEN json_type({column}) != 'array' ELSE 1 END
        )
    """).fetchone()[0]
    return row_count, malformed


def guard_counts(path: Path, admin: str, platform: str, contains_text: str | None) -> dict:
    with read_database(path) as connection:
        result = {
            "status": "READ",
            "inbound_count": connection.execute("SELECT COUNT(*) FROM inbound").fetchone()[0],
            "outbound_count": connection.execute("SELECT COUNT(*) FROM outbound").fetchone()[0],
            "unauthorized_sender_count": connection.execute(
                "SELECT COUNT(*) FROM inbound WHERE sender_id IS NULL OR sender_id != ?", (admin,)
            ).fetchone()[0],
        }
        result["outbound_state_counts"] = {
            state: connection.execute("SELECT COUNT(*) FROM outbound WHERE state=?", (state,)).fetchone()[0]
            for state in ("pending", "sent", "failed", "unknown")
        }
        result["outbound_state_counts"]["other"] = connection.execute("""
            SELECT COUNT(*) FROM outbound
            WHERE state IS NULL OR state NOT IN ('pending','sent','failed','unknown')
        """).fetchone()[0]
        result["inbound_imagechain_count"], result["malformed_inbound_chain_count"] = image_counts(
            connection, "inbound", "chain_json"
        )
        result["outbound_missing_or_unfinalized_count"] = connection.execute("""
            SELECT COUNT(*) FROM inbound AS incoming
            WHERE NOT EXISTS (
              SELECT 1 FROM outbound AS outgoing
              WHERE outgoing.platform=incoming.platform AND outgoing.session=incoming.session
                AND outgoing.message_id=incoming.message_id
            ) OR EXISTS (
              SELECT 1 FROM outbound AS outgoing
              WHERE outgoing.platform=incoming.platform AND outgoing.session=incoming.session
                AND outgoing.message_id=incoming.message_id AND (
                  outgoing.finished_at IS NULL OR outgoing.finished_at=''
                  OR outgoing.state IS NULL OR outgoing.state NOT IN ('sent','failed','unknown')
                )
            )
        """).fetchone()[0]
        result["text_assertion"] = "NOT_REQUESTED"
        if contains_text is not None:
            row = connection.execute("""
                WITH latest AS (
                  SELECT platform,session,message_id FROM inbound
                  WHERE sender_id=? AND platform=? AND session=?
                  ORDER BY received_at DESC,rowid DESC LIMIT 1
                )
                SELECT CASE WHEN outgoing.state='sent'
                    AND outgoing.finished_at IS NOT NULL AND outgoing.finished_at!=''
                    AND instr(outgoing.text,?) > 0 THEN 1 ELSE 0 END
                FROM outbound AS outgoing JOIN latest
                ON outgoing.platform=latest.platform AND outgoing.session=latest.session
                  AND outgoing.message_id=latest.message_id
                ORDER BY outgoing.id DESC LIMIT 1
            """, (admin, platform, f"{platform}:FriendMessage:{admin}", contains_text)).fetchone()
            result["text_assertion"] = "LATEST_AUTHORIZED_INBOUND_FINALIZED_SENT_REPLY"
            result["outbound_match"] = bool(row and row[0] == 1)
        return result


def recorder_counts(path: Path, admin: str) -> dict:
    with read_database(path) as connection:
        image_count, malformed = image_counts(connection, "messages", "message_chain")
        return {
            "status": "READ",
            "total_count": connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
            "authorized_sender_count": connection.execute(
                "SELECT COUNT(*) FROM messages WHERE sender_id=?", (admin,)
            ).fetchone()[0],
            "image_entry_count": image_count,
            "malformed_chain_count": malformed,
        }


def build_report(state: Path, contains_text: str | None = None) -> dict:
    result = {
        "status": "INSPECTED", "database_access": "READ_ONLY",
        "consistency": "PER_DATABASE_SNAPSHOT", "integrity_check": "NOT_RUN_ONLINE",
    }
    if contains_text is not None and (
        not isinstance(contains_text, str) or not contains_text.strip() or len(contains_text) > 256
    ):
        return {**result, "status": "UNAVAILABLE", "reason": "INVALID_TEXT_ASSERTION"}
    try:
        admin, platform = read_identity(state)
    except Exception:
        return {**result, "status": "UNAVAILABLE", "reason": "POLICY_IDENTITY_UNAVAILABLE"}
    for name, read in (
        ("guard", lambda: guard_counts(state / GUARD_PATH, admin, platform, contains_text)),
        ("recorder", lambda: recorder_counts(state / RECORDER_PATH, admin)),
    ):
        try:
            result[name] = read()
        except EvidenceError as exc:
            result[name] = {"status": "UNAVAILABLE", "reason": str(exc)}
            result["status"] = "PARTIAL"
        except Exception:
            result[name] = {"status": "UNAVAILABLE", "reason": "DATABASE_READ_FAILED"}
            result["status"] = "PARTIAL"
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--contains-text", help="Non-secret synthetic assertion; never echoed in output")
    options = parser.parse_args()
    report = build_report(options.state, options.contains_text)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["status"] == "INSPECTED" else 1)
