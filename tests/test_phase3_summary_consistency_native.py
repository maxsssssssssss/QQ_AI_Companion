"""Pinned native SQLite summary/correction races, with a blocked fake provider.

Actual native commits, source reads, corrections and withdrawal are used. No
production data, QQ, remote model or mocked commit result participates.
"""
import asyncio
import unittest
from types import SimpleNamespace

import test_phase3_memory_native as base


@unittest.skipIf(base.NativeService is None,'pinned native dependency required')
class SummaryConsistencyTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=base.NativeMemoryTests.asyncSetUp
    observe=base.NativeMemoryTests.observe
    event=base.NativeMemoryTests.event
    summary=base.NativeMemoryTests.summary
    archive=base.NativeMemoryTests.archive

    async def asyncTearDown(self):
        if self.provider.wait:self.provider.wait.set()
        await base.NativeMemoryTests.asyncTearDown(self)

    async def block_summary(self,text='我喜欢喝清茶。',source='slow'):
        self.provider.wait=asyncio.Event()
        self.provider.started.clear()
        row=await self.observe(text,source)
        task=self.co.schedule(self.ctx)
        await asyncio.wait_for(self.provider.started.wait(),5)
        return row,task

    async def fact(self):
        models=self.co.models
        record=models.MemoryRecord(id='synthetic-current-fact',memory_type='observation',
            subject=models.EntityRef(kind='user',id=self.ctx.user_id),
            object=models.EntityRef.bot_self(self.ctx.bot_id),owner_bot_id=self.ctx.bot_id,
            scope='private',platform=self.ctx.platform,session_id=self.ctx.session_id,
            visibility='private_pair',lifecycle='stable_memory',reality_level='real_user_fact',
            content='用户喜欢清茶。',evidence='我喜欢清茶。',
            metadata={'persona_id':self.ctx.persona_id,'owner_bot_id':self.ctx.bot_id})
        await self.service.store.insert_memory(record)
        return await self.service.store.get_memory(record.id)

    async def test_native_summary_and_assertion_commit_with_same_snapshot(self):
        source,memory=await self.summary('我住在苏州。','first')
        self.assertIsNotNone(memory)
        rows=await self.service.store.list_memories(memory_type='assertion',limit=10)
        self.assertTrue(any(row.validity_status=='active' and '苏州' in row.content for row in rows))
        raw=(await self.service.store.get_timeline_by_ids([source['timeline_id']]))[source['timeline_id']]
        self.assertTrue(raw['summarized_at'])
        self.assertFalse(self.co.summary_installation.wrapper.consistency.gate.locked())
        self.assertEqual(len(self.provider.calls),1)

    async def test_slow_provider_holds_no_chat_or_database_lock(self):
        await self.fact()
        source,task=await self.block_summary()
        consistency=self.co.summary_installation.wrapper.consistency
        self.assertFalse(consistency.gate.locked())
        self.assertFalse(self.co.lock.locked())
        self.assertFalse(self.service.store._conn.in_transaction)
        # The real native archive and recall remain responsive while the
        # synthetic summary provider is deliberately not completing.
        new=await asyncio.wait_for(self.observe('普通新输入仍能进入原生档案。','new-chat'),2)
        await asyncio.wait_for(self.co.recall(self.ctx,'清茶'),2)
        self.assertEqual(new['state'],'recorded')
        self.assertFalse(task.done())
        self.provider.wait.set()
        result=await asyncio.wait_for(task,10)
        self.assertNotEqual(result['state'],'failed',result)
        self.assertTrue(await self.service.store.get_timeline_by_ids([source['timeline_id'],new['timeline_id']]))

    async def test_slow_summary_does_not_block_native_guard_chat_send(self):
        import test_phase3_delivery_native as delivery
        if not delivery.NATIVE:self.skipTest('pinned AstrBot required')
        _,task=await self.block_summary()
        chat=delivery.NativeDeliveryTests('test_native_ack_progress_final_and_actual_http_order')
        await chat.asyncSetUp()
        try:
            event=chat.event('普通招呼');await chat.ready(event)
            event.set_result(delivery.MessageEventResult().message('你好，我在。'))
            await asyncio.wait_for(chat.guard.dispatch_text(event),3)
            self.assertEqual(len(chat.calls),1)
            self.assertEqual(chat.guard.store.phase3_snapshot()['deliveries'][0]['state'],'sent')
            self.assertFalse(task.done())
        finally:
            await chat.asyncTearDown()
            self.provider.wait.set()
            await asyncio.wait_for(task,10)

    async def test_correction_during_model_call_rejects_stale_completion(self):
        old=await self.fact()
        source,task=await self.block_summary()
        event=self.event('我之前说错了，我现在更喜欢白水。','concurrent-fix')
        result=await asyncio.wait_for(self.co.correct(event,memory_id=old.id,
            expected_version=self.co.revision.memory_ref(old)['version'],
            correction_id='concurrent-fix',content='用户现在更喜欢白水。'),2)
        self.assertTrue(result['ok'],result)
        self.assertFalse(task.done())
        self.provider.wait.set();await asyncio.wait_for(task,10)
        current=await self.service.store.get_memory(result['new_ref']['id'])
        self.assertTrue(self.co.revision.current_memory(current))
        self.assertEqual(current.content,'用户现在更喜欢白水。')
        raw=(await self.service.store.get_timeline_by_ids([source['timeline_id']]))[source['timeline_id']]
        self.assertFalse(raw['summarized_at'])
        progress=await self.service.store.summary_progress()
        self.assertEqual(progress['conversation_memories'],0)
        self.assertEqual(progress['quarantined_batches'],1)
        self.assertEqual(len(self.provider.calls),1)

    async def test_withdrawal_during_model_call_cannot_resurrect_source(self):
        archive=self.archive()
        archive.claim(self.ctx.platform,self.ctx.session_id,'slow',self.ctx.user_id,'我喜欢喝清茶。',[])
        source,task=await self.block_summary()
        event=self.event('停止使用这整条来源。','withdraw-slow')
        ref='timeline:'+source['timeline_id']
        read=await self.service.tool_sources(event,action='read',source_ref=ref,limit=1)
        self.assertEqual(len(read['sources']),1,read)
        result=await asyncio.wait_for(self.co.withdraw_source(event,source_ref=ref,
            expected_version=read['sources'][0]['source_version'],operation_id='withdraw-slow',confirmed=True),2)
        self.assertTrue(result['ok'],result)
        self.provider.wait.set();await asyncio.wait_for(task,10)
        self.assertFalse(await self.service.store.get_timeline_by_ids([source['timeline_id']]))
        self.assertEqual((await self.service.store.summary_progress())['conversation_memories'],0)
        self.assertEqual(await self.co.recall(self.ctx,'清茶'),[])
        self.assertEqual(archive.snapshot()['inbound'][0]['text'],'我喜欢喝清茶。')

    async def test_withdrawal_between_summary_and_assertions_waits_then_removes_both(self):
        # A timing barrier around the real operation, never a mocked commit.
        original=self.service.store.upsert_assertion
        reached,release=asyncio.Event(),asyncio.Event()
        async def delayed_assertion(record):
            reached.set();await release.wait()
            return await original(record)
        self.service.store.upsert_assertion=delayed_assertion
        pending=None;task=None
        try:
            source=await self.observe('我住在苏州。','commit-race')
            task=self.co.schedule(self.ctx)
            await asyncio.wait_for(reached.wait(),5)
            self.assertTrue(self.co.summary_installation.wrapper.consistency.gate.locked())
            event=self.event('停止使用这整条来源。','withdraw-commit-race')
            ref='timeline:'+source['timeline_id']
            read=await self.service.tool_sources(event,action='read',source_ref=ref,limit=1)
            pending=asyncio.create_task(self.co.withdraw_source(event,source_ref=ref,
                expected_version=read['sources'][0]['source_version'],
                operation_id='withdraw-commit-race',confirmed=True))
            await asyncio.sleep(.03)
            self.assertFalse(pending.done())
            release.set();await asyncio.wait_for(task,10)
            result=await asyncio.wait_for(pending,5)
            self.assertTrue(result['ok'],result)
            self.assertFalse(await self.service.store.get_timeline_by_ids([source['timeline_id']]))
            self.assertEqual(await self.co.recall(self.ctx,'苏州'),[])
            self.assertEqual(await self.service.store.list_memories(memory_type='assertion',limit=10),[])
        finally:
            release.set()
            if task and not task.done():await asyncio.wait_for(task,10)
            if pending and not pending.done():await asyncio.wait_for(pending,5)
            self.service.store.upsert_assertion=original

    async def test_stale_batch_stays_unconsumed_after_reopen_without_extra_request(self):
        old=await self.fact();source,task=await self.block_summary()
        result=await self.co.correct(self.event(),memory_id=old.id,
            expected_version=self.co.revision.memory_ref(old)['version'],
            correction_id='restart-fix',content='用户现在更喜欢白水。')
        self.assertTrue(result['ok'])
        self.provider.wait.set();await task
        await self.co.close();await self.service.aclose()
        self.service=base.NativeService(context=self.host,config=self.config,
            plugin_root=base.UPSTREAM,data_dir=self.path)
        self.co=base.MemoryCoordinator(self.service,self.path/'phase3-controls.sqlite3',
            {'synthetic-user'},enabled=True)
        await self.co.summarize(self.ctx)
        raw=(await self.service.store.get_timeline_by_ids([source['timeline_id']]))[source['timeline_id']]
        self.assertFalse(raw['summarized_at'])
        self.assertEqual(len(self.provider.calls),1)
        self.assertEqual((await self.service.store.summary_progress())['quarantined_batches'],1)

    async def test_shutdown_waits_for_local_commit_before_uninstall(self):
        original=self.service.store.upsert_assertion
        reached,release=asyncio.Event(),asyncio.Event()
        async def delayed_assertion(record):
            reached.set();await release.wait()
            return await original(record)
        self.service.store.upsert_assertion=delayed_assertion
        shutdown=None;task=None
        consistency=self.co.summary_installation.wrapper.consistency
        try:
            await self.observe('我住在苏州。','closing-commit')
            task=self.co.schedule(self.ctx)
            await asyncio.wait_for(reached.wait(),5)
            shutdown=asyncio.create_task(self.co.close())
            await asyncio.sleep(.03)
            self.assertFalse(shutdown.done())
            self.assertTrue(consistency.active)
            self.assertTrue(consistency.gate.locked())
            release.set();await asyncio.wait_for(shutdown,5)
            self.assertTrue(task.cancelled())
            self.assertFalse(consistency.active)
            self.assertFalse(consistency.gate.locked())
            rows=await self.service.store.list_memories(memory_type='assertion',limit=10)
            self.assertEqual(len(rows),1)
        finally:
            release.set()
            if shutdown and not shutdown.done():await asyncio.wait_for(shutdown,5)
            self.service.store.upsert_assertion=original

    async def test_shutdown_cancels_native_provider_worker_before_uninstall(self):
        self.provider.wait=asyncio.Event()
        source=await self.observe('我喜欢喝清茶。','native-worker')
        # The real native summary entry, tracked using its actual worker slot.
        task=asyncio.create_task(self.service.maybe_summarize_session(self.ctx))
        self.service._summary_workers[self.ctx.session_id]=task
        await asyncio.wait_for(self.provider.started.wait(),5)
        consistency=self.co.summary_installation.wrapper.consistency
        await asyncio.wait_for(self.co.close(),3)
        self.assertTrue(task.done())
        self.assertTrue(task.cancelled())
        self.assertFalse(consistency.active)
        raw=(await self.service.store.get_timeline_by_ids([source['timeline_id']]))[source['timeline_id']]
        self.assertFalse(raw['summarized_at'])


if __name__=='__main__':unittest.main()
