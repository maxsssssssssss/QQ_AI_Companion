"""T2 policy/persona persistence using the fixed native SQLite and PersonaManager.

The real Phase2Policy startup and Phase3Runtime prepare/expose hooks run here.
Required plugin discovery/command ACL descriptors are fixture metadata; actual
plugin loading and command registration belong to the full-process suite. No
production endpoint is relaxed and neither HTTP nor production files are used.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
NATIVE_AVAILABLE = importlib.util.find_spec("astrbot") is not None


def load_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


if NATIVE_AVAILABLE:
    os.environ["ASTRBOT_DISABLE_METRICS"] = "1"
    os.environ.setdefault("ASTRBOT_ROOT", tempfile.mkdtemp(prefix="qq-search-policy-"))
    from astrbot.api.star import StarTools
    from astrbot.core.db.sqlite import SQLiteDatabase
    from astrbot.core.persona_mgr import PersonaManager
    from astrbot.core.provider.entities import ProviderRequest
    from integrations.phase3_runtime import main as runtime_module
    from integrations.phase3_search.main import Phase3Search
    from integrations.phase3_runtime.persona import MARKER
    configure = load_file("search_policy_configure", ROOT / "scripts/phase2-configure.py")
    stage = load_file("search_policy_stage", ROOT / "scripts/phase3-stage.py")


class Event:
    def __init__(self, sender="10001", private=True):
        self.sender, self.private, self.stopped, self.extra = sender, private, False, {}
    def get_platform_name(self): return "aiocqhttp"
    def get_platform_id(self): return "napcat"
    def get_sender_id(self): return self.sender
    def is_private_chat(self): return self.private
    def stop_event(self): self.stopped = True
    def set_extra(self, key, value): self.extra[key] = value
    def get_extra(self, key, default=None): return self.extra.get(key, default)


@unittest.skipUnless(NATIVE_AVAILABLE, "pinned AstrBot runtime required; T2 NOT_RUN here")
class PolicyPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="qq-search-policy-")
        self.root = Path(self.directory.name)
        package = self.root / "fixture_phase2_policy"
        shutil.copytree(ROOT / "integrations/phase2_policy", package,
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copyfile(ROOT / "scripts/plugin_permissions.py", package / "plugin_permissions.py")
        load_file("fixture_phase2_policy", package / "__init__.py")
        self.policy_module = load_file("fixture_phase2_policy.main", package / "main.py")
        self.data = self.root / "data"
        for folder in ("phase2", "phase3", "plugin_data"):
            (self.data / folder).mkdir(parents=True)
        (self.data / "phase2/persona.md").write_text("SYNTHETIC_PHASE2_PERSONA")
        shutil.copyfile(ROOT / "persona/phase3-v0.1.md", self.data / "phase3/persona-v0.1.md")
        self.settings = dict(enabled=True, persona=True, natural_chat=True,
                             search=True, memory=False, proactive=False)
        self.base = configure.build_config("10001", "napcat", "synthetic-token-" + "x" * 32,
                                           "SyntheticPassword9")
        self.base["provider_settings"]["enable"] = True
        self.base["provider_sources"][0].update(enable=True, key=["synthetic-chat-key-not-a-secret"])
        self.base["provider"][0]["enable"] = True
        self.config = stage.candidate(self.base, settings=self.settings, admin_id="10001",
                                     platform_id="napcat", search_key="synthetic-search-key-not-a-secret")
        self.databases, self.extensions, self.patchers = [], [], []
        for target, name, value in (
            (self.policy_module, "get_astrbot_data_path", lambda: str(self.data)),
            (runtime_module, "get_astrbot_data_path", lambda: str(self.data)),
            (StarTools, "get_data_dir", lambda *a: self.data / "plugin_data"),
            (self.policy_module, "apply_plugin_permissions", AsyncMock(return_value={"fixture_acl": True})),
        ):
            p = patch.object(target, name, value)
            p.start()
            self.patchers.append(p)
        # Any accidental request is a test failure, not an allowed fixture URL.
        for target in ("aiohttp.ClientSession._request", "httpx.AsyncClient.send"):
            p = patch(target, AsyncMock(side_effect=AssertionError("unexpected HTTP request")))
            p.start()
            self.patchers.append(p)
        await self.start()

    async def start(self, *, enabled=True):
        db = SQLiteDatabase(str(self.root / "personas.sqlite3"))
        self.databases.append(db)
        manager = PersonaManager(db, NS(default_conf=self.config, get_conf=lambda *a: self.config))
        await manager.initialize()
        self.context = NS(persona_manager=manager, get_config=lambda *a, **k: self.config)
        self.stars = []
        self.context.get_all_stars = lambda: self.stars
        self.guard = NS(phase3_enabled=enabled, phase3_admin_id="10001", phase3_platform_id="napcat",
                        unregister_delivery_observer=lambda *a: None)
        versions = {"astrbot_plugin_message_recorder": "0.0.4", "astrbot_plugin_memory_companion": "2.3.1",
                    "astrbot_plugin_private_companion": "6.7.0", "phase1_guard": "0.1.0"}
        for name, version in versions.items():
            self.stars.append(NS(module_path=f"data.plugins.{name}.main", activated=True,
                                 star_cls=self.guard if name == "phase1_guard" else NS(), version=version))
        self.search = Phase3Search(self.context, dict(enabled=True, admin_id="10001", platform_id="napcat"))
        self.stars.append(NS(module_path="data.plugins.phase3_search.main", activated=True,
                             star_cls=self.search, version="0.1.0"))
        self.extension = runtime_module.Phase3Runtime(self.context, self.settings if enabled else {"enabled": False})
        self.extensions.append(self.extension)
        self.stars.append(NS(module_path="data.plugins.phase3_runtime.main", activated=True,
                             star_cls=self.extension, version="0.1.0"))
        self.policy = self.policy_module.Phase2Policy(self.context, dict(admin_id="10001", platform_id="napcat"))

    async def asyncTearDown(self):
        for extension in self.extensions:
            await extension.terminate()
        for db in self.databases:
            await db.engine.dispose()
        for p in reversed(self.patchers):
            p.stop()
        self.directory.cleanup()

    async def assert_ready_persona(self):
        await self.policy.apply_startup_policy()
        self.assertTrue(self.policy.ready)
        self.assertTrue(self.extension.ready)
        persona = await self.context.persona_manager.get_persona("qq_companion")
        self.assertEqual(persona.system_prompt.count(MARKER), 1)
        self.assertNotIn("SYNTHETIC_PHASE2_PERSONA", persona.system_prompt)
        self.assertEqual(persona.tools, ["web_search_deepseek", "deepseek_fetch_web_page"])
        self.assertEqual(persona.skills, [])
        self.assertIsNone(self.search.runtime)  # Startup does not incur a request.
        report = json.loads(self.policy.report_path.read_text())
        self.assertFalse(report["real_api_tested"])
        self.assertFalse(report["real_qq_tested"])
        return persona.system_prompt

    async def test_real_native_persona_sqlite_survives_restart_and_reapplied_policy(self):
        original = await self.assert_ready_persona()
        await self.extension.terminate()
        await self.databases[-1].engine.dispose()
        await self.start()
        self.assertEqual((await self.context.persona_manager.get_persona("qq_companion")).system_prompt, original)
        self.assertEqual(await self.assert_ready_persona(), original)

    async def test_core_only_exposed_to_exact_authorized_private_event(self):
        await self.assert_ready_persona()
        for event, expected in ((Event(), True), (Event("10002"), False), (Event(private=False), False)):
            await self.policy.restrict_inbound(event)
            await self.extension.expose_core(event, ProviderRequest(prompt="synthetic authorized message"))
            self.assertEqual("phase3_core_persona" in event.extra, expected)
            self.assertEqual(event.stopped, not expected)
        self.assertEqual(Event().extra, {})

    async def test_production_endpoint_cannot_be_relaxed_for_fixture(self):
        self.config["provider_sources"][0]["api_base"] = "http://127.0.0.1:1/v1"
        await self.policy.apply_startup_policy()
        self.assertFalse(self.policy.ready)
        self.assertFalse(self.extension.ready)
        report = json.loads(self.policy.report_path.read_text())
        self.assertEqual(report["reason"], "unexpected_provider_endpoint")
        self.assertFalse(any(p.persona_id == "qq_companion" for p in self.context.persona_manager.personas))

    async def test_missing_chat_key_cannot_activate_extension(self):
        self.config["provider_sources"][0]["key"] = []
        with patch.object(self.extension, "prepare", wraps=self.extension.prepare) as prepare:
            await self.policy.apply_startup_policy()
            prepare.assert_not_called()
        self.assertFalse(self.policy.ready)
        self.assertFalse(self.extension.ready)
        self.assertEqual(self.extension.tasks, set())
        self.assertEqual(json.loads(self.policy.report_path.read_text())["reason"], "missing_deepseek_key")

    async def test_requested_runtime_missing_inactive_or_uninstantiated_fails_closed(self):
        rows = list(self.stars)
        runtime_row = next(p for p in rows if p.module_path.endswith(".phase3_runtime.main"))
        for mode in ("missing", "inactive", "uninstantiated", "disabled", "wrong_version"):
            with self.subTest(mode=mode):
                self.stars[:] = rows
                runtime_row.activated, runtime_row.star_cls, runtime_row.version = True, self.extension, "0.1.0"
                self.extension.enabled = True
                if mode == "missing": self.stars.remove(runtime_row)
                elif mode == "inactive": runtime_row.activated = False
                elif mode == "uninstantiated": runtime_row.star_cls = None
                elif mode == "disabled": self.extension.enabled = False
                else: runtime_row.version = "unreviewed"
                await self.policy.apply_startup_policy()
                self.assertFalse(self.policy.ready)
                self.assertEqual(json.loads(self.policy.report_path.read_text())["reason"], "requested_phase3_runtime_unavailable")
        self.extension.enabled = True

    async def test_requested_phase3_plugin_constructor_failure_fails_closed(self):
        row = next(p for p in self.stars if p.module_path.endswith(".phase3_search.main"))
        row.star_cls = None
        await self.policy.apply_startup_policy()
        self.assertFalse(self.policy.ready)
        self.assertFalse(self.extension.ready)
        self.assertEqual(json.loads(self.policy.report_path.read_text())["reason"], "requested_phase3_plugin_unavailable")

    async def test_persona_only_requested_config_detects_missing_constructor(self):
        self.guard.phase3_enabled = False
        (self.data / "config").mkdir()
        (self.data / "config/phase3_runtime_config.json").write_text(json.dumps({"enabled":True,"persona":True}))
        self.stars[:] = [p for p in self.stars if not p.module_path.endswith(".phase3_runtime.main")]
        await self.policy.apply_startup_policy()
        self.assertFalse(self.policy.ready)
        self.assertEqual(json.loads(self.policy.report_path.read_text())["reason"], "requested_phase3_runtime_unavailable")

    async def test_invalid_runtime_config_cannot_look_disabled(self):
        self.guard.phase3_enabled = False
        (self.data / "config").mkdir()
        for raw in ('{', '[]', '{"enabled":"false"}', '{"enabled":0}', '\ufeff{"enabled":"false"}'):
            with self.subTest(raw=raw):
                (self.data / "config/phase3_runtime_config.json").write_text(raw)
                await self.policy.apply_startup_policy()
                self.assertFalse(self.policy.ready)
                self.assertEqual(json.loads(self.policy.report_path.read_text())["reason"], "invalid_phase3_runtime_config")

    async def test_native_config_bom_is_accepted_without_weakening_policy(self):
        (self.data/'config').mkdir()
        path=self.data/'config/phase3_runtime_config.json'
        from astrbot.core.config.astrbot_config import AstrBotConfig
        schema=json.loads((ROOT/'integrations/phase3_runtime/_conf_schema.json').read_text())
        path.write_text(json.dumps(self.settings))
        cfg=AstrBotConfig(str(path),schema=schema)
        cfg.save_config()
        self.assertTrue(path.read_bytes().startswith(b'\xef\xbb\xbf'))
        await self.assert_ready_persona()
        self.assertTrue(self.policy.ready)

    async def test_failure_after_prepare_deactivates_extension(self):
        manager = self.context.persona_manager
        with patch.object(manager, "create_persona", AsyncMock(side_effect=RuntimeError("synthetic-store-failure"))), \
                patch.object(self.extension, "prepare", wraps=self.extension.prepare) as prepare, \
                patch.object(self.extension, "terminate", wraps=self.extension.terminate) as terminate:
            await self.policy.apply_startup_policy()
            prepare.assert_awaited_once()
            terminate.assert_awaited_once()
        self.assertFalse(self.policy.ready)
        self.assertFalse(self.extension.ready)
        self.assertEqual(self.extension.tasks, set())
        self.assertEqual(json.loads(self.policy.report_path.read_text())["reason"], "RuntimeError")

    async def test_activate_runs_after_persist_and_failure_deactivates(self):
        async def fail_activation():
            self.assertFalse(self.policy.ready)
            self.assertFalse(self.extension.ready)
            self.assertTrue(self.extension.prepared)
            self.assertEqual((await self.context.persona_manager.get_persona("qq_companion")).system_prompt.count(MARKER), 1)
            raise RuntimeError("synthetic_activation_failure")
        with patch.object(self.extension, "activate", AsyncMock(side_effect=fail_activation)) as activate, \
                patch.object(self.extension, "terminate", wraps=self.extension.terminate) as terminate:
            await self.policy.apply_startup_policy()
            activate.assert_awaited_once()
            terminate.assert_awaited_once()
        self.assertFalse(self.policy.ready)
        self.assertFalse(self.extension.ready)
        self.assertFalse(self.extension.prepared)
        self.assertEqual(self.extension.tasks, set())

    async def test_search_scope_mismatch_fails_closed(self):
        self.search.settings["admin_id"] = "10002"
        await self.policy.apply_startup_policy()
        self.assertFalse(self.policy.ready)
        self.assertFalse(self.extension.ready)
        self.assertIsNone(self.search.runtime)

    async def test_readiness_report_failure_revokes_runtime_and_inbound_permission(self):
        original_report = self.policy._report
        for fail_blocked_report in (False, True):
            with self.subTest(fail_blocked_report=fail_blocked_report):
                def report(status, **details):
                    if status == "PASS" or fail_blocked_report:
                        raise OSError("synthetic_readiness_disk_failure")
                    original_report(status, **details)
                with patch.object(self.policy, "_report", side_effect=report), \
                        patch.object(self.extension, "activate", wraps=self.extension.activate) as activate, \
                        patch.object(self.extension, "terminate", wraps=self.extension.terminate) as terminate:
                    if fail_blocked_report:
                        with self.assertRaises(OSError):
                            await self.policy.apply_startup_policy()
                    else:
                        await self.policy.apply_startup_policy()
                    activate.assert_awaited_once()
                    terminate.assert_awaited_once()
                self.assertFalse(self.policy.ready)
                self.assertFalse(self.extension.ready)
                self.assertFalse(self.extension.prepared)
                self.assertEqual(self.extension.tasks, set())
                event = Event()
                await self.policy.restrict_inbound(event)
                self.assertTrue(event.stopped)
                if not fail_blocked_report:
                    self.assertEqual(json.loads(self.policy.report_path.read_text())["status"], "BLOCKED")

    async def test_initial_report_failure_keeps_policy_instance_and_deny_hook(self):
        for target, name in ((self.policy_module.Phase2Policy, "_report"), (Path, "mkdir")):
            with self.subTest(failure=name), patch.object(target, name, side_effect=OSError("synthetic_storage_failure")):
                self.policy = self.policy_module.Phase2Policy(self.context,
                    dict(admin_id="10001", platform_id="napcat"))
            self.assertFalse(self.policy.ready)
            event = Event()
            await self.policy.restrict_inbound(event)
            self.assertTrue(event.stopped)
        # Once storage recovers, the same loaded instance can validate normally.
        await self.assert_ready_persona()

    async def test_search_contract_rejects_key_stream_and_unbounded_loop(self):
        base = copy.deepcopy(self.config)
        mutations = [
            (lambda c: c["provider_settings"].update(websearch_deepseek_key=[]), "missing_search_key"),
            (lambda c: c["provider_settings"].update(streaming_response=True), "unsafe_streaming_response"),
            (lambda c: c["agent_runner"]["config"]["misc"].update(max_steps=99), "unbounded_agent_steps"),
            (lambda c: c["provider_settings"].update(buffer_intermediate_messages=True), "intermediate_buffer_enabled"),
        ]
        for mutate, expected in mutations:
            with self.subTest(expected=expected):
                self.config = copy.deepcopy(base)
                mutate(self.config)
                await self.policy.apply_startup_policy()
                self.assertFalse(self.policy.ready)
                self.assertFalse(self.extension.ready)
                self.assertEqual(json.loads(self.policy.report_path.read_text())["reason"], expected)

    async def test_disable_search_keeps_persona_and_database_then_full_rollback(self):
        original = await self.assert_ready_persona()
        await self.extension.terminate()
        self.settings["search"] = False
        self.config = stage.candidate(self.base, settings=self.settings, admin_id="10001", platform_id="napcat")
        await self.start()
        await self.policy.apply_startup_policy()
        self.assertTrue(self.policy.ready)
        persona = await self.context.persona_manager.get_persona("qq_companion")
        self.assertEqual(persona.system_prompt, original)
        self.assertEqual(persona.tools, [])
        await self.extension.terminate()
        await self.start(enabled=False)
        await self.policy.apply_startup_policy()
        self.assertTrue(self.policy.ready)
        persona = await self.context.persona_manager.get_persona("qq_companion")
        self.assertEqual(persona.system_prompt, "SYNTHETIC_PHASE2_PERSONA")
        self.assertEqual(persona.tools, [])
        self.assertEqual({p.persona_id for p in self.context.persona_manager.personas}, {"default", "qq_companion"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
