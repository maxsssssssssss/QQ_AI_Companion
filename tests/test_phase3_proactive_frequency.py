"""Frequency-only release: durable quotas, exact boundaries and native tier parity.

Synthetic clock/identity/storage only; no network, model or real QQ calls.
"""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from integrations.phase3_runtime.proactive import ProactiveGate, native_proactive_config
from integrations.phase3_runtime.settings import (
    FREQUENCY_PROACTIVE_POLICY, TUNED_PROACTIVE_POLICY, frequency_proactive_candidate,
    tuned_proactive_candidate, validate_settings)


def stamp(value):
    return datetime.fromisoformat(value+'+08:00').timestamp()


def settings():
    return dict(enabled=True,persona=True,natural_chat=True,memory=True,search=True,proactive=True,
        preferred_address='synthetic',context_compression_fallback_turns=80,
        proactive_window=deepcopy(TUNED_PROACTIVE_POLICY),
        proactive_history={'summary_limit':8,'ordinary_retention_hours':24},
        memory_topic_aliases={'games':['游戏']})


class FrequencySettingsTests(unittest.TestCase):
    def test_only_three_fields_change_and_activation_history_and_old_helper_survive(self):
        before=settings();snapshot=deepcopy(before)
        result=frequency_proactive_candidate(before)
        self.assertEqual(before,snapshot)
        for key,value in before.items():
            if key!='proactive_window':self.assertEqual(result[key],value)
        changes={k:(before['proactive_window'][k],v) for k,v in result['proactive_window'].items()
                 if v!=before['proactive_window'][k]}
        self.assertEqual(changes,{'max_per_day':(8,12),'max_generations_per_day':(48,96),'cooldown':(7200,5400)})
        self.assertFalse(frequency_proactive_candidate({**before,'proactive':False})['proactive'])
        self.assertEqual(tuned_proactive_candidate(before)['proactive_window'],TUNED_PROACTIVE_POLICY)

    def test_rejects_unapproved_migration_and_keeps_old_trial_bounds(self):
        for window in ({},None,{**TUNED_PROACTIVE_POLICY,'mode':'trial'},
                       {**TUNED_PROACTIVE_POLICY,'quiet_hours':[0,9]}):
            with self.subTest(window=window),self.assertRaises(ValueError):
                frequency_proactive_candidate({**settings(),'proactive_window':window})
        for change in ({'max_per_day':13},{'max_generations_per_day':97},{'cooldown':5399},
                       {'max_per_day':True},{'max_generations_per_day':False}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                validate_settings({**settings(),'proactive_window':{**FREQUENCY_PROACTIVE_POLICY,**change}})
        with self.assertRaises(ValueError):
            validate_settings({**settings(),'proactive_window':dict(mode='trial',window_start=1,
                window_end=3600,max_per_day=12,max_generations_per_day=96,cooldown=14400,quiet_hours=[23,9])})

    def test_native_frequency_config_is_cold_and_has_no_new_idle_override(self):
        old={'private_followup_config':{'idle_minutes':60,'min_interval_minutes':120},
             'proactive_dedup_config':{'proactive_dedup_sent_window_minutes':240}}
        result=native_proactive_config(old,'10001',enabled=True,policy=FREQUENCY_PROACTIVE_POLICY)
        self.assertEqual(result['private_followup_config']['min_interval_minutes'],90)
        self.assertEqual(result['private_followup_config']['idle_minutes'],60)
        self.assertEqual(result['proactive_dedup_config'],old['proactive_dedup_config'])
        self.assertEqual(result['basic_config']['quiet_hours'],'23:00-09:00')
        self.assertEqual(result['proactive_reach_config']['max_daily_messages'],0)
        self.assertFalse(result['proactive_generation_config']['enable_llm_proactive_message'])


class FrequencyGateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'gate.sqlite'
        self.now=stamp('2026-10-27T09:00:00');self.gate=self.open()

    def open(self,policy=None):
        return ProactiveGate(self.path,allowed_session='test:FriendMessage:10001',enabled=True,
            clock=lambda:self.now,**(policy or FREQUENCY_PROACTIVE_POLICY))

    async def asyncTearDown(self):
        self.gate.close();self.temp.cleanup()

    async def sent(self):return {'state':'sent','sent_count':4,'part_count':4}

    async def test_96_evaluations_skip_defer_errors_consume_and_97th_is_blocked(self):
        for i in range(96):
            candidate=str(i)
            self.assertEqual(await self.gate.reserve_generation(self.gate.session,candidate_id=candidate),'allowed')
            self.gate.finish_generation(candidate,('skipped','deferred','empty','failed','cancelled')[i%5])
        self.assertEqual(await self.gate.reserve_generation(self.gate.session,candidate_id='97'),'generation_budget')
        self.assertEqual(self.gate.db.execute('SELECT count(*) FROM proactive_attempts').fetchone()[0],0)

    async def test_90_minimum_is_not_a_timer_and_no_evaluation_interval_is_added(self):
        for i in range(2):
            self.assertEqual(await self.gate.reserve_generation(self.gate.session,candidate_id='skip'+str(i)),'allowed')
            self.gate.finish_generation('skip'+str(i),'skipped')
        self.assertEqual((await self.gate.deliver(self.gate.session,candidate_id='first',send=self.sent))['state'],'sent')
        self.now+=5399.999
        self.assertEqual(self.gate.permit(self.gate.session,candidate_id='second'),'budget_or_cooldown')
        self.assertEqual(await self.gate.reserve_generation(self.gate.session,candidate_id='second'),'budget_or_cooldown')
        self.now+=.001
        self.assertEqual(self.gate.permit(self.gate.session,candidate_id='second'),'allowed')
        # Reaching the boundary only grants eligibility; no automatic generation/send.
        self.assertEqual(self.gate.db.execute('SELECT count(*) FROM proactive_attempts').fetchone()[0],1)
        self.assertEqual(self.gate.db.execute('SELECT count(*) FROM proactive_generations').fetchone()[0],2)

    async def test_twelve_rounds_count_not_bubbles_and_failure_still_costs_attempt(self):
        # Isolate attempt quota from the production hours tested separately.
        self.gate.quiet=(0,0)
        for i in range(12):
            self.now=stamp('2026-10-27T00:00:00')+i*5400
            async def send(state=('sent','failed','unknown','cancelled')[i%4]):
                return {'state':state,'sent_count':4 if state=='sent' else 0,'part_count':4}
            self.assertNotEqual((await self.gate.deliver(self.gate.session,candidate_id=str(i),send=send))['state'],'blocked')
        self.now+=5400
        self.assertEqual(self.gate.permit(self.gate.session,candidate_id='13'),'budget_or_cooldown')
        self.assertEqual(self.gate.db.execute('SELECT count(*),sum(part_count),sum(sent_count) FROM proactive_attempts').fetchone(),(12,48,12))

    async def test_old_counters_and_blocked_topics_survive_upgrade_and_rollback(self):
        self.gate.close();self.gate=self.open(TUNED_PROACTIVE_POLICY)
        for i in range(48):
            self.assertEqual(await self.gate.reserve_generation(self.gate.session,candidate_id=str(i)),'allowed')
            self.gate.finish_generation(str(i),'skipped')
        await self.gate.deliver(self.gate.session,candidate_id='old-send',send=self.sent)
        self.gate.set_control('blocked:topic','1')
        self.gate.close();self.gate=self.open()
        self.now+=5400
        for i in range(48,96):
            self.assertEqual(await self.gate.reserve_generation(self.gate.session,candidate_id=str(i)),'allowed')
            self.gate.finish_generation(str(i),'deferred')
        self.assertEqual(self.gate._get('blocked:topic'),'1')
        self.assertEqual(self.gate.permit(self.gate.session,candidate_id='old-send'),'already_attempted')
        self.gate.close();self.gate=self.open(TUNED_PROACTIVE_POLICY)
        self.now+=1800
        self.assertEqual(await self.gate.reserve_generation(self.gate.session,candidate_id='after-rollback'),'generation_budget')
        self.assertEqual(self.gate.db.execute('SELECT count(*) FROM proactive_generations').fetchone()[0],96)
        self.assertEqual(self.gate.db.execute('SELECT count(*) FROM proactive_attempts').fetchone()[0],1)

    def test_same_topic_pause_and_exact_time_boundaries_remain(self):
        self.gate.set_control('spoken:topic',str(self.now))
        self.assertEqual(self.gate.permit(self.gate.session,candidate_id='x',topic_id='topic'),'unanswered_same_topic')
        self.assertEqual(self.gate.permit(self.gate.session,candidate_id='x',topic_id='new-topic'),'allowed')
        self.gate.pause()
        self.assertEqual(self.gate.permit(self.gate.session,candidate_id='x'),'paused_or_busy')
        self.gate.set_control('pause_indefinite','0')
        for clock,allowed in [('08:59:59',False),('09:00:00',True),('22:59:59',True),('23:00:00',False)]:
            self.now=stamp('2026-10-27T'+clock)
            self.assertEqual(self.gate.continuation_allowed(self.gate.session),allowed)

    async def test_real_daily_window_permits_only_ten_minimum_spaced_starts(self):
        for i in range(10):
            self.now=stamp('2026-10-27T09:00:00')+i*5400
            self.assertEqual((await self.gate.deliver(self.gate.session,candidate_id=str(i),send=self.sent))['state'],'sent')
        self.now=stamp('2026-10-27T23:00:00')
        self.assertEqual(self.gate.permit(self.gate.session,candidate_id='11'),'quiet_hours')
        self.now=stamp('2026-10-28T09:00:00')
        self.assertEqual(self.gate.permit(self.gate.session,candidate_id='new-day'),'allowed')


if __name__=='__main__':unittest.main()
