#!/usr/bin/env python3
"""Export allowlisted synthetic Phase 1 data from a stopped application.

Stop AstrBot and all plugin writers first. This helper cannot prove that they
have stopped. It exports chat data, not configuration, credentials, or a complete
restorable state snapshot. Text is preserved, not secret-scanned: use only the
synthetic offline fixtures in this phase. Use backup.py for complete recovery.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from zoneinfo import ZoneInfo


class ExportError(ValueError):
    """The requested export is missing data, incompatible, or would overwrite."""


# All SQL identifiers come from this constant, never from CLI arguments or DB
# metadata. Unexpected columns, configuration tables and API key tables are not
# selected. JSON media blobs, arbitrary raw events and memory metadata are omitted.
SOURCES = {
    "guard": {
        "path": "data/plugin_data/phase1_guard/guard.sqlite3",
        "required": True,
        "tables": {
            "inbound": ("platform", "session", "message_id", "sender_id", "text",
                        "chain_json", "received_at", "duplicate_count"),
            "outbound": ("id", "platform", "session", "message_id", "text", "chain_json",
                         "state", "error_type", "created_at", "finished_at"),
        },
    },
    "astrbot": {
        "path": "data/data_v4.db",
        "required": True,
        "tables": {
            "conversations": ("conversation_id", "platform_id", "user_id", "content",
                              "title", "persona_id", "created_at", "updated_at"),
        },
    },
    "message_recorder": {
        "path": "data/plugin_data/astrbot_plugin_message_recorder/messages.db",
        "required": False,
        "tables": {
            "messages": ("id", "platform", "message_id", "session_id", "group_id",
                         "sender_id", "message_type", "message_str", "message_chain",
                         "timestamp", "created_at"),
        },
    },
    "memory_companion": {
        "path": "data/plugin_data/astrbot_plugin_memory_companion/memory_companion.db",
        "required": False,
        "tables": {
            "memories": ("id", "memory_type", "subject_id", "scope", "session_id",
                         "platform", "message_id", "visibility", "content", "evidence",
                         "confidence", "validity_status", "created_at", "updated_at"),
            "timeline": ("id", "event_type", "session_id", "scope", "subject_id",
                         "content", "message_id", "occurred_at", "created_at"),
        },
    },
}

SECRET_KEYS = {"api_key", "apikey", "key", "secret", "password", "cookie",
               "authorization", "access_token", "refresh_token", "token"}


def _without_secret_fields(value):
    """Remove named secret fields from structured chat data, preserving text."""
    if isinstance(value, dict):
        return {key: _without_secret_fields(item) for key, item in value.items()
                if key.lower().replace("-", "_") not in SECRET_KEYS}
    if isinstance(value, list):
        return [_without_secret_fields(item) for item in value]
    return value


def _read_database(path, tables):
    # A mode=ro SQLite connection may create/update source WAL shared-memory
    # sidecars. Read a private copy so source bytes and sidecars remain untouched.
    with tempfile.TemporaryDirectory(prefix="qq-synthetic-export-") as temporary:
        copied = Path(temporary) / path.name
        for suffix in ("", "-wal", "-shm", "-journal"):
            candidate = Path(str(path) + suffix)
            if candidate.is_symlink():
                raise ExportError("Database symlinks are unsupported")
            if candidate.exists():
                if not candidate.is_file():
                    raise ExportError("Database source must be a regular file")
                shutil.copy2(candidate, Path(str(copied) + suffix))
        with closing(sqlite3.connect(copied.as_uri() + "?mode=ro", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            rows_by_table = {}
            for table, columns in tables.items():
                present = connection.execute(
                    "SELECT type FROM sqlite_master WHERE name=?", (table,)
                ).fetchone()
                if present is None or present[0] != "table":
                    raise ExportError(f"Missing required table: {table}")
                quoted = ",".join('"' + name + '"' for name in columns)
                # Reject schema changes instead of SQLite's permissive treatment
                # of unknown double-quoted identifiers as string constants.
                actual = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
                if not set(columns).issubset(actual):
                    raise ExportError(f"Incompatible columns in table: {table}")
                rows = [dict(row) for row in connection.execute(
                    f'SELECT {quoted} FROM "{table}" ORDER BY rowid'
                )]
                for row in rows:
                    for column in ("chain_json", "message_chain", "content"):
                        if column not in row or not isinstance(row[column], str):
                            continue
                        try:
                            structured = json.loads(row[column])
                        except (ValueError, TypeError):
                            if column in {"chain_json", "message_chain"}:
                                raise ExportError(f"Invalid structured message data in {table}") from None
                            continue
                        if isinstance(structured, (dict, list)):
                            row[column] = _without_secret_fields(structured)
                rows_by_table[table] = rows
            return rows_by_table


def export_data(source_state, output_path):
    """Export fixed tables into a new JSON file; return content-free counts.

    The caller must stop source writers. Required Guard and AstrBot databases
    must already exist. Optional plugin databases are reported when absent.
    """
    source = Path(source_state).absolute()
    output = Path(output_path).absolute()
    if source.is_symlink() or not source.is_dir():
        raise ExportError("Source must be an existing real state directory")
    if os.path.lexists(output):
        raise ExportError("Output already exists; refusing to overwrite it")
    source = source.resolve()
    components, counts = {}, {}
    try:
        for name, spec in SOURCES.items():
            path = source / spec["path"]
            for parent in (path, *path.parents):
                if parent == source:
                    break
                if parent.is_symlink():
                    raise ExportError("Source database paths must not contain symlinks")
            if not path.is_file():
                if spec["required"]:
                    raise ExportError(f"Required database missing: {name}")
                components[name] = {"status": "not_present"}
                continue
            tables = _read_database(path, spec["tables"])
            components[name] = {"status": "exported", **tables}
            counts.update({name + "." + table: len(rows) for table, rows in tables.items()})
    except sqlite3.DatabaseError as exc:
        raise ExportError(f"SQLite export failed: {type(exc).__name__}") from exc
    payload = {
        "format": "qq-ai-companion-synthetic-data",
        "version": 1,
        "timezone": "Asia/Shanghai",
        "exported_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "timestamp_semantics": {
            "guard": "ISO 8601 with original explicit offset retained",
            "astrbot": "Native naive timestamps retained; AstrBot writes UTC",
            "message_recorder": "Native Unix epoch seconds retained",
            "memory_companion": "Native timestamp strings retained without reinterpretation",
        },
        "components": components,
        "counts": counts,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".qq-export-", dir=output.parent) as temporary:
        pending = Path(temporary) / "export.json"
        pending.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.chmod(pending, 0o600)
        try:
            os.link(pending, output)
        except FileExistsError:
            raise ExportError("Output already exists; refusing to overwrite it") from None
    return {"format": payload["format"], "version": 1, "timezone": payload["timezone"],
            "counts": counts,
            "missing_optional": [name for name, data in components.items() if data["status"] == "not_present"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", help="Stopped offline state directory containing data/")
    parser.add_argument("output", help="New JSON output path; existing paths are refused")
    args = parser.parse_args(argv)
    try:
        summary = export_data(args.state, args.output)
    except (ExportError, OSError) as exc:
        # Never print SQL rows, raw database exceptions, or chat contents.
        detail = str(exc) if isinstance(exc, ExportError) else type(exc).__name__
        parser.exit(1, "Export failed: " + detail + "\n")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
