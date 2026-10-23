"""Pinned native event/queue/Guard ingress, synthetic local OneBot only.

Fault injection changes only local admission or snapshot failures. Real Guard
ledgers, event components, cancellation and HTTP send acknowledgements run.
"""
import asyncio
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import test_phase3_delivery_native as base

if base.NATIVE:
    from astrbot.api.event import MessageEventResult
    from astrbot.api.message_components import Plain
    from astrbot.core.agent.message import Message,TextPart
    from integrations.phase1_guard import media
    from integrations.phase1_guard.media_download import install


@unittest.skipUnless(base.NATIVE,'pinned AstrBot required')
class IngressTests(unittest.IsolatedAsyncioTestCase):
    event=base.NativeDeliveryTests.event

    async def asyncSetUp(self):
        await base.NativeDeliveryTests.asyncSetUp(self)
        self.queue=asyncio.Queue()
        self.handle=install(self.queue,platform_id='test',owner_id='10001',
                            on_admit=self.guard.admit_input)

    async def asyncTearDown(self):
        self.handle.restore()
        await base.NativeDeliveryTests.asyncTearDown(self)

    async def queued(self,event):
        await self.queue.put(event)
        self.assertIs(await asyncio.wait_for(self.queue.get(),2),event)

    async def ready(self,event):
        await self.guard.claim_inbound(event)
        await self.guard.group_input(event)
        await self.guard.mark_generating(event,NS())

    async def reply(self,event,text='收到这轮完整输入。'):
        message=Message(role='assistant',content=[TextPart(text=text)])
        await self.guard.bind_delivery_history(event,NS(messages=[message]),None)
        event.set_result(MessageEventResult().message(text))
        await self.guard.dispatch_text(event)
        return message

    async def test_finite_burst_preserves_order_one_leader_and_confirmed_history(self):
        events=[self.event('合成输入'+str(i)) for i in range(24)]
        for event in events:await self.queued(event)
        await asyncio.gather(*(self.guard.claim_inbound(event) for event in events))
        await asyncio.gather(*(self.guard.group_input(event) for event in events))
        leaders=[event for event in events if not event.is_stopped()]
        self.assertEqual(leaders,[events[0]])
        leader=leaders[0]
        self.assertEqual(leader.get_extra('phase3_source_message_ids'),[str(i) for i in range(1,25)])
        self.assertEqual([part.text for part in leader.message_obj.message],['合成输入'+str(i) for i in range(24)])
        await self.guard.mark_generating(leader,NS())
        history=await self.reply(leader)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(history.content[0].text,'收到这轮完整输入。')
        self.assertEqual(len(self.guard.store.snapshot()['inbound']),24)
        next_event=self.event('突发结束后仍可继续');await self.queued(next_event);await self.ready(next_event)
        self.assertFalse(next_event.is_stopped())
        self.assertTrue(self.guard.turn_is_current(next_event))
        # Completed sources leave the existing transient component maps when a
        # fresh turn arrives; their durable inbound archive remains unchanged.
        self.assertLessEqual(len(self.guard._input_components),1)
        self.assertLessEqual(len(self.guard._input_ready),1)

    async def test_ledger_failure_rejects_only_that_event_before_preprocessing(self):
        failed=self.event('不允许无账本发送')
        good=self.event('后续合法输入')
        original=self.guard.store.claim
        def claim(*args,**kwargs):
            if args[2]==failed.message_obj.message_id:raise RuntimeError('synthetic_ledger_unavailable')
            return original(*args,**kwargs)
        with patch.object(self.guard.store,'claim',claim):
            await self.queue.put(failed);await self.queue.put(good)
            self.assertIs(await asyncio.wait_for(self.queue.get(),2),good)
        self.assertTrue(failed.is_stopped())
        self.assertIsNone(failed.get_extra('phase3_turn_id'))
        await self.ready(good);await self.reply(good)
        self.assertEqual(len(self.calls),1)
        rows=self.guard.store.snapshot()['inbound']
        self.assertEqual([row['message_id'] for row in rows],[good.message_obj.message_id])

    async def test_duplicate_new_object_does_not_add_source_or_reply(self):
        first=self.event('同一入站');await self.queued(first)
        replay=self.event('同一入站');replay.message_obj.message_id=first.message_obj.message_id
        last=self.event('第二个有效入站')
        await self.queue.put(replay);await self.queue.put(last)
        self.assertIs(await asyncio.wait_for(self.queue.get(),2),last)
        self.assertTrue(replay.is_stopped())
        await asyncio.gather(self.guard.claim_inbound(first),self.guard.claim_inbound(last))
        await asyncio.gather(self.guard.group_input(first),self.guard.group_input(last))
        await self.guard.mark_generating(first,NS());await self.reply(first)
        self.assertEqual(first.get_extra('phase3_source_message_ids'),[first.message_obj.message_id,last.message_obj.message_id])
        self.assertEqual(len(self.guard.store.snapshot()['inbound']),2)
        self.assertEqual(len(self.calls),1)

    async def test_optional_snapshot_error_keeps_useful_plain_input(self):
        event=self.event('图片辅助模块失败时仍应回答这段文字');await self.queued(event)
        async def broken(*args,**kwargs):raise RuntimeError('synthetic_snapshot_error')
        with patch.object(media,'snapshot_components',broken):
            await self.guard.claim_inbound(event)
        await self.guard.group_input(event)
        self.assertFalse(event.is_stopped())
        self.assertTrue(any(isinstance(part,Plain) and '仍应回答' in part.text for part in event.message_obj.message))
        await self.guard.mark_generating(event,NS());await self.reply(event)
        self.assertEqual(len(self.calls),1)

    async def test_cancelled_snapshot_finishes_source_and_next_input_recovers(self):
        first=self.event('尚未完成的输入');await self.queued(first)
        started,release=asyncio.Event(),asyncio.Event()
        async def blocked(*args,**kwargs):
            started.set();await release.wait()
            return await original(*args,**kwargs)
        original=media.snapshot_components
        with patch.object(media,'snapshot_components',blocked):
            task=asyncio.create_task(self.guard.claim_inbound(first))
            await asyncio.wait_for(started.wait(),2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
        key=first.get_extra('phase1_guard_key')
        ready=self.guard._input_ready.get(key)
        self.assertTrue(ready is None or ready.done(),'cancelled snapshot left an unresolved source future')
        next_event=self.event('继续聊新的问题');await self.queued(next_event)
        await asyncio.wait_for(self.ready(next_event),2)
        self.assertFalse(next_event.is_stopped())
        self.assertTrue(self.guard.turn_is_current(next_event))
        await self.reply(next_event)
        self.assertEqual(len(self.calls),1)
        self.assertTrue(all(future.done() for future in self.guard._input_ready.values()))

    async def test_cancelled_preprocessing_ready_future_does_not_orphan_successor(self):
        first=self.event('前置预处理被取消');await self.queued(first)
        self.guard._input_ready[first.get_extra('phase1_guard_key')].cancel()
        next_event=self.event('新的合法消息');await self.queued(next_event)
        await asyncio.wait_for(self.ready(next_event),2)
        self.assertFalse(next_event.is_stopped())
        self.assertTrue(self.guard.turn_is_current(next_event))
        await self.reply(next_event)
        self.assertEqual(len(self.calls),1)

    async def test_cancelled_group_waiter_does_not_lock_next_turn(self):
        self.guard.turns.window=.2;self.guard.turns.max_wait=.3
        first=self.event('收集轮取消');await self.queued(first)
        await self.guard.claim_inbound(first)
        task=asyncio.create_task(self.guard.group_input(first))
        await asyncio.sleep(.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(self.guard.store.get_turn(first.get_extra('phase3_turn_id'))['state'],'cancelled')
        next_event=self.event('下一轮');await self.queued(next_event);await self.ready(next_event)
        self.assertTrue(self.guard.turn_is_current(next_event))
        self.assertFalse(next_event.is_stopped())
        await self.reply(next_event)
        self.assertEqual(len(self.calls),1)


if __name__=='__main__':unittest.main()
