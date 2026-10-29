"""T2 pinned Private candidate/generator entry integration, synthetic model I/O.

This executes original candidate creation, delivery identity and two-level
generation methods. The timer loop and provider are fixtures, not real QQ/API.
"""
import asyncio
from contextvars import ContextVar
from datetime import datetime
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

    async def test_full_text_scope_keeps_long_line_and_all_native_lines(self):
        lines=['第'+str(n)+'段讨论标点与节奏，'+('每个位置都有不同的表达价值，'*14)+'最后保留此段内容。'
               for n in range(1,7)]
        token=self.bridge._semantic_scope.set(True)
        try:
            cleaned=self.private._sanitize_proactive_text('\n'.join(lines))
            normalized=self.private._normalize_proactive_sentence_flow(cleaned)
        finally:self.bridge._semantic_scope.reset(token)
        self.assertGreater(len(normalized),1000)
        self.assertEqual(cleaned,'\n'.join(lines).removesuffix('。'))
        for line in lines:self.assertIn(line.removesuffix('。'),normalized)
        self.assertNotIn('…',normalized)

    async def test_full_text_scope_native_hidden_blocks_and_line_rejections_stay_removed(self):
        text=('第一段正文。\n<pc_history_media>\n不可展示的内部资料\n不能恢复这一行\n'
              '</pc_history_media>\n直接输出这条主动消息\n（翻了个身）\n最后一段正文。')
        token=self.bridge._semantic_scope.set(True)
        try:cleaned=self.private._sanitize_proactive_text(text)
        finally:self.bridge._semantic_scope.reset(token)
        self.assertIn('第一段正文',cleaned);self.assertIn('最后一段正文',cleaned)
        for removed in ('内部资料','恢复这一行','pc_history_media','直接输出','翻了个身'):
            self.assertNotIn(removed,cleaned)

    async def test_semantic_sanitize_removes_complete_hidden_blocks_before_restoring_boundaries(self):
        self.guard.multibubble_enabled=True;marker='<<PRIVATE_COMPANION_SPLIT>>'
        text=('第一条允许正文。\n'+marker+'\n<pc_history_media>\n隐藏原文\n'+marker+
              '\n隐藏尾部\n</pc_history_media>\n第二条允许正文。\n'+marker+'\n第三条允许正文。')
        token=self.bridge._semantic_scope.set(True)
        try:cleaned=self.private._sanitize_proactive_text(text)
        finally:self.bridge._semantic_scope.reset(token)
        parts,controlled=message.split_llm_controlled_text(cleaned)
        self.assertTrue(controlled);self.assertEqual(len(parts),3)
        self.assertEqual(cleaned.count(marker),2)
        for forbidden in ('隐藏','pc_history_media','PHASE3_BOUNDARY'):
            self.assertNotIn(forbidden,cleaned)

    async def test_full_text_scope_keeps_native_closing_topic_guard(self):
        token=self.bridge._semantic_scope.set(True)
        try:
            cleaned=self.private._normalize_proactive_sentence_flow('先到这里，晚安。今天突然觉得云层很有趣。')
        finally:self.bridge._semantic_scope.reset(token)
        self.assertIn('晚安',cleaned);self.assertNotIn('云层',cleaned)

    async def test_full_text_scope_does_not_change_native_cleanup_outside_generation(self):
        text='合成正文内容'*100
        self.assertLessEqual(len(self.private._sanitize_proactive_text(text)),261)
        token=self.bridge._semantic_scope.set(True)
        try:self.assertEqual(self.private._sanitize_proactive_text(text),text)
        finally:self.bridge._semantic_scope.reset(token)
        self.assertLessEqual(len(self.private._sanitize_proactive_text(text)),261)

    async def test_short_proactive_normalization_still_uses_original_layout(self):
        text='标点还有这种用法。第二句可以换个重心。第三句就说到这里'
        expected=self.private._normalize_proactive_sentence_flow(text)
        token=self.bridge._semantic_scope.set(True)
        try:self.assertEqual(self.private._normalize_proactive_sentence_flow(text),expected)
        finally:self.bridge._semantic_scope.reset(token)

    async def test_cleanup_does_not_run_guard_fallback_inside_a_model_bubble(self):
        self.guard.multibubble_enabled=True
        def forbidden_fallback(*args,**kwargs):
            raise AssertionError('Guard fallback belongs only to final delivery')
        self.guard.semantic_parts=forbidden_fallback
        text='这一段已经是模型选择的气泡。不能在清理里再次切它。'
        expected=self.private._normalize_proactive_sentence_flow(text)
        token=self.bridge._semantic_scope.set(True)
        try:
            self.assertEqual(self.private._normalize_proactive_sentence_flow(text),expected)
            marked=text+'\n<<PRIVATE_COMPANION_SPLIT>>\n第二个模型气泡。'
            cleaned=self.private._visible_text_without_tts_reading(marked)
            self.assertEqual(cleaned.count('<<PRIVATE_COMPANION_SPLIT>>'),1)
        finally:self.bridge._semantic_scope.reset(token)

    def local_review(self,text):
        return self.private._local_proactive_send_decision(self.private.user,text,
            reason='activity_share',action='message',motive='开放选题',topic='合成话题')

    async def test_autonomy_hypothetical_consequence_does_not_waive_real_action_checks(self):
        from integrations.phase3_runtime.autonomy import AutonomousProactiveBridge
        original=self.private._unverified_proactive_fact_decision
        scope={'decision':{'kind':'hypothesis'}}
        shim=types.SimpleNamespace(_additional_bindings={'_unverified_proactive_fact_decision':original},
            _native_gate_scope=ContextVar('test_final_gate',default=scope),
            _semantic_scope=ContextVar('test_semantic',default=True))
        kwargs={'reason':'activity_share','action':'message','action_context':''}
        for text in ('假设两个虚构角色共用一张地图，可能聊到一半才发现坐标原点不同。',
                     '也许它们争论到最后才发现说的根本不是同一种虚构颜色。'):
            self.assertIsNotNone(original(text,**kwargs))
            self.assertIsNone(AutonomousProactiveBridge.review_native_recent_fact(shim,text,**kwargs))
        for text in ('可能我昨晚才发现坐标不同。',
                     '可能吵到一半才发现概念不同。我刚看到窗外有东西。',
                     '可能吵到一半才发现概念不同，我刚查到一条新闻。',
                     '我刚刚发现了一个新现象。',
                     '他们吵到一半才发现概念不同。'):
            expected=original(text,**kwargs)
            self.assertIsNotNone(expected)
            self.assertEqual(AutonomousProactiveBridge.review_native_recent_fact(shim,text,**kwargs),expected)
        scope['decision']['kind']='opinion'
        text='可能它们吵到一半才发现坐标不同。'
        self.assertEqual(AutonomousProactiveBridge.review_native_recent_fact(shim,text,**kwargs),original(text,**kwargs))

    async def test_native_local_motive_cleaner_cannot_eat_a_valid_marker_suffix(self):
        self.guard.multibubble_enabled=True
        marker='<<PRIVATE_COMPANION_SPLIT>>'
        text=('模块化积木看起来挺有趣。\n'+marker+'\n'
              '我想了半天，连接方式比外形更值得琢磨。\n'+marker+'\n'
              '每块积木之间的约定才是关键。')
        # Reproduce the fixed upstream bug: _single_line moves the marker next
        # to a motive phrase; the native look-behind-like regex eats its suffix.
        broken=self.local_review(text)
        self.assertEqual(broken['decision'],'rewrite')
        self.assertIn('<<PRIVATE_COMPANI，',broken['text'])
        token=self.bridge._semantic_scope.set(True)
        try:
            repaired=self.local_review(text)
            accepted=self.private._normalize_proactive_review_decision_policy(
                self.private.user,repaired,strength='lenient',source='local',
                reason='activity_share',action='message',original_text=text)
        finally:self.bridge._semantic_scope.reset(token)
        self.assertEqual(accepted['decision'],'rewrite')
        self.assertEqual(accepted['text'].count(marker),2)
        self.assertNotIn('我想了半天',accepted['text'])
        self.assertEqual(repaired['decision'],'rewrite')
        parts,controlled=message.split_llm_controlled_text(repaired['text'])
        self.assertTrue(controlled);self.assertEqual(len(parts),3)
        self.assertEqual(repaired['text'].count(marker),2)
        self.assertNotIn('我想了半天',repaired['text'])
        self.assertIn('连接方式比外形更值得琢磨',parts[1])
        self.assertIn('每块积木之间的约定才是关键',parts[2])
        self.assertFalse(any('PRIVATE_COMPANI' in part for part in parts))

    async def test_local_marked_cleanup_retains_seven_hundred_character_complete_body(self):
        self.guard.multibubble_enabled=True;marker='<<PRIVATE_COMPANION_SPLIT>>'
        parts=[('第'+str(n)+'段的合成观点，'+('词语的位置会影响句子的阅读节奏，'*14)+'末尾完整。')
               for n in range(1,4)]
        parts[1]='我想了半天，'+parts[1]
        self.assertTrue(all(len(x)<=260 for x in parts))
        text=('\n'+marker+'\n').join(parts);self.assertGreater(len(text),700)
        token=self.bridge._semantic_scope.set(True)
        try:result=self.local_review(text)
        finally:self.bridge._semantic_scope.reset(token)
        self.assertEqual(result['decision'],'rewrite')
        parsed,controlled=message.split_llm_controlled_text(result['text'])
        self.assertTrue(controlled);self.assertEqual(len(parsed),3)
        self.assertEqual(parsed[0],parts[0]);self.assertEqual(parsed[2],parts[2])
        expected=self.bridge._additional_bindings['_strip_proactive_motive_leak_text'](parts[1])
        self.assertEqual(parsed[1],expected)
        self.assertGreater(len(result['text']),700)

    async def test_local_unmarked_long_send_keeps_original_and_checks_tail_hard_blocks(self):
        self.guard.multibubble_enabled=True
        text='我更喜欢在句子里留一点变化。'+('词语的位置会影响阅读节奏。'*65)+'末尾完整。'
        self.assertGreater(len(text),700)
        token=self.bridge._semantic_scope.set(True)
        try:
            self.assertEqual(self.local_review(text)['decision'],'send')
            blocked=self.local_review(text+'以后我会提醒你该休息了。')
        finally:self.bridge._semantic_scope.reset(token)
        self.assertEqual(blocked['decision'],'drop');self.assertTrue(blocked['hard'])
        self.assertIn('未来提醒',blocked['reason'])

    async def test_local_overlong_rewrite_preview_or_native_clipped_motive_is_not_delivered(self):
        self.guard.multibubble_enabled=True
        body=('这种表达里的细节都应该留下，'*55)+'最后不能丢。'
        token=self.bridge._semantic_scope.set(True)
        try:
            clipped=self.local_review('好呀，'+body)
            motive=self.local_review('我想了半天，'+body)
        finally:self.bridge._semantic_scope.reset(token)
        self.assertEqual(clipped['decision'],'drop');self.assertIn('预览',clipped['reason'])
        self.assertEqual(motive['decision'],'drop');self.assertIn('完整正文',motive['reason'])
        self.assertFalse(clipped.get('text'));self.assertFalse(motive.get('text'))

    async def test_local_marker_adapter_retains_native_hard_rejection_and_does_not_restore_removed_bubble(self):
        self.guard.multibubble_enabled=True;marker='<<PRIVATE_COMPANION_SPLIT>>'
        token=self.bridge._semantic_scope.set(True)
        try:
            rejected=self.local_review('普通正文。\n'+marker+'\n以后我会告诉你下一次更新时间。')
            removed=self.local_review('第一条留下。\n'+marker+'\n我想了半天。\n'+marker+'\n最后一条留下。')
        finally:self.bridge._semantic_scope.reset(token)
        self.assertEqual(rejected['decision'],'drop');self.assertTrue(rejected['hard'])
        self.assertEqual(removed['decision'],'rewrite')
        parts,controlled=message.split_llm_controlled_text(removed['text'])
        self.assertTrue(controlled);self.assertEqual(len(parts),2)
        self.assertNotIn('想了半天',removed['text'])

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

    def continuous_gate(self):
        self.bridge.detach()
        self.gate.close()
        self.now = datetime.fromisoformat('2026-10-24T09:00:00+08:00').timestamp()
        self.gate = ProactiveGate(Path(self.temp.name)/'gate.sqlite', allowed_session=self.private.user['umo'],
            enabled=True, mode='continuous', window_start=0, window_end=0,
            max_per_day=5, max_generations_per_day=5, cooldown=10800,
            quiet_hours=(0,9), clock=lambda:self.now)
        self.private.plan(now=self.now)
        self.bridge = NativeProactiveBridge(self.private,self.guard,self.gate,message._ProactiveSendOutcome,
            event_resolver=lambda *args:dict(self.status))
        self.bridge.attach()

    def test_continuous_native_config_authorization_and_runtime_limits(self):
        self.continuous_gate()
        cfg = native_proactive_config({}, '10001', enabled=True, policy={'mode':'continuous'})
        self.assertEqual(cfg['basic_config']['quiet_hours'], '00:00-09:00')
        self.assertEqual(cfg['private_followup_config']['min_interval_minutes'], 180)
        self.assertEqual(cfg['proactive_reach_config']['max_daily_messages'], 0)
        self.assertFalse(cfg['proactive_generation_config']['enable_llm_proactive_message'])
        for limit in (0, 13, True):
            with self.assertRaises(PermissionError):
                authorize_native_target(self.private,self.private.user['umo'],approved=True,daily_limit=limit)
        authorize_native_target(self.private,self.private.user['umo'],approved=True,daily_limit=5)
        self.private.config = cfg
        self.private.max_daily_messages = 0
        self.private.enable_llm_proactive_message = False
        self.private.quiet_hours = '23:00-09:00'
        self.private.min_interval_minutes = 240
        self.private.proactive_intensity_preset = 'off'
        self.assertTrue(self.bridge.activate_native_config())
        self.assertEqual(self.private._runtime_max_daily_messages(), 5)
        self.assertEqual(self.private._effective_user_daily_limit(self.private.user), 5)
        self.assertEqual(self.private._effective_user_min_interval_minutes(self.private.user), 180)
        self.assertEqual(self.private.quiet_hours, '00:00-09:00')
        self.assertEqual(cfg['proactive_reach_config']['max_daily_messages'], 0)
        self.assertFalse(cfg['proactive_generation_config']['enable_llm_proactive_message'])
        self.bridge.detach()
        self.assertEqual(self.private.max_daily_messages, 0)
        self.assertFalse(self.private.enable_llm_proactive_message)
        self.assertEqual(self.private.quiet_hours, '23:00-09:00')
        self.assertEqual(self.private.min_interval_minutes, 240)

    def test_continuous_native_reason_window_reaches_2359_and_restores_on_detach(self):
        from zoneinfo import ZoneInfo
        self.continuous_gate()
        self.private.max_daily_messages = 0
        self.private.enable_llm_proactive_message = False
        self.private._environment_now = lambda:datetime.fromtimestamp(self.now, ZoneInfo('Asia/Shanghai'))
        self.private._environment_fromtimestamp = lambda at:datetime.fromtimestamp(at, ZoneInfo('Asia/Shanghai'))
        native_check = types.MethodType(engine.ProactiveEngineMixin._is_reason_allowed_now, self.private)
        original_birthday = self.private._reason_windows('birthday_celebration', self.private.user)
        self.bridge.activate_native_config()
        for clock, allowed in (('08:59:59',False), ('09:00:00',True),
                               ('23:30:00',True), ('23:59:59',True), ('00:00:00',False)):
            self.now = datetime.fromisoformat('2026-10-24T'+clock+'+08:00').timestamp()
            self.assertEqual(native_check('open_loop_followup', self.private.user), allowed)
            self.assertEqual(self.gate.continuation_allowed(self.gate.session), allowed)
            self.assertEqual(self.private._quiet_hours_end_timestamp(self.now) > self.now, not allowed)
        self.now = datetime.fromisoformat('2026-10-24T23:30:00+08:00').timestamp()
        self.assertFalse(native_check('open_loop_followup', {'umo':'test:FriendMessage:10002'}))
        self.assertEqual(self.private._reason_windows('birthday_celebration', self.private.user), original_birthday)
        self.bridge.detach()
        self.assertFalse(native_check('open_loop_followup', self.private.user))

    def test_frequency_12_keeps_native_tier_idle_and_candidate_policy_at_tier3(self):
        # Real fixed native quota/idle methods, including custom relationship
        # policy. Only persona config lookup and the synthetic user are fixtures.
        self.private._proactive_quota_policy = types.MethodType(
            scheduler.ProactiveMixin._proactive_quota_policy, self.private)
        self.private._private_user_role = lambda user: 'friend'
        self.private.proactive_intensity_preset = 'off'
        self.private.enable_custom_relationship_stage_policy = True
        self.private.idle_minutes = 60
        self.private.min_interval_minutes = 120
        self.private.user.update(relationship_role='friend',relationship_mode='normal',
            relationship_score=0,proactive_idle_minutes=-1,proactive_min_interval_minutes=-1,
            current_interaction={'expression_band':'relaxed'})
        policies=[]
        for daily in (8,12):
            self.private.max_daily_messages=daily
            self.private.user['proactive_daily_limit']=daily
            self.assertEqual(self.private._effective_user_daily_limit(self.private.user),daily)
            self.assertEqual(self.private._effective_user_idle_minutes(self.private.user),45)
            self.assertEqual(self.private._effective_user_min_interval_minutes(self.private.user),90)
            policy=self.private._proactive_quota_policy(self.private.user)
            self.assertEqual(policy['tier'],3)
            policies.append({key:value for key,value in policy.items() if key!='quota'})
        self.assertEqual(*policies)
        self.private.min_interval_minutes=90
        self.assertEqual(self.private._effective_user_min_interval_minutes(self.private.user),90)
        self.private.user['proactive_idle_minutes']=60
        self.assertEqual(self.private._effective_user_idle_minutes(self.private.user),60)

    def test_frequency_native_12_target_activation_and_detach_keep_disk_cold(self):
        from integrations.phase3_runtime.settings import FREQUENCY_PROACTIVE_POLICY
        self.bridge.detach();self.gate.close()
        self.now=datetime.fromisoformat('2026-10-27T09:00:00+08:00').timestamp()
        self.gate=ProactiveGate(Path(self.temp.name)/'gate.sqlite',allowed_session=self.private.user['umo'],
            enabled=True,clock=lambda:self.now,**FREQUENCY_PROACTIVE_POLICY)
        cfg=native_proactive_config({},'10001',enabled=True,policy=FREQUENCY_PROACTIVE_POLICY)
        self.private.config=cfg;self.private.max_daily_messages=0
        self.private.enable_llm_proactive_message=False;self.private.min_interval_minutes=90
        self.private.idle_minutes=60;self.private.quiet_hours='23:00-09:00'
        self.private.proactive_intensity_preset='off'
        authorize_native_target(self.private,self.private.user['umo'],approved=True,daily_limit=12)
        self.bridge=NativeProactiveBridge(self.private,self.guard,self.gate,message._ProactiveSendOutcome)
        self.bridge.attach();self.assertTrue(self.bridge.activate_native_config())
        self.assertEqual(self.private._effective_user_daily_limit(self.private.user),12)
        self.assertEqual(self.private.min_interval_minutes,90)
        self.assertEqual(self.private.idle_minutes,60)
        self.assertEqual(self.private.quiet_hours,'23:00-09:00')
        self.assertEqual(cfg['proactive_reach_config']['max_daily_messages'],0)
        self.assertFalse(cfg['proactive_generation_config']['enable_llm_proactive_message'])
        self.bridge.detach()
        self.assertEqual(self.private.max_daily_messages,0)
        self.assertFalse(self.private.enable_llm_proactive_message)

    async def test_continuous_scheduler_does_not_expire_at_zero_or_72_hours(self):
        self.continuous_gate()
        self.private.max_daily_messages = 0
        self.private.enable_llm_proactive_message = False
        self.bridge.activate_native_config()
        self.now += 10 * 86400
        self.private.plan(now=self.now)
        await self.private._run_scheduler_cycle(immediate=True)
        self.assertTrue(self.gate.enabled)
        self.assertTrue(self.private.enable_llm_proactive_message)
        self.assertEqual(len(self.sends), 1)

    async def test_continuous_native_skip_and_defer_consume_five_evaluations(self):
        self.continuous_gate()
        for number in range(6):
            self.private.plan(event_id='budget-'+str(number), now=self.now)
            self.private.model_text = '[PHASE3_PROACTIVE:skip]' if number % 2 else '[PHASE3_PROACTIVE:defer:60]'
            self.assertEqual(await self.bridge.generate(self.private.user,'人','followup'), '')
            self.now += 60
        self.assertEqual(len(self.private.model_calls), 5)
        states = self.gate.db.execute('SELECT state FROM proactive_generations ORDER BY rowid').fetchall()
        self.assertEqual(states, [('deferred',), ('skipped',), ('deferred',), ('skipped',), ('deferred',)])
        self.assertEqual(self.gate.db.execute('SELECT count(*) FROM proactive_attempts').fetchone()[0], 0)

    async def test_continuous_offer_is_finite_and_honors_source_expiry(self):
        self.continuous_gate()
        offered = []
        self.private._offer_proactive_candidate = lambda user_id,user,candidate:offered.append(candidate) or True
        self.bridge.event_source = lambda umo:[{'event_id':'offer-one','revision':1,'user_quote':'合成待跟进事项'}]
        self.assertTrue(await self.bridge.offer_due_event())
        self.assertEqual(offered[0]['expire_at'], self.now+3600)
        expiry = datetime.fromtimestamp(self.now+900, datetime.fromisoformat('2026-10-24T00:00:00+08:00').tzinfo).isoformat()
        self.bridge.event_source = lambda umo:[{'event_id':'offer-two','revision':1,'user_quote':'合成短期事项','expires_at':expiry}]
        self.assertTrue(await self.bridge.offer_due_event())
        self.assertEqual(offered[1]['expire_at'], self.now+900)

    async def test_continuous_defer_keeps_original_expiry_even_if_native_extends(self):
        self.continuous_gate()
        native = self.private.user['proactive_impulses'][0]
        original_expiry = self.now+7200
        native['expire_at'] = original_expiry
        self.private.model_text = '[PHASE3_PROACTIVE:defer:60]'
        await self.bridge.generate(self.private.user,'人','followup')
        self.assertEqual(native['context']['phase3_decision']['expires_at'], original_expiry)
        self.assertEqual(native['expire_at'], original_expiry)
        self.now += 3600
        native['expire_at'] = self.now+86400  # Simulate native scheduler normalization.
        self.private.model_text = '[PHASE3_PROACTIVE:defer:240]'
        await self.bridge.generate(self.private.user,'人','followup')
        self.assertEqual(native['context']['phase3_decision']['decision'], 'skip')
        self.assertEqual(native['context']['phase3_decision']['expires_at'], original_expiry)
        self.assertEqual(native['expire_at'], original_expiry)
        self.assertEqual(self.gate.db.execute('SELECT state FROM proactive_generations ORDER BY rowid DESC').fetchone()[0], 'skipped')
        self.assertEqual(len(self.private.model_calls), 2)
        self.assertEqual(self.sends, [])

    async def test_continuous_missing_or_nonfinite_candidate_expiry_never_generates(self):
        self.continuous_gate()
        for expiry in (0, None, float('inf'), float('nan'), self.now):
            self.private.user['proactive_impulses'][0]['expire_at'] = expiry
            self.assertEqual(await self.bridge.generate(self.private.user,'人','followup'), '')
        self.assertEqual(self.private.model_calls, [])

    async def test_continuous_legacy_candidate_requires_finite_expiry(self):
        self.continuous_gate()
        self.private._planned_proactive_impulse = lambda user:None
        self.private._planned_proactive_delivery_key = lambda user:'legacy-plan'
        self.private.user.update(planned_candidate_id='legacy-plan', planned_proactive_source='conversation')
        self.assertEqual(await self.bridge.generate(self.private.user,'人','conversation'), '')
        self.assertEqual(self.private.model_calls, [])

    async def test_continuous_candidate_expiry_stops_remaining_bubbles(self):
        self.continuous_gate()
        expiry = self.now+30
        self.private.user['proactive_impulses'][0]['expire_at'] = expiry
        text = await self.bridge.generate(self.private.user,'人','followup')
        checked = []
        async def guarded_send(umo, text, *, delivery_id, is_current):
            checked.append(is_current())
            self.now = expiry
            checked.append(is_current())
            return {'state':'cancelled','delivered_text':'已经送达的合成首段。','sent_count':1,'part_count':2}
        self.guard.send_proactive = guarded_send
        outcome = await self.bridge.send(self.private.user['umo'], text)
        self.assertEqual(checked, [True, False])
        self.assertTrue(outcome.delivered)
        self.assertFalse(outcome.complete)
        self.assertEqual(self.gate.db.execute('SELECT state,sent_count,part_count FROM proactive_attempts').fetchone(), ('cancelled',1,2))


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
