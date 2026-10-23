"""T2 pinned Private candidate/generator entry integration, synthetic model I/O.

This executes original candidate creation, delivery identity and two-level
generation methods. The timer loop and provider are fixtures, not real QQ/API.
"""
import asyncio
import importlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest

from integrations.phase3_runtime.proactive import (
    ProactiveGate, NativeProactiveBridge, native_proactive_config, authorize_native_target,
    parse_proactive_controls, parse_proactive_decision)

ROOT = Path(__file__).resolve().parents[1]
try:
    package = types.ModuleType("phase3_native_private_fixture")
    package.__path__ = [str(ROOT / ".runtime/upstream/private_companion")]
    sys.modules[package.__name__] = package
    engine = importlib.import_module(package.__name__ + ".proactive_engine")
    message = importlib.import_module(package.__name__ + ".proactive_message")
    scheduler = importlib.import_module(package.__name__ + ".proactive")
    capability = importlib.import_module(package.__name__ + ".unified_profile_service")
    NATIVE = True
except ImportError:
    NATIVE = False


if NATIVE:
    class NativeHost(engine.ProactiveEngineMixin, message.ProactiveMessageMixin, scheduler.ProactiveMixin):
        """Use native methods under test; replace unrelated persona/provider I/O."""
        def __init__(self):
            self.user = {"user_id": "10001", "umo": "test:FriendMessage:10001"}
            self.data = {"users":{"10001":self.user}}
            self.model_calls = []
            self.model_text = "刚才说的面试，结果出来了吗？"
            self.before_model = None
            self.enable_llm_proactive_message = True
            self.saved = []
            self.ticks = 0
        def _private_user_role(self, user): return "primary"
        def _normalize_internal_motive_text(self, text): return text
        def _normalize_legacy_proactive_text(self, text, limit=40): return str(text or "")[:limit]
        def _proactive_persona_alignment(self, *a, **k): return {"score": .8}
        def _proactive_candidate_semantics(self, *a, **k): return {"kind": "followup", "score": .8}
        def _proactive_message_kind(self, **kw): return "followup"
        def _proactive_kind_policy(self, kind): return {}
        def _is_reason_allowed_now(self, reason, user): return True
        def persona_setting(self,key,default=None): return getattr(self,key,default)
        def _proactive_quota_policy(self, user): return {"tier": 1}
        def _proactive_topic_signature(self, *a): return ' '.join(str(p) for p in a)
        def _cleanup_proactive_impulses(self, user): return user.get("proactive_impulses", [])
        def _get_user(self, user_id): return self.user
        def _clear_proactive_reaction_intent(self, umo): pass
        def _proactive_reaction_intent_allows_sticker_only(self, intent): return False
        def _proactive_reaction_expression_fallback_intent(self, *a, **k): return {}
        def _store_proactive_reaction_intent(self, *a, **k): pass
        def _proactive_sticker_only_pending(self, umo): return False
        async def _finalize_proactive_generated_text(self, user, text, **kw): return text, ""
        async def _generate_proactive_message_via_framework(self, user, *a, **k):
            return await self._run_framework_agent_text(umo=user["umo"], task="proactive_message", max_steps=20)
        async def _run_framework_agent_text(self, **kwargs):
            self.model_calls.append(kwargs)
            if self.before_model: await self.before_model()
            return self.model_text
        async def _generate_proactive_message_direct_fallback(self, *a, **k):
            self.model_calls.append({"fallback": True})
            return ""
        async def _proactive_persona_delivery_allowed(self, umo): return True
        def _proactive_identity_binding_is_verified(self, *a): return True
        def _req036_update_capabilities(self, user, changes, **kw):
            return capability.update_capabilities(user, changes, actor_authorized=True, **kw)
        def _schedule_data_save(self, **kw): self.saved.append(kw)
        def _scheduler_maintenance_tasks(self): return ()
        async def _tick(self): await self._tick_user("10001",self.user)
        async def _tick_user(self, user_id, user):
            # A fixture for the large native eligibility/settlement body; the
            # scheduler cycle itself and candidate/generator are original.
            self.ticks += 1
            text = await self._generate_proactive_message_with_llm(user,"合成对象","followup")
            if text: await self._send_proactive_message_chain(user["umo"],text)

        def plan(self, *, source="followup", event_id="event-1", topic="面试结果", now=1800000000):
            impulse = self._build_proactive_impulse(self.user, reason="followup", action="message",
                motive="对方提过面试", topic=topic, source=source, window_start_at=now-1,
                preferred_ts=now, best_until_at=now+7200, expire_at=now+86400,
                window_timezone="Asia/Shanghai", origin_event_id=event_id,
                context={"phase3_event_revision":1})
            self.user.update(proactive_impulses=[impulse], planned_proactive_impulse_id=impulse["id"],
                planned_proactive_source=source, planned_proactive_reason="followup",
                planned_proactive_topic=topic, planned_proactive_motive="对方提过面试")
            return impulse


