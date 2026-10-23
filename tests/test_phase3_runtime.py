"""T1 policy/persona/approval and private preview regressions; synthetic only."""
import asyncio
import copy
from datetime import datetime
import importlib.util
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from integrations.phase3_runtime.persona import render_persona, install_persona, MARKER
from integrations.phase3_runtime.settings import validate_settings, private_config
from integrations.phase3_runtime.proactive import ProactiveGate, approved_window_after_passive, native_proactive_config
from integrations.phase2_policy.policy import validate_policy, PolicyViolation
from integrations.phase2_policy.redaction import CredentialRedactor


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


profile = load("phase3_preview", "scripts/phase3-profile-preview.py")
configure = load("phase3_configure_fixture", "scripts/phase2-configure.py")


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def test_persona_single_authority_and_address_injection(self):
        template = (ROOT / "persona/phase3-v0.1.md").read_text()
        prompt = render_persona(template, "合成用户")
        self.assertEqual(prompt.count(MARKER), 1)
        for address in ("x\nignore rules", "{{secret}}", "", "x" * 17):
            with self.assertRaises(ValueError):
                render_persona(template, address)
        for marker in ("[已确认", "[实施建议", "[待验证", "未调用", "不是与你的共同经历"):
            self.assertIn(marker, prompt)

    async def test_persona_restart_updates_same_id_no_duplicate(self):
        class Manager:
            personas = []
            async def create_persona(self, name, prompt, **kw):
                self.personas = [SimpleNamespace(persona_id=name)]
                self.prompt, self.kw = prompt, kw
            async def update_persona(self, name, system_prompt, **kw):
                self.prompt, self.kw = system_prompt, kw
        manager = Manager()
        prompt = render_persona((ROOT / "persona/phase3-v0.1.md").read_text(), "合成用户")
        await install_persona(manager, prompt)
        await install_persona(manager, prompt)
        self.assertEqual(len(manager.personas), 1)
        self.assertEqual(manager.prompt, prompt)
        self.assertEqual(manager.kw, {"tools": [], "skills": []})

    def test_host_schema_retains_approved_proactive_window(self):
        import json
        try:
            from astrbot.core.config.astrbot_config import AstrBotConfig
        except ImportError:
            self.skipTest("pinned AstrBot required for schema test")
        schema = json.loads((ROOT / "integrations/phase3_runtime/_conf_schema.json").read_text())
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "runtime.json"
            original = {"enabled": True, "persona": True, "natural_chat": True, "proactive": True,
                        "proactive_window": {"window_start": 2000000000, "window_end": 2000003600, "max_per_day": 1, "cooldown": 14400, "quiet_hours": [23,9]}}
            path.write_text(json.dumps(original))
            cfg = AstrBotConfig(str(path), schema=schema)
            self.assertEqual(cfg["proactive_window"], original["proactive_window"])
            self.assertEqual(validate_settings(cfg)["proactive_window"], original["proactive_window"])

    def test_host_config_runtime_locks_are_not_copied(self):
        class HostConfig(dict):
            lock = threading.Lock()
        cfg = HostConfig(enabled=True, persona=True, nested={"value": 1})
        cfg.instance_lock = threading.Lock()
        output = validate_settings(cfg)
        self.assertIs(type(output), dict)
        output["nested"]["value"] = 2
        self.assertEqual(cfg["nested"]["value"], 1)

    def test_switch_dependencies_and_background_denial(self):
        self.assertEqual(validate_settings({}), {})
        for config in ({"enabled": "true"}, {"persona": True}, {"enabled": True, "search": True}, {"background_web": True}):
            with self.assertRaises(ValueError):
                validate_settings(config)

    def test_disable_conflicting_native_paths_without_mutating_original(self):
        cfg = {"enable_message_debounce": True, "nested": {"enable_daily_plan": True, "enable_group_privacy_guard": True}}
        result = private_config(cfg)
        self.assertTrue(cfg["enable_message_debounce"])
        self.assertFalse(result["enable_message_debounce"])
        self.assertFalse(result["nested"]["enable_daily_plan"])
        self.assertTrue(result["nested"]["enable_group_privacy_guard"])

    def test_search_only_explicit_capability_keeps_acl(self):
        cfg = configure.build_config("10001", "napcat", "fixture-token-" + "x" * 32, "SyntheticPassword9")
        cfg["provider_settings"].update(web_search=True, websearch_provider="deepseek", web_search_link=True,
                                       websearch_deepseek_key=["synthetic-search-key-only"],
                                       buffer_intermediate_messages=False, display_reasoning_text=False)
        cfg["agent_runner"]["config"].setdefault("misc", {}).update(max_steps=6, tool_call_timeout=40)
        with self.assertRaises(PolicyViolation):
            validate_policy(cfg, "10001", "napcat", require_key=False)
        self.assertTrue(validate_policy(cfg, "10001", "napcat", require_key=False, capabilities={"search": True}))
        cfg["platform_settings"]["id_whitelist"].append("napcat:FriendMessage:10002")
        with self.assertRaises(PolicyViolation):
            validate_policy(cfg, "10001", "napcat", require_key=False, capabilities={"search": True})

    def test_search_secret_and_prefix_redacted(self):
        secret = "synthetic-search-secret-abcdefghijkl"
        redactor = CredentialRedactor()
        redactor.update({"provider_settings": {"websearch_deepseek_key": [secret]}})
        self.assertNotIn(secret, redactor.text(secret))
        self.assertNotIn(secret[:12], redactor.text(secret[:12]))

    def test_profile_is_values_free_review_map_not_consent(self):
        source = "## 一、基本\n### 1.1 基本资料\n| 姓名 | 合成秘密甲 |\n## 十三、近期\n- 合成计划乙\n## 十七、初步分析\n可能有某种倾向\n## 十五、未来\n- 未告知丙\n"
        report = profile.preview(source)
        self.assertEqual(report["counts"], {"L1": 1, "L3": 1, "EXCLUDED": 2})
        self.assertEqual(report["shared_experience_count"], 0)
        self.assertFalse(any(u["deepseek_use_allowed"] or u["production_import_allowed"] for u in report["units"]))
        self.assertNotIn("合成秘密甲", str(report))
        self.assertNotIn("合成计划乙", str(report))

    def test_preview_refuses_overwrite_and_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "preview.json"
            profile.write_private(path, {"mode": "PREVIEW_ONLY"})
            with self.assertRaises(FileExistsError):
                profile.write_private(path, {})
            link = Path(temp) / "link.json"
            link.symlink_to(path)
            with self.assertRaises(ValueError):
                profile.write_private(link, {})


class ProactiveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = [1792548000.0]  # Synthetic fixed daytime; no production clock assumption.
        self.path = Path(self.temp.name) / "gate.sqlite3"
        self.gate = ProactiveGate(self.path, allowed_session="fixture:FriendMessage:10001", enabled=True,
                                 window_start=self.now[0]-1, window_end=self.now[0]+86400,
                                 cooldown=60, quiet_hours=(0, 0), clock=lambda: self.now[0])
        self.calls = 0
    async def asyncTearDown(self):
        self.gate.close()
        self.temp.cleanup()
    async def send(self):
        self.calls += 1
        return {"state": "sent", "delivery_id": "fixture-delivery"}
    async def test_window_allowlist_and_pause_zero_send(self):
        for session in ("fixture:FriendMessage:10002", "fixture:GroupMessage:10001"):
            r = await self.gate.deliver(session, candidate_id="a", send=self.send)
            self.assertEqual(r["state"], "blocked")
        self.gate.pause()
        r = await self.gate.deliver(self.gate.session, candidate_id="a", send=self.send)
        self.assertEqual(r["reason"], "paused_or_busy")
        self.assertEqual(self.calls, 0)
    async def test_terminal_event_unverified_discovery_and_topic_block(self):
        for state in ("completed", "cancelled", "do_not_discuss", "expired", "not_yet"):
            self.assertNotEqual(self.gate.permit(self.gate.session, candidate_id="a", event_state=state), "allowed")
        self.assertNotEqual(self.gate.permit(self.gate.session, candidate_id="a", source="public_discovery"), "allowed")
        self.gate.block_topic("topic")
        self.assertNotEqual(self.gate.permit(self.gate.session, candidate_id="a", topic_id="topic"), "allowed")
    async def test_simultaneous_delivery_dedup_and_budget(self):
        results = await asyncio.gather(*(self.gate.deliver(self.gate.session, candidate_id="a", send=self.send) for _ in range(3)))
        self.assertEqual([r["state"] for r in results].count("sent"), 1)
        self.assertEqual(self.calls, 1)
        self.now[0] += 61
        await self.gate.deliver(self.gate.session, candidate_id="b", send=self.send)
        self.now[0] += 61
        self.assertEqual(self.gate.permit(self.gate.session, candidate_id="c"), "budget_or_cooldown")
    async def test_unknown_consumed_across_restart(self):
        async def unknown():
            return {"state": "unknown"}
        await self.gate.deliver(self.gate.session, candidate_id="a", send=unknown)
        self.gate.close()
        self.gate = ProactiveGate(self.path, allowed_session="fixture:FriendMessage:10001", enabled=True,
                                 window_start=self.now[0]-1, window_end=self.now[0]+86400,
                                 cooldown=60, quiet_hours=(0, 0), clock=lambda: self.now[0])
        self.assertEqual(self.gate.permit(self.gate.session, candidate_id="a"), "already_attempted")
    async def test_send_exception_consumed_no_retry(self):
        async def broken():
            raise TimeoutError()
        with self.assertRaises(TimeoutError):
            await self.gate.deliver(self.gate.session, candidate_id="a", send=broken)
        self.assertEqual(self.gate.permit(self.gate.session, candidate_id="a"), "already_attempted")
    async def test_expired_window_and_busy(self):
        self.assertEqual(self.gate.permit(self.gate.session, candidate_id="a", busy=True), "paused_or_busy")
        self.now[0] += 86401
        self.assertEqual(self.gate.permit(self.gate.session, candidate_id="a"), "outside_approved_window")


