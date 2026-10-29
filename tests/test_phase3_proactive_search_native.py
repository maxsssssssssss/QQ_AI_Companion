"""Fixed native Agent/Provider/Search/Fetch with synthetic loopback HTTP only."""
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
AVAILABLE = importlib.util.find_spec('astrbot') is not None
if AVAILABLE:
    import test_phase3_search_native as fixture
    from astrbot.core.agent.hooks import BaseAgentRunHooks
    from astrbot.core.agent.run_context import ContextWrapper
    from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
    from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
    from astrbot.core.provider.entities import ProviderRequest
    from integrations.phase3_runtime.autonomy_state import AutonomyState
    from integrations.phase3_search.proactive import ProactiveSearchContext, ProactiveSearchRuntime, build_proactive_toolset
    from integrations.phase3_search.policy import PublicPolicy, SEARCH, FETCH


@unittest.skipUnless(AVAILABLE, 'pinned AstrBot runtime required; T2 NOT_RUN here')
class NativeProactiveSearchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = fixture.NativeSearchTests(methodName='runTest')
        await self.fixture.asyncSetUp()
        self.now = datetime.fromisoformat('2026-10-27T12:00:00+08:00').timestamp()
        self.db = sqlite3.connect(':memory:')
        self.state = AutonomyState(self.db, clock=lambda:self.now)
        session = 'test-onebot:FriendMessage:10001'
        self.state.begin_opportunity('native-opportunity',evaluation_id='native-eval',candidate_id='native-candidate',
            session=session,deadline=self.now+1000,input_version='epoch-1')
        async def resolver(host): return ('1.1.1.1',)
        config = NS(get_config=lambda **kw:{'provider_settings':{'websearch_deepseek_key':['synthetic-search-key-not-secret']}})
        self.runtime = ProactiveSearchRuntime(
            scope=ProactiveSearchContext(session,'native-opportunity','native-eval',self.now,self.now+600,'epoch-1'),
            state=self.state,policy=PublicPolicy(scope='general_public',resolver=resolver),allowed_session=session,
            config_context=config,admit=lambda scope:'allowed',key_ready=True,enabled=True,clock=lambda:self.now)

    async def asyncTearDown(self):
        self.db.close()
        await self.fixture.asyncTearDown()

    async def run_native(self):
        # The ordinary native runner requires a route event. It is never
        # claimed as inbound, and active authorization never reads it.
        event = self.fixture.event('synthetic-proactive-route')
        context = ContextWrapper(context=NS(context=self.runtime.native_context.context.context,event=event),tool_call_timeout=10)
        req = ProviderRequest(prompt='自主探索公开资料后自行决定是否分享。',system_prompt='SYNTHETIC_PERSONA_STABLE',
            func_tool=build_proactive_toolset(self.runtime))
        runner = ToolLoopAgentRunner()
        await runner.reset(provider=self.fixture.provider,request=req,run_context=context,
            tool_executor=FunctionToolExecutor(),agent_hooks=BaseAgentRunHooks(),streaming=False,request_max_retries=1)
        async for response in runner.step_until_done(5):
            pass
        return runner, context, req

    async def test_fixed_agent_search_fetch_same_context_no_progress_or_qq(self):
        runner,context,req = await self.run_native()
        self.assertTrue(runner.done())
        self.assertEqual([row['kind'] for row in self.fixture.http],['search','fetch'])
        self.assertEqual(len(self.fixture.llm),3)
        self.assertEqual(self.fixture.guard.sent,[])
        self.assertEqual(self.fixture.runtime.metrics['progress_calls'],0)
        history=[m.model_dump(exclude_none=True) for m in context.messages]
        calls=[c['id'] for m in history for c in m.get('tool_calls',[])]
        results=[m['tool_call_id'] for m in history if m['role']=='tool']
        self.assertEqual(calls,results)
        self.assertIn('SYNTHETIC_FACT_27',json.dumps(history[-1]['content']))
        self.assertNotIn('ori_question',self.fixture.http[0]['body'])
        self.assertNotIn('SyntheticPrivateOriginal',json.dumps(self.fixture.http))
        self.assertEqual(set(t.name for t in req.func_tool.tools),{SEARCH,FETCH})
        self.assertEqual([r[0] for r in self.db.execute('SELECT state FROM autonomy_calls ORDER BY ordinal')],['succeeded','succeeded'])

    async def test_native_tool_error_is_not_retrieval_evidence(self):
        self.fixture.search_status=503
        runner,context,req = await self.run_native()
        self.assertTrue(runner.done())
        self.assertEqual(self.state.tool_sources('native-opportunity'),[])
        self.assertEqual(self.fixture.guard.sent,[])
        history=json.dumps([m.model_dump(exclude_none=True) for m in context.messages])
        self.assertNotIn('synthetic-private-error-body',history)
        self.assertEqual(self.db.execute('SELECT state FROM autonomy_calls LIMIT 1').fetchone()[0],'failed')

    async def test_native_executor_keeps_three_tool_total_bound(self):
        self.fixture.mode='repeat_search_fetch'
        runner,context,req=await self.run_native()
        self.assertTrue(runner.done())
        self.assertEqual([row['kind'] for row in self.fixture.http],['search','search','fetch'])
        self.assertEqual(len(self.fixture.llm),4)
        self.assertEqual(self.fixture.guard.sent,[])
        self.assertEqual(self.db.execute('SELECT count(*) FROM autonomy_calls').fetchone()[0],3)

    async def test_real_worker_isolated_accounting_with_native_http_fixture(self):
        import importlib.util
        root=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location('autonomy_real_fixture',root/'scripts/phase3-test-autonomy-real.py')
        probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)
        from integrations.phase3_runtime.persona import render_persona
        folder=Path(self.fixture.guard_dir.name)
        config=folder/'live-config.json';persona=folder/'live-persona.txt'
        config.write_text(json.dumps({'agent_runner':{'config':{'model':{'provider_id':'current'}}},
            'provider':[{'id':'current','provider_source_id':'official','model':'deepseek-flash','enable':True}],
            'provider_sources':[{'id':'official','type':'openai_chat_completion','enable':True,
                'api_base':'https://api.deepseek.com','key':['synthetic-not-a-secret'],'timeout':120,
                'custom_extra_body':{'max_tokens':8192,'thinking':{'type':'disabled'}}}],
            'provider_settings':{'streaming_response':False,'websearch_provider':'deepseek',
                'websearch_deepseek_key':['synthetic-not-a-secret']}}))
        persona.write_text(render_persona((root/'persona/phase3-v0.4.md').read_text(),'畅'))
        args=NS(execute=True,live_config=config,live_persona=persona,output=folder/'real-worker-fixture',
            repetitions=2,baseline=False,max_requests=256,max_tools=512)
        # Simulate the observed missing wrapper at the terminal SDK response,
        # while the fixed native Agent, provider and HTTP tool flow still run.
        original_create=self.fixture.provider.client.chat.completions.create
        async def missing_wrapper(*pos,**kw):
            response=await original_create(*pos,**kw)
            message=response.choices[0].message
            if not message.tool_calls:
                message.content=('[PHASE3_PROACTIVE:send]\n[PHASE3_TOPIC:'+json.dumps({
                    'topic':'Synthetic retrieved fixture','kind':'opinion','source_refs':[]})+'\n'+str(message.content))
            return response
        self.fixture.provider.client.chat.completions.create=missing_wrapper
        async def public_dns(host):return ('1.1.1.1',)
        from integrations.phase3_search.policy import PublicPolicy as NativePolicy
        with patch.object(probe,'CASES',({'id':'synthetic-native-chain','context':'只读合成公开问题','tools':True},)), \
                patch('astrbot.core.provider.sources.openai_source.ProviderOpenAIOfficial',return_value=self.fixture.provider), \
                patch('integrations.phase3_search.policy.PublicPolicy',side_effect=lambda **kw:NativePolicy(resolver=public_dns,**kw)):
            summary=await probe.worker(args)
        self.assertEqual(summary['status'],'COMPLETE')
        self.assertEqual(summary['exact_model_requests'],6)
        self.assertEqual(summary['exact_tool_attempts'],4)
        self.assertEqual(summary['usage_totals']['prompt_tokens'],120)
        self.assertEqual(summary['usage_totals']['completion_tokens'],60)
        self.assertTrue(summary['production_config_unchanged'])
        self.assertEqual(summary['real_qq_messages'],0)
        self.assertEqual(self.fixture.guard.sent,[])
        self.assertEqual(len(list((args.output/'audit').glob('*.json'))),12)
        private=json.loads((args.output/'synthetic-native-chain-1-candidate.json').read_text())
        self.assertIn('SYNTHETIC_FACT_27',private['output'])
        self.assertEqual(private['parsed']['metadata_repair'],'missing_topic_bracket')
        self.assertEqual(private['parsed']['reason'],'model_decision')
        self.assertEqual(private['parsed']['text'],'已核实：SYNTHETIC_FACT_27。')
        self.assertNotIn('synthetic-not-a-secret',json.dumps(private))


if __name__ == '__main__': unittest.main()
