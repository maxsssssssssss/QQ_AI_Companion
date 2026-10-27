"""Approved context/budget candidate, existing ledger continuity, synthetic only."""
from copy import deepcopy
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from integrations.phase3_runtime.proactive import ProactiveGate
from integrations.phase3_runtime.settings import tuned_proactive_candidate, validate_settings, CONTINUOUS_PROACTIVE_POLICY

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('proactive_tune_fixture', ROOT/'scripts/phase3-proactive-tune.py')
tuning = importlib.util.module_from_spec(spec);spec.loader.exec_module(tuning)

def stamp(value):return datetime.fromisoformat(value+'+08:00').timestamp()

def settings():
    return dict(enabled=True, persona=True, natural_chat=True, memory=True, search=True,
        proactive=True, context_compression_fallback_turns=80, preferred_address='fixture',
        proactive_window=deepcopy(CONTINUOUS_PROACTIVE_POLICY))

class TuningCandidateTests(unittest.TestCase):
    def test_changes_only_reviewed_runtime_fields_and_keeps_current_activation(self):
        base=settings();before=deepcopy(base);new=tuned_proactive_candidate(base)
        self.assertEqual(base,before)
        self.assertEqual({k:v for k,v in new.items() if k not in ('proactive_window','proactive_history')},
                         {k:v for k,v in base.items() if k!='proactive_window'})
        self.assertTrue(new['proactive']);self.assertEqual(new['proactive_window']['max_generations_per_day'],48)
        self.assertFalse(tuned_proactive_candidate({**base,'proactive':False})['proactive'])
        self.assertEqual(validate_settings(base),base)
    def test_rejects_caps_and_leaves_trial_limits_intact(self):
        good=tuned_proactive_candidate(settings())
        for change in ({'max_per_day':13},{'max_generations_per_day':97},{'cooldown':5399},
                       {'max_generations_per_day':True},{'quiet_hours':[22,8]}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                validate_settings({**good,'proactive_window':{**good['proactive_window'],**change}})
        for h in ({'summary_limit':9,'ordinary_retention_hours':24},
                  {'summary_limit':8,'ordinary_retention_hours':25},
                  {'summary_limit':True,'ordinary_retention_hours':24}):
            with self.assertRaises(ValueError):validate_settings({**good,'proactive_history':h})
        with self.assertRaises(ValueError):
            validate_settings({**good,'proactive_window':dict(mode='trial',window_start=1,
                window_end=3600,max_per_day=8,max_generations_per_day=48,cooldown=14400,quiet_hours=[23,9])})
    def test_native_context_patch_preserves_dedup_and_cold_start(self):
        base={'secret':'synthetic-no-output','proactive_generation_history_limit':20,
            'proactive_reach_config':{'max_daily_messages':0},
            'proactive_generation_config':{'enable_llm_proactive_message':False},
            'proactive_dedup_config':{'proactive_dedup_sent_window_minutes':240}}
        new=tuning.native_context_candidate(base)
        self.assertEqual(base['proactive_generation_history_limit'],20)
        self.assertEqual(new['proactive_generation_history_limit'],80)
        self.assertEqual(new['proactive_generation_config']['proactive_history_max_chars'],16000)
        for k in ('secret','proactive_reach_config','proactive_dedup_config'):
            self.assertEqual(base[k],new[k])
        self.assertFalse(new['proactive_generation_config']['enable_llm_proactive_message'])
    def test_protected_candidate_leaves_live_bytes_and_existing_activation_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cfg=root/'live/data/config';cfg.mkdir(parents=True)
            paths=[cfg/'runtime.json',cfg/'private.json']
            for p,v in zip(paths,(settings(),{'synthetic_secret':'never-in-report'})):
                p.write_bytes(b'\xef\xbb\xbf'+json.dumps(v).encode());p.chmod(0o600)
            before=[p.read_bytes() for p in paths]
            report=tuning.save_candidate(*paths,root/'candidate')
            self.assertEqual([p.read_bytes() for p in paths],before)
            self.assertEqual(report['status'],'SAVED_NOT_APPLIED')
            self.assertTrue(report['runtime_proactive_switch_preserved'])
            self.assertNotIn('never-in-report',json.dumps(report))
            self.assertFalse(report['service_restarted']);self.assertFalse(report['production_applied'])
            for p in (root/'candidate').iterdir():self.assertEqual(p.stat().st_mode&0o777,0o600)
            for dest in (cfg/'bad',root/'candidate'):
                with self.assertRaises((ValueError,FileExistsError)):tuning.save_candidate(*paths,dest)

class TunedGateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'gate.sqlite'
        self.now=stamp('2026-10-25T09:00:00');self.gate=self.open()
    def open(self,**overrides):
        return ProactiveGate(self.path,allowed_session='test:FriendMessage:10001',enabled=True,
            clock=lambda:self.now,**{**tuned_proactive_candidate(settings())['proactive_window'],**overrides})
    async def asyncTearDown(self):self.gate.close();self.temp.cleanup()
    async def send(self):return {'state':'sent','sent_count':4,'part_count':4}
    async def test_48_decisions_no_new_evaluation_interval_and_49th_blocked(self):
        for i in range(48):
            self.assertEqual(await self.gate.reserve_generation(self.gate.session,candidate_id=str(i)),'allowed')
            self.gate.finish_generation(str(i),('skipped','deferred','failed','empty','cancelled')[i%5])
        self.assertEqual(await self.gate.reserve_generation(self.gate.session,candidate_id='49'),'generation_budget')
        self.assertEqual(self.gate.db.execute('SELECT count(*) FROM proactive_attempts').fetchone()[0],0)
    async def test_prior_counters_pause_and_unknown_are_not_reset(self):
        for i in range(5):
            await self.gate.reserve_generation(self.gate.session,candidate_id='old'+str(i))
            self.gate.finish_generation('old'+str(i),'skipped')
        await self.gate.deliver(self.gate.session,candidate_id='sent',send=self.send)
        self.gate.set_control('blocked:old-topic','1')
        self.gate.close();self.gate=self.open()
        self.assertEqual(self.gate.db.execute('SELECT count(*) FROM proactive_generations').fetchone()[0],5)
        self.assertEqual(self.gate.db.execute('SELECT count(*) FROM proactive_attempts').fetchone()[0],1)
        self.assertEqual(self.gate._get('blocked:old-topic'),'1')
        self.now+=7200
        for i in range(43):
            self.assertEqual(await self.gate.reserve_generation(self.gate.session,candidate_id='new'+str(i)),'allowed')
            self.gate.finish_generation('new'+str(i),'skipped')
        self.assertEqual(await self.gate.reserve_generation(self.gate.session,candidate_id='excess'),'generation_budget')
        self.assertEqual(self.gate.permit(self.gate.session,candidate_id='sent'),'already_attempted')
    async def test_two_hour_send_cooldown_still_also_blocks_evaluation(self):
        await self.gate.deliver(self.gate.session,candidate_id='first',send=self.send)
        self.now+=7199.999
        self.assertEqual(await self.gate.reserve_generation(self.gate.session,candidate_id='second'),'budget_or_cooldown')
        self.now+=.001
        self.assertEqual(await self.gate.reserve_generation(self.gate.session,candidate_id='second'),'allowed')
    async def test_round_limit_counts_plans_not_bubbles_including_failures(self):
        # Isolate the daily quota from the separately tested production hours.
        # The approved 09–23 window itself permits only seven two-hour starts.
        self.gate.quiet=(0,0)
        for i in range(8):
            self.now=stamp('2026-10-25T00:00:00')+i*7200
            async def send(state=('sent','failed','unknown','cancelled')[i%4]):
                return {'state':state,'sent_count':4 if state=='sent' else 0,'part_count':4}
            result=await self.gate.deliver(self.gate.session,candidate_id=str(i),send=send)
            self.assertNotEqual(result['state'],'blocked')
        self.now+=7200
        self.assertEqual(self.gate.permit(self.gate.session,candidate_id='ninth'),'budget_or_cooldown')
        self.assertEqual(self.gate.db.execute('SELECT count(*),sum(part_count),sum(sent_count) FROM proactive_attempts').fetchone(),(8,32,8))
    def test_exact_hours_and_tail_cancellation_at_2300(self):
        for time,allowed in [('08:59:59',False),('09:00:00',True),('22:59:59',True),('23:00:00',False)]:
            self.now=stamp('2026-10-25T'+time)
            self.assertEqual(self.gate.permit(self.gate.session,candidate_id='x')=='allowed',allowed)
            self.assertEqual(self.gate.continuation_allowed(self.gate.session),allowed)
    async def test_seven_real_window_opportunities_then_next_day_reset(self):
        for i in range(7):
            self.now=stamp('2026-10-25T09:00:00')+i*7200
            self.assertEqual((await self.gate.deliver(self.gate.session,candidate_id=str(i),send=self.send))['state'],'sent')
        self.now+=7200
        self.assertEqual(self.gate.permit(self.gate.session,candidate_id='eighth'),'quiet_hours')
        self.now=stamp('2026-10-26T09:00:00')
        self.assertEqual(self.gate.permit(self.gate.session,candidate_id='next-day'),'allowed')

if __name__=='__main__':unittest.main()
