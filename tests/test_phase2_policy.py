"""Safety checks for production staging; no AstrBot, credentials or network required."""
import copy
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import shutil

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "scripts"))
from integrations.phase2_policy.policy import PolicyViolation, validate_policy

spec = importlib.util.spec_from_file_location("phase2_configure", PROJECT / "scripts/phase2-configure.py")
configure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure)


class ProductionPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = configure.build_config(
            "10001", "napcat", "synthetic-onebot-token-" + "x" * 32, "SyntheticPassword9"
        )

    def test_no_key_is_staged_disabled_and_runtime_fails_closed(self):
        self.assertTrue(validate_policy(self.config, "10001", "napcat", require_key=False))
        self.assertFalse(self.config["provider_settings"]["enable"])
        self.assertFalse(self.config["provider"][0]["enable"])
        self.assertEqual(self.config["provider_sources"][0]["key"], [])
        with self.assertRaisesRegex(PolicyViolation, "deepseek_not_enabled"):
            validate_policy(self.config, "10001", "napcat")

    def test_exactly_one_transport_attempt_is_required(self):
        self.assertEqual(self.config["agent_runner"]["config"]["model"]["request_max_retries"], 1)
        for invalid in (0, -1, 2, 5, None, True, 1.0, "1"):
            with self.subTest(attempts=invalid):
                config = copy.deepcopy(self.config)
                config["agent_runner"]["config"]["model"]["request_max_retries"] = invalid
                with self.assertRaisesRegex(PolicyViolation, "automatic_model_retries"):
                    validate_policy(config, "10001", "napcat", require_key=False)

    def test_staged_config_survives_pinned_upstream_normalization(self):
        upstream = PROJECT / ".runtime/upstream/AstrBot/astrbot/core/config/agent_runner.py"
        if not upstream.exists():
            self.skipTest("requires the fetched locked AstrBot source")
        upstream_spec = importlib.util.spec_from_file_location("pinned_agent_runner", upstream)
        normalizer = importlib.util.module_from_spec(upstream_spec)
        upstream_spec.loader.exec_module(normalizer)
        config = copy.deepcopy(self.config)
        config["agent_runner"] = normalizer.normalize_agent_runner(config["agent_runner"])
        self.assertTrue(validate_policy(config, "10001", "napcat", require_key=False))
        self.assertEqual(config["agent_runner"]["config"]["model"]["request_max_retries"], 1)
        # Reproduce the original mismatch using the actual pinned normalizer.
        config["agent_runner"]["config"]["model"]["request_max_retries"] = 0
        normalized = normalizer.normalize_agent_runner(config["agent_runner"])
        self.assertEqual(normalized["config"]["model"]["request_max_retries"], 1)

    def test_group_or_extra_user_whitelist_rejected(self):
        for unauthorized in ("10002", "napcat:GroupMessage:20001"):
            config = copy.deepcopy(self.config)
            config["platform_settings"]["id_whitelist"].append(unauthorized)
            with self.assertRaisesRegex(PolicyViolation, "unexpected_whitelist"):
                validate_policy(config, "10001", "napcat", require_key=False)

    def test_admin_group_bypass_and_complex_sending_rejected(self):
        for option in ("wl_ignore_admin_on_group", "wl_ignore_admin_on_friend",
                       "reply_with_mention", "reply_with_quote"):
            config = copy.deepcopy(self.config)
            config["platform_settings"][option] = True
            with self.assertRaises(PolicyViolation):
                validate_policy(config, "10001", "napcat", require_key=False)
        config = copy.deepcopy(self.config)
        config["platform_settings"]["segmented_reply"]["enable"] = True
        with self.assertRaisesRegex(PolicyViolation, "segmented_reply_enabled"):
            validate_policy(config, "10001", "napcat", require_key=False)

    def test_endpoint_cannot_be_redirected_with_secret(self):
        config = copy.deepcopy(self.config)
        config["provider_sources"][0]["api_base"] = "https://unexpected.invalid"
        with self.assertRaisesRegex(PolicyViolation, "unexpected_provider_endpoint"):
            validate_policy(config, "10001", "napcat", require_key=False)

    def test_dashboard_password_uses_upstream_pbkdf2_format(self):
        dashboard = self.config["dashboard"]
        algorithm, rounds, salt, digest = dashboard["pbkdf2_password"].split("$")
        self.assertEqual(algorithm, "pbkdf2_sha256")
        self.assertEqual(int(rounds), 600000)
        expected = hashlib.pbkdf2_hmac(
            "sha256", b"SyntheticPassword9", bytes.fromhex(salt), int(rounds)
        ).hex()
        self.assertEqual(digest, expected)
        self.assertNotEqual(dashboard["password"], "SyntheticPassword9")

    def prepare_state(self, directory):
        state = Path(directory)
        configure.write_private(state / "data/cmd_config.json", json.dumps(self.config))
        configure.write_private(state / "data/config/phase2_policy_config.json",
                                json.dumps({"admin_id": "10001", "platform_id": "napcat"}))
        return state

    def test_key_injection_preserves_identity_and_returns_no_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.prepare_state(directory)
            secret = state / "input-key"
            synthetic = "synthetic-fixture-not-a-real-api-key"
            configure.write_private(secret, synthetic)
            report = configure.inject_key(state, secret)
            self.assertNotIn(synthetic, json.dumps(report))
            updated = json.loads((state / "data/cmd_config.json").read_text())
            self.assertEqual(updated["admins_id"], ["10001"])
            self.assertTrue(validate_policy(updated, "10001", "napcat"))
            self.assertEqual(updated["provider_sources"][0]["key"], [synthetic])
            self.assertEqual(os.stat(state / "data/cmd_config.json").st_mode & 0o777, 0o600)

    def test_insecure_key_file_rejected_without_configuration_change(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.prepare_state(directory)
            before = (state / "data/cmd_config.json").read_bytes()
            secret = state / "input-key"
            configure.write_private(secret, "synthetic-fixture-not-a-real-api-key")
            os.chmod(secret, 0o644)
            with self.assertRaisesRegex(ValueError, "owner-only"):
                configure.inject_key(state, secret)
            self.assertEqual((state / "data/cmd_config.json").read_bytes(), before)

    def test_symlink_secret_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.prepare_state(directory)
            secret = state / "input-key"
            configure.write_private(secret, "synthetic-fixture-not-a-real-api-key")
            symlink = state / "link-key"
            symlink.symlink_to(secret)
            with self.assertRaises(OSError):
                configure.inject_key(state, symlink)

    def test_control_character_secret_rejected_without_configuration_change(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.prepare_state(directory)
            before = (state / "data/cmd_config.json").read_bytes()
            secret = state / "input-key"
            configure.write_private(secret, "synthetic-fixture-\x00-not-a-real-key")
            with self.assertRaisesRegex(ValueError, "invalid length or format"):
                configure.inject_key(state, secret)
            self.assertEqual((state / "data/cmd_config.json").read_bytes(), before)

    def test_init_will_not_replace_existing_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.prepare_state(directory)
            original = (state / "data/cmd_config.json").read_bytes()
            with self.assertRaises(FileExistsError):
                configure.initialize(state, state / "missing-sources", "10001", "napcat")
            self.assertEqual((state / "data/cmd_config.json").read_bytes(), original)

    def test_private_replacement_preserves_existing_mode_and_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "configuration"
            configure.write_private(path, "before")
            path.chmod(0o400)
            before = path.stat()
            configure.write_private(path, "after", replace=True)
            after = path.stat()
            self.assertEqual(path.read_text(), "after")
            self.assertEqual(after.st_mode & 0o777, 0o400)
            self.assertEqual((after.st_uid, after.st_gid), (before.st_uid, before.st_gid))

    def test_root_style_replacement_restores_service_owner_on_temporary_fd(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "configuration"
            configure.write_private(path, "before")
            real_fstat = configure.os.fstat
            observations = 0

            def simulated_service_owner(fd):
                nonlocal observations
                info = real_fstat(fd)
                observations += 1
                if observations == 1:
                    values = list(info)
                    values[4], values[5] = 10001, 10001
                    return os.stat_result(values)
                return info

            with patch.object(configure.os, "fstat", side_effect=simulated_service_owner), \
                    patch.object(configure.os, "fchown") as chown:
                configure.write_private(path, "after", replace=True)
            chown.assert_called_once()
            self.assertEqual(chown.call_args.args[1:], (10001, 10001))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def submit_dashboard_key(self, state, values):
        path = state / "data/cmd_config.json"
        config = json.loads(path.read_text())
        config["provider_sources"][0]["key"] = values
        configure.write_private(path, json.dumps(config), replace=True)
        return path

    def test_dashboard_status_is_read_only_and_secret_free(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.prepare_state(directory)
            path = state / "data/cmd_config.json"
            before = path.read_bytes()
            self.assertEqual(configure.dashboard_key_status(state)["status"], "WAITING")
            self.assertEqual(path.read_bytes(), before)
            synthetic = "synthetic-dashboard-key-never-for-api"
            self.submit_dashboard_key(state, [synthetic])
            before = path.read_bytes()
            report = configure.dashboard_key_status(state)
            self.assertEqual(report["status"], "READY")
            self.assertTrue(report["key_present"])
            self.assertFalse(report["provider_enabled"])
            self.assertFalse(report["flash_enabled"])
            self.assertNotIn(synthetic, json.dumps(report))
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse((state / "secrets").exists())

    def test_dashboard_accept_protects_secret_and_enables_only_flash(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.prepare_state(directory)
            secret_directory = state / "secrets"
            secret_directory.mkdir(mode=0o700)
            synthetic = "synthetic-dashboard-key-never-for-api"
            config_path = self.submit_dashboard_key(state, [synthetic])
            config_owner = (config_path.stat().st_uid, config_path.stat().st_gid)
            report = configure.accept_dashboard_key(state)
            self.assertEqual(report["status"], "CONFIGURED")
            self.assertNotIn(synthetic, json.dumps(report))
            target = secret_directory / "deepseek-key"
            self.assertEqual(target.read_text().strip(), synthetic)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual((target.stat().st_uid, target.stat().st_gid),
                             (secret_directory.stat().st_uid, secret_directory.stat().st_gid))
            self.assertEqual((config_path.stat().st_uid, config_path.stat().st_gid), config_owner)
            self.assertEqual(config_path.stat().st_mode & 0o777, 0o600)
            config = json.loads(config_path.read_text())
            self.assertTrue(validate_policy(config, "10001", "napcat"))
            self.assertFalse(config["provider"][1]["enable"])
            inode = target.stat().st_ino
            configure.accept_dashboard_key(state)
            self.assertEqual(target.stat().st_ino, inode)

    def test_dashboard_accept_refuses_different_existing_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.prepare_state(directory)
            secret_directory = state / "secrets"
            secret_directory.mkdir(mode=0o700)
            target = secret_directory / "deepseek-key"
            configure.write_private(target, "synthetic-existing-different-key\n")
            path = self.submit_dashboard_key(state, ["synthetic-new-dashboard-key"])
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "differs"):
                configure.accept_dashboard_key(state)
            self.assertEqual(target.read_text(), "synthetic-existing-different-key\n")
            self.assertEqual(path.read_bytes(), before)

    def test_dashboard_bad_keys_block_without_export_or_activation(self):
        for keys in (["short"], ["synthetic-valid-key", "synthetic-second-key"],
                     ["synthetic-key-\x00-with-control"], "synthetic-not-a-list"):
            with self.subTest(key_shape=type(keys).__name__):
                with tempfile.TemporaryDirectory() as directory:
                    state = self.prepare_state(directory)
                    path = self.submit_dashboard_key(state, keys)
                    before = path.read_bytes()
                    self.assertEqual(configure.dashboard_key_status(state)["status"], "BLOCKED")
                    with self.assertRaises(ValueError):
                        configure.accept_dashboard_key(state)
                    self.assertEqual(path.read_bytes(), before)
                    self.assertFalse((state / "secrets").exists())

    def test_dashboard_accept_rejects_unsafe_or_linked_secret_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.prepare_state(directory)
            path = self.submit_dashboard_key(state, ["synthetic-dashboard-key"])
            before = path.read_bytes()
            secret_directory = state / "secrets"
            secret_directory.mkdir(mode=0o755)
            with self.assertRaisesRegex(ValueError, "owner-only"):
                configure.accept_dashboard_key(state)
            secret_directory.rmdir()
            destination = state / "other"
            destination.mkdir(mode=0o700)
            secret_directory.symlink_to(destination, target_is_directory=True)
            with self.assertRaises(OSError):
                configure.accept_dashboard_key(state)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(destination.iterdir()), [])

    def test_no_clobber_publication_refuses_racing_new_target(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "new-secret"
            real_link = configure.os.link

            def racing_link(source, destination, **kwargs):
                path.write_text("other-writer-value")
                return real_link(source, destination, **kwargs)

            with patch.object(configure.os, "link", side_effect=racing_link):
                with self.assertRaises(FileExistsError):
                    configure.write_private(path, "must-not-replace")
            self.assertEqual(path.read_text(), "other-writer-value")
            self.assertEqual(list(path.parent.iterdir()), [path])


class ProductionHookTests(unittest.IsolatedAsyncioTestCase):
    """Exercise hook failure behavior with API fakes, not an online acceptance test."""

    def setUp(self):
        self.addCleanup(logging.setLogRecordFactory, logging.getLogRecordFactory())
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        plugin_root = self.root / "plugin"
        shutil.copytree(PROJECT / "integrations/phase2_policy", plugin_root)
        shutil.copyfile(PROJECT / "scripts/plugin_permissions.py",
                        plugin_root / "plugin_permissions.py")
        data = self.root / "data"
        (data / "phase2").mkdir(parents=True)
        (data / "phase2/persona.md").write_text("Synthetic policy hook test")
        self.config = configure.build_config(
            "10001", "napcat", "synthetic-onebot-token-" + "x" * 32, "SyntheticPassword9")
        self.commands = [{
            "module_path": "data.plugins.astrbot_plugin_message_recorder.main",
            "handler_full_name": "synthetic.recorder.search",
            "effective_command": "history", "permission": "member",
        }]

        async def list_commands():
            return copy.deepcopy(self.commands)

        async def update_permission(name, permission):
            self.commands[0]["permission"] = permission

        class Star:
            def __init__(self, context):
                self.context = context

        def get_data_dir(name):
            result = data / "plugin_data" / name
            result.mkdir(parents=True, exist_ok=True)
            return result

        class PersonaManager:
            def __init__(self):
                self.personas = []
                self.tools = None
                self.skills = None

            async def create_persona(self, name, prompt, *, tools, skills):
                self.personas.append(types.SimpleNamespace(persona_id=name))
                self.tools, self.skills = tools, skills

        manager = PersonaManager()
        expected = {"astrbot_plugin_message_recorder": "0.0.4",
                    "astrbot_plugin_memory_companion": "2.3.1",
                    "astrbot_plugin_private_companion": "6.7.0", "phase1_guard": "0.1.0"}
        self.context = types.SimpleNamespace(
            get_config=lambda: self.config, persona_manager=manager,
            get_all_stars=lambda: [
                types.SimpleNamespace(module_path=f"data.plugins.{name}.main",
                                      version=version, activated=True, star_cls=object())
                for name, version in expected.items()
            ],
        )
        decorated = lambda *args, **kwargs: lambda function: function
        api = types.ModuleType("astrbot.api")
        api.AstrBotConfig = dict
        api.logger = types.SimpleNamespace(info=lambda *a: None, error=lambda *a: None)
        event = types.ModuleType("astrbot.api.event")
        event.AstrMessageEvent = object
        event.filter = types.SimpleNamespace(
            on_astrbot_loaded=decorated, event_message_type=decorated,
            EventMessageType=types.SimpleNamespace(ALL="all"))
        star = types.ModuleType("astrbot.api.star")
        star.Context, star.Star = object, Star
        star.StarTools = types.SimpleNamespace(get_data_dir=get_data_dir)
        paths = types.ModuleType("astrbot.core.utils.astrbot_path")
        paths.get_astrbot_data_path = lambda: str(data)
        commands = types.ModuleType("astrbot.core.star.command_management")
        commands.list_commands, commands.update_command_permission = list_commands, update_permission
        self.modules = patch.dict(sys.modules, {
            "astrbot": types.ModuleType("astrbot"), "astrbot.api": api,
            "astrbot.api.event": event, "astrbot.api.star": star,
            "astrbot.core": types.ModuleType("astrbot.core"),
            "astrbot.core.utils": types.ModuleType("astrbot.core.utils"),
            "astrbot.core.utils.astrbot_path": paths,
            "astrbot.core.star": types.ModuleType("astrbot.core.star"),
            "astrbot.core.star.command_management": commands,
            # Credential-redaction behavior has separate synthetic tests.
            "loguru": types.SimpleNamespace(
                logger=types.SimpleNamespace(configure=lambda **kwargs: None)),
        })
        self.modules.start()
        package_spec = importlib.util.spec_from_file_location(
            "phase2_hook_fixture", plugin_root / "__init__.py",
            submodule_search_locations=[str(plugin_root)])
        package = importlib.util.module_from_spec(package_spec)
        sys.modules[package_spec.name] = package
        package_spec.loader.exec_module(package)
        main_spec = importlib.util.spec_from_file_location(
            "phase2_hook_fixture.main", plugin_root / "main.py")
        main = importlib.util.module_from_spec(main_spec)
        main_spec.loader.exec_module(main)
        self.plugin = main.Phase2Policy(self.context,
                                        {"admin_id": "10001", "platform_id": "napcat"})

    def tearDown(self):
        self.modules.stop()
        self.temporary.cleanup()

    @staticmethod
    def event(sender="10001", private=True):
        result = types.SimpleNamespace(stopped=False)
        result.get_platform_name = lambda: "aiocqhttp"
        result.get_platform_id = lambda: "napcat"
        result.is_private_chat = lambda: private
        result.get_sender_id = lambda: sender
        result.stop_event = lambda: setattr(result, "stopped", True)
        return result

    async def test_missing_key_still_sets_acl_but_stops_chat(self):
        await self.plugin.apply_startup_policy()
        self.assertFalse(self.plugin.ready)
        self.assertEqual(self.commands[0]["permission"], "admin")
        self.assertEqual(self.context.persona_manager.tools, [])
        event = self.event()
        await self.plugin.restrict_inbound(event)
        self.assertTrue(event.stopped)
        self.assertEqual(json.loads(self.plugin.report_path.read_text())["status"], "BLOCKED")

    async def test_ready_allows_only_authorized_private_sender(self):
        self.config["provider_sources"][0].update(enable=True, key=["synthetic-key-for-tests"])
        self.config["provider_settings"]["enable"] = True
        self.config["provider"][0]["enable"] = True
        await self.plugin.apply_startup_policy()
        self.assertTrue(self.plugin.ready)
        for sender, private, blocked in [("10001", True, False),
                                          ("10002", True, True), ("10001", False, True)]:
            event = self.event(sender, private)
            await self.plugin.restrict_inbound(event)
            self.assertEqual(event.stopped, blocked)
        report = json.loads(self.plugin.report_path.read_text())
        self.assertEqual(report["scope"], "startup_policy_only")
        self.assertFalse(report["real_api_tested"])
        self.assertNotIn("synthetic-key-for-tests", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
