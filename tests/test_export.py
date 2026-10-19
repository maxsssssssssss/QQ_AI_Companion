"""Real SQLite export tests; all content and credentials below are synthetic."""

import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/export_data.py"
SPEC = importlib.util.spec_from_file_location("export_data", SCRIPT)
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.output = self.root / "export.json"
        self.paths = {}
        for name, spec in exporter.SOURCES.items():
            if not spec["required"]:
                continue
            path = self.state / spec["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            self.paths[name] = path
            with sqlite3.connect(path) as db:
                for table, columns in spec["tables"].items():
                    db.execute(f'CREATE TABLE "{table}" (' + ",".join(f'"{c}"' for c in columns) + ", secret_key TEXT)")
                db.execute("CREATE TABLE api_keys (value TEXT)")
                db.execute("INSERT INTO api_keys VALUES ('SYNTHETIC_CONFIG_SECRET')")
        with sqlite3.connect(self.paths["guard"]) as db:
            db.execute("INSERT INTO inbound VALUES (?,?,?,?,?,?,?,?,?)", (
                "offline", "offline:FriendMessage:10001", "123", "10001", "中文测试🙂",
                json.dumps([{"type": "text", "data": {"text": "中文测试🙂", "token": "SYNTHETIC_NESTED_SECRET"}}]),
                "2026-10-19T20:00:00+08:00", 1, "SYNTHETIC_EXTRA_SECRET"))
            for index, state in enumerate(("sent", "failed", "unknown")):
                db.execute("INSERT INTO outbound VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                    index, "offline", "offline:FriendMessage:10001", "123", "回复", "[]", state,
                    None if state == "sent" else "SyntheticError", "2026-10-19T20:00:01+08:00",
                    "2026-10-19T20:00:02+08:00", "SYNTHETIC_EXTRA_SECRET"))
        with sqlite3.connect(self.paths["astrbot"]) as db:
            db.execute("INSERT INTO conversations VALUES (?,?,?,?,?,?,?,?,?)", (
                "synthetic-conversation", "offline", "10001", '[{"role":"user","content":"中文测试🙂"}]',
                "Synthetic title", "offline", "2026-10-19 12:00:00", "2026-10-19 12:00:02",
                "SYNTHETIC_EXTRA_SECRET"))
        (self.state / "config.json").write_text('{"api_key":"SYNTHETIC_CONFIG_SECRET"}')

    def tearDown(self):
        self.temp.cleanup()

    def test_export_parses_and_preserves_status_timezone_and_unicode(self):
        before = {p.relative_to(self.state).as_posix(): p.read_bytes() for p in self.state.rglob("*") if p.is_file()}
        summary = exporter.export_data(self.state, self.output)
        payload = json.loads(self.output.read_text())
        self.assertEqual(payload["timezone"], "Asia/Shanghai")
        self.assertTrue(payload["exported_at"].endswith("+08:00"))
        rows = payload["components"]["guard"]
        self.assertEqual(rows["inbound"][0]["text"], "中文测试🙂")
        self.assertEqual(rows["inbound"][0]["received_at"], "2026-10-19T20:00:00+08:00")
        self.assertEqual([row["state"] for row in rows["outbound"]], ["sent", "failed", "unknown"])
        self.assertEqual(summary["counts"]["guard.outbound"], 3)
        self.assertEqual(summary["missing_optional"], ["message_recorder", "memory_companion"])
        self.assertEqual(payload["components"]["astrbot"]["conversations"][0]["content"][0]["role"], "user")
        self.assertNotIn("SECRET", self.output.read_text())
        self.assertEqual(os.stat(self.output).st_mode & 0o777, 0o600)
        after = {p.relative_to(self.state).as_posix(): p.read_bytes() for p in self.state.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_existing_target_is_preserved(self):
        self.output.write_text("keep existing output")
        with self.assertRaises(exporter.ExportError):
            exporter.export_data(self.state, self.output)
        self.assertEqual(self.output.read_text(), "keep existing output")

    def test_missing_source_never_creates_empty_database_or_output(self):
        source = self.root / "missing"
        with self.assertRaises(exporter.ExportError):
            exporter.export_data(source, self.output)
        self.assertFalse(source.exists())
        self.paths["guard"].unlink()
        with self.assertRaises(exporter.ExportError):
            exporter.export_data(self.state, self.output)
        self.assertFalse(self.paths["guard"].exists())
        self.assertFalse(self.output.exists())

    def test_optional_sources_are_exported_when_present(self):
        for name in ("message_recorder", "memory_companion"):
            spec = exporter.SOURCES[name]
            path = self.state / spec["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(path) as db:
                for table, columns in spec["tables"].items():
                    db.execute(f'CREATE TABLE "{table}" (' + ",".join(f'"{c}"' for c in columns) + ")")
                    db.execute(f'INSERT INTO "{table}" VALUES (' + ",".join("?" for _ in columns) + ")",
                               tuple("[]" if c == "message_chain" else "synthetic" for c in columns))
        summary = exporter.export_data(self.state, self.output)
        self.assertFalse(summary["missing_optional"])
        self.assertEqual(summary["counts"]["message_recorder.messages"], 1)
        self.assertEqual(summary["counts"]["memory_companion.memories"], 1)
        self.assertEqual(summary["counts"]["memory_companion.timeline"], 1)

    def test_wrong_schema_fails_without_exporting_or_printing_data(self):
        with sqlite3.connect(self.paths["astrbot"]) as db:
            db.execute("DROP TABLE conversations")
            db.execute("CREATE TABLE conversations (content TEXT)")
        run = subprocess.run([sys.executable, str(SCRIPT), str(self.state), str(self.output)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 1)
        self.assertIn("Incompatible columns", run.stderr)
        self.assertFalse(run.stdout)
        self.assertNotIn("中文测试", run.stderr)
        self.assertFalse(self.output.exists())

    def test_cli_stdout_only_contains_counts_and_metadata(self):
        run = subprocess.run([sys.executable, str(SCRIPT), str(self.state), str(self.output)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        summary = json.loads(run.stdout)
        self.assertEqual(summary["counts"]["astrbot.conversations"], 1)
        self.assertNotIn("中文测试", run.stdout)
        self.assertNotIn("SECRET", run.stdout)
        self.assertFalse(run.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
