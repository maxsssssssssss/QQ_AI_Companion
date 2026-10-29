"""Pinned AstrBot events, parser and real local OneBot HTTP; synthetic content."""
import asyncio
import ast
from functools import lru_cache
import hashlib
import importlib
import json
from pathlib import Path
import re
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


@lru_cache(maxsize=1)
def native_cleanup_functions():
    """Execute the locked source functions unchanged, without plugin startup.

    This does not vendor native implementation. Only synthetic tag-free text
    reaches these functions; no network, scheduler or private state is loaded.
    """
    native=base.PROJECT/'.runtime/upstream/private_companion'
    functions=[]
    for filename,name in [('llm_tool_actions.py','_extract_reaction_expression_hidden_intent'),
                          ('helpers.py','_strip_terminal_sentence_period')]:
        path=native/filename
        tree=ast.parse(path.read_text())
        node=next(node for node in ast.walk(tree)
                  if isinstance(node,ast.FunctionDef) and node.name==name)
        namespace={'re':re,'json':json,'Any':object}
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),namespace)
        functions.append(namespace[name])
    return tuple(functions)


def native_clean_text(text):
    hidden_intent,terminal_period=native_cleanup_functions()
    cleaned,intent=hidden_intent(None,text)
    if intent:raise AssertionError('Synthetic text unexpectedly contains a native directive')
    return terminal_period(cleaned)


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
        # Only source-structure protection is preserved early; splitting happens
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

    async def test_v4_short_reactions_have_independent_receipts_and_history(self):
        first='今天可算忙完了！';second='今晚可以安心歇一会儿了。'
        cases=[('终于忙完啦！'+second,['终于忙完啦！',second]),
               ('好耶！'+first+second,['好耶！',first,second]),
               (first+'哈？'+second,[first,'哈？',second]),
               (first+second+'嗯。',[first,second,'嗯。'])]
        for text,expected in cases:
            with self.subTest(text=text):
                before=len(self.calls);event,history=await self.v3_prepared(text)
                await self.guard.preserve_semantic_boundaries(event);await self.guard.dispatch_text(event)
                actual=[row[1]['message'][0]['data']['text'] for row in self.calls[before:]]
                self.assertEqual(actual,expected);self.assertEqual(''.join(actual),text)
                self.assertEqual(self.history_text(history),'\n'.join(expected))

    async def test_random_pacing_waits_only_after_first_ack_and_history_stays_confirmed(self):
        text='今天可算忙完了！今晚可以安心歇一会儿了。明天的事就留给明天吧。'
        event,history=await self.v3_prepared(text)
        self.guard.pacing=Pacing()
        self.assertEqual((self.guard.pacing.minimum,self.guard.pacing.maximum),(2,4))
        waits=[]
        async def fake_interval(delay,is_current,**kwargs):
            waits.append((len(self.calls),delay))
            return is_current()
        with patch('integrations.phase1_guard.bubbles.wait_between',side_effect=fake_interval), \
                patch('integrations.phase1_guard.bubbles.random.uniform',side_effect=[2.1,3.9]) as draw:
            await self.guard.preserve_semantic_boundaries(event);await self.guard.dispatch_text(event)
        self.assertEqual([count for count,_ in waits],[1,2])
        self.assertEqual([delay for _,delay in waits],[2.1,3.9])
        self.assertEqual(draw.call_count,2)
        self.assertEqual(len(self.calls),3)
        self.assertEqual(self.history_text(history),'\n'.join(
            row[1]['message'][0]['data']['text'] for row in self.calls))

    async def test_v4_native_terminal_period_removal_uses_final_text_without_restoring_period(self):
        text='今天真的忙完啦！🎉  今晚可以安心歇一会儿了。'
        cleaned=text[:-1]
        expected=['今天真的忙完啦！🎉  ','今晚可以安心歇一会儿了']
        event,history=await self.v3_prepared(text)
        self.guard.plain_text_enabled=True
        # Pinned Private 6.7.0's on_llm_response drops the terminal Chinese
        # period before decoration. Split the final cleaned text;
        # never resurrect the period or any other removed source character.
        event.set_result(MessageEventResult().message(cleaned))
        await self.guard.preserve_semantic_boundaries(event);await self.guard.dispatch_text(event)
        actual=[row[1]['message'][0]['data']['text'] for row in self.calls]
        self.assertEqual(actual,expected);self.assertEqual(''.join(actual),cleaned)
        self.assertNotEqual(''.join(actual),text)
        self.assertEqual(self.history_text(history),'\n'.join(expected))

    async def test_v4_native_line_end_cleanup_uses_only_final_body_with_short_tail(self):
        cases=[
            ('今天总算忙完这一阵了。 \n今晚想不想一起聊聊天？',
             ['今天总算忙完这一阵了。','今晚想不想一起聊聊天？']),
            ('今天总算忙完这一阵了。 \n今晚可以安心休息一会儿了。 慢慢来吧。',
             ['今天总算忙完这一阵了。','今晚可以安心休息一会儿了。 ','慢慢来吧'])]
        for raw,expected in cases:
            with self.subTest(raw=raw):
                before=len(self.calls);event,history=await self.v3_prepared(raw)
                cleaned=native_clean_text(raw)
                self.assertNotEqual(raw,cleaned)
                event.set_result(MessageEventResult().message(cleaned))
                await self.guard.preserve_semantic_boundaries(event)
                # Split the allowed text itself; no raw offsets or deleted
                # terminal punctuation can re-enter the outgoing content.
                parts=self.guard.semantic_parts(event.get_result(),event=event)
                self.assertEqual(''.join(part.get_plain_text() for part in parts),cleaned)
                await self.guard.dispatch_text(event)
                self.assert_final_bodies_match(event.get_extra('phase3_turn_id'),
                                              'normal',expected,self.calls[before:])
                self.assertEqual(self.history_text(history),'\n'.join(expected))
                diagnostic=event.get_extra('phase3_segment_plan')
                self.assertEqual(diagnostic['reason'],'bounded_sentence_boundaries_v4')
                self.assertEqual(diagnostic['scope'],'final_allowed_text')

    async def test_native_line_end_cleanup_preserves_three_four_bubbles_and_emoji_spacing(self):
        sentences=['今天终于能歇一会儿了！🙂', '那就慢慢挑一首喜欢的歌吧。',
                   '我也想听听你最近的发现。', '今晚咱们可以轻轻松松聊天。']
        for count in (3,4):
            with self.subTest(count=count):
                raw=' \t\n'.join(sentences[:count])
                before=len(self.calls);event,history=await self.v3_prepared(raw)
                cleaned=native_clean_text(raw)
                expected=sentences[:count-1]+[sentences[count-1][:-1]]
                event.set_result(MessageEventResult().message(cleaned))
                await self.guard.preserve_semantic_boundaries(event)
                mapped=self.guard.semantic_parts(event.get_result(),event=event)
                self.assertEqual(len(mapped),count)
                self.assertEqual(''.join(part.get_plain_text() for part in mapped),cleaned)
                await self.guard.dispatch_text(event)
                self.assert_final_bodies_match(event.get_extra('phase3_turn_id'),
                                              'normal',expected,self.calls[before:])
                self.assertEqual(self.history_text(history),'\n'.join(expected))

    async def test_v4_native_cleanup_content_edit_splits_only_final_text_and_resets_source_flags(self):
        raw='今天总算忙完这一阵了。 \n今晚想不想一起聊聊天？'
        event,history=await self.v3_prepared(raw)
        cleaned=native_clean_text(raw).replace('今晚','明晚')
        event.set_result(MessageEventResult().message(cleaned))
        await self.guard.preserve_semantic_boundaries(event)
        await self.guard.dispatch_text(event)
        diagnostic=event.get_extra('phase3_segment_plan')
        self.assertEqual(diagnostic['reason'],'bounded_sentence_boundaries_v4')
        self.assertEqual(diagnostic['scope'],'final_allowed_text')
        expected=['今天总算忙完这一阵了。','明晚想不想一起聊聊天？']
        self.assert_final_bodies_match(event.get_extra('phase3_turn_id'),'normal',expected,self.calls)
        self.assertEqual(self.history_text(history),'\n'.join(expected))
        self.assertNotIn('今晚',self.history_text(history))
        # A reused event must not inherit model-marker/structure protection.
        event.set_extra('phase3_fallback_marker_protected',True)
        event.set_extra('phase3_fallback_source_protected',True)
        event.set_extra('phase3_semantic_boundaries',True)
        await self.guard.recover_empty_final(event,types.SimpleNamespace(
            role='assistant',completion_text=raw,tools_call_name=[]))
        self.assertFalse(event.get_extra('phase3_fallback_marker_protected'))
        self.assertFalse(event.get_extra('phase3_fallback_source_protected'))
        self.assertFalse(event.get_extra('phase3_semantic_boundaries'))

    async def test_native_cleanup_eligible_result_dropped_empty_or_cancelled_never_restores_text(self):
        raw='今天总算忙完这一阵了。 \n今晚想不想一起聊聊天？'
        for mode in ('dropped','empty','stopped','cancelled'):
            with self.subTest(mode=mode):
                event,history=await self.v3_prepared(raw)
                event.set_result(MessageEventResult().message(native_clean_text(raw)))
                if mode=='dropped':event.clear_result()
                elif mode=='empty':event.set_result(MessageEventResult().message(''))
                elif mode=='stopped':event.stop_event()
                else:event.set_extra('phase3_cancelled',True)
                await self.guard.preserve_semantic_boundaries(event)
                await self.guard.dispatch_text(event)
                self.assertEqual(self.calls,[])
                # A dropped native result retains the unconfirmed placeholder;
                # a processed-but-unsent result saves the fixed no-delivery
                # notice. Neither may project the discarded generated body.
                self.assertNotIn('今天总算忙完',self.history_text(history))
                self.assertNotIn('今晚想不想',self.history_text(history))
                if mode=='dropped':self.assertTrue(history._no_save)
                else:
                    self.assertFalse(history._no_save)
                    self.assertIn('没有已确认送达',self.history_text(history))

    async def test_v4_native_cleanup_failure_and_restart_keep_only_confirmed_prefix(self):
        raw='今天总算忙完这一阵了。 \n今晚可以安心休息一会儿了。 \n明天的事就留给明天吧。'
        event,history=await self.v3_prepared(raw)
        event.set_result(MessageEventResult().message(native_clean_text(raw)))
        async def after(payload):self.mode='reject'
        self.guard.register_delivery_observer(after)
        await self.guard.preserve_semantic_boundaries(event);await self.guard.dispatch_text(event)
        tid=event.get_extra('phase3_turn_id')
        result=self.guard.store.bubble_result(tid,'normal')
        self.assertEqual([part['state'] for part in result['parts']],['sent','failed','cancelled'])
        self.assertEqual(result['delivered_text'],'今天总算忙完这一阵了。')
        self.assertEqual([row[1]['message'][0]['data']['text'] for row in self.calls],
                         ['今天总算忙完这一阵了。','今晚可以安心休息一会儿了。'])
        self.assertNotIn('今晚可以安心休息',self.history_text(history))
        self.assertNotIn('明天的事',self.history_text(history))
        await self.guard.terminate()
        self.guard=base.Phase1Guard(self.context,{'phase3_enabled':True,'phase3_admin_id':'10001',
            'phase3_platform_id':'test','phase3_group_window':.01,'phase3_group_max_wait':.03})
        self.assertEqual(self.guard.store.bubble_result(tid,'normal'),result)
        self.assertEqual(len(self.calls),2)

    async def test_v4_final_body_accepts_cleanup_edits_but_empty_drop_stop_cancel_send_nothing(self):
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
                if mode=='dropped':event.clear_result()
                else:event.set_result(MessageEventResult().message(cleaned))
                if mode=='stopped':event.stop_event()
                if mode=='cancelled':event.set_extra('phase3_cancelled',True)
                await self.guard.preserve_semantic_boundaries(event);await self.guard.dispatch_text(event)
                actual=[row[1]['message'][0]['data']['text'] for row in self.calls[before:]]
                if mode in {'changed','trim','crlf'}:
                    expected=[cleaned.split('！')[0]+'！',cleaned.split('！')[1].lstrip('\n')]
                    self.assertEqual(actual,expected)
                    self.assertEqual(''.join(actual),cleaned.replace('\n',''))
                    self.assertEqual(self.history_text(history),'\n'.join(expected))
                else:self.assertEqual(actual,[])

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

    async def test_v4_newline_edges_trim_after_final_segmentation_before_http_ledger_and_history(self):
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
        self.assertEqual(event.get_extra('phase3_segment_plan')['reason'],'bounded_sentence_boundaries_v4')

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

    async def test_v4_quotes_parentheses_and_unpunctuated_tail_use_final_native_body(self):
        cases=[
            ('我说的是“等会儿再聊”，你别急呀。后面还有不少有趣的话题',
             ['我说的是“等会儿再聊”，你别急呀。','后面还有不少有趣的话题']),
            ('你说“真的假的？先别急。”我当然记着呢。哈？那就慢慢来吧。',
             ['你说“真的假的？先别急。”我当然记着呢。','哈？','那就慢慢来吧']),
            ('我知道呀（真的！不用担心）。今晚就轻松一点',
             ['我知道呀（真的！不用担心）。','今晚就轻松一点']),
            ('数据库终于恢复正常了！现在可算能安心吃饭了。',
             ['数据库终于恢复正常了！','现在可算能安心吃饭了'])]
        for raw,expected in cases:
            with self.subTest(raw=raw):
                before=len(self.calls);event,history=await self.v3_prepared(raw)
                cleaned=native_clean_text(raw)
                event.set_result(MessageEventResult().message(cleaned))
                await self.guard.preserve_semantic_boundaries(event)
                await self.guard.dispatch_text(event)
                self.assert_final_bodies_match(event.get_extra('phase3_turn_id'),
                                              'normal',expected,self.calls[before:])
                self.assertEqual(''.join(expected),cleaned)
                self.assertEqual(self.history_text(history),'\n'.join(expected))

    async def test_v4_final_body_up_to_160_and_more_than_four_sentences_preserves_every_character(self):
        raw='今天的事情总算有了一个不错的结果。我们之前花的时间也不算白费了！真的？我还有一点意外呢。那就慢慢享受今晚吧。'
        event,history=await self.v3_prepared(raw)
        cleaned=native_clean_text(raw)
        event.set_result(MessageEventResult().message(cleaned))
        await self.guard.preserve_semantic_boundaries(event);await self.guard.dispatch_text(event)
        actual=[row[1]['message'][0]['data']['text'] for row in self.calls]
        self.assertEqual(len(actual),4)
        self.assertEqual(''.join(actual),cleaned)
        self.assert_final_bodies_match(event.get_extra('phase3_turn_id'),'normal',actual,self.calls)
        self.assertEqual(self.history_text(history),'\n'.join(actual))

    async def test_v4_source_length_does_not_block_short_final_allowed_text(self):
        raw='这段话原来很长。'*30
        cleaned='终于说清楚了！那咱们接着聊吧'
        event,history=await self.v3_prepared(raw)
        event.set_result(MessageEventResult().message(cleaned))
        await self.guard.preserve_semantic_boundaries(event);await self.guard.dispatch_text(event)
        expected=['终于说清楚了！','那咱们接着聊吧']
        self.assert_final_bodies_match(event.get_extra('phase3_turn_id'),'normal',expected,self.calls)
        self.assertEqual(self.history_text(history),'\n'.join(expected))
        self.assertNotIn('这段话原来很长',self.history_text(history))

    async def test_v4_raw_control_marker_removed_by_native_cleanup_never_enables_fallback(self):
        for marker in (MARKER,'{{split_marker}}'):
            with self.subTest(marker=marker):
                raw='第一条完整回复\n'+marker+'\n第二条完整回复'
                cleaned='这是插件最终允许的正文。还有一句保留下来的补充。'
                before=len(self.calls);event,history=await self.v3_prepared(raw)
                event.set_result(MessageEventResult().message(cleaned))
                await self.guard.preserve_semantic_boundaries(event);await self.guard.dispatch_text(event)
                self.assert_final_bodies_match(event.get_extra('phase3_turn_id'),
                                              'normal',[cleaned],self.calls[before:])
                self.assertEqual(self.history_text(history),cleaned)
                self.assertTrue(event.get_extra('phase3_segment_plan')['fallback_marker_protected'])

    async def test_v4_complex_source_remains_single_even_if_native_removes_structure_before_decoration(self):
        cleaned='这段原本属于复杂结构。清理以后也不能自动改成闲聊。'
        for raw in ('```text\n'+cleaned+'\n```',
                    '> '+cleaned,
                    '[原始链接](https://example.invalid/doc) '+cleaned,
                    '- '+cleaned):
            with self.subTest(raw=raw):
                before=len(self.calls);event,history=await self.v3_prepared(raw)
                event.set_result(MessageEventResult().message(cleaned))
                await self.guard.preserve_semantic_boundaries(event);await self.guard.dispatch_text(event)
                self.assert_final_bodies_match(event.get_extra('phase3_turn_id'),
                                              'normal',[cleaned],self.calls[before:])
                self.assertEqual(self.history_text(history),cleaned)
                self.assertTrue(event.get_extra('phase3_segment_plan')['fallback_source_protected'])

    async def test_v4_late_final_structure_is_not_split_after_early_plain_result(self):
        event,history=await self.v3_prepared('简单回应。后面还有一句补充。')
        await self.guard.preserve_semantic_boundaries(event)
        code='```text\n简单回应。后面还有一句补充。\n```'
        event.set_result(MessageEventResult().message(code))
        await self.guard.dispatch_text(event)
        self.assert_final_bodies_match(event.get_extra('phase3_turn_id'),'normal',[code],self.calls)
        self.assertEqual(self.history_text(history),code)

    async def test_v4_valid_model_marker_keeps_code_block_whole_in_one_bubble(self):
        code='```python\nvalue = 1\nprint(value)\n```'
        raw='先看看这个例子。\n'+MARKER+'\n'+code
        event,history=await self.v3_prepared(raw)
        self.guard.plain_text_enabled=False
        await self.guard.preserve_semantic_boundaries(event)
        self.assertTrue(event.get_extra('phase3_semantic_boundaries'))
        await self.guard.dispatch_text(event)
        expected=['先看看这个例子。',code]
        self.assert_final_bodies_match(event.get_extra('phase3_turn_id'),'normal',expected,self.calls)
        self.assertEqual(self.history_text(history),'\n'.join(expected))

    async def test_v4_more_than_four_model_parts_merge_with_code_fence_boundary_intact(self):
        code='```python\nvalue = 1\n```'
        originals=['第一段完整说明。'*6,code,'好','第四段完整说明。'*6,'第五段完整说明。'*6]
        raw=('\n'+MARKER+'\n').join(originals)
        event,history=await self.v3_prepared(raw)
        self.guard.plain_text_enabled=False
        await self.guard.preserve_semantic_boundaries(event)
        self.assertTrue(event.get_extra('phase3_semantic_boundaries'))
        await self.guard.dispatch_text(event)
        expected=[originals[0],code+'\n好',originals[3],originals[4]]
        self.assert_final_bodies_match(event.get_extra('phase3_turn_id'),'normal',expected,self.calls)
        self.assertEqual(self.history_text(history),'\n'.join(expected))
        self.assertIn('```\n好',expected[1])
        self.assertNotIn('```好',expected[1])
