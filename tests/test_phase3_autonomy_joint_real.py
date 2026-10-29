"""Joint probe safety/contract tests; never invoke real model, QQ or production."""
import importlib.util
import json
from pathlib import Path
import tempfile
import sqlite3
import unittest
import time
import sys
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('joint_probe',ROOT/'scripts/phase3-test-autonomy-joint-real.py')
probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)
from phase3_autonomy_joint_probe import STAGES,FixtureClock,visible_tail_preserved,confirmed_music_preference,actual_message_text,confirmed_history_present,send_outcome_valid,native_cleanup_replayer,native_cleanup_proof,native_cleanup_records_from_log,stage_cases
from phase3_autonomy_joint_fixture import synthetic_final
from phase3_autonomy_joint_probe import guard_receipt_clock_patch

class JointContractTests(unittest.TestCase):
    def args(self,*values):return probe.parser().parse_args(list(values))
    def test_default_is_plan_only_and_existing_budget_not_reduced(self):
        args=self.args();self.assertFalse(args.execute)
        p=probe.plan(args);self.assertEqual(p['native_opportunities'],15)
        self.assertEqual(p['host_processes'],6);self.assertEqual(p['isolated_request_limit_per_run'],256)
        self.assertEqual(p['isolated_tool_limit_per_run'],512)
        self.assertFalse(p['real_qq']);self.assertFalse(p['production_counters_used'])
    def test_synthetic_label_cannot_be_real_model_evidence(self):
        p=probe.plan(self.args('--backend','synthetic','--repetitions','1'))
        self.assertFalse(p['real_deepseek']);self.assertEqual(p['native_opportunities'],5)
    def test_targeted_interest_research_keeps_budget_and_real_native_restart(self):
        p=probe.plan(self.args('--scenario','interest-research','--repetitions','1'))
        self.assertEqual(p['native_opportunities'],2);self.assertEqual(p['host_processes'],2)
        self.assertEqual(p['isolated_request_limit_per_run'],256)
        self.assertEqual(p['isolated_tool_limit_per_run'],512)
        self.assertEqual([c[0] for c in stage_cases({'scenario':'interest-research','stage':'first'})],['controlled_explicit_preference'])
        cases=stage_cases({'scenario':'interest-research','stage':'resume'})
        self.assertEqual([c[0] for c in cases],['interest_research'])
        self.assertIn('PHASE3_INTEREST',cases[0][1]);self.assertIn('Search',cases[0][1]);self.assertIn('Fetch',cases[0][1])
        self.assertIn('不编造成功',cases[0][1]);self.assertFalse(cases[0][2])
    def test_stage_lifecycle_includes_fresh_process_restore_and_native_memory(self):
        self.assertEqual(tuple(STAGES),('first','resume'))
        self.assertEqual([c[0] for c in STAGES['first']],['open_empty','controlled_explicit_preference','cooldown_research'])
        self.assertEqual([c[0] for c in STAGES['resume']],['resume_research','recall_preference'])
        self.assertTrue(STAGES['first'][-1][2]);self.assertEqual(STAGES['first'][0][1],'')
    def test_controlled_music_expression_scope_and_source_lead_are_explicit(self):
        brief=STAGES['first'][1][1]
        self.assertIn('我喜欢',brief);self.assertIn('我偏爱',brief)
        self.assertIn('音乐类别和理由由你决定',brief)
        self.assertIn('正文第一句以',brief);self.assertIn('必须包含“音乐”',brief)
        self.assertIn('earlier unconstrained misses remain',probe.plan(self.args())['preference_coverage'])
        self.assertIn('40511715',STAGES['first'][2][1])
        self.assertIn('先Search',STAGES['first'][2][1]);self.assertIn('最多3次',STAGES['first'][2][1])
    def test_fixture_final_contrast_and_external_claim_bind_actual_body(self):
        from integrations.phase3_runtime.autonomy_contract import parse
        from integrations.phase3_runtime.autonomy_content import review_payload
        for case in ('open_empty','controlled_explicit_preference','resume_research','recall_preference'):
            with self.subTest(case=case):
                raw=synthetic_final(case,{'messages':[]});parsed=parse(raw);check=review_payload(raw)
                self.assertEqual(parsed['reason'],'model_decision')
                self.assertEqual(check['contrast']['history_index'],-1)
                self.assertIn(check['contrast']['new_body_quote'],parsed['text'])
                for claim in check['claims']:self.assertIn(claim['body_quote'],parsed['text'])
    def test_preference_pass_requires_new_confirmed_music_receipt(self):
        pref={'category':'music','source':'confirmed_visible_reply','statement':'我喜欢纯音乐',
            'confirmed_source':{'delivery_id':'new'}}
        sent={'delivery_id':'new','text':'我喜欢纯音乐。','state':'sent'}
        self.assertTrue(confirmed_music_preference([pref],[sent]))
        self.assertFalse(confirmed_music_preference([pref],[{**sent,'state':'unknown'}]))
        self.assertFalse(confirmed_music_preference([pref],[{**sent,'delivery_id':'old'}]))
        self.assertFalse(confirmed_music_preference([{**pref,'statement':'我喜欢爵士音乐'}],[sent]))
    def test_invalid_limits_rejected(self):
        for a in [('--repetitions','0'),('--max-requests','1'),('--max-tools','4')]:
            with self.assertRaises(ValueError):probe.plan(self.args(*a))
    def test_fresh_private_paths_and_readonly_inputs_required(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            args=self.args('--backend','real','--state-root',str(root/'state'),'--output',str(root/'output'),'--upstream',str(ROOT/'.runtime/upstream'))
            with self.assertRaisesRegex(ValueError,'readonly_live'):probe.validate_paths(args)
            args.backend='synthetic';s,o=probe.validate_paths(args);self.assertNotEqual(s,o)
            s.mkdir()
            with self.assertRaisesRegex(ValueError,'fresh_tmp'):probe.validate_paths(args)
    def test_state_cannot_be_production_or_nested_with_evidence(self):
        args=self.args('--backend','synthetic','--state-root','/opt/qq-companion/data','--output','/tmp/joint-new-output','--upstream','/tmp/upstream')
        with self.assertRaisesRegex(ValueError,'fresh_tmp'):probe.validate_paths(args)
        with tempfile.TemporaryDirectory() as folder:
            args.state_root=Path(folder)/'state';args.output=args.state_root/'evidence'
            with self.assertRaisesRegex(ValueError,'separate_fresh'):probe.validate_paths(args)
    def test_completed_send_cannot_hide_native_tail_truncation(self):
        self.assertFalse(visible_tail_preserved('完整正文。最后一段需要全部保留。','完整正文。最后一段需'))
        self.assertTrue(visible_tail_preserved('前一泡。\n<<PRIVATE_COMPANION_SPLIT>>\n完整尾句。','前一泡完整尾句'))
    def test_history_is_decoded_and_entire_text_retained(self):
        text='第一句。\n第二句必须仍在。'
        requests=[{'messages':[{'role':'assistant','content':text.replace('\n',' ')}]}]
        self.assertTrue(confirmed_history_present([text],requests))
        self.assertFalse(confirmed_history_present([text],[{'messages':[{'role':'assistant','content':'第一句。'}]}]))
        self.assertNotIn('\\n',actual_message_text(requests))
    def test_cooldown_send_requires_ready_pocket_no_delivery_and_metering(self):
        ev={'effective_state':'ready_unshared'};pocket=[{'id':'persisted'}];calls=[{'kind':'model'}]
        self.assertTrue(send_outcome_valid('send',ev,True,[],pocket,calls))
        self.assertFalse(send_outcome_valid('send',ev,False,[],pocket,calls))
        self.assertFalse(send_outcome_valid('send',ev,True,[{'state':'sent'}],pocket,calls))
        self.assertFalse(send_outcome_valid('send',ev,True,[],[],calls))
        self.assertFalse(send_outcome_valid('send',ev,True,[],pocket,[]))
        self.assertFalse(send_outcome_valid('send',ev,True,[],pocket,calls+[{'kind':'fetch'}]*4))
    def test_native_cleanup_is_exact_pinned_function_replay_only(self):
        source=ROOT/'.runtime/upstream/private_companion/proactive_message.py'
        if not source.is_file():self.skipTest('pinned source not installed')
        replay,digest=native_cleanup_replayer(source)
        before='这是完整保留的前句。你说我是不是在找理由'
        after=replay(before,['inverts_initiator'])
        records=[{'before':before,'after':after,'flags':['inverts_initiator']}]
        proof=native_cleanup_proof(before+'。',after.rstrip('。'),records,replay,digest)
        self.assertTrue(proof['allowed_native_cleanup']);self.assertEqual(len(digest),64)
        self.assertFalse(native_cleanup_proof(before,after[:-4],records,replay)['allowed_native_cleanup'])
        self.assertFalse(native_cleanup_proof(before,after,[],replay)['allowed_native_cleanup'])
        self.assertFalse(native_cleanup_proof(before,after,[{**records[0],'after':'人为删除后伪造'}],replay)['allowed_native_cleanup'])
        self.assertFalse(native_cleanup_proof(before,after,[{**records[0],'flags':['answers_old_context']}],replay)['allowed_native_cleanup'])
    def test_native_log_truncated_or_different_repair_cannot_excuse_tail(self):
        source=ROOT/'.runtime/upstream/private_companion/proactive_message.py'
        if not source.is_file():self.skipTest('pinned source not installed')
        replay,_=native_cleanup_replayer(source)
        log='主动消息疑似回复空气,已用本地轻量规则修正: flags=inverts_initiator before=前文被截断… after=前文'
        records=native_cleanup_records_from_log(log)
        self.assertEqual(len(records),1)
        self.assertFalse(native_cleanup_proof('完整正文不能丢失。','完整正文',records,replay)['allowed_native_cleanup'])
    def test_retains_isolated_persistence_but_not_provider_config(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);state=base/'state';out=base/'output';out.mkdir()
            (state/'joint-autonomy/audit').mkdir(parents=True)
            (state/'joint-autonomy/audit/request.json').write_text('{}')
            (state/'data').mkdir();(state/'data/cmd_config.json').write_text('DO_NOT_COPY_CONFIG')
            with sqlite3.connect(state/'data/data_v4.db') as db:
                db.execute('CREATE TABLE messages(text TEXT)');db.execute("INSERT INTO messages VALUES ('synthetic confirmed')")
            probe.retain_native_evidence(state,out)
            dest=out/'native-persistence';self.assertTrue((dest/'autonomy/audit/request.json').exists())
            self.assertFalse((dest/'data/cmd_config.json').exists())
            with sqlite3.connect(dest/'data/data_v4.db') as db:
                self.assertEqual(db.execute('SELECT text FROM messages').fetchone()[0],'synthetic confirmed')
    def test_fixture_clock_does_not_mutate_global_time(self):
        clock=FixtureClock(1793160000);first=clock();self.assertGreaterEqual(first,1793160000)
        clock.move(1793246400);self.assertGreaterEqual(clock(),1793246400)
        self.assertEqual(clock.datetime().utcoffset().total_seconds(),28800)
    def test_guard_receipt_clock_tracks_next_day_and_restores_exact_module(self):
        from integrations.phase1_guard.store import GuardStore
        module=sys.modules[GuardStore.__module__]
        original=module.now_shanghai;monotonic=time.monotonic;wall=time.time
        clock=FixtureClock(1793160000)
        store=object.__new__(GuardStore)
        with guard_receipt_clock_patch(store,clock):
            before=module.now_shanghai()
            clock.move(1793246400)
            after=module.now_shanghai()
            self.assertEqual(after[:10],clock.datetime().date().isoformat())
            self.assertNotEqual(before[:10],after[:10])
            self.assertTrue(after.endswith('+08:00'))
            self.assertIs(time.monotonic,monotonic);self.assertIs(time.time,wall)
        self.assertIs(module.now_shanghai,original)

if __name__=='__main__':unittest.main()