@unittest.skipUnless(NATIVE, "requires pinned AstrBot/Private dependencies")
class NativeProactiveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = 1800000000.0
        self.gate = ProactiveGate(Path(self.temp.name) / "gate.sqlite", allowed_session="test:FriendMessage:10001",
            enabled=True, window_start=self.now-1, window_end=self.now+10000,
            cooldown=60, quiet_hours=(0, 0), clock=lambda:self.now)
        self.private = NativeHost()
        self.private.plan()
        self.busy = False
        self.sends = []
        self.observations = []
        self.status = {"state":"awaiting_result", "due":True, "eligible":True, "revision":1}
        async def send(umo, text, *, delivery_id, is_current):
            self.assertTrue(is_current())
            self.sends.append((umo, text, delivery_id))
            return {"state":"sent", "delivery_id":delivery_id}
        def resolve(umo, event_id): return dict(self.status)
        def observed(*args): self.observations.append(args)
        self.guard = types.SimpleNamespace(session_busy=lambda u:self.busy, send_proactive=send)
        self.bridge = NativeProactiveBridge(self.private, self.guard, self.gate, message._ProactiveSendOutcome,
            event_resolver=resolve, event_delivered=observed)
        self.bridge.attach()

    async def asyncTearDown(self):
        self.bridge.detach()
        self.gate.close()
        self.temp.cleanup()

    async def test_native_candidate_generation_delivery_and_unique_receipt(self):
        candidate = self.private._planned_proactive_impulse(self.private.user)
        self.assertEqual(candidate["origin_event_id"], "event-1")
        text = await self.private._generate_proactive_message_with_llm(self.private.user,"合成对象","followup")
        self.assertEqual(self.private.model_calls[0]["max_steps"],4)
        self.assertIn('[PHASE3_PROACTIVE_DECISION_V1]',self.private.model_calls[0]['prompt'])
        outcome = await self.private._send_proactive_message_chain(self.private.user["umo"],text)
        self.assertIsInstance(outcome,message._ProactiveSendOutcome)
        self.assertTrue(outcome.delivered)
        repeat = await self.private._send_proactive_message_chain(self.private.user["umo"],text)
        self.assertFalse(repeat.delivered)
        self.assertEqual(len(self.sends),1)
        self.assertEqual(self.observations[0][1],"event-1")
        self.assertEqual(self.status["state"],"awaiting_result")  # bot send is not user response

    async def test_pause_busy_and_lifecycle_gate_before_any_model(self):
        self.gate.pause()
        self.assertEqual(await self.bridge.generate(self.private.user,"人","followup"),"")
        self.gate.pause(0); self.busy=True
        self.assertEqual(await self.bridge.generate(self.private.user,"人","followup"),"")
        self.busy=False; self.status.update(state="responded",eligible=False)
        self.assertEqual(await self.bridge.generate(self.private.user,"人","followup"),"")
        self.assertEqual(self.private.model_calls,[])

    async def test_native_single_scheduler_cycle_calls_guarded_generation_and_delivery(self):
        self.gate.pause()
        await self.private._run_scheduler_cycle(immediate=True)
        self.assertEqual(self.private.ticks,0)
        self.gate.pause(0)
        await self.private._run_scheduler_cycle(immediate=True)
        self.assertEqual(self.private.ticks,1)
        self.assertEqual(len(self.private.model_calls),1)
        self.assertEqual(len(self.sends),1)
        await self.private._run_scheduler_cycle(immediate=True)
        self.assertEqual(self.private.ticks,1)

    async def test_due_event_is_offered_to_native_candidate_entry_once_per_revision(self):
        offered=[]
        def offer(user_id,user,candidate):
            offered.append(candidate)
            self.private.plan(event_id=candidate['origin_event_id'])
            self.private.user['proactive_impulses'][0]['context']=candidate['context']
            return True
        self.private._offer_proactive_candidate=offer
        self.bridge.event_source=lambda umo: asyncio.sleep(0,result=[{
            'event_id':'event-2','revision':1,'user_quote':'合成面试待结果','source_id':'inbound-2'}])
        self.assertTrue(await self.bridge.offer_due_event())
        self.assertFalse(await self.bridge.offer_due_event())
        self.assertEqual(len(offered),1)
        self.assertEqual(offered[0]['origin_event_id'],'event-2')
        self.assertEqual(offered[0]['context']['phase3_event_revision'],1)
        self.assertEqual(offered[0]['trigger_message_id'],'inbound-2')
        self.status['revision']=2
        self.assertEqual(await self.bridge.generate(self.private.user,'人','followup'),'')

    def test_explicit_user_pause_today_and_topic_suppression_are_literal(self):
        actions=parse_proactive_controls('我现在忙，今天先不要主动找我。',now=self.now,window_end=self.gate.end)
        self.assertEqual(actions[0]['kind'],'pause')
        self.assertGreater(actions[0]['until'],self.now)
        self.assertLessEqual(actions[0]['until'],self.gate.end)
        self.assertEqual(parse_proactive_controls('这件事不想再聊了',now=self.now,window_end=self.gate.end),[{'kind':'block_last_topic'}])
        for text in ('别忘了主动找我','不要停止主动找我','如果今天不要主动找我会怎样','他说“不要主动找我”'):
            self.assertEqual(parse_proactive_controls(text,now=self.now,window_end=self.gate.end),[])

    def test_proactive_runner_receives_no_search_file_cron_or_memory_tools(self):
        from astrbot.api.provider import ProviderRequest
        from astrbot.core.agent.tool import ToolSet, FunctionTool
        request=ProviderRequest(prompt='proactive',func_tool=ToolSet())
        for name in ['web_search','fetch','phase3_event','astrbot_file_read_tool','create_future_task']:
            request.func_tool.add_tool(FunctionTool(name=name,description='Synthetic',parameters={'type':'object','properties':{}}))
        removed=self.private._filter_incompatible_proactive_framework_tools(request,{'AIsearch'})
        self.assertEqual(len(removed),5)
        self.assertIsNone(request.func_tool)

    async def test_dynamic_user_reply_during_model_discards_stale_output(self):
        async def reply(): self.status.update(state="responded",eligible=False)
        self.private.before_model=reply
        self.assertEqual(await self.bridge.generate(self.private.user,"人","followup"),"")
        self.assertEqual(self.sends,[])
        self.assertEqual(self.gate.db.execute("SELECT state FROM proactive_generations").fetchone()[0],"expired")

    async def test_generation_timeout_and_repeated_candidate_do_not_loop(self):
        async def slow(): await asyncio.sleep(.2)
        self.private.before_model=slow; self.bridge.generation_timeout=.01
        self.assertEqual(await self.bridge.generate(self.private.user,"人","followup"),"")
        self.assertEqual(await self.bridge.generate(self.private.user,"人","followup"),"")
        self.assertEqual(len(self.private.model_calls),1)
        self.assertEqual(self.bridge.metrics["generation_errors"],1)

    async def test_native_candidate_without_provenance_and_media_blocked(self):
        self.private.plan(source="news")
        self.assertEqual(await self.bridge.generate(self.private.user,"人","news"),"")
        outcome=await self.bridge.send(self.private.user["umo"],"x",image_path="x.png")
        self.assertFalse(outcome.delivered)
        self.private.user.pop("planned_proactive_impulse_id")
        self.assertEqual(await self.bridge.generate(self.private.user,"人","followup"),"")
        self.assertEqual(self.sends,[])

    async def test_restart_does_not_repeat_model_or_pending_send(self):
        await self.bridge.generate(self.private.user,"人","followup")
        self.bridge.detach(); self.gate.close()
        self.gate=ProactiveGate(Path(self.temp.name)/"gate.sqlite",allowed_session="test:FriendMessage:10001",
            enabled=True,window_start=self.now-1,window_end=self.now+10000,quiet_hours=(0,0),clock=lambda:self.now)
        self.bridge=NativeProactiveBridge(self.private,self.guard,self.gate,message._ProactiveSendOutcome,
            event_resolver=lambda *a: asyncio.sleep(0,result=self.status))
        self.bridge.attach()
        self.assertEqual(await self.bridge.generate(self.private.user,"人","followup"),"")
        self.assertEqual(len(self.private.model_calls),1)

    def test_config_matches_pinned_schema_and_target_migration_needs_explicit_approval(self):
        cfg=native_proactive_config({},"10001",enabled=True)
        schema=json.loads((ROOT/".runtime/upstream/private_companion/_conf_schema.json").read_text())
        for group, values in cfg.items():
            for key in values: self.assertIn(key,schema[group]["items"])
        self.assertEqual(cfg["proactive_reach_config"]["max_daily_messages"],0)
        self.assertFalse(cfg["proactive_generation_config"]["enable_llm_proactive_message"])
        self.assertFalse(cfg["basic_config"]["default_proactive_enabled"])
        with self.assertRaises(PermissionError): authorize_native_target(self.private,self.private.user["umo"])
        result=authorize_native_target(self.private,self.private.user["umo"],approved=True)
        self.assertTrue(result["native_capability_updated"])
        self.assertTrue(capability.capability_summary(self.private.user)["proactive_private_enabled"])
        self.assertEqual(self.private.user["proactive_daily_limit"],2)
        self.assertEqual(len(self.private.saved),1)

    def test_cold_start_activation_is_memory_only_and_detach_disarms(self):
        self.private.config=native_proactive_config({},"10001",enabled=True)
        self.private.max_daily_messages=0
        self.private.enable_llm_proactive_message=False
        self.gate.enabled=False
        with self.assertRaises(PermissionError): self.bridge.activate_native_config()
        self.gate.enabled=True
        self.bridge.activate_native_config()
        self.assertEqual(self.private.max_daily_messages,2)
        self.assertTrue(self.private.enable_llm_proactive_message)
        self.assertEqual(self.private.config['proactive_reach_config']['max_daily_messages'],0)
        self.assertFalse(self.private.config['proactive_generation_config']['enable_llm_proactive_message'])
        self.bridge.detach()
        self.assertEqual(self.private.max_daily_messages,0)
        self.assertFalse(self.private.enable_llm_proactive_message)

    def test_expired_approval_on_restart_keeps_native_off_without_failing_passive(self):
        self.private.max_daily_messages=0
        self.private.enable_llm_proactive_message=False
        self.now=self.gate.end
        self.assertFalse(self.bridge.activate_native_config())
        self.assertFalse(self.gate.enabled)
        self.assertEqual(self.private.max_daily_messages,0)
        self.assertFalse(self.private.enable_llm_proactive_message)

    async def test_scheduler_expiry_disables_native_and_preserves_passive_cycle(self):
        self.private.max_daily_messages=0
        self.private.enable_llm_proactive_message=False
        self.assertTrue(self.bridge.activate_native_config())
        self.now=self.gate.end
        native_cycle=self.bridge._additional_bindings['_run_scheduler_cycle']
        cycles=[]
        async def observed_cycle(**kwargs):
            self.assertEqual(self.private.max_daily_messages,0)
            cycles.append(kwargs)
            return await native_cycle(**kwargs)
        self.bridge._additional_bindings['_run_scheduler_cycle']=observed_cycle
        await self.private._run_scheduler_cycle(immediate=True)
        self.assertFalse(self.gate.enabled)
        self.assertEqual(self.private.max_daily_messages,0)
        self.assertFalse(self.private.enable_llm_proactive_message)
        self.assertEqual(self.private.ticks,0)
        self.assertEqual(self.private.model_calls,[])
        self.assertEqual(self.sends,[])
        self.assertEqual(cycles,[{'immediate':True}])

    async def test_scheduler_waits_until_window_and_approved_daytime(self):
        self.now=self.gate.start-1
        await self.private._run_scheduler_cycle(immediate=True)
        self.assertEqual(self.private.ticks,0)
        from datetime import datetime
        self.now=datetime.fromisoformat('2026-10-22T23:00:00+08:00').timestamp()
        self.gate.start=self.now-1;self.gate.end=self.now+86400;self.gate.quiet=(23,9)
        await self.private._run_scheduler_cycle(immediate=True)
        self.assertEqual(self.private.ticks,0)
        self.assertEqual(self.private.model_calls,[])

    async def test_same_model_send_defer_skip_and_no_direct_fallback(self):
        for decision, text, state in (
            ('send','[PHASE3_PROACTIVE:send]\n想起你说的那件事。','generated'),
            ('defer','[PHASE3_PROACTIVE:defer:60]','deferred'),
            ('skip','[PHASE3_PROACTIVE:skip]','skipped')):
            with self.subTest(decision=decision):
                # Each subcase has a distinct native candidate/evaluation.
                self.now += 86400
                self.gate.end = self.now+86400
                self.private.plan(event_id='decision-'+decision, now=self.now)
                self.private.model_text = text
                before = len(self.private.model_calls)
                value = await self.bridge.generate(self.private.user,'人','followup')
                self.assertEqual(len(self.private.model_calls)-before,1)
                self.assertFalse(any(r.get('fallback') for r in self.private.model_calls))
                self.assertEqual(bool(value),decision=='send')
                self.assertNotIn('PHASE3_PROACTIVE:',value)
                self.assertEqual(self.gate.db.execute('SELECT state FROM proactive_generations ORDER BY rowid DESC').fetchone()[0],state)
                if decision!='send':
                    impulse=self.private._planned_proactive_impulse(self.private.user)
                    # Native queued lookup may exclude skipped candidates; the
                    # source object and its metadata remain in its own store.
                    impulse=impulse or self.private.user['proactive_impulses'][0]
                    self.assertEqual(impulse['context']['phase3_decision']['decision'],decision)
                    self.assertEqual(self.gate.db.execute('SELECT count(*) FROM proactive_attempts').fetchone()[0],0)

    async def test_unanswered_new_topic_allowed_but_same_spoken_topic_waits(self):
        self.private.plan(source='conversation',topic='合成面试结果',now=self.now)
        self.private.model_text='想起合成面试那件事了。'
        text=await self.bridge.generate(self.private.user,'人','conversation')
        self.assertTrue((await self.bridge.send(self.private.user['umo'],text)).delivered)
        self.now+=self.gate.cooldown
        self.private.plan(source='conversation',event_id='same-again',topic='合成面试结果',now=self.now)
        before=len(self.private.model_calls)
        self.assertEqual(await self.bridge.generate(self.private.user,'人','conversation'),'')
        self.assertEqual(len(self.private.model_calls),before)
        self.private.plan(source='conversation',event_id='new-topic',topic='合成拼图想法',now=self.now)
        self.private.user.update(awaiting_reply=True,ignored_proactive_count=1)
        self.assertEqual(self.private._proactive_unanswered_pause_reason(self.private.user),'')
        self.assertTrue(await self.bridge.generate(self.private.user,'人','conversation'))
        self.gate.pause()
        self.private.plan(source='conversation',event_id='paused',topic='又一个合成话题',now=self.now)
        self.assertEqual(await self.bridge.generate(self.private.user,'人','conversation'),'')

    async def test_failed_send_consumes_attempt_without_marking_topic_spoken(self):
        async def failed(*args,**kwargs):
            return {'state':'failed','delivered_text':'','sent_count':0,'part_count':2}
        self.guard.send_proactive=failed
        text=await self.bridge.generate(self.private.user,'人','followup')
        outcome=await self.bridge.send(self.private.user['umo'],text)
        self.assertFalse(outcome.delivered)
        self.assertEqual(self.gate._get('last_topic'),'')
        self.assertEqual(self.gate.db.execute('SELECT state,sent_count,part_count FROM proactive_attempts').fetchone(),('failed',0,2))
        self.assertEqual(self.observations,[])

    async def test_partial_prefix_observed_once_and_cancelled_tail_is_not_sent(self):
        async def partial(*args,**kwargs):
            return {'state':'cancelled','delivered_text':'合成已送达前缀。','sent_count':1,'part_count':3}
        self.guard.send_proactive=partial
        text=await self.bridge.generate(self.private.user,'人','followup')
        outcome=await self.bridge.send(self.private.user['umo'],text)
        self.assertTrue(outcome.delivered)
        self.assertFalse(outcome.complete)
        self.assertEqual(outcome.delivered_text,'合成已送达前缀。')
        self.assertEqual(self.gate.db.execute('SELECT state,sent_count,part_count FROM proactive_attempts').fetchone(),('cancelled',1,3))
        self.assertEqual(len(self.observations),1)
        self.private._mark_planned_candidate_status(self.private.user,'sent','native whole plan assertion')
        native=self.private.user['proactive_impulses'][0]
        self.assertEqual(native['state'],'blocked')
        self.assertEqual(native['context']['phase3_delivery'],{'state':'cancelled','sent_count':1,'part_count':3})
        self.assertFalse((await self.bridge.send(self.private.user['umo'],text)).delivered)

    async def test_defer_is_persistent_bounded_and_rechecks_after_due(self):
        self.gate.end=self.now+86400
        self.private.model_text='[PHASE3_PROACTIVE:defer:60]'
        self.assertEqual(await self.bridge.generate(self.private.user,'人','followup'),'')
        native=self.private.user['proactive_impulses'][0]
        self.assertEqual(native['context']['phase3_decision']['next_consider_at'],self.now+3600)
        self.assertEqual(await self.bridge.generate(self.private.user,'人','followup'),'')
        self.assertEqual(len(self.private.model_calls),1)
        self.now+=3600
        self.private.model_text='[PHASE3_PROACTIVE:send]\n现在有个合成想法。'
        self.assertTrue(await self.bridge.generate(self.private.user,'人','followup'))
        self.assertEqual(len(self.private.model_calls),2)
        self.assertEqual(self.gate.db.execute('SELECT count(*) FROM proactive_attempts').fetchone()[0],0)

    async def test_expired_candidate_has_zero_model_calls(self):
        self.private.user['proactive_impulses'][0]['expire_at']=self.now
        self.assertEqual(await self.bridge.generate(self.private.user,'人','followup'),'')
        self.assertEqual(self.private.model_calls,[])

    async def test_empty_model_is_not_recorded_as_deliberate_skip_or_retried(self):
        self.private.model_text=''
        self.assertEqual(await self.bridge.generate(self.private.user,'人','followup'),'')
        self.assertEqual(len(self.private.model_calls),1)
        self.assertEqual(self.gate.db.execute('SELECT state FROM proactive_generations').fetchone()[0],'empty')
        self.assertNotIn('phase3_decision',self.private.user['proactive_impulses'][0]['context'])
        self.assertEqual(await self.bridge.generate(self.private.user,'人','followup'),'')
        self.assertEqual(len(self.private.model_calls),1)

    async def test_cancelled_task_reconciles_acknowledged_prefix_from_guard(self):
        async def cancelled(*args,**kwargs):raise asyncio.CancelledError()
        self.guard.send_proactive=cancelled
        self.guard.store=types.SimpleNamespace(bubble_result=lambda *args:{
            'state':'cancelled','parts':[{'state':'sent'},{'state':'cancelled'}],
            'sent_count':1,'part_count':2,'delivered_text':'已经确认的合成前缀。'})
        text=await self.bridge.generate(self.private.user,'人','followup')
        with self.assertRaises(asyncio.CancelledError):
            await self.bridge.send(self.private.user['umo'],text)
        self.assertEqual(self.gate.db.execute('SELECT state,sent_count,part_count FROM proactive_attempts').fetchone(),('cancelled',1,2))
        self.assertTrue(self.gate._get('last_topic'))
        self.assertEqual(self.gate.permit(self.gate.session,candidate_id=self.private._planned_proactive_delivery_key(self.private.user)), 'already_attempted')

    async def test_explicit_busy_is_temporary_and_free_does_not_clear_pause(self):
        actions=parse_proactive_controls('我现在忙，稍后再聊。',now=self.now,window_end=self.gate.end)
        self.assertEqual(actions,[{'kind':'busy','until':min(self.gate.end,self.now+14400)}])
        self.gate.set_control('busy_until',actions[0]['until'])
        self.assertEqual(await self.bridge.generate(self.private.user,'人','followup'),'')
        self.assertEqual(self.private.model_calls,[])
        self.gate.pause()
        action=parse_proactive_controls('我忙完了。',now=self.now,window_end=self.gate.end)[0]
        self.gate.set_control('busy_until',action['until'])
        self.assertFalse(self.gate.continuation_allowed(self.gate.session))
        self.gate.pause(0)
        self.assertTrue(self.gate.continuation_allowed(self.gate.session))
        for text in ('你现在忙吗？','他说“我现在忙”','我在忙着想你','我现在忙吗？'):
            self.assertEqual(parse_proactive_controls(text,now=self.now,window_end=self.gate.end),[])

    async def test_restart_reconciles_guard_prefix_without_replaying_plan(self):
        candidate=self.private._planned_proactive_delivery_key(self.private.user)
        self.gate.db.execute("INSERT INTO proactive_attempts(candidate,day,attempted,state,topic_id) VALUES (?,?,?,'pending','synthetic-topic')",
                             (candidate,'synthetic-day',self.now))
        self.gate.db.commit()
        self.bridge.detach();self.gate.close()
        self.gate=ProactiveGate(Path(self.temp.name)/'gate.sqlite',allowed_session=self.private.user['umo'],
            enabled=True,window_start=self.now-1,window_end=self.now+10000,quiet_hours=(0,0),clock=lambda:self.now)
        self.guard.store=types.SimpleNamespace(bubble_result=lambda *args:{
            'state':'unknown','parts':[{'state':'sent'},{'state':'unknown'}],
            'sent_count':1,'part_count':2,'delivered_text':'已经确认的合成前缀。'})
        self.bridge=NativeProactiveBridge(self.private,self.guard,self.gate,message._ProactiveSendOutcome,
            event_resolver=lambda *args:self.status)
        self.bridge.attach()
        self.assertEqual(self.gate.db.execute('SELECT state,sent_count,part_count FROM proactive_attempts').fetchone(),('unknown',1,2))
        self.assertEqual(self.gate._get('last_topic'),'synthetic-topic')
        self.assertEqual(await self.bridge.generate(self.private.user,'人','followup'),'')
        self.assertEqual(self.sends,[])
        self.assertEqual(self.private.model_calls,[])


class DecisionParsingTests(unittest.TestCase):
    def test_optional_header_preserves_native_bubbles_and_plain_text(self):
        text='一条。\n<<PRIVATE_COMPANION_SPLIT>>\n二条。'
        self.assertEqual(parse_proactive_decision(text)['text'],text)
        self.assertEqual(parse_proactive_decision('[PHASE3_PROACTIVE:send]\n'+text)['text'],text)
        self.assertEqual(parse_proactive_decision('[PHASE3_PROACTIVE:defer:1]')['delay_minutes'],60)
        self.assertEqual(parse_proactive_decision('[PHASE3_PROACTIVE:defer:999]')['delay_minutes'],240)
        for value in ('[PHASE3_PROACTIVE:other]\n秘密','[PHASE3_PROACTIVE:send]',
                      '[PHASE3_PROACTIVE:skip]\n不应发送正文','',
                      '[PHASE3_PROACTIVE:send]\n[PHASE3_PROACTIVE:skip]'):
            self.assertEqual(parse_proactive_decision(value)['text'],'')


if __name__ == "__main__": unittest.main()
