"""T2: fixed AstrBot/Private parser + Guard + loopback OneBot HTTP only.

No model, external tools, production state or QQ account is used. Most cases
replace only the pacing clock; one case uses the actual first wait to prove it
is outside the transport timeout. Run with the locked phase3 venv and an
isolated ASTRBOT_ROOT as for test_phase3_delivery_native.
"""
import asyncio
from pathlib import Path
import sqlite3
import time
import types
import unittest
from unittest.mock import patch

import test_phase3_delivery_native as base
import test_phase3_multibubble_native as bubble_base

if base.NATIVE:
    from astrbot.api.event import MessageChain, MessageEventResult
    from astrbot.api.message_components import Plain
    from astrbot.core.agent.message import Message, TextPart
    from integrations.phase1_guard import bubbles
    from integrations.phase1_guard.main import Phase1Guard


@unittest.skipUnless(base.NATIVE, 'requires fixed AstrBot and locked dependencies')
class NativeFirstBubbleTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = base.NativeDeliveryTests.asyncTearDown
    event = base.NativeDeliveryTests.event
    ready = base.NativeDeliveryTests.ready
    parser = bubble_base.NativeBubbleTests.parser
    history_text = bubble_base.NativeBubbleTests.history_text

    async def asyncSetUp(self):
        await base.NativeDeliveryTests.asyncSetUp(self)
        await self.guard.terminate()
        self.guard = Phase1Guard(self.context, {
            'phase3_enabled': True, 'phase3_admin_id': '10001',
            'phase3_platform_id': 'test', 'phase3_group_window': .01,
            'phase3_group_max_wait': .03, 'send_timeout_seconds': 2.0,
            'phase3_proactive_enabled': True,
            'phase3_bubble_min_interval': 2.0,
            'phase3_bubble_max_interval': 4.0,
            'phase3_first_bubble_min_delay': .8,
            'phase3_first_bubble_max_delay': 1.8,
        })
        self.parser()

    async def prepared(self, text='这是本轮完整回复', *, kind='normal'):
        event = self.event()
        await self.ready(event)
        history = Message(role='assistant', content=[TextPart(text=text)])
        history._no_save = True
        await self.guard.bind_delivery_history(
            event, types.SimpleNamespace(messages=[history]), None)
        event.set_extra('phase3_delivery_kind', kind)
        event.set_result(MessageEventResult().message(text))
        return event, history

    def recording_wait(self, waits):
        async def wait(delay, is_current, **kwargs):
            waits.append((delay, len(self.calls)))
            await asyncio.sleep(0)
            return bool(is_current())
        return wait

    async def test_real_guard_initialization_reads_independent_ranges(self):
        self.assertEqual((self.guard.pacing.minimum, self.guard.pacing.maximum), (2., 4.))
        self.assertEqual((self.guard.pacing.first_minimum,
                          self.guard.pacing.first_maximum), (.8, 1.8))
        self.assertEqual(self.guard.delivery.timeout, 2.)

    async def test_single_reply_delays_once_then_acks_and_projects_identical_history(self):
        event, history = await self.prepared()
        waits = []
        with patch.object(bubbles.random, 'uniform', return_value=1.1) as draw, \
                patch.object(bubbles, 'wait_between', self.recording_wait(waits)):
            await self.guard.dispatch_text(event)
        self.assertEqual(waits, [(1.1, 0)])
        draw.assert_called_once_with(.8, 1.8)
        result = self.guard.store.bubble_result(event.get_extra('phase3_turn_id'), 'normal')
        self.assertEqual(result['state'], 'sent')
        self.assertEqual(result['sent_count'], 1)
        self.assertEqual(self.calls[0][1]['message'][0]['data']['text'], self.history_text(history))
        self.assertEqual(result['delivered_text'], self.history_text(history))
        self.assertFalse(history._no_save)

    async def test_native_parser_two_bubbles_keep_later_wait_after_first_confirmation(self):
        event, history = await self.prepared('第一泡\n' + bubbles.MARKER + '\n第二泡')
        waits = []
        with patch.object(bubbles.random, 'uniform', side_effect=[1.2, 3.1]) as draw, \
                patch.object(bubbles, 'wait_between', self.recording_wait(waits)):
            await self.guard.preserve_semantic_boundaries(event)
            await self.guard.dispatch_text(event)
        self.assertEqual(waits, [(1.2, 0), (3.1, 1)])
        self.assertEqual([call.args for call in draw.call_args_list], [(.8, 1.8), (2., 4.)])
        self.assertEqual(self.history_text(history), '第一泡\n第二泡')
        self.assertEqual([r['state'] for r in self.guard.store.phase3_snapshot()['deliveries']], ['sent', 'sent'])

    async def test_search_progress_is_immediate_but_final_has_its_own_first_wait(self):
        event, history = await self.prepared('已取得来源的最终回答', kind='final')
        waits = []
        with patch.object(bubbles.random, 'uniform', return_value=.9) as draw, \
                patch.object(bubbles, 'wait_between', self.recording_wait(waits)):
            progress = await self.guard.send_controlled(
                event, MessageChain([Plain('先核对一下')]), kind='progress')
            self.assertEqual(progress.state, 'sent')
            self.assertEqual(waits, [])
            self.assertEqual(draw.call_count, 0)
            await self.guard.dispatch_text(event)
        self.assertEqual(waits, [(.9, 1)])
        rows = self.guard.store.phase3_snapshot()['deliveries']
        self.assertEqual([r['kind'] for r in rows], ['progress', 'final'])
        self.assertEqual(self.history_text(history), '已取得来源的最终回答')

    async def test_proactive_uses_same_first_wait_and_duplicate_plan_never_sends_again(self):
        self.context.get_platform_inst = lambda _: types.SimpleNamespace(
            meta=lambda: types.SimpleNamespace(name='aiocqhttp'))
        async def send(umo, message):
            await self.bot.send_private_msg(user_id=10001,
                message=[part.toDict() for part in message.chain])
            return True
        self.context.send_message = send
        text = '主动第一泡\n' + bubbles.MARKER + '\n主动第二泡'
        waits = []
        with patch.object(bubbles.random, 'uniform', side_effect=[1.4, 2.7]) as draw, \
                patch.object(bubbles, 'wait_between', self.recording_wait(waits)):
            result = await self.guard.send_proactive(
                'test:FriendMessage:10001', text, delivery_id='synthetic-first-delay')
            again = await self.guard.send_proactive(
                'test:FriendMessage:10001', text, delivery_id='synthetic-first-delay')
        self.assertEqual(result, again)
        self.assertEqual(draw.call_count, 2)
        self.assertEqual(waits, [(1.4, 0), (2.7, 1)])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(result['sent_count'], 2)
        self.assertEqual(result['delivered_text'], '主动第一泡\n主动第二泡')

    async def test_new_inbound_during_first_wait_cancels_all_without_false_history(self):
        event, history = await self.prepared('过时的第一泡\n' + bubbles.MARKER + '\n过时的第二泡')
        follow = self.event('先停一下，换个问题')
        original_wait = bubbles.wait_between
        async def interrupt(delay, is_current, **kwargs):
            self.assertEqual(self.calls, [])
            await self.guard.claim_inbound(follow)
            return await original_wait(.001, is_current)
        with patch.object(bubbles, 'wait_between', interrupt):
            await self.guard.dispatch_text(event)
        result = self.guard.store.bubble_result(event.get_extra('phase3_turn_id'), 'normal')
        self.assertEqual(self.calls, [])
        self.assertEqual(result['sent_count'], 0)
        self.assertEqual([part['state'] for part in result['parts']], ['cancelled', 'cancelled'])
        self.assertNotIn('过时', self.history_text(history))
        self.assertIn('没有已确认送达', self.history_text(history))
        self.assertFalse(history._no_save)
        self.assertIsNone(event.get_result())

    async def test_actual_first_wait_is_outside_two_second_transport_timeout(self):
        event, history = await self.prepared('等待后正常发送')
        original = event.get_extra('phase3_native_send')
        async def slow_ack(message):
            await asyncio.sleep(.4)
            return await original(message)
        event.set_extra('phase3_native_send', slow_ack)
        started = time.monotonic()
        with patch.object(bubbles.random, 'uniform', return_value=1.8):
            await self.guard.dispatch_text(event)
        self.assertGreaterEqual(time.monotonic() - started, 2.1)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.guard.store.phase3_snapshot()['deliveries'][0]['state'], 'sent')
        self.assertEqual(self.history_text(history), '等待后正常发送')

    async def test_shutdown_during_first_wait_leaves_only_cancelled_plan(self):
        event, history = await self.prepared('停机前不应发出')
        waiting = asyncio.Event()
        original_wait = bubbles.wait_between
        async def wait(delay, is_current, **kwargs):
            waiting.set()
            return await original_wait(delay, is_current, **kwargs)
        with patch.object(bubbles, 'wait_between', wait):
            task = asyncio.create_task(self.guard.dispatch_text(event))
            await asyncio.wait_for(waiting.wait(), 2)
            await self.guard.terminate()
        self.assertTrue(task.done())
        self.assertEqual(self.calls, [])
        with sqlite3.connect(Path(self.temp.name) / 'guard.sqlite3') as db:
            self.assertEqual(db.execute('SELECT state FROM bubble_parts').fetchall(), [('cancelled',)])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM deliveries').fetchone()[0], 0)
        self.assertNotIn('停机前不应发出', self.history_text(history))


if __name__ == '__main__':
    unittest.main()
