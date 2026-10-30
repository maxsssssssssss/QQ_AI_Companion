"""Interruptible first-bubble wait with real SQLite receipts and synthetic I/O."""
import asyncio
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from integrations.phase1_guard.bubbles import Pacing, send_sequence
from integrations.phase1_guard.delivery import DeliveryReceipt, DeliveryService
from integrations.phase1_guard.store import GuardStore
from integrations.phase3_runtime.proactive import ProactiveGate


class FirstBubbleDelayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'guard.sqlite3'
        self.store = GuardStore(self.path)
        self.store.enable_phase3()
        self.store.enable_bubbles()
        self.session = 'test:FriendMessage:10001'
        self.store.claim('test', self.session, 'm1', '10001', '合成入站', [])
        self.store.create_turn('first', 'test', self.session, ['m1'])
        self.service = DeliveryService(self.store, timeout=.02)
        self.pacing = Pacing(2, 4, first_minimum=.8, first_maximum=1.8)
        self.clock = 0.0
        self.current = True
        self.stop = False
        self.calls = []
        self.events = []
        self.texts = ['第一条🙂', '第二条', '最后一条']

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    async def sleep(self, delay):
        self.assertLessEqual(delay, .1)
        self.clock += delay
        if self.stop:
            self.current = False
        await asyncio.sleep(0)

    async def part(self, index, delivery_id):
        async def sender():
            self.calls.append((index, self.clock))
            self.events.append('send:' + str(index))
            return DeliveryReceipt(True, source='synthetic_ack')
        result = await self.service.send(turn_id='first', text=self.texts[index],
            chain=[{'type': 'text', 'data': {'text': self.texts[index]}}],
            sender=sender, kind='normal', part_index=index, delivery_id=delivery_id,
            is_current=lambda: self.current)
        self.events.append('confirmed:' + str(index))
        return result

    async def send(self, **overrides):
        args = dict(turn_id='first', kind='normal', texts=self.texts,
                    send_part=self.part, is_current=lambda: self.current,
                    pacing=self.pacing, sleep=self.sleep)
        args.update(overrides)
        return await send_sequence(self.store, **args)

    async def test_first_and_later_waits_have_independent_bounds_after_confirmations(self):
        def draw(low, high):
            self.events.append('draw:' + str((low, high)))
            return 1.3 if low == .8 else 3.0
        with patch('integrations.phase1_guard.bubbles.random.uniform', side_effect=draw) as random:
            result = await self.send()
        self.assertEqual(random.call_args_list[0].args, (.8, 1.8))
        self.assertEqual([c.args for c in random.call_args_list[1:]], [(2, 4)] * 2)
        for actual, expected in zip(self.calls, (1.3, 4.3, 7.3)):
            self.assertAlmostEqual(actual[1], expected)
        self.assertEqual(self.events, ['draw:(0.8, 1.8)', 'send:0', 'confirmed:0',
            'draw:(2, 4)', 'send:1', 'confirmed:1', 'draw:(2, 4)', 'send:2', 'confirmed:2'])
        self.assertEqual(result['state'], 'sent')
        self.assertEqual(result['delivered_text'], '\n'.join(self.texts))
        self.assertEqual([p['transport_attempts'] for p in result['parts']], [1, 1, 1])

    async def test_single_reply_also_waits_once(self):
        self.texts = self.texts[:1]
        with patch('integrations.phase1_guard.bubbles.random.uniform', return_value=1.8) as draw:
            result = await self.send()
        draw.assert_called_once_with(.8, 1.8)
        self.assertAlmostEqual(self.calls[0][1], 1.8)
        self.assertEqual(result['state'], 'sent')

    async def test_legacy_zero_pair_keeps_single_send_immediate(self):
        self.texts = self.texts[:1]
        with patch('integrations.phase1_guard.bubbles.random.uniform') as draw:
            result = await self.send(pacing=Pacing())
        draw.assert_not_called()
        self.assertEqual(self.calls, [(0, 0)])
        self.assertEqual(result['state'], 'sent')

    async def test_cancel_before_plan_send_does_not_draw_or_transport(self):
        self.current = False
        with patch('integrations.phase1_guard.bubbles.random.uniform') as draw:
            result = await self.send()
        draw.assert_not_called()
        self.assertEqual(self.calls, [])
        self.assertEqual([p['state'] for p in result['parts']], ['cancelled'] * 3)

    async def test_new_input_during_first_wait_cancels_all_without_visible_history(self):
        self.stop = True
        result = await self.send()
        self.assertEqual(self.calls, [])
        self.assertEqual(result['sent_count'], 0)
        self.assertEqual(result['delivered_text'], '')
        self.assertEqual([p['state'] for p in result['parts']], ['cancelled'] * 3)
        self.assertTrue(all(p['transport_attempts'] == 0 for p in result['parts']))
        self.assertLessEqual(self.clock, .1)

    async def test_shutdown_in_first_wait_and_restart_do_not_replay(self):
        waiting = asyncio.Event()
        async def blocked_sleep(delay):
            waiting.set()
            await asyncio.Event().wait()
        task = asyncio.create_task(self.send(sleep=blocked_sleep))
        await asyncio.wait_for(waiting.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.store.close()
        self.store = GuardStore(self.path)
        self.store.enable_phase3()
        self.store.recover_phase3_interrupted()
        self.store.enable_bubbles()
        self.store.recover_bubbles()
        with patch('integrations.phase1_guard.bubbles.random.uniform') as draw:
            result = await self.send()
        draw.assert_not_called()
        self.assertEqual(self.calls, [])
        self.assertEqual([p['state'] for p in result['parts']], ['cancelled'] * 3)

    async def test_existing_plan_does_not_draw_again_or_duplicate(self):
        with patch('integrations.phase1_guard.bubbles.random.uniform', side_effect=[.8, 2, 4]) as draw:
            result = await self.send()
            second = await self.send()
        self.assertEqual(result, second)
        self.assertEqual(draw.call_count, 3)
        self.assertEqual(len(self.calls), 3)

    async def test_first_wait_is_outside_transport_timeout(self):
        self.texts = self.texts[:1]
        self.service = DeliveryService(self.store, timeout=.01)
        # A real wait longer than the transport deadline must not cause unknown.
        result = await self.send(pacing=Pacing(first_minimum=.03, first_maximum=.03),
                                 sleep=asyncio.sleep)
        self.assertEqual(result['state'], 'sent')
        self.assertEqual(len(self.calls), 1)

    async def test_network_timeout_still_stops_tail_and_is_not_retried(self):
        async def timed_out(index, delivery_id):
            async def sender():
                self.calls.append((index, self.clock))
                await asyncio.sleep(.2)
            return await self.service.send(turn_id='first', text=self.texts[index],
                chain=[{'type': 'text'}], sender=sender, kind='normal',
                part_index=index, delivery_id=delivery_id)
        with patch('integrations.phase1_guard.bubbles.random.uniform', return_value=1.3) as draw:
            result = await self.send(send_part=timed_out)
            again = await self.send(send_part=timed_out)
        self.assertEqual([p['state'] for p in result['parts']], ['unknown', 'cancelled', 'cancelled'])
        self.assertEqual(result, again)
        draw.assert_called_once_with(.8, 1.8)
        self.assertEqual(len(self.calls), 1)

    async def test_cancelled_proactive_wait_still_consumes_one_attempt_and_cooldown(self):
        now = datetime.fromisoformat('2026-10-29T12:00:00+08:00').timestamp()
        gate = ProactiveGate(Path(self.tmp.name) / 'active.sqlite3', allowed_session=self.session,
            enabled=True, mode='continuous', window_start=0, window_end=0,
            max_per_day=12, max_generations_per_day=96, cooldown=5400, clock=lambda: now)
        self.stop = True
        try:
            self.store.create_turn('active', 'test', self.session, proactive=True)
            result = await gate.deliver(self.session, candidate_id='synthetic-opportunity',
                send=lambda: self.send(kind='proactive', turn_id='active'))
            self.assertEqual(result['state'], 'cancelled')
            self.assertEqual(self.calls, [])
            self.assertEqual(gate.db.execute('SELECT count(*) FROM proactive_attempts').fetchone()[0], 1)
            self.assertEqual(gate.permit(self.session, candidate_id='next'), 'budget_or_cooldown')
        finally:
            gate.close()

    def test_invalid_first_wait_config_rejected(self):
        for low, high in [(-1, 1), (1.8, .8), (0, 4.01), (False, 1),
                          (.8, True), ('0.8', 1.8), (0, float('nan')), (0, float('inf'))]:
            with self.subTest(low=low, high=high), self.assertRaisesRegex(ValueError, 'first_bubble'):
                Pacing(first_minimum=low, first_maximum=high)


if __name__ == '__main__':
    unittest.main()
