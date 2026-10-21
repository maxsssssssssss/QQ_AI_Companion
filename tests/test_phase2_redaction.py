"""Synthetic credential tests; no real API, QQ identity or user message."""
import contextlib
import importlib.util
import io
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from integrations.phase2_policy import redaction

API_KEY = "synthetic-api-credential-AaBbCcDdEeFf001122"
ONEBOT_TOKEN = "synthetic-onebot-credential-998877665544"
CONFIG = {
    "provider_sources": [{"key": [API_KEY]}],
    "platform": [{"ws_reverse_token": ONEBOT_TOKEN}],
    "dashboard": {"jwt_secret": "synthetic-dashboard-credential-44556677"},
}


class RecordingLoguru:
    def __init__(self):
        self.patcher = None

    def configure(self, *, patcher):
        self.patcher = patcher


class MemoryHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(self.format(record))


class CredentialRedactionTests(unittest.TestCase):
    def setUp(self):
        self.original_factory = logging.getLogRecordFactory()
        self.fake_loguru = RecordingLoguru()
        self.loguru_patch = patch.dict(
            sys.modules, {
                "loguru": types.SimpleNamespace(logger=self.fake_loguru),
                "secret_redaction": redaction,
            }
        )
        self.loguru_patch.start()
        self.redactor = redaction.install_redaction(CONFIG)
        self.output = io.StringIO()
        self.memory = MemoryHandler()
        self.logger = logging.getLogger("phase2-redaction-synthetic")
        self.original_handlers = self.logger.handlers[:]
        self.original_propagate = self.logger.propagate
        self.original_level = self.logger.level
        self.logger.handlers = [logging.StreamHandler(self.output), self.memory]
        self.logger.propagate = False
        self.logger.setLevel(logging.DEBUG)

    def tearDown(self):
        self.logger.handlers = self.original_handlers
        self.logger.propagate = self.original_propagate
        self.logger.setLevel(self.original_level)
        logging.setLogRecordFactory(self.original_factory)
        self.loguru_patch.stop()

    def assert_safe(self, output):
        for secret in redaction.configured_secrets(CONFIG):
            self.assertNotIn(secret, output)

    def test_429_prefix_and_full_tokens_never_reach_either_handler(self):
        self.logger.warning("API 429 current key: %s", API_KEY[:12])
        self.logger.info("synthetic headers %s", {"key": API_KEY, "token": ONEBOT_TOKEN})
        for output in (self.output.getvalue(), "\n".join(self.memory.records)):
            self.assert_safe(output)
            self.assertIn("API 429", output)
            self.assertIn(redaction.REDACTED, output)

    def test_exception_messages_and_locals_are_not_rendered_unfiltered(self):
        try:
            synthetic_local = ONEBOT_TOKEN
            raise ValueError(API_KEY)
        except ValueError:
            self.logger.exception("synthetic failure %s", synthetic_local)
        for output in (self.output.getvalue(), "\n".join(self.memory.records)):
            self.assert_safe(output)
            self.assertIn("ValueError", output)
            self.assertIn("Traceback", output)

    def test_malformed_format_arguments_do_not_escape_from_logging(self):
        self.logger.warning("synthetic %d %d", API_KEY)
        self.assert_safe(self.output.getvalue())
        self.assertIn("formatting unavailable", self.output.getvalue())

    def test_direct_loguru_metadata_and_exception_are_redacted(self):
        try:
            raise ValueError(ONEBOT_TOKEN)
        except ValueError:
            record = {
                "message": API_KEY[:12],
                "extra": {"headers": {"authorization": API_KEY}},
                "exception": sys.exc_info(),
            }
            self.fake_loguru.patcher(record)
        self.assertIsNone(record["exception"])
        self.assert_safe(json.dumps(record))
        self.assertIn("ValueError", record["message"])

    def test_install_is_idempotent_and_preserves_previous_secrets(self):
        installed_factory = logging.getLogRecordFactory()
        newer = {"provider_sources": [{"key": ["fixture-replacement-credential-123456"]}]}
        again = redaction.install_redaction(newer)
        self.assertIs(again, self.redactor)
        self.assertIs(installed_factory, logging.getLogRecordFactory())
        self.assertEqual(self.redactor.text(API_KEY), redaction.REDACTED)
        self.assertEqual(
            self.redactor.text(newer["provider_sources"][0]["key"][0]), redaction.REDACTED
        )

    def test_safe_diagnostic_message_is_unchanged(self):
        self.logger.info("synthetic startup status %s", "ready")
        self.assertEqual(self.memory.records, ["synthetic startup status ready"])

    def test_live_config_adds_key_without_restart_or_file_refresh(self):
        live = {"provider_sources": [{"key": []}]}
        redaction.install_redaction(live, config_getter=lambda: live)
        new_key = "fixture-hot-entered-credential-0011223344"
        live["provider_sources"][0]["key"] = [new_key]
        self.logger.warning("synthetic hot key %s and prefix %s", new_key, new_key[:12])
        for output in (self.output.getvalue(), "\n".join(self.memory.records)):
            self.assertNotIn(new_key, output)
            self.assertNotIn(new_key[:12], output)
            self.assertIn(redaction.REDACTED, output)

    def test_unsaved_new_api_key_and_partial_warning_are_redacted(self):
        new_key = "sk-fixtureUnsaved01234567890123456789"
        prefix = "Unknown12Key"
        self.logger.warning("当前 Key: %s", prefix)
        self.logger.warning("synthetic submitted credential %s", new_key)
        self.logger.warning("Authorization: Bearer %s", "fixtureUncommittedBearer123")
        for output in (self.output.getvalue(), "\n".join(self.memory.records)):
            for value in (new_key, prefix, "fixtureUncommittedBearer123"):
                self.assertNotIn(value, output)
            self.assertIn("当前 Key: [REDACTED]", output)

    def load_bootstrap(self):
        spec = importlib.util.spec_from_file_location(
            "phase2_sitecustomize_test", PROJECT / "scripts/phase2-sitecustomize.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_bootstrap_reads_only_protected_owned_regular_configuration(self):
        module = self.load_bootstrap()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(CONFIG))
            path.chmod(0o600)
            module.bootstrap(path)
            path.chmod(0o644)
            with contextlib.redirect_stderr(io.StringIO()) as output:
                with self.assertRaises(SystemExit) as stopped:
                    module.bootstrap(path)
            self.assertEqual(stopped.exception.code, 78)
            self.assert_safe(output.getvalue())
            self.assertIn("startup blocked", output.getvalue())

    def test_bad_json_containing_secret_stops_without_echoing_input(self):
        module = self.load_bootstrap()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(API_KEY)
            path.chmod(0o600)
            with contextlib.redirect_stderr(io.StringIO()) as output:
                with self.assertRaises(SystemExit):
                    module.bootstrap(path)
            self.assert_safe(output.getvalue())

    def test_symlink_configuration_is_rejected(self):
        module = self.load_bootstrap()
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / "config", Path(directory) / "link"
            source.write_text(json.dumps(CONFIG))
            source.chmod(0o600)
            target.symlink_to(source)
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    module.bootstrap(target)

    def test_sitecustomize_failure_actually_stops_python_before_main(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # Import the real bootstrap through Python's automatic sitecustomize.
            # A missing fixed production path must not be ignored by site.py.
            (root / "sitecustomize.py").write_text(
                (PROJECT / "scripts/phase2-sitecustomize.py").read_text()
            )
            (root / "secret_redaction.py").write_text(
                "def install_redaction(config):\n    pass\n"
            )
            env = dict(os.environ, PYTHONPATH=directory, PYTHONDONTWRITEBYTECODE="1")
            result = subprocess.run(
                [sys.executable, "-c", "print('UNSAFE_MAIN_EXECUTED')"],
                env=env, capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("UNSAFE_MAIN_EXECUTED", result.stdout)
            self.assertIn("startup blocked", result.stderr)
            self.assert_safe(result.stderr)


class PinnedUpstreamLoggingTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("loguru"), "requires locked image's Loguru")
    def test_real_upstream_logbroker_stdout_and_direct_loguru(self):
        source = Path(os.environ.get(
            "ASTRBOT_LOG_SOURCE",
            str(PROJECT / ".runtime/upstream/AstrBot/astrbot/core/log.py"),
        ))
        if not source.is_file():
            source = Path("/AstrBot/astrbot/core/log.py")
        self.assertTrue(source.is_file(), "pinned AstrBot log.py must be available")
        # Isolate actual Loguru sink/global state and import only the pinned
        # upstream logger module. Never import AstrBot core or start its app.
        program = r'''
import contextlib, importlib.util, io, json, logging, pathlib, sys, types
sys.path.insert(0, sys.argv[3])
from loguru import logger
import loguru
assert loguru.__version__ == "0.7.3"
sys.path.insert(0, sys.argv[1])
from integrations.phase2_policy.redaction import install_redaction, configured_secrets
key = "synthetic-api-credential-AaBbCcDdEeFf001122"
token = "synthetic-onebot-credential-998877665544"
config = {"provider_sources": [{"key": [key]}], "platform": [{"ws_reverse_token": token}]}
install_redaction(config)
defaults = types.ModuleType("astrbot.core.config.default")
defaults.VERSION = "4.29.1"
paths = types.ModuleType("astrbot.core.utils.astrbot_path")
paths.get_astrbot_config_path = lambda: "/tmp"
paths.get_astrbot_data_path = lambda: "/tmp"
sys.modules[defaults.__name__] = defaults
sys.modules[paths.__name__] = paths
spec = importlib.util.spec_from_file_location("tested_pinned_upstream_log", sys.argv[2])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
output = io.StringIO()
with contextlib.redirect_stdout(output):
    upstream_logger = module.LogManager.GetLogger("astrbot")
    broker = module.LogBroker()
    module.LogManager.set_queue_handler(upstream_logger, broker)
    upstream_logger.warning("API 429 current key: %s", key[:12])
    upstream_logger.info("synthetic key=%s token=%s", key, token)
    try:
        secret_local_that_must_not_be_diagnosed = token
        raise ValueError(key)
    except ValueError:
        upstream_logger.exception("synthetic provider failure")
        logger.bind(authorization=key).exception("synthetic direct Loguru %s " + token)
    plugin_logger = module.LogManager.get_plugin_logger("synthetic")
    plugin_logger.warning("synthetic plugin %s", token)
stdout = output.getvalue()
cache = json.dumps(list(broker.log_cache), ensure_ascii=False)
for rendered in (stdout, cache):
    for secret in configured_secrets(config):
        assert secret not in rendered, "credential reached actual upstream sink"
    assert "[REDACTED]" in rendered
    assert "API 429" in rendered
    assert "ValueError" in rendered
assert "synthetic direct Loguru" in stdout
assert "synthetic plugin" in stdout and "synthetic plugin" in cache
print(json.dumps({"status": "PASS", "upstream_logbroker": True, "upstream_stdout": True,
                  "direct_loguru": True, "provider_calls": 0}))
'''
        result = subprocess.run(
            [sys.executable, "-c", program, str(PROJECT), str(source),
             str(Path(importlib.util.find_spec("loguru").origin).parent.parent)],
            env=dict(os.environ, PYTHONPATH="", PYTHONDONTWRITEBYTECODE="1"),
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", "Loguru must not emit unfiltered handler diagnostics")
        self.assertEqual(json.loads(result.stdout)["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
