"""T1 real Guard-plan/state integration for optional native supplement offers.

Only the native candidate offer is a synthetic boundary. No model, QQ, network,
production configuration or production counters are involved.
"""
import asyncio
from datetime import datetime
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
import uuid

from integrations.phase1_guard.bubbles import sha
from integrations.phase1_guard.store import GuardStore
from integrations.phase3_runtime.autonomy import AutonomousProactiveBridge
from integrations.phase3_runtime.autonomy_state import AutonomyState
from integrations.phase3_runtime.autonomy_supplement import check_supplement
from integrations.phase3_runtime.proactive import ProactiveGate
from integrations.phase3_runtime.settings import AUTONOMY_DEFAULTS, FREQUENCY_PROACTIVE_POLICY
import test_phase3_autonomy_bridge as fixtures


class SupplementOfferTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = datetime.fromisoformat('2026-10-27T12:00:00+08:00').timestamp()
        self.gate = ProactiveGate(self.root / 'active.db', allowed_session=fixtures.SESSION,
            enabled=True, clock=lambda: self.now, **FREQUENCY_PROACTIVE_POLICY)
        self.addCleanup(self.gate.close)
        self.archive = GuardStore(self.root / 'guard.db')
        self.archive.enable_phase3()
        self.archive.enable_bubbles()
        self.addCleanup(self.archive.close)
        self.busy = False
        self.guard = NS(store=self.archive, phase3_platform_id='test', phase3_admin_id='10001',
                        session_busy=lambda _: self.busy, stopping=False)
        self.host = fixtures.Host(self.now)
        self.state = AutonomyState(self.gate.db, clock=lambda: self.now,
                                   audit_dir=self.root / 'audit')
        self.bridge = AutonomousProactiveBridge(self.host, self.guard, self.gate,
            lambda *args, **kwargs: NS(), autonomy_state=self.state,
            autonomy_settings=dict(AUTONOMY_DEFAULTS))
        self.bridge.attach()
        self.addCleanup(self.bridge.detach)

    def incoming(self, message_id, at):
        self.archive.claim('test', fixtures.SESSION, message_id, '10001', '合成输入', [])
        self.archive.db.execute('UPDATE inbound SET received_at=? WHERE message_id=?',
                                (datetime.fromtimestamp(at).astimezone().isoformat(), message_id))
        self.archive.db.commit()

    def plan(self, *, kind='final', candidate=None, texts=('合成已确认正文',),
             states=('sent',), age=600):
        """Persist the actual Guard schema and its receipts, never invented send flags."""
        turn_id = (str(uuid.uuid5(uuid.NAMESPACE_URL,
                       'qq-companion-proactive:proactive:' + candidate))
                   if candidate else str(uuid.uuid4()))
        if kind == 'proactive':
            self.archive.create_turn(turn_id, 'test', fixtures.SESSION, proactive=True)
            self.gate.db.execute('INSERT INTO proactive_attempts(candidate,day,attempted,state) VALUES (?,?,?,?)',
                (candidate, '2026-10-27', self.now-age, 'sent' if all(s=='sent' for s in states) else 'unknown'))
            self.gate.db.commit()
        else:
            inbound = 'input:' + turn_id
            self.incoming(inbound, self.now-age-1)
            self.archive.create_turn(turn_id, 'test', fixtures.SESSION, (inbound,))
        ids = [turn_id + ':' + str(i) for i in range(len(texts))]
        self.archive.plan_bubbles(turn_id, kind, texts, ids, sha(''.join(texts)))
        at = datetime.fromtimestamp(self.now-age).astimezone().isoformat()
        for i, (text, state) in enumerate(zip(texts, states)):
            if state == 'cancelled':
                continue
            self.archive.begin_delivery(ids[i], turn_id, kind, i, text, [])
            self.archive.mark_bubble_transport(ids[i])
            self.archive.finish_delivery(ids[i], state,
                receipt_source='synthetic_fixed_adapter_ack' if state=='sent' else None)
            self.archive.finish_bubble(turn_id, kind, i, state)
            self.archive.db.execute('UPDATE deliveries SET finished_at=? WHERE delivery_id=?', (at, ids[i]))
        self.archive.db.commit()
        self.archive.cancel_remaining_bubbles(turn_id, kind, 'synthetic_tail_cancelled')
        self.archive.set_turn_state(turn_id, 'done')
        return turn_id

    async def test_passive_confirmed_group_offered_once_via_native_pool(self):
        turn = self.plan(texts=('第一泡', '第二泡'), states=('sent', 'sent'))
        self.assertTrue(await self.bridge.offer_supplement())
        self.assertFalse(await self.bridge.offer_supplement())
        self.assertEqual(len(self.host.offers), 1)
        candidate = self.host.offers[0]
        source = candidate['context']['phase3_supplement']
        self.assertEqual(source['parent_attempt'], 'turn:' + turn)
        self.assertEqual(source['parent_kind'], 'passive')
        self.assertEqual(source['confirmed_text'], '第一泡\n第二泡')
        self.assertEqual(candidate['expire_at'], self.now + 3000)
        self.assertEqual(self.gate.db.execute('SELECT count(*) FROM proactive_attempts').fetchone()[0], 0)
        self.assertEqual(self.gate.db.execute('SELECT count(*) FROM proactive_generations').fetchone()[0], 0)

    def bind_parent(self, turn, *, parent=None, parent_kind='passive'):
        metadata = {'exchange_id':'exchange:'+turn,
            'parent_attempt':parent or 'turn:'+turn, 'parent_kind':parent_kind,
            'input_version':self.bridge.input_version(fixtures.SESSION),
            'confirmed_text':'Untrusted candidate text is not receipt evidence'}
        self.host.native['context']['phase3_supplement'] = metadata
        return metadata

    async def test_short_supplement_checks_actual_parent_at_ten_minutes(self):
        turn = self.plan(age=600)
        self.bind_parent(turn)
        result = check_supplement(self.bridge, self.host.user)
        self.assertTrue(result['allowed'])
        self.assertEqual(result['idle_seconds'], 300)
        self.assertEqual(result['parent_turn_id'], turn)
        self.assertEqual(result['parent_finished_at'], self.now-600)
        self.assertEqual(self.gate.db.execute('SELECT count(*) FROM autonomy_supplements').fetchone()[0],0)

    async def test_short_supplement_two_minutes_and_expiry_fail_closed(self):
        turn = self.plan(age=120)
        self.bind_parent(turn)
        self.assertEqual(check_supplement(self.bridge,self.host.user)['reason'],'supplement_too_soon')
        self.now += 180
        self.assertTrue(check_supplement(self.bridge,self.host.user)['allowed'])
        self.now += 3300
        self.assertEqual(check_supplement(self.bridge,self.host.user)['reason'],'supplement_expired')

    async def test_parent_epoch_blocks_new_input_even_with_backdated_timestamp(self):
        turn = self.plan(age=600)
        metadata = self.bind_parent(turn)
        self.incoming('supplement-new-input',self.now-700)
        self.assertEqual(check_supplement(self.bridge,self.host.user)['reason'],'supplement_no_longer_current')
        # Updating the candidate's epoch cannot make an old passive parent current.
        metadata['input_version'] = self.bridge.input_version(fixtures.SESSION)
        self.assertEqual(check_supplement(self.bridge,self.host.user)['reason'],'supplement_no_longer_current')

    async def test_short_supplement_rejects_wrong_parent_and_partial_receipt(self):
        for states in (('sent','unknown'),('sent','failed'),('sent','cancelled')):
            turn = self.plan(texts=('confirmed prefix','unconfirmed tail'),states=states,age=600)
            self.bind_parent(turn)
            self.assertEqual(check_supplement(self.bridge,self.host.user)['reason'],'supplement_parent_unconfirmed')
        turn = self.plan(age=600)
        self.bind_parent(turn)
        with self.archive.db:
            self.archive.db.execute('UPDATE turns SET session=? WHERE turn_id=?',('test:FriendMessage:10002',turn))
        self.assertEqual(check_supplement(self.bridge,self.host.user)['reason'],'supplement_parent_wrong_identity')

    async def test_confirmed_interest_result_is_same_opportunity_not_new_idle_wait(self):
        parent = 'candidate-1:interest'
        turn = self.plan(kind='proactive',candidate=parent,age=1)
        scope={'candidate_id':'candidate-1','session':fixtures.SESSION,
            'input_version':self.bridge.input_version(fixtures.SESSION),'interest_attempt':parent}
        result=check_supplement(self.bridge,self.host.user,scope=scope)
        self.assertTrue(result['allowed']);self.assertTrue(result['same_opportunity_interest'])
        self.assertEqual(result['idle_seconds'],0)
        self.assertEqual(result['parent_turn_id'],turn)
        self.state.reserve_supplement('candidate-1',parent_attempt=parent,parent_kind='interest',attempt_id='candidate-1')
        self.assertEqual(check_supplement(self.bridge,self.host.user,scope=scope)['reason'],'supplement_already_attempted')

    async def test_interest_result_epoch_and_unknown_are_not_bypassed(self):
        parent='candidate-1:interest'
        self.plan(kind='proactive',candidate=parent,age=1)
        scope={'candidate_id':'candidate-1','session':fixtures.SESSION,
            'input_version':self.bridge.input_version(fixtures.SESSION),'interest_attempt':parent}
        self.incoming('after-interest',self.now)
        self.assertEqual(check_supplement(self.bridge,self.host.user,scope=scope)['reason'],'supplement_no_longer_current')
        scope['input_version']=self.bridge.input_version(fixtures.SESSION)
        self.gate.db.execute('UPDATE proactive_attempts SET state=\'unknown\' WHERE candidate=?',(parent,));self.gate.db.commit()
        self.assertEqual(check_supplement(self.bridge,self.host.user,scope=scope)['reason'],'supplement_parent_unconfirmed')

    async def test_proactive_parent_keeps_original_audited_epoch(self):
        parent='audited-parent'
        self.state.begin_opportunity(parent,evaluation_id=parent,candidate_id=parent,
            session=fixtures.SESSION,deadline=self.now+600,input_version='0')
        turn=self.plan(kind='proactive',candidate=parent,age=600)
        self.incoming('later-input-with-old-clock',self.now-700)
        self.bind_parent(turn,parent=parent,parent_kind='proactive')
        self.assertEqual(check_supplement(self.bridge,self.host.user)['reason'],'supplement_no_longer_current')

    async def test_supplement_cannot_parent_another_or_bypass_daily_budget(self):
        parent='child-of-another'
        turn=self.plan(kind='proactive',candidate=parent,age=600)
        self.bind_parent(turn,parent=parent,parent_kind='proactive')
        self.state.reserve_supplement('earlier-exchange',parent_attempt='turn:earlier',
            parent_kind='passive',attempt_id=parent)
        self.state.finish_supplement(parent,'sent')
        self.assertEqual(check_supplement(self.bridge,self.host.user)['reason'],'supplement_cannot_parent_supplement')
        self.assertEqual(self.gate.permit(fixtures.SESSION,candidate_id='new',source='want_to_chat',
            supplement=True),'allowed')
        for index in range(self.gate.limit):
            self.gate.db.execute('INSERT INTO proactive_attempts(candidate,day,attempted,state) VALUES (?,?,?,?)',
                ('spent-'+str(index),'2026-10-27',self.now-600,'failed'))
        self.gate.db.commit()
        self.assertEqual(self.gate.permit(fixtures.SESSION,candidate_id='new',source='want_to_chat',
            supplement=True),'budget_or_cooldown')

    async def test_progress_alone_never_offers_supplement(self):
        self.plan(kind='progress')
        self.assertFalse(await self.bridge.offer_supplement())
        self.assertFalse(self.host.offers)

    async def test_partial_and_unknown_plans_never_offered_as_complete_exchange(self):
        for states in (('sent', 'unknown'), ('sent', 'failed'), ('sent', 'cancelled')):
            with self.subTest(states=states):
                self.plan(texts=('实际前缀', '尚未确认尾部'), states=states)
                self.assertFalse(await self.bridge.offer_supplement())
        self.assertFalse(self.host.offers)

    async def test_new_input_since_confirmed_delivery_blocks_stale_exchange(self):
        self.plan()
        self.incoming('new-message', self.now-599)
        self.assertFalse(await self.bridge.offer_supplement())

    async def test_configured_delay_and_expiry_are_enforced(self):
        turn = self.plan(age=299)
        self.assertFalse(await self.bridge.offer_supplement())
        self.now += 1
        self.assertTrue(await self.bridge.offer_supplement())
        self.assertEqual(self.host.offers[0]['id'], 'supplement:' + turn)
        self.host.offers.clear()
        self.plan(age=3600)
        self.assertFalse(await self.bridge.offer_supplement())

    async def test_pause_busy_quiet_and_feature_switch_block_native_offer(self):
        self.plan()
        self.gate.pause()
        self.assertFalse(await self.bridge.offer_supplement())
        self.gate.pause(0)
        self.busy = True
        self.assertFalse(await self.bridge.offer_supplement())
        self.busy = False
        self.bridge.options['supplements'] = False
        self.assertFalse(await self.bridge.offer_supplement())
        self.bridge.options['supplements'] = True
        self.now = datetime.fromisoformat('2026-10-27T23:00:00+08:00').timestamp()
        self.plan()
        self.assertFalse(await self.bridge.offer_supplement())

    async def test_rejected_native_offer_is_not_recorded_as_accepted(self):
        self.plan()
        original = self.host._offer_proactive_candidate
        self.host._offer_proactive_candidate = lambda *args: False
        self.assertFalse(await self.bridge.offer_supplement())
        self.host._offer_proactive_candidate = original
        self.assertTrue(await self.bridge.offer_supplement())

    async def test_normal_proactive_parent_uses_actual_attempt_identity(self):
        self.plan(kind='proactive', candidate='candidate-original')
        self.assertTrue(await self.bridge.offer_supplement())
        source = self.host.offers[-1]['context']['phase3_supplement']
        self.assertEqual(source['parent_attempt'], 'candidate-original')
        self.assertEqual(source['parent_kind'], 'proactive')

    async def test_supplement_cannot_become_parent_even_after_recovery(self):
        self.state.reserve_supplement('exchange-parent', parent_attempt='passive-parent',
            parent_kind='passive', attempt_id='candidate-child')
        self.state.finish_supplement('candidate-child', 'sent')
        self.plan(kind='proactive', candidate='candidate-child')
        self.state = AutonomyState(self.gate.db, clock=lambda:self.now, audit_dir=self.root/'audit')
        self.bridge.state = self.state
        self.assertFalse(await self.bridge.offer_supplement())

    async def test_unknown_interest_child_cannot_get_new_offer_under_other_exchange_id(self):
        """Interest→unknown result must not spawn another result from last sent prefix."""
        self.plan(kind='proactive', candidate='candidate-original:interest')
        self.state.reserve_supplement('candidate-original',
            parent_attempt='candidate-original:interest', parent_kind='interest', attempt_id='candidate-original')
        self.state.finish_supplement('candidate-original', 'unknown')
        self.assertFalse(await self.bridge.offer_supplement())
        self.assertFalse(self.host.offers)

    async def test_accepted_offer_remains_one_time_after_state_reinstantiation(self):
        self.plan()
        self.assertTrue(await self.bridge.offer_supplement())
        self.state = AutonomyState(self.gate.db, clock=lambda:self.now, audit_dir=self.root/'audit')
        self.bridge.state = self.state
        self.assertFalse(await self.bridge.offer_supplement())
        self.assertEqual(len(self.host.offers), 1)

    async def cancelled_prefix(self, *, interest, started_tail):
        admitted=self.state.begin_opportunity('candidate-1',evaluation_id='candidate-1',
            candidate_id='candidate-1',session=fixtures.SESSION,deadline=self.now+600,input_version='0')
        scope={**admitted,'candidate_id':'candidate-1','session':fixtures.SESSION,
            'input_version':'0','user':self.host.user,'decision':{'kind':'opinion'},
            'interest_attempt':None,'supplement':None}
        arrived=asyncio.Event();never=asyncio.Event();transports=[]
        async def transport(umo,text,*,delivery_id,is_current):
            self.assertTrue(is_current())
            transports.append(delivery_id)
            turn=str(uuid.uuid5(uuid.NAMESPACE_URL,'qq-companion-proactive:'+delivery_id))
            self.archive.create_turn(turn,'test',fixtures.SESSION,proactive=True)
            ids=[delivery_id+':0',delivery_id+':1']
            texts=['实际确认的第一泡','没有确认的第二泡']
            self.archive.plan_bubbles(turn,'proactive',texts,ids,sha(text))
            self.archive.begin_delivery(ids[0],turn,'proactive',0,texts[0],[])
            self.archive.mark_bubble_transport(ids[0])
            self.archive.finish_delivery(ids[0],'sent',receipt_source='synthetic_fixed_adapter_ack')
            self.archive.finish_bubble(turn,'proactive',0,'sent')
            if started_tail:
                self.archive.begin_delivery(ids[1],turn,'proactive',1,texts[1],[])
                self.archive.mark_bubble_transport(ids[1])
            arrived.set()
            try:
                await never.wait()
            finally:
                # Actual Guard cancellation behavior: reconcile receipts and
                # retire the unsent tail, never resume it after cancellation.
                self.archive.cancel_remaining_bubbles(turn,'proactive','task_cancelled')
                self.archive.set_turn_state(turn,'done')
        self.guard.send_proactive=transport
        task=asyncio.create_task(self.bridge.dispatch(scope,'原始两泡计划',interest=interest))
        await asyncio.wait_for(arrived.wait(),2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(len(transports),1)
        self.assertEqual(len(self.host.archives),1)
        archived=self.host.archives[0]
        self.assertEqual(archived['assistant_response'],'实际确认的第一泡')
        self.assertEqual(archived['user_prompt'],'')
        expected='unknown' if started_tail else 'cancelled'
        receipt=self.gate.db.execute('SELECT state,sent_count,part_count FROM proactive_attempts').fetchone()
        self.assertEqual(tuple(receipt),(expected,1,2))
        # Restart-style observation and another dispatch do not resume a tail.
        self.bridge._reconcile_guard_attempts()
        retry=await self.bridge.dispatch(scope,'原始两泡计划',interest=interest)
        self.assertEqual(retry['state'],'blocked')
        self.assertEqual(len(transports),1)
        self.assertEqual(len(self.host.archives),1)
        self.assertEqual(self.gate.db.execute('SELECT count(*) FROM proactive_attempts').fetchone()[0],1)

    async def test_cancelled_interest_archives_only_acknowledged_prefix_and_never_replays(self):
        await self.cancelled_prefix(interest=True,started_tail=True)

    async def test_cancelled_final_archives_only_acknowledged_prefix_and_never_replays(self):
        await self.cancelled_prefix(interest=False,started_tail=False)


if __name__ == '__main__':
    unittest.main()
