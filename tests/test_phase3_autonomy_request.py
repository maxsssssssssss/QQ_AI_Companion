"""Provider-boundary accounting with the actual autonomy ledger, no network."""
import asyncio
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from integrations.phase3_runtime.autonomy_request import install_runner, OpportunityStopped
from integrations.phase3_runtime.autonomy_state import AutonomyState

try:
    from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial as FixedProvider
except ImportError:
    FixedProvider=None


class Runner:
    def __init__(self, responses=(), *, operation=None):
        self.provider=SimpleNamespace(provider_config={
            'id':'same-provider','model':'deepseek-flash','timeout':120,'max_tokens':8192,
            'extra_body':{'thinking':{'type':'disabled'}},'api_key':'synthetic-secret'})
        self.req=SimpleNamespace(model='deepseek-flash',func_tool=None)
        self.run_context=SimpleNamespace(messages=[{'role':'system','content':'Actual complete persona'},
            {'role':'user','content':'Synthetic opportunity'}])
        self.request_max_retries=3;self.fallback_providers=['do-not-use'];self.streaming=True
        self.responses=responses;self.operation=operation;self.requests=0;self.http_requests=0;self.calls=[]
        self.provider.client=SimpleNamespace(max_retries=0,chat=SimpleNamespace(
            completions=SimpleNamespace(create=self.create)))

    def _sanitize_contexts_for_provider(self,messages):
        return list(messages)

    async def create(self,**kwargs):
        self.http_requests+=1
        if self.operation:await self.operation()
        item=self.responses[0] if self.responses else response('')
        message=SimpleNamespace(content=item.completion_text,tool_calls=[
            {'id':'call-'+str(i),'function':{'name':name,'arguments':'{}'}}
            for i,name in enumerate(item.tools_call_name)])
        return SimpleNamespace(choices=[SimpleNamespace(message=message,finish_reason=item.finish_reason)],
            usage=SimpleNamespace(prompt_tokens=12,completion_tokens=8,total_tokens=20,
                prompt_tokens_details=SimpleNamespace(cached_tokens=3),
                completion_tokens_details=SimpleNamespace(reasoning_tokens=0)),llm=item)

    async def _iter_llm_responses(self,*args,**kwargs):
        self.requests+=1;self.calls.append((args,kwargs))
        raw=await self.provider.client.chat.completions.create(model=self.req.model,
            messages=self._sanitize_contexts_for_provider(self.run_context.messages),
            stream=self.streaming,max_tokens=8192,extra_body=self.provider.provider_config['extra_body'])
        yield raw.llm


def response(text='[PHASE3_PROACTIVE:send]\nSynthetic reply',tools=()):
    return SimpleNamespace(completion_text=text,usage=SimpleNamespace(input_tokens=12,
        output_tokens=8,input_cached_tokens=3,reasoning_tokens=0),
        tools_call_name=list(tools),tools_call_args=[{'query':'synthetic public topic'}]*len(tools),
        finish_reason='tool_calls' if tools else 'stop',reasoning_content='must never appear in audit')


class AutonomyRequestTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.db=sqlite3.connect(self.root/'gate.sqlite')
        self.addCleanup(self.db.close)
        self.now=datetime.fromisoformat('2026-10-28T10:00:00+08:00').timestamp()
        self.state=AutonomyState(self.db,clock=lambda:self.now,audit_dir=self.root/'audit')
        self.state.begin_opportunity('op',evaluation_id='eval',candidate_id='native-candidate',
            session='test:FriendMessage:10001',deadline=self.now+3600,input_version='v1')
        self.reason='allowed'

    def install(self,runner,**kwargs):
        install_runner(runner,state=self.state,evaluation_id='eval',admit=lambda:self.reason,**kwargs)
        return runner

    async def collect(self,runner):
        return [item async for item in runner._iter_llm_responses('same-arg',flag='same-value')]

    def calls(self):
        return self.db.execute('SELECT state,usage FROM autonomy_calls ORDER BY started,id').fetchall()

    def audits(self,kind):
        paths=self.db.execute('SELECT path FROM autonomy_audits WHERE kind=? AND state=?',(kind,'retained')).fetchall()
        return [json.loads((self.root/'audit'/p[0]).read_text()) for p in paths]

    async def test_each_real_continuation_charged_once_same_runner_provider_and_parameters(self):
        runner=Runner([response()]);provider=runner.provider;self.install(runner)
        for _ in range(3):self.assertEqual(len(await self.collect(runner)),1)
        self.assertIs(runner.provider,provider);self.assertEqual(runner.requests,3)
        self.assertEqual(runner.request_max_retries,1);self.assertEqual(runner.fallback_providers,[])
        self.assertFalse(runner.streaming);self.assertEqual(len(self.calls()),3)
        self.assertTrue(all(row[0]=='returned' for row in self.calls()))
        self.assertEqual(runner.calls,[(('same-arg',),{'flag':'same-value'})]*3)
        self.assertEqual(self.state.summary()['usage']['prompt_tokens'],36)
        self.assertEqual(self.state.summary()['usage']['completion_tokens'],24)

    async def test_multiple_sdk_requests_inside_one_provider_invocation_are_each_counted(self):
        runner=Runner([response()])
        async def native_recovery_path(*args,**kwargs):
            for i in range(2):
                raw=await runner.provider.client.chat.completions.create(
                    model='deepseek-flash',messages=[{'role':'user','content':'Payload revision '+str(i)}],stream=False)
            yield raw.llm
        runner._iter_llm_responses=native_recovery_path;self.install(runner)
        await self.collect(runner)
        self.assertEqual(runner.http_requests,2);self.assertEqual(len(self.calls()),2)
        self.assertEqual([a['messages'][0]['content'] for a in self.audits('request')],['Payload revision 0','Payload revision 1'])

    async def test_passive_same_client_calls_do_not_use_autonomy_budget_or_modify_payload(self):
        runner=self.install(Runner([response()]))
        await runner.provider.client.chat.completions.create(model='passive-model',messages=[],stream=True)
        self.assertEqual(self.calls(),[]);self.assertEqual(runner.http_requests,1)
        await self.collect(runner)
        await runner.provider.client.chat.completions.create(model='passive-model',messages=[],stream=True)
        self.assertEqual(len(self.calls()),1);self.assertEqual(runner.http_requests,3)

    async def test_sdk_hidden_retries_or_provider_replacement_fail_closed(self):
        runner=Runner();runner.provider.client.max_retries=2
        with self.assertRaisesRegex(OpportunityStopped,'sdk_retries_must_be_zero'):self.install(runner)
        runner=Runner([response()]);self.install(runner);runner.provider=SimpleNamespace()
        with self.assertRaisesRegex(OpportunityStopped,'unexpected_autonomy_provider'):await self.collect(runner)
        self.assertEqual(self.calls(),[])

    async def test_plugin_context_reload_rebinds_original_client_without_double_charge(self):
        from contextvars import ContextVar
        from integrations.phase3_runtime import autonomy_request as module
        first=self.install(Runner([response()]));await self.collect(first)
        second=Runner();second.provider=first.provider
        replacement=ContextVar('synthetic-reloaded-module',default=None)
        with patch.object(module,'_ACTIVE',replacement):
            self.install(second);await self.collect(second)
            self.assertIs(first.provider.client.chat.completions._phase3_autonomy_context,replacement)
        self.assertEqual(first.http_requests,2);self.assertEqual(len(self.calls()),2)

    async def test_fixed_astrbot_provider_and_real_openai_sdk_with_mock_transport(self):
        if FixedProvider is None:self.skipTest('requires pinned AstrBot on PYTHONPATH')
        import httpx
        payloads=[]
        async def transport(request):
            payloads.append(json.loads(request.content))
            return httpx.Response(200,json={'id':'synthetic-native-completion','object':'chat.completion',
                'created':1800000000,'model':'deepseek-flash','choices':[{'index':0,'finish_reason':'stop',
                    'message':{'role':'assistant','content':'[PHASE3_PROACTIVE:send]\nSynthetic native reply'}}],
                'usage':{'prompt_tokens':20,'completion_tokens':10,'total_tokens':30,
                         'prompt_tokens_details':{'cached_tokens':4}}})
        class IsolatedProvider(FixedProvider):
            def _create_http_client(self,config):
                return httpx.AsyncClient(transport=httpx.MockTransport(transport))
        provider=IsolatedProvider({'id':'synthetic-fixed-provider','type':'openai_chat_completion',
            'model':'deepseek-flash','key':['synthetic-test-only'], 'timeout':120,
            'api_base':'https://api.deepseek.com/v1',
            'custom_extra_body':{'thinking':{'type':'disabled'},'max_tokens':8192}}, {})
        self.addAsyncCleanup(provider.client.close)
        runner=Runner();runner.provider=provider
        runner.run_context.messages=[{'role':'system','content':'Actual persona'},
            {'role':'assistant','content':''},{'role':'tool','tool_call_id':'orphan','content':'Must be removed'},
            {'role':'user','content':'Synthetic user opportunity'}]
        async def native_provider(*args,**kwargs):
            yield await provider.text_chat(contexts=runner.run_context.messages,
                model=runner.req.model,request_max_retries=runner.request_max_retries)
        runner._iter_llm_responses=native_provider;self.install(runner)
        result=await self.collect(runner)
        self.assertEqual(result[0].completion_text,'[PHASE3_PROACTIVE:send]\nSynthetic native reply')
        self.assertEqual(len(payloads),1)
        self.assertEqual(payloads[0]['messages'],self.audits('request')[0]['messages'])
        self.assertEqual(len(payloads[0]['messages']),2)
        self.assertEqual(payloads[0]['thinking'],{'type':'disabled'});self.assertEqual(payloads[0]['max_tokens'],8192)
        self.assertEqual(self.state.summary()['usage'],{'prompt_tokens':20,'completion_tokens':10,'total_tokens':30,'cached_tokens':4})
        await provider.text_chat(contexts=[{'role':'user','content':'Separate passive'}],request_max_retries=1)
        self.assertEqual(len(payloads),2);self.assertEqual(len(self.calls()),1)

    async def test_final_request_audited_before_io_visible_output_excludes_reasoning(self):
        async def before_io():
            self.assertEqual(len(self.audits('request')),1)
            self.assertEqual(self.calls()[0][0],'running')
        runner=self.install(Runner([response()],operation=before_io))
        runner.run_context.messages.append({'role':'tool','tool_call_id':'real-context-id','content':'Tool already returned'})
        await self.collect(runner)
        request=self.audits('request')[0]
        self.assertEqual(request['messages'],runner.run_context.messages)
        self.assertEqual(request['model'],'deepseek-flash')
        self.assertEqual(request['parameters']['extra_body'],{'thinking':{'type':'disabled'}})
        self.assertFalse(request['stream']);self.assertEqual(request['sdk_retries'],0)
        self.assertNotIn('synthetic-secret',json.dumps(request))
        output=self.audits('output')[0]
        self.assertEqual(output['choices'][0]['content'],runner.responses[0].completion_text)
        self.assertNotIn('must never appear',json.dumps(output))

    async def test_budget_exhaustion_stops_before_provider(self):
        self.state.model_limit=1
        runner=self.install(Runner([response()]))
        await self.collect(runner)
        with self.assertRaisesRegex(OpportunityStopped,'model_request_budget'):await self.collect(runner)
        self.assertEqual(runner.http_requests,1);self.assertEqual(len(self.calls()),1)

    async def test_current_admission_stops_before_request_and_does_not_count_new_request(self):
        runner=self.install(Runner([response()]))
        for reason in ('quiet_hours','superseded','paused_or_busy'):
            self.reason=reason
            with self.subTest(reason=reason),self.assertRaises(OpportunityStopped):await self.collect(runner)
        self.assertEqual(runner.http_requests,0);self.assertEqual(self.calls(),[])

    async def test_new_input_or_night_cancels_inflight_provider_and_settles_ledger(self):
        cancelled=[];started=asyncio.Event()
        async def pending():
            started.set()
            try:await asyncio.sleep(10)
            finally:cancelled.append(True)
        runner=self.install(Runner([response()],operation=pending))
        task=asyncio.create_task(self.collect(runner));await started.wait();self.reason='quiet_hours'
        with self.assertRaisesRegex(OpportunityStopped,'quiet_hours'):await task
        self.assertEqual(cancelled,[True]);self.assertEqual(self.calls()[0][0],'cancelled')

    async def test_admission_rechecked_after_provider_finished_before_output_or_interest(self):
        callbacks=[]
        async def race():self.reason='superseded'
        async def interest(text):callbacks.append(text);return 'sent'
        runner=self.install(Runner([response('[PHASE3_INTEREST]\nSpecific interest',('search',))],operation=race),interest=interest)
        with self.assertRaisesRegex(OpportunityStopped,'superseded'):await self.collect(runner)
        self.assertEqual(callbacks,[]);self.assertEqual(self.calls()[0][0],'cancelled')

    async def test_interest_only_with_tools_and_send_callback_returns_explicit_state(self):
        callbacks=[]
        async def interest(text):callbacks.append(text);return 'sent'
        runner=self.install(Runner([response('[PHASE3_INTEREST]\nSpecific interest',('search',))]),interest=interest)
        delivered=(await self.collect(runner))[0]
        self.assertEqual(callbacks,['Specific interest']);self.assertIn('sent',delivered.completion_text)
        self.assertEqual(self.audits('output')[0]['choices'][0]['content'],'[PHASE3_INTEREST]\nSpecific interest')
        runner.responses=[response('[PHASE3_INTEREST]\nMust not auto-send')]
        await self.collect(runner);self.assertEqual(callbacks,['Specific interest'])
        runner.responses=[response('[PHASE3_INTEREST]\nDenied expression',('fetch',))]
        async def denied(text):callbacks.append(text);return 'cooldown'
        # Existing instance wrapper is idempotent, never stacks reservations.
        install_runner(runner,state=self.state,evaluation_id='eval',admit=lambda:self.reason,interest=denied)
        self.assertEqual(len(self.calls()),2)

    async def test_interest_callback_denied_does_not_claim_contacted(self):
        async def interest(text):return 'cooldown'
        runner=self.install(Runner([response('[PHASE3_INTEREST]\nSpecific question',('search',))]),interest=interest)
        text=(await self.collect(runner))[0].completion_text
        self.assertIn('保持静默探索',text);self.assertNotIn('Specific question',text)

    async def test_ledger_reservation_error_fails_closed_before_io(self):
        runner=self.install(Runner([response()]))
        def fail(*args,**kwargs):raise sqlite3.OperationalError('synthetic reservation failure')
        self.state.reserve_model_request=fail
        with self.assertRaises(sqlite3.OperationalError):await self.collect(runner)
        self.assertEqual(runner.http_requests,0)

    async def test_request_audit_error_and_incomplete_audit_never_start_provider_or_leave_running(self):
        runner=self.install(Runner([response()]))
        original=self.state.write_audit
        def fail(*args,**kwargs):raise OSError('synthetic audit failure')
        self.state.write_audit=fail
        with self.assertRaises(Exception):await self.collect(runner)
        self.assertEqual(runner.http_requests,0);self.assertFalse(any(row[0]=='running' for row in self.calls()))
        self.state.write_audit=lambda *args,**kwargs:{'complete':False,'reason':'audit_entry_capacity'}
        with self.assertRaises(Exception):await self.collect(runner)
        self.assertEqual(runner.http_requests,0);self.assertFalse(any(row[0]=='running' for row in self.calls()))
        self.state.write_audit=original

    async def test_iterator_creation_failure_settles_reserved_call(self):
        runner=Runner()
        def fail(*args,**kwargs):raise RuntimeError('synthetic iterator creation failure')
        runner._iter_llm_responses=fail;self.install(runner)
        with self.assertRaises(RuntimeError):await self.collect(runner)
        self.assertEqual(len(self.calls()),0)

    async def test_output_audit_failure_settles_reserved_call(self):
        runner=self.install(Runner([response()]))
        original=self.state.write_audit
        def audit(evaluation,kind,payload):
            if kind!='request':raise OSError('synthetic output audit error')
            return original(evaluation,kind,payload)
        self.state.write_audit=audit
        with self.assertRaises(OpportunityStopped):await self.collect(runner)
        self.assertEqual(self.calls()[0][0],'failed')

    async def test_iterator_close_error_still_settles_call(self):
        class Iterator:
            def __aiter__(self):return self
            async def __anext__(self):raise RuntimeError('synthetic provider failure')
            async def aclose(self):raise RuntimeError('synthetic close failure')
        runner=Runner();runner._iter_llm_responses=lambda *args,**kwargs:Iterator();self.install(runner)
        with self.assertRaises(RuntimeError):await self.collect(runner)
        self.assertEqual(self.calls(),[])

    async def test_iterator_close_failure_after_http_success_does_not_lose_actual_call(self):
        runner=Runner([response()])
        class Iterator:
            sent=False
            def __aiter__(self):return self
            async def __anext__(self):
                if self.sent:raise StopAsyncIteration
                self.sent=True
                raw=await runner.provider.client.chat.completions.create(model='deepseek-flash',messages=[],stream=False)
                return raw.llm
            async def aclose(self):raise RuntimeError('synthetic close failure')
        runner._iter_llm_responses=lambda *args,**kwargs:Iterator();self.install(runner)
        with self.assertRaisesRegex(RuntimeError,'synthetic close failure'):await self.collect(runner)
        self.assertEqual(self.calls()[0][0],'returned')

    async def test_external_cancellation_finishes_call_and_closes_native_iterator(self):
        started=asyncio.Event()
        async def pending():started.set();await asyncio.sleep(10)
        runner=self.install(Runner([response()],operation=pending));task=asyncio.create_task(self.collect(runner))
        await started.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(self.calls()[0][0],'cancelled')

    async def test_empty_response_and_provider_exception_count_without_retry(self):
        runner=self.install(Runner([response('')]))
        self.assertEqual(len(await self.collect(runner)),1)
        self.assertEqual(self.calls()[0][0],'empty')
        async def fail():raise TimeoutError('synthetic timeout')
        runner.operation=fail
        with self.assertRaisesRegex(OpportunityStopped,'provider_timeout'):await self.collect(runner)
        self.assertEqual(runner.requests,2)
        self.assertFalse(any(row[0]=='running' for row in self.calls()))


if __name__=='__main__':unittest.main()
