"""Pinned AstrBot events, parser and real local OneBot HTTP; synthetic content."""
import asyncio
import importlib
from pathlib import Path
import sqlite3
import sys
import types
import unittest
from unittest.mock import patch
import test_phase3_delivery_native as base

if base.NATIVE:
    from astrbot.api.event import MessageEventResult,MessageChain
    from astrbot.api.message_components import Plain,Image,Face,Reply
    from astrbot.core.agent.message import Message,TextPart
    from integrations.phase1_guard.bubbles import MARKER,Pacing


@unittest.skipUnless(base.NATIVE,'requires pinned dependencies')
class NativeBubbleTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=base.NativeDeliveryTests.asyncSetUp
    asyncTearDown=base.NativeDeliveryTests.asyncTearDown
    event=base.NativeDeliveryTests.event
    ready=base.NativeDeliveryTests.ready

    def parser(self):
        package='bubble_native.astrbot_plugin_private_companion'
        namespace=types.ModuleType('bubble_native');namespace.__path__=[]
        module=types.ModuleType(package);module.__path__=[str(base.PROJECT/'.runtime/upstream/private_companion')]
        sys.modules['bubble_native']=namespace;sys.modules[package]=module
        native=importlib.import_module(package+'.segmented_message')
        self.context.get_all_stars=lambda:[types.SimpleNamespace(module_path=package+'.main',activated=True)]
        return native

    async def prepared(self):
        self.parser();event=self.event();await self.ready(event)
        text='已确认的第一条\n'+MARKER+'\n未必送达的第二条'
        history=Message(role='assistant',content=[TextPart(text=text)])
        history._no_save=True  # pinned Private's on_agent_done takeover
        await self.guard.bind_delivery_history(event,types.SimpleNamespace(messages=[history]),None)
        event.set_result(MessageEventResult().message(text))
        return event,history

    def history_text(self,message):return ''.join(getattr(p,'text','') for p in message.content)

    async def test_boundaries_survive_cleanup_and_native_history_has_confirmed_text(self):
        event,history=await self.prepared()
        await self.guard.preserve_semantic_boundaries(event)
        native=self.parser()
        for p in event.get_result().chain:p.text=native.sanitize_llm_segment_control_tokens(p.text)
        await self.guard.dispatch_text(event)
        self.assertEqual(len(self.calls),2)
        self.assertEqual(self.history_text(history),'已确认的第一条\n未必送达的第二条')
        self.assertNotIn(MARKER,self.history_text(history))
        self.assertFalse(history._no_save)

    async def test_second_rejected_not_in_model_history_or_successful_observation(self):
        event,history=await self.prepared();observed=[]
        async def after(payload):
            observed.append(payload)
            if payload['part_index']==0:self.mode='reject'
        self.guard.register_delivery_observer(after)
        await self.guard.dispatch_text(event)
        self.assertEqual([p['state'] for p in observed],['sent','failed'])
        self.assertIn('已确认的第一条',self.history_text(history))
        self.assertNotIn('未必送达的第二条',self.history_text(history))
        self.assertFalse(history._no_save)

    async def test_interjection_cancels_unsent_and_successor_retains_source(self):
        event,history=await self.prepared();follow=self.event('已经解决，补充一个新事实')
        async def after(payload):
            if payload['part_index']==0:await self.guard.claim_inbound(follow)
        self.guard.register_delivery_observer(after)
        await self.guard.dispatch_text(event)
        self.assertEqual(len(self.calls),1)
        self.assertNotIn('未必送达的第二条',self.history_text(history))
        result=self.guard.store.bubble_result(event.get_extra('phase3_turn_id'),'normal')
        self.assertEqual([p['state'] for p in result['parts']],['sent','cancelled'])
        await self.guard.group_input(follow)
        self.assertEqual(follow.get_extra('phase3_source_message_ids'),['1','2'])
        self.assertIn('新事实',follow.message_str)

    async def test_shutdown_cancels_waiting_bubbles_before_closing_store(self):
        event,history=await self.prepared();first=asyncio.Event()
        self.guard.pacing=Pacing(1,1)
        async def after(payload):first.set()
        self.guard.register_delivery_observer(after)
        task=asyncio.create_task(self.guard.dispatch_text(event))
        await asyncio.wait_for(first.wait(),3);await self.guard.terminate()
        self.assertTrue(task.done());self.assertEqual(len(self.calls),1)
        with sqlite3.connect(Path(self.temp.name)/'guard.sqlite3') as db:
            self.assertEqual(db.execute('SELECT state FROM bubble_parts ORDER BY part_index').fetchall(),[('sent',),('cancelled',)])

    async def test_mixed_components_remain_one_chain_and_fail_closed_without_transport(self):
        self.parser()
        for special in [Face(id=1),Image.fromURL('https://example.invalid/x.png'),Reply(id='123')]:
            result=MessageEventResult(chain=[Plain('正文 🙂 '+MARKER),special])
            parts=self.guard.semantic_parts(result)
            self.assertIs(parts[0],result);self.assertEqual(len(parts),1)
            event=self.event();await self.ready(event);event.set_result(result)
            await self.guard.dispatch_text(event)
        self.assertEqual(self.calls,[])

    async def proactive_setup(self):
        self.parser();self.guard.phase3_proactive_enabled=True
        self.guard.pacing=Pacing(0,0)
        self.context.get_platform_inst=lambda _:types.SimpleNamespace(meta=lambda:types.SimpleNamespace(name='aiocqhttp'))
        async def send(umo,message):
            await self.bot.send_private_msg(user_id=10001,message=[p.toDict() for p in message.chain])
            return True
        self.context.send_message=send

    async def test_proactive_reuses_parser_acl_ledger_and_one_candidate(self):
        await self.proactive_setup()
        with self.assertRaises(PermissionError):
            await self.guard.send_proactive('test:FriendMessage:10002','禁止',delivery_id='wrong')
        text='主动第一条\n'+MARKER+'\n主动第二条'
        result=await self.guard.send_proactive('test:FriendMessage:10001',text,delivery_id='candidate')
        again=await self.guard.send_proactive('test:FriendMessage:10001',text,delivery_id='candidate')
        self.assertEqual(result,again);self.assertEqual(len(self.calls),2)
        self.assertEqual(result['delivered_text'],'主动第一条\n主动第二条')
        rows=self.guard.store.phase3_snapshot()['deliveries']
        self.assertEqual([r['kind'] for r in rows],['proactive']*2)

    async def test_proactive_partial_failure_reports_only_sent_for_native_archive(self):
        await self.proactive_setup()
        async def after(payload):self.mode='reject'
        self.guard.register_delivery_observer(after)
        result=await self.guard.send_proactive('test:FriendMessage:10001','甲\n'+MARKER+'\n乙',delivery_id='candidate')
        self.assertEqual(result['state'],'failed');self.assertEqual(result['delivered_text'],'甲')
        self.assertEqual(result['sent_count'],1)

    async def test_disable_multibubble_is_single_clean_send(self):
        event,history=await self.prepared();self.guard.multibubble_enabled=False
        await self.guard.preserve_semantic_boundaries(event);await self.guard.dispatch_text(event)
        self.assertEqual(len(self.calls),1)
        self.assertNotIn(MARKER,self.history_text(history))

    async def test_parser_failure_falls_back_to_one_complete_acknowledged_answer(self):
        self.parser();event=self.event();await self.ready(event)
        body='技术说明。'*800
        text='先说明\n'+MARKER+'\n'+body
        history=Message(role='assistant',content=[TextPart(text=text)])
        await self.guard.bind_delivery_history(event,types.SimpleNamespace(messages=[history]),None)
        event.set_result(MessageEventResult().message(text))
        native=self.parser()
        with patch.object(native,'parse_llm_segment_control',side_effect=ValueError('synthetic')):
            await self.guard.preserve_semantic_boundaries(event)
            await self.guard.dispatch_text(event)
        self.assertEqual(len(self.calls),1)
        sent=self.calls[0][1]['message'][0]['data']['text']
        self.assertEqual(sent,'先说明\n\n'+body)
        self.assertEqual(self.history_text(history),sent)
        self.assertEqual(self.guard.store.phase3_snapshot()['deliveries'][0]['state'],'sent')

    async def test_unknown_tail_blocks_only_old_plan_and_new_input_can_be_answered(self):
        event,history=await self.prepared()
        self.guard.pacing=Pacing(0,0)
        async def after(payload):
            if payload['part_index']==0:
                self.mode='timeout';self.guard.delivery.timeout=.05
        self.guard.register_delivery_observer(after)
        await self.guard.dispatch_text(event)
        old=self.guard.store.bubble_result(event.get_extra('phase3_turn_id'),'normal')
        self.assertEqual([p['state'] for p in old['parts']],['sent','unknown'])
        self.assertNotIn('未必送达的第二条',self.history_text(history))
        self.guard.unregister_delivery_observer(after);self.mode='ack';self.guard.delivery.timeout=2
        new=self.event('新问题仍应回应');await self.ready(new)
        new.set_result(MessageEventResult().message('这是新轮的有效回复'))
        await self.guard.dispatch_text(new)
        fresh=self.guard.store.bubble_result(new.get_extra('phase3_turn_id'),'normal')
        self.assertEqual(fresh['state'],'sent')
        self.assertEqual(fresh['delivered_text'],'这是新轮的有效回复')
        self.assertEqual(self.guard.store.bubble_result(event.get_extra('phase3_turn_id'),'normal'),old)
        # UNKNOWN may time out before the peer sees bytes or after acceptance.
        # Only the two acknowledged bodies are guaranteed to have reached it.
        known=[row[1]['message'][0]['data']['text'] for row in self.calls
               if row[1]['message'][0]['data']['text'] in {'已确认的第一条','这是新轮的有效回复'}]
        self.assertEqual(known,['已确认的第一条','这是新轮的有效回复'])
        self.assertIn(len(self.calls),(2,3))

    async def test_history_projection_failure_cannot_leave_native_resend_result(self):
        event,history=await self.prepared();self.guard.pacing=Pacing(0,0)
        with patch.object(self.guard,'update_delivery_history',side_effect=sqlite3.OperationalError('synthetic')):
            with self.assertRaises(sqlite3.OperationalError):
                await self.guard.dispatch_text(event)
        # AstrBot catches decoration-hook errors and then examines event.result.
        # Leaving it populated here would bypass Guard and resend the whole plan.
        self.assertIsNone(event.get_result())
        self.assertNotIn(asyncio.current_task(),self.guard._sending_tasks)
        self.assertEqual(len(self.calls),2)

    async def test_plain_format_actual_body_ledger_and_history_match(self):
        self.guard.plain_text_enabled=True
        self.parser();event=self.event();await self.ready(event)
        text='**第一条**\n'+MARKER+'\n`**代码字面值**`'
        history=Message(role='assistant',content=[TextPart(text=text)]);history._no_save=True
        await self.guard.bind_delivery_history(event,types.SimpleNamespace(messages=[history]),None)
        event.set_result(MessageEventResult().message(text))
        await self.guard.preserve_semantic_boundaries(event);await self.guard.dispatch_text(event)
        rows=self.guard.store.phase3_snapshot()['deliveries']
        self.assertEqual([r['text'] for r in rows],['第一条','**代码字面值**'])
        self.assertEqual(self.history_text(history),'第一条\n**代码字面值**')
        bubbles=self.guard.store.bubble_result(event.get_extra('phase3_turn_id'),'normal')
        self.assertEqual(bubbles['delivered_text'],self.history_text(history))
        self.assertEqual(len(self.calls),2)

    async def test_plain_format_progress_and_proactive_same_guard_path(self):
        self.guard.plain_text_enabled=True
        self.parser();event=self.event();await self.ready(event)
        await self.guard.send_controlled(event,MessageChain([Plain('**去核对一下**')]),kind='progress')
        self.assertEqual(self.guard.store.phase3_snapshot()['deliveries'][0]['text'],'去核对一下')
        self.guard.turns.complete(event.get_extra('phase3_turn_id'))
        await self.proactive_setup()
        out=await self.guard.send_proactive('test:FriendMessage:10001','**主动正文**',delivery_id='plain-candidate')
        self.assertEqual(out['delivered_text'],'主动正文');self.assertEqual(out['state'],'sent')
