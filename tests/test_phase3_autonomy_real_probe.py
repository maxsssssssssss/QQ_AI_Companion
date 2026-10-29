"""Real-probe contracts; never perform a model or network call."""
import ast
import importlib.util
import json
import sqlite3
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('autonomy_probe_contract',ROOT/'scripts/phase3-test-autonomy-real.py')
probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)
from integrations.phase3_runtime.persona import render_persona
from integrations.phase3_runtime.autonomy_state import AutonomyState


class AutonomyRealProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.config=Path(self.tmp.name)/'config.json';self.persona=Path(self.tmp.name)/'persona.txt'
        self.document={'agent_runner':{'config':{'model':{'provider_id':'current'}}},
            'provider':[{'id':'current','provider_source_id':'official','model':'deepseek-flash','enable':True}],
            'provider_sources':[{'id':'official','type':'openai_chat_completion','enable':True,
                'api_base':'https://api.deepseek.com','key':['synthetic-not-a-secret'],'timeout':120,
                'custom_extra_body':{'max_tokens':8192,'thinking':{'type':'disabled'}}}],
            'provider_settings':{'streaming_response':False,'websearch_provider':'deepseek',
                'websearch_deepseek_key':['synthetic-not-a-secret']}}
        self.persona.write_text(render_persona((ROOT/'persona/phase3-v0.4.md').read_text(),'畅'))
        self.save()

    def save(self):self.config.write_text(json.dumps(self.document))

    def test_plan_covers_repeats_comparable_baseline_and_independent_budget(self):
        result=probe.plan(probe.parser().parse_args([]))
        self.assertEqual(result['candidate_scenarios'],12)
        self.assertEqual(result['repetitions'],3)
        self.assertEqual(result['matched_baseline_runs'],12)
        self.assertFalse(result['old_ten_yuan_budget_reused'])
        self.assertFalse(result['production_counters_used'])
        self.assertEqual(result['model_request_safety_ceiling'],256)

    def test_actual_v04_off_provider_is_loaded_without_writing(self):
        before=self.config.read_bytes()
        provider,tools,persona=probe.live_provider(self.config,self.persona)
        self.assertEqual(provider['custom_extra_body'],{'max_tokens':8192,'thinking':{'type':'disabled'}})
        self.assertEqual(persona,self.persona.read_text())
        self.assertEqual(before,self.config.read_bytes())

    def test_targeted_followup_has_independent_explicit_scenarios(self):
        args=probe.parser().parse_args(['--no-baseline','--repetitions','3',
            '--scenarios','fetch_science_body','fetch_game_body','science_sources','uncertain_sources'])
        result=probe.plan(args)
        self.assertEqual(result['candidate_scenarios'],4)
        self.assertEqual(result['matched_baseline_runs'],0)
        self.assertEqual(len(probe.CASES),12)
        self.assertEqual(result['model_request_safety_ceiling'],256)
        self.assertEqual(probe.selected_cases(args)[0]['id'],'fetch_science_body')

    def test_duplicate_targeted_scenario_is_rejected(self):
        args=probe.parser().parse_args(['--scenarios','science_sources','science_sources'])
        with self.assertRaisesRegex(ValueError,'invalid_probe_scenarios'):probe.plan(args)

    def test_followup_runs_original_36_and_twelve_directed_without_old_baseline(self):
        args=probe.parser().parse_args(['--suite','followup','--no-baseline'])
        result=probe.plan(args)
        self.assertEqual(result['candidate_scenarios']*result['repetitions'],48)
        self.assertEqual(result['matched_baseline_runs'],0)
        self.assertEqual(result['scenario_ids'][:12],[case['id'] for case in probe.CASES])
        self.assertEqual(result['controlled_url_fixtures'],['https://httpbin.org/status/404'])

    def test_controlled_url_fixture_is_not_false_retrieval_or_production_counter(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        state=AutonomyState(db,clock=lambda:1000)
        state.begin_opportunity('case',evaluation_id='case',candidate_id='case',
            session='isolated',deadline=1600,input_version='fixture')
        case=next(case for case in probe.DIRECTED_CASES if case['id']=='fetch_public_failure')
        fixture=probe.seed_controlled_public_url(state,case=case,case_id='case',session='isolated',clock=lambda:1000)
        self.assertFalse(fixture['verified']);self.assertFalse(fixture['retrieved'])
        self.assertEqual(state.tool_sources('case'),[])
        self.assertEqual(db.execute('SELECT count(*) FROM autonomy_calls').fetchone()[0],0)
        self.assertFalse(state.list_pocket('isolated')[0]['sources'][0]['verified'])
        self.assertEqual(probe.tool_validation(case,[])['status'],'NOT_TESTED')
        self.assertEqual(probe.tool_validation(case,[{'kind':'fetch','state':'rejected','io_started':False}])['status'],'NOT_TESTED')
        self.assertEqual(probe.tool_validation(case,[{'kind':'fetch','state':'failed','io_started':True}])['status'],'PASS')

    def test_title_and_success_regressions_are_explicit_isolated_four_calls(self):
        args=probe.parser().parse_args(['--no-baseline','--repetitions','2','--scenarios',
            'fetch_title_only_regression','fetch_body_success_regression'])
        cases=probe.selected_cases(args)
        self.assertEqual(probe.plan(args)['candidate_scenarios']*args.repetitions,4)
        self.assertEqual([case['expected_tool_outcome'] for case in cases],['non_success','succeeded'])
        for case in cases:
            db=sqlite3.connect(':memory:');self.addCleanup(db.close)
            state=AutonomyState(db,clock=lambda:1000)
            state.begin_opportunity(case['id'],evaluation_id=case['id'],candidate_id=case['id'],
                session='isolated',deadline=1600,input_version='fixture')
            fixture=probe.seed_controlled_public_url(state,case=case,case_id=case['id'],session='isolated',clock=lambda:1000)
            self.assertFalse(fixture['verified']);self.assertFalse(fixture['retrieved'])
            self.assertEqual(db.execute('SELECT count(*) FROM autonomy_calls').fetchone()[0],0)
        self.assertEqual(probe.tool_validation(cases[0],[{'kind':'fetch','state':'empty','io_started':True}])['status'],'PASS')
        self.assertEqual(probe.tool_validation(cases[0],[{'kind':'fetch','state':'succeeded','io_started':True}])['status'],'FAIL')
        self.assertEqual(probe.tool_validation(cases[1],[{'kind':'fetch','state':'succeeded','io_started':True}])['status'],'PASS')

    def test_arbitrary_fixture_url_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'unapproved_controlled_public_url_fixture'):
            probe.seed_controlled_public_url(None,case={'controlled_public_url_fixture':'https://example.org/not-approved'},case_id='x',session='isolated',clock=lambda:1000)

    def test_wrong_model_or_endpoint_refuses(self):
        self.document['provider'][0]['model']='another-model';self.save()
        with self.assertRaisesRegex(ValueError,'current_official_flash'):
            probe.live_provider(self.config,self.persona)
        self.document['provider'][0]['model']='deepseek-flash'
        self.document['provider_sources'][0]['api_base']='https://unapproved.example';self.save()
        with self.assertRaisesRegex(ValueError,'current_official_flash'):
            probe.live_provider(self.config,self.persona)

    def test_wrong_persona_or_thinking_refuses(self):
        self.document['provider_sources'][0]['custom_extra_body']['thinking']={'type':'enabled'};self.save()
        with self.assertRaisesRegex(ValueError,'current_off_8192_120'):
            probe.live_provider(self.config,self.persona)
        self.document['provider_sources'][0]['custom_extra_body']['thinking']={'type':'disabled'};self.save()
        self.persona.write_text('different persona')
        with self.assertRaisesRegex(ValueError,'exact_current_v04'):
            probe.live_provider(self.config,self.persona)

    def test_missing_existing_search_key_is_not_silently_replaced(self):
        self.document['provider_settings']['websearch_deepseek_key']=[];self.save()
        with self.assertRaisesRegex(ValueError,'existing_chat_and_search_credentials'):
            probe.live_provider(self.config,self.persona)

    def test_raw_visible_content_not_rewritten_by_feature_flags(self):
        text='这只是一个脑洞，不用每句都问问题。'
        result=probe.quality_features(text,{'text':text,'reason':'model_decision'})
        self.assertEqual(result['visible_chars'],len(text))
        self.assertFalse(result['tail_question'])
        self.assertEqual(text,'这只是一个脑洞，不用每句都问问题。')

    def test_content_review_wires_original_body_projection_to_native_runner(self):
        # Dynamic header-only/body/audit behavior is covered by the runner's
        # targeted tests. This contract prevents the standalone probe from
        # silently omitting that production callback and reporting empty text.
        tree=ast.parse((ROOT/'scripts/phase3-test-autonomy-real.py').read_text())
        worker=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='worker')
        calls=[n for n in ast.walk(worker) if isinstance(n,ast.Call)
               and isinstance(n.func,ast.Name) and n.func.id=='install_runner']
        self.assertEqual(len(calls),1)
        keywords={item.arg:item.value for item in calls[0].keywords}
        for name,method in (('revision','inspect'),('finalize_revision','finalize')):
            with self.subTest(callback=name):
                self.assertIn(name,keywords)
                expected=ast.parse(f'revision.{method} if revision else None',mode='eval').body
                self.assertEqual(ast.dump(keywords[name]),ast.dump(expected))


if __name__=='__main__':unittest.main()
