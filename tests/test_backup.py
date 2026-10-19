"""Real filesystem/SQLite tests for the offline backup helper."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile


SPEC = importlib.util.spec_from_file_location("backup", Path(__file__).resolve().parents[1] / "scripts" / "backup.py")
backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = self.root / "original"
        (self.state / "data" / "plugin_data").mkdir(parents=True)
        (self.state / "empty_directory").mkdir()
        (self.state / "config.json").write_text('{"provider": "offline-mock"}\n')
        (self.state / "data" / "plugin_data" / "record.txt").write_text("synthetic plugin state\n")
        os.chmod(self.state / "config.json", 0o600)
        self.db_path = self.state / "data" / "state.db"
        with sqlite3.connect(self.db_path) as db:
            db.execute("CREATE TABLE history (id INTEGER PRIMARY KEY, message TEXT)")
            db.execute("INSERT INTO history(message) VALUES ('S1 synthetic conversation')")
        self.archive = self.root / "backup.zip"
        self.restored = self.root / "restored"

    def tearDown(self):
        self.temp.cleanup()

    def rewrite_archive(self, mutate):
        with zipfile.ZipFile(self.archive) as source:
            members = [(entry, source.read(entry.filename)) for entry in source.infolist()]
        changed = self.root / "changed.zip"
        with zipfile.ZipFile(changed, "w") as target:
            for info, payload in mutate(members):
                target.writestr(info, payload)
        return changed

    def test_round_trip_recovers_complete_snapshot_before_later_writes(self):
        expected = backup.create_backup(self.state, self.archive)
        self.assertEqual(expected["sqlite_integrity_checked"], ["data/state.db"])
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO history(message) VALUES ('S2 after backup')")
        (self.state / "config.json").write_text('{"changed": true}')
        actual = backup.restore_backup(self.archive, self.restored)
        self.assertEqual(actual, expected)
        with sqlite3.connect(self.restored / "data" / "state.db") as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
            self.assertEqual(db.execute("SELECT message FROM history").fetchall(), [("S1 synthetic conversation",)])
        self.assertEqual((self.restored / "config.json").read_text(), '{"provider": "offline-mock"}\n')
        self.assertEqual((self.restored / "data" / "plugin_data" / "record.txt").read_text(), "synthetic plugin state\n")
        self.assertTrue((self.restored / "empty_directory").is_dir())
        self.assertEqual(stat.S_IMODE((self.restored / "config.json").stat().st_mode), 0o600)
        with sqlite3.connect(self.db_path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM history").fetchone()[0], 2)
        for relative, metadata in actual["files"].items():
            self.assertEqual(hashlib.sha256((self.restored / relative).read_bytes()).hexdigest(), metadata["sha256"])

    def test_refuses_existing_archive_and_existing_destination(self):
        backup.create_backup(self.state, self.archive)
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        with self.assertRaises(backup.BackupError):
            backup.create_backup(self.state, self.archive)
        self.assertEqual(hashlib.sha256(self.archive.read_bytes()).hexdigest(), digest)
        self.restored.mkdir()
        with self.assertRaises(backup.BackupError):
            backup.restore_backup(self.archive, self.restored)
        (self.restored / "keep.txt").write_text("user file")
        with self.assertRaises(backup.BackupError):
            backup.restore_backup(self.archive, self.restored)
        self.assertEqual((self.restored / "keep.txt").read_text(), "user file")

    def test_recovers_committed_wal_from_stopped_writer(self):
        wal_db = self.state / "data" / "wal.sqlite3"
        # Abrupt exit leaves committed rows in WAL, with no live writer. The
        # backup must include that state instead of copying only the main DB.
        subprocess.run([
            sys.executable, "-c",
            "import os, sqlite3, sys; "
            "db=sqlite3.connect(sys.argv[1]); "
            "db.execute('PRAGMA journal_mode=WAL'); "
            "db.execute('CREATE TABLE items(value TEXT)'); "
            "db.execute(\"INSERT INTO items VALUES ('committed in WAL')\"); "
            "db.commit(); os._exit(0)", str(wal_db)
        ], check=True)
        self.assertTrue(Path(str(wal_db) + "-wal").exists())
        manifest = backup.create_backup(self.state, self.archive)
        self.assertIn("data/wal.sqlite3-wal", manifest["files"])
        backup.restore_backup(self.archive, self.restored)
        with sqlite3.connect(self.restored / "data" / "wal.sqlite3") as db:
            self.assertEqual(db.execute("SELECT value FROM items").fetchall(), [("committed in WAL",)])

    def test_rejects_checksum_tampering_without_creating_destination(self):
        backup.create_backup(self.state, self.archive)
        def mutate(members):
            return [(info, payload.replace(b"offline-mock", b"tampered-key") if info.filename == "state/config.json" else payload)
                    for info, payload in members]
        with self.assertRaisesRegex(backup.BackupError, "Checksum mismatch"):
            backup.restore_backup(self.rewrite_archive(mutate), self.restored)
        self.assertFalse(self.restored.exists())

    def test_rejects_traversal_absolute_and_windows_paths(self):
        backup.create_backup(self.state, self.archive)
        for unsafe in ["../escaped.txt", "/tmp/escaped.txt", "state/../../escaped.txt", "C:/escaped.txt", "state\\escaped.txt"]:
            with self.subTest(path=unsafe):
                changed = self.rewrite_archive(lambda members: members + [(zipfile.ZipInfo(unsafe), b"escape")])
                with self.assertRaises(backup.BackupError):
                    backup.restore_backup(changed, self.restored)
                self.assertFalse(self.restored.exists())
        self.assertFalse((self.root / "escaped.txt").exists())

    def test_rejects_zip_symlink(self):
        backup.create_backup(self.state, self.archive)
        info = zipfile.ZipInfo("state/link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        changed = self.rewrite_archive(lambda members: members + [(info, b"../../outside")])
        with self.assertRaisesRegex(backup.BackupError, "links or special"):
            backup.restore_backup(changed, self.restored)
        self.assertFalse(self.restored.exists())

    def test_rejects_source_symlinks_and_hardlinks(self):
        link = self.state / "link"
        link.symlink_to(self.state / "config.json")
        with self.assertRaisesRegex(backup.BackupError, "Links"):
            backup.create_backup(self.state, self.archive)
        link.unlink()
        os.link(self.state / "config.json", link)
        with self.assertRaisesRegex(backup.BackupError, "Hard links"):
            backup.create_backup(self.state, self.archive)
        self.assertFalse(self.archive.exists())

    def test_rejects_corrupt_source_sqlite(self):
        self.db_path.write_bytes(b"not a SQLite database")
        with self.assertRaisesRegex(backup.BackupError, "SQLite integrity"):
            backup.create_backup(self.state, self.archive)
        self.assertFalse(self.archive.exists())

    def test_rejects_corrupt_sqlite_even_with_updated_manifest_hash(self):
        backup.create_backup(self.state, self.archive)
        corrupt = b"corrupt database contents"
        def mutate(members):
            result = []
            for info, payload in members:
                if info.filename == "state/data/state.db":
                    payload = corrupt
                elif info.filename == "manifest.json":
                    manifest = json.loads(payload)
                    manifest["files"]["data/state.db"]["sha256"] = hashlib.sha256(corrupt).hexdigest()
                    manifest["files"]["data/state.db"]["size"] = len(corrupt)
                    payload = json.dumps(manifest).encode()
                result.append((info, payload))
            return result
        with self.assertRaisesRegex(backup.BackupError, "SQLite integrity"):
            backup.restore_backup(self.rewrite_archive(mutate), self.restored)
        self.assertFalse(self.restored.exists())

    def test_rejects_unlisted_files_and_missing_files(self):
        backup.create_backup(self.state, self.archive)
        changes = [
            lambda members: members + [(zipfile.ZipInfo("state/unlisted.txt"), b"unlisted")],
            lambda members: [(info, data) for info, data in members if info.filename != "state/config.json"],
        ]
        for mutate in changes:
            with self.subTest(mutation=mutate):
                with self.assertRaises(backup.BackupError):
                    backup.restore_backup(self.rewrite_archive(mutate), self.restored)
                self.assertFalse(self.restored.exists())

    def test_rejects_archive_inside_source(self):
        with self.assertRaisesRegex(backup.BackupError, "outside"):
            backup.create_backup(self.state, self.state / "recursive.zip")

    def test_rejects_truncated_archive(self):
        backup.create_backup(self.state, self.archive)
        self.archive.write_bytes(self.archive.read_bytes()[:50])
        with self.assertRaises(backup.BackupError):
            backup.restore_backup(self.archive, self.restored)
        self.assertFalse(self.restored.exists())


if __name__ == "__main__":
    unittest.main()
