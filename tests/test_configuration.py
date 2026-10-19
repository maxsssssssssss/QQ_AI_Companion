"""Configuration failures and timezone boundaries use only synthetic fixtures."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from configuration import local_timestamp, validate_config


class ConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = json.loads((ROOT / "config/offline.json").read_text())

    def validate(self, config):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config))
            return validate_config(path)

    def invalid(self, path, value, expected):
        config = deepcopy(self.template)
        target = config
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = value
        with self.assertRaises(ValueError) as caught:
            self.validate(config)
        self.assertIn(expected, str(caught.exception))

    def test_valid_config_returns_original_dictionary(self):
        self.assertEqual(self.validate(self.template), self.template)

    def test_astrbot_restart_accepts_utf8_bom(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cmd_config.json"
            path.write_text(json.dumps(self.template), encoding="utf-8-sig")
            self.assertEqual(validate_config(path), self.template)

    def test_astrbot_schema_rewrite_uses_explicit_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "data").mkdir()
            path = state / "data/cmd_config.json"
            rewritten = deepcopy(self.template)
            metadata = rewritten.pop("phase1")
            path.write_text(json.dumps(rewritten), encoding="utf-8-sig")
            persisted_bytes = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "phase1.*sidecar"):
                validate_config(path)
            sidecar = state / "phase1.json"
            sidecar.write_text(json.dumps(metadata))
            self.assertEqual(validate_config(path), self.template)
            self.assertEqual(path.read_bytes(), persisted_bytes)
            sidecar.write_text("{invalid")
            with self.assertRaisesRegex(ValueError, "phase1.*sidecar"):
                validate_config(path)
            sidecar.write_text("[]")
            with self.assertRaisesRegex(ValueError, "phase1 sidecar"):
                validate_config(path)

    def test_missing_fields_are_actionable(self):
        for field in ["provider_sources", "provider", "platform", "platform_settings", "timezone", "phase1", "plugin_set"]:
            with self.subTest(field=field):
                config = deepcopy(self.template)
                config.pop(field)
                with self.assertRaises(ValueError) as caught:
                    self.validate(config)
                self.assertIn(field, str(caught.exception))

    def test_provider_reference_and_model(self):
        self.invalid(["provider", 0, "provider_source_id"], "missing", "provider[0].provider_source_id")
        self.invalid(["provider", 0, "model"], "", "provider[0].model")
        self.invalid(["agent_runner", "config", "model", "provider_id"], "missing", "provider_id")

    def test_loopback_and_fixture_only(self):
        for endpoint in ["https://example.com/v1", "http://127.0.0.1:18080/other", "http://user:pass@127.0.0.1:18080/v1"]:
            with self.subTest(endpoint=endpoint):
                self.invalid(["provider_sources", 0, "api_base"], endpoint, "api_base")
        self.invalid(["provider_sources", 0, "key"], ["not-an-offline-fixture"], "key")
        self.invalid(["provider_sources", 0, "proxy"], "http://example.com", "proxy")
        self.invalid(["platform", 0, "ws_reverse_host"], "0.0.0.0", "ws_reverse_host")
        self.invalid(["platform", 0, "ws_reverse_token"], "", "ws_reverse_token")
        self.invalid(["platform", 0, "ws_reverse_port"], True, "ws_reverse_port")
        self.invalid(["dashboard", "enable"], True, "dashboard.enable")

    def test_whitelist_and_isolation_required(self):
        self.invalid(["platform_settings", "enable_id_white_list"], False, "enable_id_white_list")
        self.invalid(["platform_settings", "id_whitelist"], [], "id_whitelist")
        self.invalid(["platform_settings", "id_whitelist"], ["*"], "id_whitelist")
        self.invalid(["platform_settings", "unique_session"], False, "unique_session")
        self.invalid(["platform_settings", "wl_ignore_admin_on_friend"], True, "wl_ignore_admin_on_friend")
        self.invalid(["admins_id"], ["synthetic-admin"], "admins_id")

    def test_identity_paths_and_plugin_candidates(self):
        self.invalid(["phase1", "mock_self_id"], 0, "mock_self_id")
        self.invalid(["phase1", "mock_self_id"], True, "mock_self_id")
        for path in ["/tmp/data", "../data", "data/../../outside", "C:\\data"]:
            with self.subTest(path=path):
                self.invalid(["phase1", "paths", "data_dir"], path, "data_dir")
        self.invalid(["phase1", "plugins"], ["unreviewed_plugin"], "plugins")
        self.invalid(["phase1", "plugins"], ["message_recorder", "message_recorder"], "plugins")

    def test_explicit_admin_fixture_is_limited_to_private_allowlist(self):
        config = deepcopy(self.template)
        config["phase1"]["admin_fixture_enabled"] = True
        private_user = next(entry.rsplit(":", 1)[1] for entry in config["platform_settings"]["id_whitelist"]
                            if ":FriendMessage:" in entry)
        for fixture in (private_user, int(private_user)):
            config["admins_id"] = [fixture]
            self.assertEqual(self.validate(config), config)
        config["admins_id"] = ["999999999"]
        with self.assertRaisesRegex(ValueError, r"admins_id\[0\]"):
            self.validate(config)
        config["admins_id"] = [True]
        with self.assertRaisesRegex(ValueError, r"admins_id\[0\]"):
            self.validate(config)
        self.invalid(["phase1", "admin_fixture_enabled"], "true", "admin_fixture_enabled")

    def test_wrong_types_have_field_specific_value_error(self):
        cases = [(["platform", 0], [], "platform[0]"),
                 (["platform", 0, "ws_reverse_host"], [], "ws_reverse_host"),
                 (["provider_sources", 0, "key"], None, "key"),
                 (["provider", 0, "provider_source_id"], {}, "provider_source_id"),
                 (["phase1", "paths"], [], "paths"),
                 (["phase1", "plugins"], [{}], "plugins")]
        for path, value, field in cases:
            with self.subTest(path=path):
                self.invalid(path, value, field)

    def test_malformed_json_and_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text("{invalid")
            with self.assertRaisesRegex(ValueError, "config:"):
                validate_config(path)
            path.unlink()
            with self.assertRaisesRegex(ValueError, "config:"):
                validate_config(path)

    def test_shanghai_utc_conversion_and_cross_day(self):
        self.assertEqual(local_timestamp("2026-10-19T00:00:00Z"), "2026-10-19T08:00:00+08:00")
        self.assertEqual(local_timestamp("2026-10-19T20:30:00Z"), "2026-10-20T04:30:00+08:00")
        self.assertEqual(local_timestamp("2026-10-19T00:00:00+08:00"), "2026-10-19T00:00:00+08:00")
        self.invalid(["timezone"], "UTC", "timezone")
        with self.assertRaisesRegex(ValueError, "timestamp:"):
            local_timestamp("2026-10-19T00:00:00")
        with self.assertRaisesRegex(ValueError, "timezone:"):
            local_timestamp("2026-10-19T00:00:00Z", "Not/A_Zone")


if __name__ == "__main__":
    unittest.main(verbosity=2)
