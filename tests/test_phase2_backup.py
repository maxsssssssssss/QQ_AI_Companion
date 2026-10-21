"""Local filesystem tests with a fake service controller; never invoke Docker."""
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
from unittest.mock import patch
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "scripts"))
SPEC = importlib.util.spec_from_file_location("phase2_backup", PROJECT / "scripts/phase2-backup.py")
phase2 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(phase2)

PRIVATE_NAME = "synthetic-private-qq-account-file.db"
PRIVATE_VALUE = b"synthetic-private-credential-never-print"


class FakeServices:
    def __init__(self, running=("astrbot", "napcat"), *, stop_error=None, start_error=None):
        self.state = set(running)
        self.calls = []
        self.stop_error, self.start_error = stop_error, start_error

    def running(self):
        self.calls.append(("running",))
        return set(self.state)

    def stop(self):
        self.calls.append(("stop",))
        self.state.clear()
        if self.stop_error:
            raise self.stop_error

    def start(self, services):
        self.calls.append(("start", tuple(sorted(services))))
        if self.start_error:
            raise self.start_error
        self.state.update(services)


class ProductionBackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "deployment"
        self.root.mkdir(mode=0o700)
        self.state = self.root / "state"
        (self.state / "data").mkdir(parents=True)
        (self.state / "secrets").mkdir(mode=0o700)
        (self.state / "secrets/key").write_bytes(PRIVATE_VALUE)
        (self.state / "secrets/key").chmod(0o600)
        (self.state / "data/config.json").write_text('{"synthetic": true}')
        (self.state / "data/config.json").chmod(0o600)
        with sqlite3.connect(self.state / "data/history.sqlite3") as db:
            db.execute("CREATE TABLE messages(value TEXT)")
            db.execute("INSERT INTO messages VALUES ('synthetic private message')")
        self.napcat = self.root / "napcat/config"
        self.napcat.mkdir(parents=True)
        (self.napcat / "private-config.json").write_bytes(PRIVATE_VALUE)
        self.ntqq = self.root / "ntqq"
        self.ntqq.mkdir()
        # This is intentionally NOT SQLite, despite the extension.
        (self.ntqq / PRIVATE_NAME).write_bytes(b"opaque encrypted QQ database fixture")
        (self.ntqq / "empty").mkdir()

    def check_report_private(self, report):
        rendered = json.dumps(report)
        for private in (PRIVATE_NAME, PRIVATE_VALUE.decode(), "synthetic private message"):
            self.assertNotIn(private, rendered)

    def test_dry_run_is_read_only_and_never_touches_service_controller(self):
        services = FakeServices()
        report = phase2.exercise(self.root, "dry-run", services=services)
        self.assertEqual(report["status"], "NOT TESTED")
        self.assertEqual(report["reason"], "explicit_apply_required")
        self.assertFalse((self.root / "backups").exists())
        self.assertEqual(services.calls, [])
        self.check_report_private(report)

    def test_complete_round_trip_checks_astrbot_sqlite_but_keeps_qq_opaque(self):
        services = FakeServices()
        report = phase2.exercise(self.root, "complete", apply=True, services=services)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["sqlite_integrity_checked_count"], 1)
        self.assertEqual(report["opaque_sqlite_integrity"], "NOT APPLICABLE")
        self.assertEqual(report["backup_integrity"], "PASS")
        self.assertEqual(report["restore_integrity"], "PASS")
        self.assertTrue(report["service_state_restored"])
        self.assertFalse(report["restored_copy_started"])
        self.assertEqual(services.state, {"astrbot", "napcat"})
        run = self.root / "backups/complete"
        restored = run / "isolated-restore"
        self.assertEqual(
            (restored / "napcat/ntqq" / PRIVATE_NAME).read_bytes(),
            (self.ntqq / PRIVATE_NAME).read_bytes(),
        )
        self.assertEqual((restored / "astrbot-state/secrets/key").read_bytes(), PRIVATE_VALUE)
        for directory, directories, files in os.walk(run):
            self.assertEqual(stat.S_IMODE(Path(directory).stat().st_mode), 0o700)
            for name in files:
                self.assertEqual(stat.S_IMODE((Path(directory) / name).stat().st_mode), 0o600)
        self.assertEqual(phase2.backup._hash(run / "astrbot-state.zip"), report["astrbot_archive_sha256"])
        self.check_report_private(report)

    def test_only_originally_running_services_are_restarted(self):
        for original in ((), ("astrbot",), ("napcat",)):
            with self.subTest(original=original):
                services = FakeServices(original)
                run_id = "running-" + ("-".join(original) or "none")
                report = phase2.exercise(self.root, run_id, apply=True, services=services)
                self.assertEqual(report["status"], "PASS")
                starts = [call for call in services.calls if call[0] == "start"]
                self.assertEqual(starts, [("start", tuple(sorted(original)))] if original else [])
                self.assertEqual(services.state, set(original))

    def test_failed_or_interrupted_snapshot_still_restores_original_services(self):
        for failure in (
            phase2.backup.BackupError("SQLite integrity failure: " + PRIVATE_NAME),
            InterruptedError(PRIVATE_VALUE.decode()),
        ):
            with self.subTest(failure=type(failure).__name__):
                services = FakeServices(("astrbot",))
                with patch.object(phase2.backup, "create_backup", side_effect=failure):
                    report = phase2.exercise(
                        self.root, "fail-" + type(failure).__name__, apply=True, services=services
                    )
                self.assertEqual(report["status"], "FAIL")
                self.assertTrue(report["service_state_restored"])
                self.assertEqual(services.state, {"astrbot"})
                self.assertEqual(report["backup_integrity"], "NOT TESTED")
                self.check_report_private(report)

    def test_partially_failed_stop_restores_original_services(self):
        services = FakeServices(stop_error=RuntimeError(PRIVATE_VALUE.decode()))
        report = phase2.exercise(self.root, "stop-failure", apply=True, services=services)
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(report["service_state_restored"])
        self.assertEqual(services.state, {"astrbot", "napcat"})
        self.check_report_private(report)

    def test_failed_resume_is_reported_even_when_backups_succeeded(self):
        services = FakeServices(start_error=RuntimeError(PRIVATE_VALUE.decode()))
        report = phase2.exercise(self.root, "resume-failure", apply=True, services=services)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["restore_integrity"], "PASS")
        self.assertFalse(report["service_state_restored"])
        self.assertEqual(report["service_restore_error"], "original_service_state_not_restored")
        self.check_report_private(report)

    def test_existing_run_is_rejected_before_stop_without_modifying_it(self):
        parent = self.root / "backups"
        parent.mkdir(mode=0o700)
        output = parent / "existing"
        output.mkdir(mode=0o700)
        (output / "keep").write_bytes(PRIVATE_VALUE)
        services = FakeServices()
        report = phase2.exercise(self.root, "existing", apply=True, services=services)
        self.assertEqual(report["reason"], "backup_run_already_exists")
        self.assertEqual(services.calls, [])
        self.assertEqual((output / "keep").read_bytes(), PRIVATE_VALUE)
        self.assertFalse((output / "result.json").exists())

    def test_links_and_special_files_are_refused_and_never_deleted(self):
        for kind in ("symlink", "hardlink", "fifo"):
            with self.subTest(kind=kind):
                suspect = self.ntqq / ("synthetic-private-" + kind)
                if kind == "symlink":
                    suspect.symlink_to(self.ntqq / PRIVATE_NAME)
                elif kind == "hardlink":
                    os.link(self.ntqq / PRIVATE_NAME, suspect)
                else:
                    os.mkfifo(suspect)
                try:
                    dry = phase2.exercise(self.root, "dry-" + kind)
                    self.assertEqual(dry["status"], "BLOCKED")
                    services = FakeServices(("astrbot",))
                    report = phase2.exercise(self.root, kind, apply=True, services=services)
                    self.assertEqual(report["status"], "FAIL")
                    self.assertTrue(report["service_state_restored"])
                    self.assertTrue(os.path.lexists(suspect))
                    self.assertFalse((self.root / "backups" / kind / "astrbot-state.zip").exists())
                    self.check_report_private(report)
                finally:
                    suspect.unlink()

    def test_opaque_archive_tamper_and_existing_restore_are_refused(self):
        parent = self.root / "private-test"
        parent.mkdir(mode=0o700)
        archive = parent / "opaque.zip"
        manifest = phase2.create_opaque_backup({"ntqq": self.ntqq}, archive)
        existing = parent / "existing"
        existing.mkdir()
        with self.assertRaisesRegex(phase2.Phase2BackupError, "already_exists"):
            phase2.restore_opaque_backup(archive, existing, manifest)
        original_digest = phase2.backup._hash(archive)
        with self.assertRaisesRegex(phase2.Phase2BackupError, "archive_already_exists"):
            phase2.create_opaque_backup({"ntqq": self.ntqq}, archive)
        self.assertEqual(phase2.backup._hash(archive), original_digest)
        tampered = parent / "tampered.zip"
        with zipfile.ZipFile(archive) as source, zipfile.ZipFile(tampered, "w") as target:
            for entry in source.infolist():
                payload = source.read(entry)
                if entry.filename != "manifest.json":
                    payload = b"x" * len(payload)
                target.writestr(entry, payload)
        destination = parent / "new-restore"
        with self.assertRaisesRegex(phase2.Phase2BackupError, "digest_mismatch"):
            phase2.restore_opaque_backup(tampered, destination, manifest)
        self.assertFalse(destination.exists())

    def test_unsafe_output_parent_or_missing_sources_never_stop_services(self):
        parent = self.root / "backups"
        parent.mkdir(mode=0o755)
        services = FakeServices()
        report = phase2.exercise(self.root, "unsafe-parent", apply=True, services=services)
        self.assertEqual(report["reason"], "unsafe_output_permissions")
        self.assertEqual(services.calls, [])
        parent.chmod(0o700)
        self.ntqq.rename(self.root / "moved")
        report = phase2.exercise(self.root, "missing-source", apply=True, services=services)
        self.assertEqual(report["reason"], "invalid_source_directory")
        self.assertEqual(services.calls, [])

    def test_subprocess_output_and_error_text_never_escape(self):
        completed = subprocess.CompletedProcess(
            ["docker"], 1, stdout=PRIVATE_VALUE.decode(), stderr=PRIVATE_NAME
        )
        with patch.object(phase2.subprocess, "run", return_value=completed) as run:
            with self.assertRaisesRegex(phase2.Phase2BackupError, "^docker_command_failed$"):
                phase2.run_command(["docker", "compose", "ps"])
        self.assertEqual(run.call_args.kwargs["stderr"], subprocess.DEVNULL)


if __name__ == "__main__":
    unittest.main()
