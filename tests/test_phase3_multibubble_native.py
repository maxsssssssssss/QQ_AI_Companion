"""Pinned AstrBot events, parser and real local OneBot HTTP; synthetic content."""
import asyncio
import hashlib
import importlib
import json
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

    async def v3_prepared(self, text='今天可算忙完了！今晚可以安心歇一会儿了。', kind='normal'):
        self.parser();self.guard.casual_split_fallback_enabled=True
        self.guard.pacing=Pacing(0,0)
        event=self.event('合成闲聊');await self.ready(event)
        event.set_extra('phase3_delivery_kind',kind)
        history=Message(role='assistant',content=[TextPart(text=text)])
        history._no_save=True
        await self.guard.bind_delivery_history(event,types.SimpleNamespace(messages=[history]),None)
        await self.guard.recover_empty_final(event,types.SimpleNamespace(
            role='assistant',completion_text=text,tools_call_name=[]))
        event.set_result(MessageEventResult().message(text))
        return event,history

    async def test_v3_no_marker_fallback_survives_native_cleanup_and_projects_two_acks(self):
        event,history=await self.v3_prepared()
        await self.guard.preserve_semantic_boundaries(event)
        # Heuristic eligibility is preserved early; the actual split happens
        # once after all whole-message cleanup, so spacing cannot be stripped
        # independently at the start/end of a new component.
        self.assertEqual(len(event.get_result().chain),1)
        for part in event.get_result().chain:
            part.text=self.parser().sanitize_llm_segment_control_tokens(part.text)
        await self.guard.dispatch_text(event)
        expected=['今天可算忙完了！','今晚可以安心歇一会儿了。']
        self.assertEqual([r[1]['message'][0]['data']['text'] for r in self.calls],expected)
        rows=self.guard.store.phase3_snapshot()['deliveries']
        self.assertEqual([(r['part_index'],r['state'],r['text']) for r in rows],
                         [(i,'sent',text) for i,text in enumerate(expected)])
        self.assertEqual(self.history_text(history),'\n'.join(expected))

    async def test_v31_complete_three_and_four_sentences_each_keep_native_guard_receipts(self):
        sentences=['今天可算忙完了！','今晚可以安心歇一会儿了。',
                   '明天的事就留给明天吧。','这会儿只想陪你随便聊聊。']
        for count in (3,4):
            with self.subTest(count=count):
                expected=sentences[:count];text=''.join(expected);before=len(self.calls)
                self.assertLessEqual(len(text),100)
                event,history=await self.v3_prepared(text)
                await self.guard.preserve_semantic_boundaries(event)
                self.assertEqual(len(event.get_result().chain),1)
                for part in event.get_result().chain:
                    part.text=self.parser().sanitize_llm_segment_control_tokens(part.text)
                await self.guard.dispatch_text(event)
                actual=[row[1]['message'][0]['data']['text'] for row in self.calls[before:]]
                self.assertEqual(actual,expected);self.assertEqual(''.join(actual),text)
                result=self.guard.store.bubble_result(event.get_extra('phase3_turn_id'),'normal')
                self.assertEqual([(p['part_index'],p['state'],
                                   self.guard.store.get_delivery(p['delivery_id'])['text']) for p in result['parts']],
                                 [(i,'sent',part) for i,part in enumerate(expected)])
                self.assertEqual(self.history_text(history),'\n'.join(expected))

    async def test_v31_short_sentences_merge_forward_and_trailing_short_merges_back(self):
        first='今天可算忙完了！';second='今晚可以安心歇一会儿了。'
        cases=[('终于忙完啦！'+second,['终于忙完啦！'+second]),
               ('好耶！'+first+second,['好耶！'+first,second]),
               (first+'好耶！'+second,[first,'好耶！'+second]),
               (first+second+'嗯。',[first,second+'嗯。'])]
        for text,expected in cases:
            with self.subTest(text=text):
                before=len(self.calls);event,history=await self.v3_prepared(text)
                await self.guard.preserve_semantic_boundaries(event);await self.guard.dispatch_text(event)
                actual=[row[1]['message'][0]['data']['text'] for row in self.calls[before:]]
                self.assertEqual(actual,expected);self.assertEqual(''.join(actual),text)
                self.assertEqual(self.history_text(history),'\n'.join(expected))

    async def test_v31_pacing_waits_only_after_first_ack_and_is_bounded_one_to_two_seconds(self):
        text='今天可算忙完了！今晚可以安心歇一会儿了。明天的事就留给明天吧。'
        event,history=await self.v3_prepared(text)
        self.guard.pacing=Pacing()
        self.assertEqual((self.guard.pacing.minimum,self.guard.pacing.maximum),(1,2))
        waits=[]
        async def fake_interval(delay,is_current,**kwargs):
            waits.append((len(self.calls),delay))
            return is_current()
        with patch('integrations.phase1_guard.bubbles.wait_between',side_effect=fake_interval):
            await self.guard.preserve_semantic_boundaries(event);await self.guard.dispatch_text(event)
        self.assertEqual([count for count,_ in waits],[1,2])
        self.assertTrue(all(1<=delay<=2 for _,delay in waits))
        self.assertEqual(len(self.calls),3)
        self.assertEqual(self.history_text(history),'\n'.join(
            row[1]['message'][0]['data']['text'] for row in self.calls))

    async def test_v31_native_terminal_period_removal_maps_boundaries_without_restoring_text(self):
        text='今天真的忙完啦！🎉  今晚可以安心歇一会儿了。'
        cleaned=text[:-1]
        expected=['今天真的忙完啦！🎉  ','今晚可以安心歇一会儿了']
        event,history=await self.v3_prepared(text)
        self.guard.plain_text_enabled=True
        # Pinned Private 6.7.0's on_llm_response drops the terminal Chinese
        # period before decoration. Map validated offsets to the cleaned text;
        # never resurrect the period or any other removed source character.
        event.set_result(MessageEventResult().message(cleaned))
        await self.guard.preserve_semantic_boundaries(event);await self.guard.dispatch_text(event)
        actual=[row[1]['message'][0]['data']['text'] for row in self.calls]
        self.assertEqual(actual,expected);self.assertEqual(''.join(actual),cleaned)
        self.assertNotEqual(''.join(actual),text)
        self.assertEqual(self.history_text(history),'\n'.join(expected))

    async def test_v31_candidate_rejects_text_changes_trim_crlf_empty_drop_stop_and_cancel(self):
        raw='今天可算忙完了！今晚可以安心歇一会儿了。'
        crlf=raw.replace('！','！\r\n')
        cases=[('changed',raw,'今天终于轻松了！今晚可以安心歇一会儿了。'),
               ('trim','  '+raw+'  ',raw),
               ('crlf',crlf,crlf.replace('\r\n','\n')),
               ('empty',raw,''),('dropped',raw,None),
               ('stopped',raw,raw),('cancelled',raw,raw)]
        for mode,source,cleaned in cases:
            with self.subTest(mode=mode):
                before=len(self.calls);event,history=await self.v3_prepared(source)
                self.guard.plain_text_enabled=True
                if mode=='dropped':
                    event.clear_result()
                else:event.set_result(MessageEventResult().message(cleaned))
                if mode=='stopped':event.stop_event()
                if mode=='cancelled':event.set_extra('phase3_cancelled',True)
                await self.guard.preserve_semantic_boundaries(event);await self.guard.dispatch_text(event)
                actual=[row[1]['message'][0]['data']['text'] for row in self.calls[before:]]
                expected=[cleaned] if mode in {'changed','trim','crlf'} else []
                self.assertEqual(actual,expected)
                if expected:
                    self.assertEqual(''.join(actual),cleaned)
                    self.assertEqual(self.history_text(history),cleaned)

    async def test_v3_marker_priority_keeps_three_parts_and_fallback_can_be_disabled(self):
        text='今天可算忙完了！今晚可以安心歇一会儿了。\n'+MARKER+'\n真开心。\n'+MARKER+'\n好呀🙂'
        event,_=await self.v3_prepared(text)
        await self.guard.preserve_semantic_boundaries(event);await self.guard.dispatch_text(event)
        self.assertEqual(len(self.calls),3)
        self.assertEqual(self.calls[0][1]['message'][0]['data']['text'],'今天可算忙完了！今晚可以安心歇一会儿了。')
        event,_=await self.v3_prepared()
        self.guard.casual_split_fallback_enabled=False
        await self.guard.preserve_semantic_boundaries(event);await self.guard.dispatch_text(event)
        self.assertEqual(len(self.calls),4)
        self.assertEqual(self.calls[-1][1]['message'][0]['data']['text'],'今天可算忙完了！今晚可以安心歇一会儿了。')

    async def test_v3_search_progress_and_final_exclude_fallback_before_final_kind_changes(self):
        text='今天可算忙完了！今晚可以安心歇一会儿了。'
        for kind,search in [('progress',False),('final',False),('normal',True)]:
            event,_=await self.v3_prepared(text,kind)
            if search:event.set_extra('phase3_search_executed',True)
            await self.guard.preserve_semantic_boundaries(event)
            if search:event.set_extra('phase3_delivery_kind','final')
            await self.guard.dispatch_text(event)
        self.assertEqual([r[1]['message'][0]['data']['text'] for r in self.calls],[text]*3)

    async def test_v3_structured_reply_cannot_be_split_after_markdown_cleanup(self):
        for text in ['好呀🙂','> 今天可算忙完了！今晚可以安心歇一会儿了。',
                     '```text\n今天可算忙完了！今晚可以安心歇一会儿了。\n```',
                     '查阅 https://example.invalid/doc 。下一步请对照文档。']:
            event,history=await self.v3_prepared(text)
            await self.guard.preserve_semantic_boundaries(event)
            self.assertEqual(len(event.get_result().chain),1)
            # Simulate a later optional decorator stripping source structure;
            # Guard must not re-classify the cleaned text as casual chat.
            from integrations.phase1_guard.bubbles import plain_qq_text
            for part in event.get_result().chain:part.text=plain_qq_text(part.text)
            await self.guard.dispatch_text(event)
            self.assertEqual(self.history_text(history),self.calls[-1][1]['message'][0]['data']['text'])
        self.assertEqual(len(self.calls),4)

    async def test_v3_fallback_interjection_preserves_only_confirmed_prefix(self):
        event,history=await self.v3_prepared();follow=self.event('先停一下，已经解决')
        async def after(payload):
            if payload['part_index']==0:await self.guard.claim_inbound(follow)
        self.guard.register_delivery_observer(after)
        await self.guard.preserve_semantic_boundaries(event);await self.guard.dispatch_text(event)
        self.assertEqual(len(self.calls),1)
        self.assertTrue(self.history_text(history).startswith('今天可算忙完了！'))
        self.assertNotIn('今晚可以安心歇一会儿了',self.history_text(history))
        self.assertIn('其余气泡未确认送达',self.history_text(history))
        result=self.guard.store.bubble_result(event.get_extra('phase3_turn_id'),'normal')
        self.assertEqual([p['state'] for p in result['parts']],['sent','cancelled'])

    async def test_v31_native_stop_after_first_ack_cancels_tail_without_second_api(self):
        event,history=await self.v3_prepared()
        async def after(payload):
            if payload['part_index']==0:event.stop_event()
        self.guard.register_delivery_observer(after)
        await self.guard.preserve_semantic_boundaries(event);await self.guard.dispatch_text(event)
        self.assertTrue(event.is_stopped())
        self.assertEqual([row[1]['message'][0]['data']['text'] for row in self.calls],
                         ['今天可算忙完了！'])
        result=self.guard.store.bubble_result(event.get_extra('phase3_turn_id'),'normal')
        self.assertEqual([p['state'] for p in result['parts']],['sent','cancelled'])
        self.assertEqual([p['transport_attempts'] for p in result['parts']],[1,0])
        self.assertEqual(result['delivered_text'],'今天可算忙完了！')
        self.assertTrue(self.history_text(history).startswith('今天可算忙完了！'))
        self.assertNotIn('今晚可以安心歇一会儿了',self.history_text(history))
        self.assertIn('其余气泡未确认送达',self.history_text(history))

    async def test_v3_fallback_failure_persists_without_group_resend_on_restart(self):
        event,history=await self.v3_prepared()
        async def after(payload):self.mode='reject'
        self.guard.register_delivery_observer(after)
        await self.guard.preserve_semantic_boundaries(event);await self.guard.dispatch_text(event)
        self.assertEqual(len(self.calls),2)
        self.assertTrue(self.history_text(history).startswith('今天可算忙完了！'))
        self.assertNotIn('今晚可以安心歇一会儿了',self.history_text(history))
        self.assertIn('其余气泡未确认送达',self.history_text(history))
        tid=event.get_extra('phase3_turn_id')
        before=self.guard.store.bubble_result(tid,'normal')
        self.assertEqual([p['state'] for p in before['parts']],['sent','failed'])
        await self.guard.terminate()
        self.guard=base.Phase1Guard(self.context,{'phase3_enabled':True,'phase3_admin_id':'10001',
            'phase3_platform_id':'test','phase3_casual_split_fallback_enabled':True,
            'phase3_group_window':.01,'phase3_group_max_wait':.03,'send_timeout_seconds':2.0})
        self.assertEqual(self.guard.store.bubble_result(tid,'normal'),before)
        await asyncio.sleep(.01)
        self.assertEqual(len(self.calls),2)

    async def test_v3_proactive_plain_text_uses_same_fallback_acl_and_independent_receipts(self):
        await self.proactive_setup();self.guard.casual_split_fallback_enabled=True
        expected=['今天可算忙完了！','今晚可以安心歇一会儿了。','明天的事就留给明天吧。']
        text=''.join(expected)
        with self.assertRaises(PermissionError):
            await self.guard.send_proactive('test:FriendMessage:10002',text,delivery_id='wrong-v3')
        result=await self.guard.send_proactive('test:FriendMessage:10001',text,delivery_id='v3-candidate')
        again=await self.guard.send_proactive('test:FriendMessage:10001',text,delivery_id='v3-candidate')
        self.assertEqual(result,again);self.assertEqual(len(self.calls),3)
        self.assertEqual(result['delivered_text'],'\n'.join(expected))
        self.assertEqual(result['sent_count'],3)
        self.assertEqual([r['kind'] for r in self.guard.store.phase3_snapshot()['deliveries']],['proactive']*3)

    async def test_v3_exact_spacing_and_emoji_survive_passive_and_proactive_delivery(self):
        text='今天真的忙完啦！🎉  今晚可以安心歇一会儿了。  明天的事就留给明天吧！'
        event,history=await self.v3_prepared(text)
        self.guard.plain_text_enabled=True
        await self.guard.preserve_semantic_boundaries(event);await self.guard.dispatch_text(event)
        bodies=[row[1]['message'][0]['data']['text'] for row in self.calls]
        self.assertEqual(len(bodies),3)
        self.assertEqual(''.join(bodies),text)
        self.assertEqual(bodies[0],'今天真的忙完啦！🎉  ')
        self.assertEqual(bodies[1],'今晚可以安心歇一会儿了。  ')
        self.assertEqual(bodies[2],'明天的事就留给明天吧！')
        await self.proactive_setup()
        result=await self.guard.send_proactive('test:FriendMessage:10001',text,delivery_id='v3-spacing')
        outgoing=[row[1]['message'][0]['data']['text'] for row in self.calls[3:]]
        self.assertEqual(outgoing,bodies)
        self.assertEqual(result['sent_count'],3)
        self.assertEqual(result['delivered_text'],'\n'.join(outgoing))
        self.assertEqual(self.history_text(history),'\n'.join(bodies))

    def assert_final_bodies_match(self,turn_id,kind,expected,calls):
        actual=[row[1]['message'][0]['data']['text'] for row in calls]
        self.assertEqual(actual,expected)
        result=self.guard.store.bubble_result(turn_id,kind)
        rows=[self.guard.store.get_delivery(part['delivery_id']) for part in result['parts']]
        self.assertEqual([row['text'] for row in rows],expected)
        self.assertEqual([json.loads(row['chain_json']) for row in rows],
                         [[Plain(body).toDict()] for body in expected])
        planned=self.guard.store.db.execute(
            'SELECT content_sha256,chars FROM bubble_parts WHERE turn_id=? AND kind=? ORDER BY part_index',(turn_id,kind)).fetchall()
        self.assertEqual([(row['content_sha256'],row['chars']) for row in planned],
                         [(hashlib.sha256(body.encode()).hexdigest(),len(body)) for body in expected])
        self.assertEqual(result['delivered_text'],'\n'.join(expected))
        self.assertEqual([row['state'] for row in rows],['sent']*len(expected))

    async def test_v31_newline_edges_trim_after_exact_mapping_before_http_ledger_and_history(self):
        raw='今天可算忙完了！\n\n今晚可以安心歇一会儿了。'
        event,history=await self.v3_prepared(raw)
        self.guard.plain_text_enabled=True
        # The fixed native cleanup only removes the final Chinese period.
        event.set_result(MessageEventResult().message(raw[:-1]))
        await self.guard.preserve_semantic_boundaries(event)
        self.assertEqual(len(event.get_result().chain),1)
        await self.guard.dispatch_text(event)
        expected=['今天可算忙完了！','今晚可以安心歇一会儿了']
        self.assert_final_bodies_match(event.get_extra('phase3_turn_id'),'normal',expected,self.calls)
        self.assertEqual(self.history_text(history),'\n'.join(expected))
        self.assertEqual(event.get_extra('phase3_segment_plan')['reason'],'candidate_terminal_period_removed')

    async def test_preserved_marker_plan_trims_late_edge_newlines_without_changing_internal_format(self):
        raw='第一条\n内部第二行\n'+MARKER+'\n另一条完整正文'
        event,history=await self.v3_prepared(raw)
        self.guard.plain_text_enabled=False
        await self.guard.preserve_semantic_boundaries(event)
        self.assertTrue(event.get_extra('phase3_semantic_boundaries'))
        # Late native hooks may leave cosmetic whitespace around surviving parts.
        for part in event.get_result().chain:part.text='\r\n'+part.text+'\n\n'
        await self.guard.dispatch_text(event)
        expected=['第一条\n内部第二行','另一条完整正文']
        self.assert_final_bodies_match(event.get_extra('phase3_turn_id'),'normal',expected,self.calls)
        self.assertEqual(self.history_text(history),'\n'.join(expected))

    async def test_proactive_fallback_crlf_edges_share_cleaned_plan_without_replay(self):
        await self.proactive_setup();self.guard.casual_split_fallback_enabled=True
        raw='今天可算忙完了！🙂 \r\n \r\n今晚可以安心歇一会儿了。\r\n'
        expected=['今天可算忙完了！🙂  ','今晚可以安心歇一会儿了。']
        result=await self.guard.send_proactive('test:FriendMessage:10001',raw,delivery_id='newline-candidate')
        turn_id=self.guard.store.get_delivery(result['delivery_id'])['turn_id']
        self.assert_final_bodies_match(turn_id,'proactive',expected,self.calls)
        again=await self.guard.send_proactive('test:FriendMessage:10001',raw,delivery_id='newline-candidate')
        self.assertEqual(again,result);self.assertEqual(len(self.calls),2)

    async def test_edge_cleanup_keeps_internal_code_and_mixed_components_untouched(self):
        code='```python\n    x = 1\n\n    print(x)\n```'
        result=MessageEventResult().message('\n'+code+'\r\n')
        parts,removed=self.guard.trim_bubble_edges([result])
        self.assertEqual(parts[0].get_plain_text(),code);self.assertEqual(removed,3)
        mixed=MessageEventResult(chain=[Plain('\n正文\n'),Face(id=1)])
        parts,removed=self.guard.trim_bubble_edges([mixed])
        self.assertIs(parts[0],mixed);self.assertEqual(removed,0)

    async def test_progress_retains_existing_single_path_and_no_false_cleanup_count(self):
        text='\n去核对一下\n'
        event,_=await self.v3_prepared(text,kind='progress')
        self.guard.plain_text_enabled=False
        await self.guard.preserve_semantic_boundaries(event);await self.guard.dispatch_text(event)
        self.assertEqual([row[1]['message'][0]['data']['text'] for row in self.calls],[text])
        row=self.guard.store.get_turn(event.get_extra('phase3_turn_id'))
        self.assertEqual(json.loads(row['diagnostics_json'])['cleaned_final']['boundary_newlines_removed'],0)
        self.assertEqual(self.guard.store.phase3_snapshot()['deliveries'][0]['text'],text)

    async def test_trimmed_fallback_failure_and_restart_preserve_only_confirmed_prefix(self):
        raw='今天可算忙完了！\n今晚可以安心歇一会儿了。\n明天的事就留给明天吧。'
        event,history=await self.v3_prepared(raw)
        async def after(payload):self.mode='reject'
        self.guard.register_delivery_observer(after)
        await self.guard.preserve_semantic_boundaries(event);await self.guard.dispatch_text(event)
        tid=event.get_extra('phase3_turn_id')
        result=self.guard.store.bubble_result(tid,'normal')
        self.assertEqual([part['state'] for part in result['parts']],['sent','failed','cancelled'])
        self.assertEqual(result['delivered_text'],'今天可算忙完了！')
        self.assertEqual([row[1]['message'][0]['data']['text'] for row in self.calls],
                         ['今天可算忙完了！','今晚可以安心歇一会儿了。'])
        self.assertTrue(self.history_text(history).startswith('今天可算忙完了！\n'))
        self.assertNotIn('今晚可以安心歇一会儿了',self.history_text(history))
        await self.guard.terminate()
        self.guard=base.Phase1Guard(self.context,{'phase3_enabled':True,'phase3_admin_id':'10001',
            'phase3_platform_id':'test','phase3_group_window':.01,'phase3_group_max_wait':.03})
        self.assertEqual(self.guard.store.bubble_result(tid,'normal'),result)
        self.assertEqual(len(self.calls),2)
