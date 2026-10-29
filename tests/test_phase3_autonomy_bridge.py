"""T1 lifecycle integration: real Guard/Gate/state databases, synthetic native I/O."""
import asyncio
from datetime import datetime
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
import uuid

from integrations.phase1_guard.store import GuardStore
from integrations.phase1_guard.bubbles import sha
from integrations.phase3_runtime.proactive import ProactiveGate
from integrations.phase3_runtime.autonomy import AutonomousProactiveBridge
from integrations.phase3_runtime.autonomy_state import AutonomyState
from integrations.phase3_runtime.settings import AUTONOMY_DEFAULTS, FREQUENCY_PROACTIVE_POLICY, autonomy_candidate

SESSION='test:FriendMessage:10001'


def answer(body='省略号简直像标点里的摸鱼冠军。',decision='send',kind='opinion',progress=''):
    return '[PHASE3_PROACTIVE:'+decision+']\n[PHASE3_TOPIC:'+json.dumps(
        {'topic':'标点的性格','kind':kind,'progress':progress,'pocket_id':''},ensure_ascii=False)+']\n'+body


class Host:
    def __init__(self,now):
        self.user={'umo':SESSION,'user_id':'10001','planned_proactive_source':'random',
                   'planned_proactive_reason':'activity_share','ignored_streak':2,'next_proactive_at':now-1}
        self.native={'id':'candidate-1','expire_at':now+3600,'state':'queued','context':{}}
        self.data={'users':{'10001':self.user}}
        self.response=answer();self.prompts=[];self.before=None;self.archives=[]
        self.final_gate={'decision':'send','reason':'synthetic native allowed'}
        self.no_transport=False
        self._data_lock=asyncio.Lock();self.offers=[]
    def _proactive_identity_binding_is_verified(self,*a):return True
    def _planned_proactive_delivery_key(self,user):return self.native['id']
    def _planned_proactive_impulse(self,user):return self.native
    def _schedule_data_save(self,**kw):pass
    def _user_enabled_for_proactive(self,*a):return True
    async def _generate_proactive_message_with_llm(self,user,*a,**kw):
        text=await self._run_framework_agent_text(umo=SESSION,task='proactive_message',prompt='旧限制：必须围绕用户最近的事')
        return text.rstrip('。')  # representative native final cleanup remains authoritative
    async def _run_framework_agent_text(self,**kw):
        self.prompts.append(kw)
        if self.before:await self.before()
        return self.response
    async def _send_proactive_message_chain(self,*a,**k):raise AssertionError('native transport bypass')
    async def _tick_user(self,uid,user):
        text=await self._generate_proactive_message_with_llm(user,'测试者','activity_share')
        if text:
            review=await self._review_proactive_message_send_decision(user,text,reason='activity_share',action='message')
            if review['decision'] in {'drop','defer'} or self.no_transport:return
            if review['decision']=='rewrite':text=review['text']
            return await self._send_proactive_message_chain(SESSION,text)
    async def _review_proactive_message_send_decision(self,user,text,**kwargs):return dict(self.final_gate)
    def _filter_incompatible_proactive_framework_tools(self,*a,**k):pass
    def _install_proactive_semantic_provider_fallback(self,*a,**k):return True
    async def _proactive_persona_delivery_allowed(self,umo):return True
    async def _resolve_proactive_persona_prompt(self,user):return '[QQ_COMPANION_PERSONA_V0_4]\nSynthetic complete persona'
    async def _recent_private_conversation_for_proactive_review(self,*a,**k):return '合成历史；不是新的用户提问'
    def _format_recent_proactive_topics_hint(self,user):return '先前确认说过一个咖啡玩笑'
    def _proactive_history_limit(self,*a):return 80
    async def _archive_proactive_message_to_conversation(self,**kw):self.archives.append(kw)
    def _offer_proactive_candidate(self,uid,user,candidate):self.offers.append(candidate);return True


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.now=datetime.fromisoformat('2026-10-27T12:00:00+08:00').timestamp()
        root=Path(self.tmp.name)
        self.gate=ProactiveGate(root/'active.db',allowed_session=SESSION,enabled=True,
            clock=lambda:self.now,**FREQUENCY_PROACTIVE_POLICY)
        self.addCleanup(self.gate.close)
        self.archive=GuardStore(root/'guard.db');self.archive.enable_phase3();self.archive.enable_bubbles();self.addCleanup(self.archive.db.close)
        self.busy=False;self.sent=[]
        async def send(umo,text,*,delivery_id,is_current):
            if not is_current():return {'state':'cancelled','sent_count':0,'part_count':1,'delivered_text':''}
            self.sent.append((text,delivery_id))
            turn=str(uuid.uuid5(uuid.NAMESPACE_URL,'qq-companion-proactive:'+delivery_id))
            self.archive.create_turn(turn,'test',umo,proactive=True)
            part=delivery_id+':0'
            self.archive.plan_bubbles(turn,'proactive',[text],[part],sha(text))
            self.archive.begin_delivery(part,turn,'proactive',0,text,[])
            self.archive.mark_bubble_transport(part)
            self.archive.finish_delivery(part,'sent',receipt_source='synthetic_guard_adapter_ack')
            self.archive.finish_bubble(turn,'proactive',0,'sent')
            with self.archive.db:
                self.archive.db.execute('UPDATE deliveries SET finished_at=? WHERE delivery_id=?',
                    (datetime.fromtimestamp(self.now).astimezone().isoformat(),part))
            self.archive.set_turn_state(turn,'done')
            return self.archive.bubble_result(turn,'proactive')
        self.guard=NS(store=self.archive,phase3_platform_id='test',phase3_admin_id='10001',
                      session_busy=lambda _:self.busy,send_proactive=send,stopping=False)
        self.host=Host(self.now)
        self.state=AutonomyState(self.gate.db,clock=lambda:self.now,audit_dir=root/'audit')
        self.bridge=AutonomousProactiveBridge(self.host,self.guard,self.gate,
            lambda sent,complete,**kw:NS(sent=sent,complete=complete,**kw),
            autonomy_state=self.state,autonomy_settings=dict(AUTONOMY_DEFAULTS))
        self.bridge.attach();self.addCleanup(self.bridge.detach)

    def confirmed_passive_parent(self):
        turn='passive-parent';message='parent-source';text='此前确认的合成交流'
        self.archive.claim('test',SESSION,message,'10001','合成输入',[])
        self.archive.create_turn(turn,'test',SESSION,[message])
        self.archive.plan_bubbles(turn,'normal',[text],['parent-part'],sha(text))
        self.archive.begin_delivery('parent-part',turn,'normal',0,text,[])
        self.archive.mark_bubble_transport('parent-part')
        self.archive.finish_delivery('parent-part','sent',receipt_source='synthetic_guard_adapter_ack')
        self.archive.finish_bubble(turn,'normal',0,'sent')
        self.archive.set_turn_state(turn,'done')
        with self.archive.db:
            self.archive.db.execute('UPDATE inbound SET received_at=? WHERE message_id=?',
                (datetime.fromtimestamp(self.now-601).astimezone().isoformat(),message))
            self.archive.db.execute('UPDATE deliveries SET finished_at=? WHERE delivery_id=?',
                (datetime.fromtimestamp(self.now-600).astimezone().isoformat(),'parent-part'))
        self.host.native['context']['phase3_supplement']={'exchange_id':'exchange:'+turn,
            'parent_attempt':'turn:'+turn,'parent_kind':'passive',
            'input_version':self.bridge.input_version(SESSION)}

    async def test_empty_preferences_open_topic_and_final_cleaned_body(self):
        await self.bridge.tick_user('10001',self.host.user)
        self.assertEqual(self.sent[0][0],'省略号简直像标点里的摸鱼冠军')
        prompt=self.host.prompts[0]['prompt']
        self.assertIn('open_topic',prompt);self.assertIn('尚无已确认自身偏好',prompt)
        self.assertNotIn('必须围绕用户最近的事',prompt)
        self.assertEqual(self.host.user['ignored_streak'],2)
        self.assertEqual(self.gate.db.execute('SELECT count(*) FROM proactive_attempts').fetchone()[0],1)

    async def test_native_drop_preserves_model_send_and_records_zero_attempt_rejection(self):
        self.host.final_gate={'decision':'drop','reason':'synthetic native reality-source rejection','hard':True}
        await self.bridge.tick_user('10001',self.host.user)
        row=self.state.evaluation('candidate-1')
        self.assertEqual(row['effective_state'],'native_rejected')
        raw,parsed=self.gate.db.execute('SELECT raw_control,parsed FROM autonomy_evaluations').fetchone()
        self.assertEqual(raw,'[PHASE3_PROACTIVE:send]')
        self.assertEqual(json.loads(parsed)['decision'],'send')
        self.assertEqual(row['delivery_attempt'],'')
        self.assertEqual(row['reason'],self.host.final_gate['reason'])
        self.assertFalse(self.sent)
        self.assertEqual(self.gate.db.execute('SELECT count(*) FROM proactive_attempts').fetchone()[0],0)

    async def test_native_defer_is_distinct_from_model_defer_and_does_not_hang_ready(self):
        self.host.final_gate={'decision':'defer','delay_minutes':15,'reason':'synthetic native defer'}
        await self.bridge.tick_user('10001',self.host.user)
        row=self.state.evaluation('candidate-1')
        self.assertEqual(row['effective_state'],'native_deferred')
        self.assertEqual(json.loads(self.gate.db.execute('SELECT parsed FROM autonomy_evaluations').fetchone()[0])['decision'],'send');self.assertFalse(self.sent)

    async def test_native_accepted_rewrite_is_the_only_sent_and_archived_text(self):
        self.host.final_gate={'decision':'rewrite','text':'原生允许的合成改写','reason':'synthetic native rewrite'}
        await self.bridge.tick_user('10001',self.host.user)
        self.assertEqual(self.sent[0][0],'原生允许的合成改写')
        self.assertEqual(self.state.evaluation('candidate-1')['effective_state'],'sent')
        self.assertEqual(self.bridge.recent_confirmed(SESSION,include_parts=True),[['原生允许的合成改写']])

    async def test_native_tick_without_transport_has_explicit_no_attempt_outcome(self):
        self.host.no_transport=True
        await self.bridge.tick_user('10001',self.host.user)
        row=self.state.evaluation('candidate-1')
        self.assertEqual(row['effective_state'],'native_not_sent')
        self.assertEqual(row['delivery_attempt'],'')
        self.assertEqual(json.loads(self.gate.db.execute('SELECT parsed FROM autonomy_evaluations').fetchone()[0])['decision'],'send')

    async def test_native_no_send_cleanup_never_overwrites_existing_transport_attempt(self):
        self.host.no_transport=True
        original=self.bridge.settle_native_no_send
        def existing_attempt(scope,*args,**kwargs):
            self.gate.db.execute("INSERT INTO proactive_attempts(candidate,day,attempted,state) VALUES (?,'2026-10-27',?,'unknown')",(scope['candidate_id'],self.now));self.gate.db.commit()
            return original(scope,*args,**kwargs)
        self.bridge.settle_native_no_send=existing_attempt
        await self.bridge.tick_user('10001',self.host.user)
        row=self.state.evaluation('candidate-1')
        self.assertEqual(row['effective_state'],'ready_to_send')
        self.assertEqual(self.gate.db.execute('SELECT state FROM proactive_attempts').fetchone()[0],'unknown')

    async def test_cooldown_allows_evaluation_but_stashes_not_send(self):
        self.gate.db.execute("INSERT INTO proactive_attempts(candidate,day,attempted,state) VALUES ('old','2026-10-27',?,'sent')",(self.now-60,));self.gate.db.commit()
        await self.bridge.tick_user('10001',self.host.user)
        self.assertEqual(len(self.host.prompts),1);self.assertFalse(self.sent)
        self.assertEqual(len(self.state.list_pocket(SESSION)),1)
        self.assertEqual(self.state.evaluation('candidate-1')['effective_state'],'ready_unshared')

    async def test_new_input_during_generation_never_sends_old_draft(self):
        async def interrupt():
            self.archive.db.execute("INSERT INTO inbound(platform,session,message_id,sender_id,text,chain_json,received_at) VALUES ('test',?,'new','10001','synthetic','[]','2026-10-27T12:00:01+08:00')",(SESSION,));self.archive.db.commit()
        self.host.before=interrupt
        await self.bridge.tick_user('10001',self.host.user)
        self.assertFalse(self.sent)
        self.assertEqual(self.state.evaluation('candidate-1')['reason'],'new_user_input')

    async def test_defer_beyond_lifetime_not_model_skip(self):
        self.host.native['expire_at']=self.now+300
        self.host.response=answer('',decision='defer:30')
        await self.bridge.tick_user('10001',self.host.user)
        row=self.state.evaluation('candidate-1')
        self.assertEqual(row['effective_state'],'defer_expired')
        saved=self.gate.db.execute('SELECT raw_control,parsed FROM autonomy_evaluations').fetchone()
        self.assertIn('defer:30',saved[0]);self.assertEqual(json.loads(saved[1])['decision'],'defer')
        self.assertFalse(self.sent)

    async def test_executable_defer_aligns_user_plan_and_reconsiders_same_opportunity(self):
        self.host.user['planned_proactive_impulse_id']=self.host.native['id']
        self.host.response=answer('',decision='defer:5')
        expected=self.now+300
        await self.bridge.tick_user('10001',self.host.user)
        self.assertEqual(self.host.native['state'],'deferred')
        self.assertEqual(self.host.user['planned_proactive_delivery_state'],'deferred')
        self.assertEqual(self.host.user['next_proactive_at'],expected)
        self.assertEqual(self.host.user['planned_proactive_window_start_at'],expected)
        self.assertEqual(self.host.native['window_start_at'],expected)
        self.assertEqual(self.host.user['planned_proactive_expire_at'],self.host.native['expire_at'])
        self.assertEqual(float(self.gate._get('autonomy_not_before','0')),expected)
        self.now=expected-1
        await self.bridge.tick_user('10001',self.host.user)
        self.assertEqual(len(self.host.prompts),1)
        self.now=expected;self.host.response=answer()
        await self.bridge.tick_user('10001',self.host.user)
        self.assertEqual(len(self.host.prompts),2)
        self.assertEqual(len(self.sent),1)
        evaluations=self.gate.db.execute('SELECT opportunity_id,effective_state FROM autonomy_evaluations ORDER BY started').fetchall()
        self.assertEqual([tuple(row) for row in evaluations],[('candidate-1','deferred'),('candidate-1','sent')])
        self.assertEqual(self.gate.db.execute('SELECT count(*) FROM autonomy_opportunities').fetchone()[0],1)

    async def test_pause_and_night_prevent_generation(self):
        self.gate.pause();await self.bridge.tick_user('10001',self.host.user)
        self.assertFalse(self.host.prompts)
        self.gate.pause(0);self.now+=12*3600
        self.host.native['expire_at']=self.now+1000
        await self.bridge.tick_user('10001',self.host.user)
        self.assertFalse(self.host.prompts)

    async def test_external_claim_without_evidence_stays_unshared(self):
        self.host.response=answer('某个新物种今天被发现了。',kind='external_fact')
        await self.bridge.tick_user('10001',self.host.user)
        self.assertFalse(self.sent)
        self.assertEqual(self.state.evaluation('candidate-1')['reason'],'external_fact_without_provenance')
        self.assertEqual(self.state.list_pocket(SESSION)[0]['kind'],'external_fact')

    async def test_confirmed_delivery_finishes_execution_not_only_generation(self):
        await self.bridge.tick_user('10001',self.host.user)
        self.assertEqual(self.state.evaluation('candidate-1')['effective_state'],'sent')
        self.assertEqual(self.bridge.metrics['generation_attempts'],1)
        self.assertEqual(self.bridge.metrics['delivered'],1)

    async def test_optional_preference_failure_does_not_drop_normal_proactive(self):
        class Broken:
            async def drain(self,**kw):raise OSError('synthetic unavailable')
            def prompt(self):raise OSError('synthetic unavailable')
        self.bridge.self_preferences=Broken()
        await self.bridge.tick_user('10001',self.host.user)
        self.assertEqual(len(self.sent),1)
        self.assertIn('自身偏好暂不可用',self.host.prompts[0]['prompt'])

    async def test_unknown_interest_prefix_blocks_dependent_final(self):
        async def unknown(*a,**k):
            return {'state':'unknown','sent_count':1,'part_count':2,'delivered_text':'已确认前缀'}
        self.guard.send_proactive=unknown
        async def steps():
            scope=self.bridge.scopes[SESSION]
            self.assertEqual(await self.bridge.send_interest(scope,'一个具体问题'),'unknown')
        self.host.before=steps
        await self.bridge.tick_user('10001',self.host.user)
        self.assertEqual(self.gate.db.execute('SELECT count(*) FROM proactive_attempts').fetchone()[0],1)
        self.assertEqual(self.state.evaluation('candidate-1')['reason'],'supplement_parent_unconfirmed')
        self.assertEqual(self.host.archives[0]['assistant_response'],'已确认前缀')

    async def test_supplement_model_sees_actual_cooldown_exception(self):
        self.gate.db.execute("INSERT INTO proactive_attempts(candidate,day,attempted,state) VALUES ('old','2026-10-27',?,'sent')",(self.now-60,));self.gate.db.commit()
        self.confirmed_passive_parent()
        await self.bridge.tick_user('10001',self.host.user)
        self.assertIn('"send_allowed": true',self.host.prompts[0]['prompt'])
        self.assertEqual(len(self.sent),1)

    async def test_new_candidate_after_unknown_can_still_evaluate(self):
        self.gate.db.execute("INSERT INTO proactive_attempts(candidate,day,attempted,state) VALUES ('uncertain','2026-10-27',?,'unknown')",(self.now-5500,));self.gate.db.commit()
        await self.bridge.tick_user('10001',self.host.user)
        self.assertEqual(len(self.sent),1)

    async def test_feature_candidate_preserves_current_frequency(self):
        original={'enabled':True,'persona':True,'natural_chat':True,'proactive':True,
                  'proactive_window':dict(FREQUENCY_PROACTIVE_POLICY),'proactive_history':{'summary_limit':8,'ordinary_retention_hours':24}}
        result=autonomy_candidate(original)
        self.assertEqual(result['proactive_window'],original['proactive_window'])
        self.assertNotIn('proactive_autonomy',original)

    async def test_interest_and_independent_result_count_two_attempts(self):
        async def model_steps():
            scope=self.bridge.scopes[SESSION]
            self.assertEqual(await self.bridge.send_interest(scope,'章鱼的皮肤会不会自己看见光？这事有点意思。'),'sent')
        self.host.before=model_steps
        await self.bridge.tick_user('10001',self.host.user)
        self.assertEqual(len(self.sent),2)
        self.assertEqual(self.gate.db.execute('SELECT count(*) FROM proactive_attempts').fetchone()[0],2)
        self.assertEqual(self.gate.db.execute('SELECT count(*) FROM autonomy_supplements').fetchone()[0],1)
        self.assertEqual(len(self.host.archives),1) # interest only; native tick owns final archive

    async def test_native_final_drop_preserves_confirmed_interest_attempt_and_history(self):
        async def model_steps():
            scope=self.bridge.scopes[SESSION]
            self.assertEqual(await self.bridge.send_interest(scope,'标点省略时节奏会怎样变化，这个问题挺有趣。'),'sent')
        self.host.before=model_steps
        self.host.final_gate={'decision':'drop','reason':'synthetic final source rejection','hard':True}
        await self.bridge.tick_user('10001',self.host.user)
        self.assertEqual(self.state.evaluation('candidate-1')['effective_state'],'native_rejected')
        self.assertEqual(len(self.sent),1);self.assertEqual(len(self.host.archives),1)
        attempts=self.gate.db.execute('SELECT candidate,state FROM proactive_attempts').fetchall()
        self.assertEqual([tuple(x) for x in attempts],[('candidate-1:interest','sent')])
        audits=[json.loads(path.read_text()) for path in self.state.audit_dir.glob('*.json')]
        execution=next(a for a in audits if a.get('stage')=='native_final_execution')
        self.assertIs(execution['final_guard_attempt_created'],False)
        self.assertEqual(execution['interest_attempt'],'candidate-1:interest')
        self.assertNotIn('guard_attempt_created',execution)

    async def rejected_interest_continues(self,text):
        from test_phase3_autonomy_request import Runner,response
        from integrations.phase3_runtime.autonomy_request import install_runner
        observed={}
        async def model_steps():
            scope=self.bridge.scopes[SESSION]
            runner=Runner([response('[PHASE3_INTEREST]\n'+text,('search',))])
            install_runner(runner,state=self.state,evaluation_id=scope['evaluation_id'],
                admit=lambda:self.bridge.scope_admission(scope),
                interest=lambda visible:self.bridge.send_interest(scope,visible))
            values=[item async for item in runner._iter_llm_responses()]
            self.assertEqual(len(values),1)
            self.assertEqual(values[0].tools_call_name,['search'])
            self.assertIn('保持静默探索',values[0].completion_text)
            self.assertNotIn(text,values[0].completion_text)
            self.assertFalse(self.sent)
            self.assertIsNone(scope.get('interest_attempt'))
            self.assertTrue(scope['interest_content_rejected'])
            # Local executor admission still works. This is a synthetic failed
            # tool attempt, not evidence of a successful network operation.
            tool=self.state.reserve_tool_attempt(scope['evaluation_id'],'search')
            self.assertTrue(tool['allowed'])
            self.state.finish_call(tool['id'],'failed',metadata={'io_started':False,'fixture':'no_external_io'})
            observed['model_requests']=runner.http_requests
        self.host.before=model_steps
        self.host.response=answer('这是探索结束后独立判断过的合成观点。')
        await self.bridge.tick_user('10001',self.host.user)
        self.assertEqual(observed['model_requests'],1)
        self.assertEqual(len(self.sent),1)
        self.assertNotIn(text,self.sent[0][0])
        attempts=self.gate.db.execute('SELECT candidate FROM proactive_attempts').fetchall()
        self.assertEqual([row[0] for row in attempts],['candidate-1'])
        self.assertFalse(self.gate.db.execute('SELECT 1 FROM autonomy_supplements').fetchone())
        self.assertFalse(self.host.archives)
        self.assertEqual(self.gate.db.execute("SELECT count(*) FROM autonomy_calls WHERE kind='model'").fetchone()[0],1)
        self.assertEqual(self.gate.db.execute("SELECT count(*) FROM autonomy_calls WHERE kind='search'").fetchone()[0],1)

    async def test_rejected_internal_json_interest_remains_silent_and_tool_path_continues(self):
        await self.rejected_interest_continues('这点有意思。\n{"content_check":{"novelty":"new"}}')

    async def test_rejected_concrete_episode_interest_remains_silent_and_tool_path_continues(self):
        await self.rejected_interest_continues('我昨天玩了那款游戏，突然有个想法。')

    async def test_ordinary_technical_json_interest_is_not_internal_protocol(self):
        interest='这个配置差异挺有意思：{"mode":"demo","timeout":5}'
        async def model_steps():
            self.assertEqual(await self.bridge.send_interest(self.bridge.scopes[SESSION],interest),'sent')
        self.host.before=model_steps
        await self.bridge.tick_user('10001',self.host.user)
        self.assertEqual(len(self.sent),2);self.assertEqual(self.sent[0][0],interest)
        self.assertEqual(self.gate.db.execute('SELECT count(*) FROM proactive_attempts').fetchone()[0],2)
        self.assertEqual(self.gate.db.execute('SELECT count(*) FROM autonomy_supplements').fetchone()[0],1)

    async def test_supplement_switch_stops_interest_followup_too(self):
        self.bridge.options['supplements']=False
        async def model_steps():
            scope=self.bridge.scopes[SESSION]
            self.assertEqual(await self.bridge.send_interest(scope,'这个具体想法值得看看。'),'sent')
        self.host.before=model_steps
        await self.bridge.tick_user('10001',self.host.user)
        self.assertEqual(len(self.sent),1)
        self.assertEqual(self.state.evaluation('candidate-1')['reason'],'supplement_disabled')
        self.assertFalse(self.gate.db.execute('SELECT 1 FROM autonomy_supplements').fetchone())

    async def test_supplement_cannot_bypass_pause_or_daily_attempt_limit(self):
        self.confirmed_passive_parent()
        for i in range(12):
            self.gate.db.execute('INSERT INTO proactive_attempts(candidate,day,attempted,state) VALUES (?,?,?,?)',
                (str(i),'2026-10-27',self.now-10,'failed'))
        self.gate.db.commit()
        await self.bridge.tick_user('10001',self.host.user)
        self.assertFalse(self.sent)
        self.assertEqual(len(self.host.prompts),1) # exploration remains possible after send allowance

    async def test_supplement_uses_cooldown_exception_once(self):
        self.confirmed_passive_parent()
        self.gate.db.execute("INSERT INTO proactive_attempts(candidate,day,attempted,state) VALUES ('old','2026-10-27',?,'sent')",(self.now-60,));self.gate.db.commit()
        await self.bridge.tick_user('10001',self.host.user)
        self.assertEqual(len(self.sent),1)
        self.host.native.update(id='candidate-2',state='queued')
        await self.bridge.tick_user('10001',self.host.user)
        self.assertEqual(len(self.sent),1)
        self.assertEqual(self.gate.db.execute('SELECT count(*) FROM autonomy_supplements').fetchone()[0],1)

    async def test_send_failure_consumes_attempt_and_no_blind_retry(self):
        async def failed(*a,**k):return {'state':'unknown','sent_count':0,'part_count':2,'delivered_text':''}
        self.guard.send_proactive=failed
        await self.bridge.tick_user('10001',self.host.user)
        await self.bridge.tick_user('10001',self.host.user)
        self.assertEqual(len(self.host.prompts),1)
        self.assertEqual(tuple(self.gate.db.execute('SELECT state,sent_count FROM proactive_attempts').fetchone()),('unknown',0))

    async def test_changed_input_cancels_interest_remainder(self):
        async def change_after_interest():
            scope=self.bridge.scopes[SESSION]
            await self.bridge.send_interest(scope,'这个语言小问题真有点意思。')
            self.archive.db.execute("INSERT INTO inbound VALUES ('test',?,'n','10001','synthetic','[]','2026-10-27T12:00:01+08:00',0)",(SESSION,));self.archive.db.commit()
        self.host.before=change_after_interest
        await self.bridge.tick_user('10001',self.host.user)
        self.assertEqual(len(self.sent),1)
        self.assertEqual(self.state.evaluation('candidate-1')['reason'],'new_user_input')


if __name__=='__main__':unittest.main()
