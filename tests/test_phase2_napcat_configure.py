"""Offline checks for safe first-login NapCat configuration staging."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/phase2-napcat-configure.py"
SPEC = importlib.util.spec_from_file_location("phase2_napcat_configure", SCRIPT)
napcat = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(napcat)


class NapCatConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "state"
        self.deployment = self.root / "deployment"
        self.secret_dir = self.state / "secrets"
        self.secret_dir.mkdir(parents=True, mode=0o700)
        self.onebot = "onebot_synthetic_" + "a" * 48
        self.webui = "webui_synthetic_" + "b" * 48
        self.write_secret("onebot-token", self.onebot)
        self.write_secret("napcat-webui-token", self.webui)

    def write_secret(self, name, value, mode=0o600):
        path = self.secret_dir / name
        path.write_text(value + "\n")
        path.chmod(mode)
        return path

    def run_cli(self):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--state", str(self.state),
             "--deployment", str(self.deployment)],
            capture_output=True, text=True, check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

    def assert_no_output(self):
        self.assertFalse((self.deployment / "napcat" / "config").exists())
        if (self.deployment / "napcat").exists():
            self.assertEqual(list((self.deployment / "napcat").iterdir()), [])

    def test_fresh_configuration_uses_matching_secrets_without_printing_them(self):
        process = self.run_cli()
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertNotIn(self.onebot, process.stdout + process.stderr)
        self.assertNotIn(self.webui, process.stdout + process.stderr)
        result = json.loads(process.stdout)
        self.assertTrue(result["upstream_initial_in_memory_token_logging_remains"])
        config = self.deployment / "napcat" / "config"
        onebot = json.loads((config / "onebot11.json").read_text())
        client, = onebot["network"]["websocketClients"]
        self.assertEqual(client["token"], self.onebot)
        self.assertEqual(client["url"], "ws://astrbot:6199/ws")
        self.assertFalse(client["reportSelfMessage"])
        self.assertFalse(client["debug"])
        self.assertEqual(onebot["network"]["websocketServers"], [])
        webui = json.loads((config / "webui.json").read_text())
        self.assertEqual(webui["token"], self.webui)
        self.assertEqual(webui["autoLoginAccount"], "")
        self.assertEqual(webui["host"], "0.0.0.0")
        core = json.loads((config / "napcat.json").read_text())
        self.assertFalse(core["fileLog"])
        self.assertFalse(core["consoleLog"])
        self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o700)
        for path in config.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertFalse((self.deployment / "ntqq").exists())

    def test_rerun_preserves_every_existing_byte(self):
        napcat.configure(self.state, self.deployment)
        directory = self.deployment / "napcat" / "config"
        before = {path.name: path.read_bytes() for path in directory.iterdir()}
        with self.assertRaises(FileExistsError):
            napcat.configure(self.state, self.deployment)
        self.assertEqual(before, {path.name: path.read_bytes() for path in directory.iterdir()})

    def test_account_configuration_is_never_overwritten(self):
        directory = self.deployment / "napcat" / "config"
        directory.mkdir(parents=True)
        account = directory / "onebot11_123456789.json"
        account.write_text("synthetic existing account configuration")
        with self.assertRaises(FileExistsError):
            napcat.configure(self.state, self.deployment)
        self.assertEqual(account.read_text(), "synthetic existing account configuration")
        self.assertEqual(list(directory.iterdir()), [account])

    def test_existing_login_state_blocks_configuration(self):
        login = self.deployment / "ntqq"
        login.mkdir(parents=True)
        marker = login / "synthetic-session"
        marker.write_bytes(b"preserve")
        with self.assertRaises(FileExistsError):
            napcat.configure(self.state, self.deployment)
        self.assertEqual(marker.read_bytes(), b"preserve")
        self.assert_no_output()

    def test_empty_precreated_directories_are_supported(self):
        (self.deployment / "ntqq").mkdir(parents=True)
        (self.deployment / "napcat" / "config").mkdir(parents=True)
        napcat.configure(self.state, self.deployment)
        self.assertEqual(list((self.deployment / "ntqq").iterdir()), [])

    def test_non_0600_secret_is_rejected_before_writing(self):
        for mode in (0o400, 0o640, 0o644, 0o660):
            with self.subTest(mode=oct(mode)):
                (self.secret_dir / "onebot-token").chmod(mode)
                with self.assertRaises(ValueError):
                    napcat.configure(self.state, self.deployment)
                self.assert_no_output()

    def test_secret_owned_by_another_user_is_rejected(self):
        with patch.object(napcat.os, "geteuid", return_value=os.geteuid() + 1):
            with self.assertRaises(ValueError):
                napcat.configure(self.state, self.deployment)
        self.assert_no_output()

    def test_symlink_secret_and_symlink_parent_are_rejected(self):
        token = self.secret_dir / "onebot-token"
        actual = self.secret_dir / "actual-token"
        token.rename(actual)
        token.symlink_to(actual)
        with self.assertRaises(ValueError):
            napcat.configure(self.state, self.deployment)
        self.assert_no_output()
        token.unlink()
        actual.rename(token)
        real = self.root / "actual-secrets"
        self.secret_dir.rename(real)
        self.secret_dir.symlink_to(real, target_is_directory=True)
        with self.assertRaises(ValueError):
            napcat.configure(self.state, self.deployment)
        self.assert_no_output()

    def test_destination_symlink_is_rejected_without_touching_target(self):
        other = self.root / "other"
        other.mkdir()
        self.deployment.symlink_to(other, target_is_directory=True)
        with self.assertRaises(ValueError):
            napcat.configure(self.state, self.deployment)
        self.assertEqual(list(other.iterdir()), [])

    def test_same_token_is_rejected(self):
        self.write_secret("napcat-webui-token", self.onebot)
        with self.assertRaises(ValueError):
            napcat.configure(self.state, self.deployment)
        self.assert_no_output()

    def test_invalid_token_and_cli_error_do_not_disclose_content(self):
        invalid = "invalid synthetic token " + "secret" * 10
        self.write_secret("onebot-token", invalid)
        process = self.run_cli()
        self.assertEqual(process.returncode, 1)
        self.assertEqual(process.stderr, "")
        self.assertNotIn(invalid, process.stdout)
        self.assertNotIn(self.webui, process.stdout)
        self.assertEqual(json.loads(process.stdout)["status"], "ERROR")
        self.assert_no_output()

    def test_partial_write_failure_does_not_publish_any_config(self):
        original = napcat.write_private_json
        calls = 0

        def fail_second(path, value):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("synthetic disk failure")
            original(path, value)

        with patch.object(napcat, "write_private_json", side_effect=fail_second):
            with self.assertRaises(OSError):
                napcat.configure(self.state, self.deployment)
        self.assert_no_output()

    def test_new_account_state_during_staging_prevents_publication(self):
        original = napcat.write_private_json

        def introduce_login(path, value):
            original(path, value)
            login = self.deployment / "ntqq"
            login.mkdir(exist_ok=True)
            (login / "synthetic-session").write_bytes(b"keep")

        with patch.object(napcat, "write_private_json", side_effect=introduce_login):
            with self.assertRaises(FileExistsError):
                napcat.configure(self.state, self.deployment)
        self.assert_no_output()


if __name__ == "__main__":
    unittest.main()
