"""Synthetic T1 contracts for migration and the shared send boundary."""
import asyncio
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from integrations.phase1_guard.store import GuardStore
from integrations.phase1_guard.delivery import DeliveryReceipt, DeliveryService


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'guard.sqlite3'
        self.store = GuardStore(self.path)
        self.key = ('test', 'test:FriendMessage:10001', 'm1')
        self.store.claim(*self.key, '10001', '原文', [{'type':'text','data':{'text':'原文'}}])
        self.legacy = self.store.begin_outbound(self.key, '旧回复', [])
        self.store.finish(self.legacy, 'sent')
        self.original = self.store.snapshot()
        self.store.enable_phase3()
        self.store.create_turn('t1', *self.key[:2], ['m1'])
        self.service = DeliveryService(self.store, timeout=.03)
        self.calls = 0

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    async def ack(self):
        self.calls += 1
        return DeliveryReceipt(True, 'qq-out-99', 'synthetic_onebot_ack')

    async def send(self, **overrides):
        params = dict(turn_id='t1', text='回复', chain=[{'type':'text','data':{'text':'回复'}}], sender=self.ack)
        params.update(overrides)
        return await self.service.send(**params)

    async def test_multi_output_same_inbound_and_progress_claim(self):
        self.assertTrue(self.store.claim_progress('t1'))
        self.assertFalse(self.store.claim_progress('t1'))
        rows = [await self.send(kind='progress'), await self.send(kind='final'), await self.send(kind='final', part_index=1)]
        self.assertEqual([r.state for r in rows], ['sent']*3)
        self.assertEqual(len({r.delivery_id for r in rows}), 3)
        self.assertEqual(self.store.snapshot(), self.original)
        self.assertEqual(self.store.source_ids('t1'), ['m1'])
        self.assertEqual(self.calls, 3)

    async def test_idempotent_outbound_attempt_cannot_be_repeated(self):
        a = await self.send()
        b = await self.send()
        self.assertEqual(a, b)
        self.assertEqual(self.calls, 1)

    async def test_same_delivery_id_with_changed_content_rejected(self):
        await self.send(delivery_id='d1')
        with self.assertRaisesRegex(ValueError, 'Collision'):
            await self.send(delivery_id='d1', text='另一条')
        self.assertEqual(self.calls, 1)

    async def test_concurrent_progress_final_keeps_real_send_order(self):
        order = []
        async def slow():
            order.append('progress-start')
            await asyncio.sleep(.005)
            order.append('progress-ack')
            return DeliveryReceipt(True)
        async def fast():
            order.append('final-start')
            return DeliveryReceipt(True)
        await asyncio.gather(self.send(kind='progress', sender=slow), self.send(kind='final', sender=fast))
        self.assertEqual(order, ['progress-start','progress-ack','final-start'])

    async def test_timeout_is_unknown_and_not_retried(self):
        async def missing_ack():
            self.calls += 1
            await asyncio.sleep(1)
        a = await self.send(sender=missing_ack)
        b = await self.send(sender=self.ack)
        self.assertEqual((a.state,b.state,self.calls), ('unknown','unknown',1))

    async def test_native_rejection_is_failed(self):
        class ActionFailed(Exception): pass
        async def reject(): raise ActionFailed('synthetic')
        self.assertEqual((await self.send(sender=reject)).state, 'failed')

    async def test_transport_failure_is_conservatively_unknown(self):
        async def disconnect(): raise ConnectionResetError('synthetic')
        self.assertEqual((await self.send(sender=disconnect)).state, 'unknown')

    async def test_none_and_false_are_not_confirmed_receipts(self):
        async def none(): return None
        async def false(): return False
        async def true(): return True
        self.assertEqual((await self.send(sender=none)).state,'unknown')
        self.assertEqual((await self.send(sender=false,part_index=1)).state,'unknown')
        self.assertEqual((await self.send(sender=true,part_index=2)).state,'unknown')

    async def test_critical_attempt_ledger_failure_never_bypasses_transport(self):
        original=self.store.begin_delivery
        def fail(*args,**kwargs):raise sqlite3.OperationalError('synthetic_disk_failure')
        self.store.begin_delivery=fail
        with self.assertRaises(sqlite3.OperationalError):await self.send()
        self.assertEqual(self.calls,0)
        self.store.begin_delivery=original
        self.assertEqual((await self.send()).state,'sent')

    async def test_explicit_no_matching_adapter_is_failed(self):
        async def missing(): return DeliveryReceipt(False)
        self.assertEqual((await self.send(sender=missing)).state,'failed')

    async def test_cancelled_send_is_unknown(self):
        async def cancel(): raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.send(sender=cancel)
        self.assertEqual(self.store.phase3_snapshot()['deliveries'][0]['state'], 'unknown')

    async def test_stale_output_prevented_before_network(self):
        self.assertEqual((await self.send(is_current=lambda:False)).state,'failed')
        self.assertEqual(self.calls,0)

    async def test_empty_output_never_attempts_transport(self):
        self.assertEqual((await self.send(text=' ')).state,'failed')
        self.assertEqual(self.calls,0)

    async def test_outbound_id_is_real_ack_not_inbound_id(self):
        result=await self.send()
        self.assertEqual(result.platform_message_id,'qq-out-99')
        self.assertNotEqual(result.platform_message_id,self.key[2])

    async def test_restart_interruption_retains_archive_without_replay(self):
        self.store.claim_progress('t1')
        self.store.begin_delivery('interrupted','t1','progress',0,'去看看',[])
        self.store.close()
        self.store=GuardStore(self.path)
        self.store.enable_phase3()
        self.assertEqual(self.store.recover_phase3_interrupted(),1)
        self.assertEqual(self.store.recover_phase3_interrupted(),0)
        self.assertFalse(self.store.claim_progress('t1'))
        self.assertEqual(self.store.get_turn('t1')['state'],'interrupted')
        self.assertEqual(self.store.get_delivery('interrupted')['state'],'unknown')
        self.assertEqual(self.store.snapshot(),self.original)

    async def test_proactive_turn_has_no_fabricated_inbound(self):
        self.store.create_turn('p1',*self.key[:2],proactive=True)
        result=await self.send(turn_id='p1',kind='proactive')
        self.assertEqual(result.state,'sent')
        self.assertEqual(self.store.source_ids('p1'),[])
        self.assertEqual(len(self.store.snapshot()['inbound']),1)

    async def test_proactive_cannot_claim_inbound_and_cross_session_rejected(self):
        with self.assertRaises(ValueError):
            self.store.create_turn('p1',*self.key[:2],['m1'],proactive=True)
        with self.assertRaises(ValueError):
            self.store.attach_source('t1',('test','another','m1'))

    async def test_migration_backup_and_old_guard_reopen_are_lossless(self):
        backup=self.path.with_name(self.path.name+'.pre-phase3.sqlite3')
        old=GuardStore(backup)
        self.assertEqual(old.snapshot(),self.original)
        self.assertEqual(old.db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        self.assertIsNone(old.db.execute("SELECT 1 FROM sqlite_master WHERE name='deliveries'").fetchone())
        old.close()
        self.assertEqual(self.store.db.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.store.enable_phase3()
        self.assertEqual(self.store.snapshot(),self.original)

    async def test_unknown_schema_or_existing_backup_fail_closed(self):
        with self.store.db:
            self.store.db.execute('UPDATE guard_schema SET version=99')
        with self.assertRaisesRegex(ValueError,'Unsupported'):
            self.store.enable_phase3()
        other=GuardStore(Path(self.temp.name)/'other.sqlite3')
        try:
            other.path.with_name(other.path.name+'.pre-phase3.sqlite3').write_text('existing')
            with self.assertRaisesRegex(ValueError,'AlreadyExists'):
                other.enable_phase3()
        finally: other.close()

    async def test_sent_requires_receipt_source_and_final_states_immutable(self):
        self.store.begin_delivery('d1','t1','normal',0,'text',[])
        with self.assertRaises(ValueError): self.store.finish_delivery('d1','sent')
        self.store.finish_delivery('d1','unknown')
        with self.assertRaises(ValueError): self.store.finish_delivery('d1','sent',receipt_source='test')

if __name__ == '__main__': unittest.main()