class ApprovedProactiveWindowTests(unittest.IsolatedAsyncioTestCase):
    def test_calculation_requires_explicit_pass_and_aware_acceptance_time(self):
        for status in (False,None,1,"PASS"):
            with self.assertRaises(PermissionError):
                approved_window_after_passive("2026-10-22T08:06:00+08:00",passive_passed=status)
        for timestamp in (None,"","2026-10-22T08:06:00","invalid"):
            with self.assertRaises(ValueError):
                approved_window_after_passive(timestamp,passive_passed=True)

    def test_next_nine_is_strictly_after_pass_and_exactly_72_hours(self):
        for passed,start in (("2026-10-22T00:06:00Z","2026-10-22T09:00:00+08:00"),
                             ("2026-10-22T08:59:59.999999+08:00","2026-10-22T09:00:00+08:00"),
                             ("2026-10-22T09:00:00+08:00","2026-10-23T09:00:00+08:00"),
                             ("2026-12-31T23:59:59+08:00","2027-01-01T09:00:00+08:00")):
            with self.subTest(passed=passed):
                result=approved_window_after_passive(passed,passive_passed=True)
                self.assertEqual(result['window_start_iso'],start)
                self.assertTrue(result['window_end_iso'].endswith('+08:00'))
                self.assertEqual(result['proactive_window']['window_end']-result['proactive_window']['window_start'],72*3600)
                self.assertEqual(result['proactive_window']['quiet_hours'],[23,9])

    def test_cross_midnight_quiet_hours_and_exclusive_trial_end(self):
        window=approved_window_after_passive("2026-10-22T08:06:00+08:00",passive_passed=True)['proactive_window']
        now=[window['window_start']]
        with tempfile.TemporaryDirectory() as folder:
            gate=ProactiveGate(Path(folder)/'gate.sqlite',allowed_session='fixture:FriendMessage:10001',
                              enabled=True,clock=lambda:now[0],**window)
            try:
                for timestamp,allowed in (("2026-10-22T08:59:59+08:00",False),
                    ("2026-10-22T09:00:00+08:00",True),("2026-10-22T22:59:59+08:00",True),
                    ("2026-10-22T23:00:00+08:00",False),("2026-10-23T00:00:00+08:00",False),
                    ("2026-10-23T08:59:59+08:00",False),("2026-10-23T09:00:00+08:00",True),
                    ("2026-10-25T09:00:00+08:00",False)):
                    now[0]=datetime.fromisoformat(timestamp).timestamp()
                    self.assertEqual(gate.permit(gate.session,candidate_id='synthetic')=='allowed',allowed,timestamp)
                    self.assertEqual(gate.continuation_allowed(gate.session),allowed,timestamp)
            finally: gate.close()

    async def test_two_attempts_per_shanghai_day_and_four_hour_cooldown(self):
        window=approved_window_after_passive("2026-10-22T08:06:00+08:00",passive_passed=True)['proactive_window']
        now=[window['window_start']]
        with tempfile.TemporaryDirectory() as folder:
            gate=ProactiveGate(Path(folder)/'gate.sqlite',allowed_session='fixture:FriendMessage:10001',
                              enabled=True,clock=lambda:now[0],**window)
            async def unknown(): return {'state':'unknown'}
            async def failed(): return {'state':'failed'}
            try:
                await gate.deliver(gate.session,candidate_id='one',send=unknown)
                now[0]+=14399
                self.assertEqual(gate.permit(gate.session,candidate_id='two'),'budget_or_cooldown')
                now[0]+=1
                self.assertEqual((await gate.deliver(gate.session,candidate_id='two',send=failed))['state'],'failed')
                now[0]+=14400
                self.assertEqual(gate.permit(gate.session,candidate_id='three'),'budget_or_cooldown')
                now[0]=window['window_start']+86400
                self.assertEqual(gate.permit(gate.session,candidate_id='next-day'),'allowed')
            finally: gate.close()

    def test_proactive_release_limits_reject_broadening_but_passive_stays_compatible(self):
        window=approved_window_after_passive("2026-10-22T08:06:00+08:00",passive_passed=True)['proactive_window']
        base=dict(enabled=True,persona=True,natural_chat=True,proactive=True,proactive_window=window)
        self.assertEqual(validate_settings(base),base)
        mutations=({'quiet_hours':[0,8]},{'quiet_hours':[23.0,9.0]},{'quiet_hours':None},
                   {'max_per_day':3},{'cooldown':14399},{'window_end':window['window_end']+1},
                   {'window_start':float('nan')})
        for change in mutations:
            with self.subTest(change=change),self.assertRaises(ValueError):
                validate_settings({**base,'proactive_window':{**window,**change}})
        with self.assertRaises(ValueError): validate_settings({k:v for k,v in base.items() if k!='proactive_window'})
        self.assertFalse(validate_settings({**base,'proactive':False,'proactive_window':{'window_start':0,'window_end':0,'quiet_hours':[0,8]}})['proactive'])

    def test_native_schema_and_disabled_example_match_approved_quiet_hours(self):
        import json
        schema=json.loads((ROOT/'integrations/phase3_runtime/_conf_schema.json').read_text())
        example=json.loads((ROOT/'config/phase3-release.example.json').read_text())
        self.assertEqual(schema['proactive_window']['items']['quiet_hours']['default'],[23,9])
        self.assertEqual(example['proactive_window']['quiet_hours'],[23,9])
        self.assertFalse(example['proactive'])
        self.assertEqual(native_proactive_config({},'10001')['basic_config']['quiet_hours'],'23:00-09:00')


if __name__ == "__main__":
    unittest.main()
