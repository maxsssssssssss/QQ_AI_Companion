"""Fixed AstrBot/Guard empty-final recovery; synthetic SDK and OneBot peers."""
import asyncio
import types
import unittest
from unittest.mock import AsyncMock
import test_phase3_delivery_native as base

if base.NATIVE:
    from astrbot.api.event import MessageEventResult
    from astrbot.core.agent.message import Message, TextPart, ThinkPart
    from astrbot.core.provider.entities import LLMResponse
    from integrations.phase1_guard import recovery


@unittest.skipUnless(base.NATIVE, 'pinned AstrBot required')
class EmptyFinalTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = base.NativeDeliveryTests.asyncSetUp
    asyncTearDown = base.NativeDeliveryTests.asyncTearDown
    event = base.NativeDeliveryTests.event
    ready = base.NativeDeliveryTests.ready

    async def setup_case(self):
        self.guard.empty_recovery_enabled = True
        recovery.initialize(self.guard.store.db)
        event = self.event('只根据记忆回答'); await self.ready(event)
        messages = [Message(role='system', content='原人格及允许的合成记忆'),
                    Message(role='user', content='当前问题'),
                    Message(role='assistant', content=[], tool_calls=[{'id':'c1','type':'function',
                        'function':{'name':'memory_companion_sources','arguments':'{}'}}]),
                    Message(role='tool', tool_call_id='c1', content='合成来源'),
                    Message(role='assistant', content=[ThinkPart(think='hidden test reasoning')])]
        ctx = types.SimpleNamespace(messages=messages)
        await self.guard.retain_generation_context(event, ctx)
        choice = types.SimpleNamespace(finish_reason='stop', message=types.SimpleNamespace(content='记得你喜欢合成游戏甲。',tool_calls=None))
        result = types.SimpleNamespace(choices=[choice],usage=types.SimpleNamespace(prompt_tokens=15,completion_tokens=10))
        self.sdk = AsyncMock(return_value=result)
        self.options = []
        def options(**kw):
            self.options.append(kw)
            return types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=self.sdk)))
        provider = types.SimpleNamespace(client=types.SimpleNamespace(with_options=options),get_model=lambda:'synthetic')
        self.context.get_using_provider_async = AsyncMock(return_value=provider)
        return event,ctx,LLMResponse(role='assistant',completion_text='',reasoning_content='hidden test reasoning')

    async def test_one_continuation_keeps_context_and_uses_controlled_delivery_history(self):
        event,ctx,response = await self.setup_case()
        await self.guard.recover_empty_final(event,response)
        await self.guard.recover_empty_final(event,response)
        self.assertEqual(self.sdk.await_count,1)
        kwargs = self.sdk.call_args.kwargs
        self.assertNotIn('tools',kwargs);self.assertEqual(kwargs['max_tokens'],768)
        self.assertEqual(self.options,[{'max_retries':0,'timeout':12}])
        self.assertEqual(kwargs['messages'][-2]['content'],'合成来源')
        self.assertNotIn('hidden test reasoning',str(kwargs['messages']))
        await self.guard.bind_delivery_history(event,ctx,response)
        event.set_result(MessageEventResult().message(response.completion_text))
        await self.guard.dispatch_text(event)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(ctx.messages[-1].content[0].text,response.completion_text)
        row = self.guard.store.db.execute('SELECT * FROM response_recoveries').fetchone()
        self.assertEqual((row['state'],row['input_tokens'],row['output_tokens']),('recovered',15,10))

    async def test_ordinary_and_deliberately_suppressed_reply_never_retried(self):
        event,ctx,response = await self.setup_case()
        response.completion_text='正常答复'
        await self.guard.recover_empty_final(event,response)
        response.completion_text='';ctx.messages[-1].content=[TextPart(text='原本非空，后来被取消')]
        await self.guard.recover_empty_final(event,response)
        self.sdk.assert_not_awaited()

    async def test_empty_continuation_gets_honest_notice_not_fabricated_answer(self):
        event,ctx,response = await self.setup_case()
        self.sdk.return_value.choices[0].message.content=''
        await self.guard.recover_empty_final(event,response)
        self.assertEqual(response.completion_text,recovery.NOTICE)
        self.assertEqual(self.guard.store.db.execute('SELECT state FROM response_recoveries').fetchone()[0],'failed')

    async def test_request_failure_is_bounded_and_does_not_leak_exception(self):
        event,ctx,response = await self.setup_case()
        self.sdk.side_effect=TimeoutError('private transport details')
        await self.guard.recover_empty_final(event,response)
        self.assertEqual(response.completion_text,recovery.NOTICE)
        self.assertEqual(self.sdk.await_count,1)
        self.assertEqual(self.guard.store.db.execute('SELECT reason FROM response_recoveries').fetchone()[0],'timeout')

    async def test_interjection_cancels_unfinished_continuation_without_sending(self):
        event,ctx,response = await self.setup_case();started=asyncio.Event()
        async def slow(**kwargs):started.set();await asyncio.sleep(30)
        self.sdk.side_effect=slow
        task=asyncio.create_task(self.guard.recover_empty_final(event,response))
        await started.wait();event.set_extra('phase3_cancelled',True)
        with self.assertRaises(asyncio.CancelledError):await asyncio.wait_for(task,1)
        self.assertEqual(self.calls,[])
        self.assertEqual(self.guard.store.db.execute('SELECT state FROM response_recoveries').fetchone()[0],'cancelled')

    async def test_existing_unknown_send_is_not_repeated(self):
        event,ctx,response = await self.setup_case()
        from astrbot.api.event import MessageChain
        self.mode='timeout';self.guard.delivery.timeout=5
        sending=asyncio.create_task(self.guard.send_controlled(event,MessageChain().message('已有尝试')))
        async def received():
            while not self.calls:await asyncio.sleep(.01)
        # Cancel after actual receipt but before the deliberately delayed ack.
        # This proves UNKNOWN without assuming localhost connects in 0.5s on
        # a busy test host. Deadline handling has its separate test below.
        await asyncio.wait_for(received(),2)
        sending.cancel()
        with self.assertRaises(asyncio.CancelledError):await sending
        self.assertEqual(self.guard.store.db.execute('SELECT state FROM deliveries').fetchone()[0],'unknown')
        await self.guard.recover_empty_final(event,response)
        self.sdk.assert_not_awaited();self.assertEqual(len(self.calls),1)

    async def test_daily_cap_and_restart_unknown_remain_claimed(self):
        event,ctx,response = await self.setup_case()
        tid=event.get_extra('phase3_turn_id')
        self.assertTrue(recovery.claim(self.guard.store.db,tid));recovery.initialize(self.guard.store.db)
        self.assertFalse(recovery.claim(self.guard.store.db,tid))
        self.assertEqual(self.guard.store.db.execute('SELECT state FROM response_recoveries').fetchone()[0],'unknown')
        self.guard.turns.complete(tid)
        for _ in range(3):
            e=self.event();await self.ready(e);self.assertTrue(recovery.claim(self.guard.store.db,e.get_extra('phase3_turn_id')))
            self.guard.turns.complete(e.get_extra('phase3_turn_id'))
        e=self.event();await self.ready(e)
        self.assertFalse(recovery.claim(self.guard.store.db,e.get_extra('phase3_turn_id')))

    async def test_tools_and_media_and_unapproved_switch_do_not_recover(self):
        event,ctx,response = await self.setup_case()
        response.tools_call_name=['memory_companion_sources']
        await self.guard.recover_empty_final(event,response)
        response.tools_call_name=[];self.guard.empty_recovery_enabled=False
        await self.guard.recover_empty_final(event,response)
        self.sdk.assert_not_awaited()

    async def test_media_is_not_reinterpreted_as_missing_text(self):
        event,ctx,response = await self.setup_case()
        from astrbot.api.event import MessageChain
        from astrbot.api.message_components import Image
        response.result_chain=MessageChain([Image.fromURL('https://example.invalid/synthetic.png')])
        await self.guard.recover_empty_final(event,response)
        self.sdk.assert_not_awaited()

    async def test_wall_deadline_cancels_slow_sdk_task(self):
        event,ctx,response = await self.setup_case();cancelled=asyncio.Event()
        async def slow(**kwargs):
            try:await asyncio.sleep(30)
            finally:cancelled.set()
        self.sdk.side_effect=slow
        provider=await self.context.get_using_provider_async(event.unified_msg_origin)
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(recovery.continue_once(provider,ctx.messages,lambda:True,timeout=.05),1)
        self.assertTrue(cancelled.is_set())

    async def test_native_image_history_reaches_same_sdk_for_text_continuation(self):
        event,ctx,response = await self.setup_case()
        import base64, io
        from PIL import Image
        from astrbot.core.agent.message import ImageURLPart
        image = io.BytesIO();Image.new('RGB',(2,2),'red').save(image,format='PNG')
        data = 'data:image/png;base64,'+base64.b64encode(image.getvalue()).decode()
        ctx.messages.insert(1,Message(role='user',content=[ImageURLPart(
            image_url=ImageURLPart.ImageURL(url=data))]))
        await self.guard.recover_empty_final(event,response)
        self.assertEqual(self.sdk.await_count,1)
        rows=self.sdk.call_args.kwargs['messages']
        self.assertEqual(rows[1]['content'][0]['image_url']['url'],data)
        self.assertEqual(self.guard.store.db.execute('SELECT state FROM response_recoveries').fetchone()[0],'recovered')

    async def test_expired_history_does_not_prevent_next_text_answer(self):
        event,ctx,response = await self.setup_case()
        from astrbot.core.agent.message import ImageURLPart
        ctx.messages.insert(1,Message(role='user',content=[ImageURLPart(
            image_url=ImageURLPart.ImageURL(url='/unavailable/synthetic.png'))]))
        await self.guard.recover_empty_final(event,response)
        self.assertEqual(self.sdk.await_count,1)
        self.assertIn('历史消息中的图片',self.sdk.call_args.kwargs['messages'][1]['content'])
        self.assertEqual(response.completion_text,'记得你喜欢合成游戏甲。')


if __name__ == '__main__':unittest.main()
