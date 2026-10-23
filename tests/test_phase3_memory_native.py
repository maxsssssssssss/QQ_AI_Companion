"""Pinned MemoryCompanion real-service/SQLite tests, synthetic provider only.

This is native component integration, NOT full AstrBot startup, real QQ or
DeepSeek. No upstream method, callback, or database content is monkeypatched.
"""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone, timedelta
import importlib
import importlib.util
import json
import re
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from integrations.phase3_memory import MemoryCoordinator, MemoryDenied, native_config
from integrations.phase1_guard.store import GuardStore

UPSTREAM = ROOT / '.runtime/upstream/memory_companion'
PACKAGE = 'phase3_test_native_memory'
if UPSTREAM.is_dir():
    pkg = ModuleType(PACKAGE)
    pkg.__path__ = [str(UPSTREAM)]
    sys.modules.setdefault(PACKAGE, pkg)
    NativeService = importlib.import_module(PACKAGE + '.core.service').MemoryCompanionService
else:
    NativeService = None


class SyntheticProvider:
    def __init__(self, bad=False, wait=None):
        self.calls = []
        self.bad = bad
        self.wait = wait
        self.started = asyncio.Event()

    async def text_chat(self, **kwargs):
        self.calls.append(kwargs)
        self.started.set()
        if self.wait: await self.wait.wait()
        if self.bad: return SimpleNamespace(completion_text='not valid json')
        rows = []
        for line in kwargs['prompt'].splitlines():
            try: row = json.loads(line)
            except ValueError: continue
            if isinstance(row, dict) and 'event_id' in row and 'content' in row:
                rows.append(row)
        facts = [{'fact': row['content'], 'refs': [row['event_id']],
                  'evidence': [{'ref': row['event_id'], 'quote': row['content']}]} for row in rows if row['event_type'] == 'user_message']
        payload = {'outcome': 'memory', 'summary': '我们聊到了：' + ' '.join(row['content'] for row in rows),
                   'summary_refs': [r['event_id'] for r in rows], 'key_facts': facts,
                   'canonical_summary': ' '.join(row['content'] for row in rows), 'importance': 0.7}
        payload['assertions'] = [{'subject': row['speaker_id'], 'predicate': 'residence',
            'value': found.group(0), 'polarity': 'positive', 'durability': 'stable', 'refs': [row['event_id']]}
            for row in rows if row['event_type'] == 'user_message'
            for found in re.finditer(r'(?:现在)?住在[^。！？]+', row['content'])]
        return SimpleNamespace(completion_text=json.dumps(payload, ensure_ascii=False))


@unittest.skipIf(NativeService is None, 'pinned upstream source unavailable; not a PASS')
class NativeMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        actual = subprocess.check_output(['git', '-C', str(UPSTREAM), 'rev-parse', 'HEAD'], text=True).strip()
        self.assertEqual(actual, 'e40a83005b0a2e0817e5680c7d14398c9ab49790')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.provider = SyntheticProvider()
        cfg = native_config({}, enabled=True, provider_id='synthetic')
        cfg['startup'] = {'background_grace_seconds': 60}
        cfg['memory_summary'].update(min_events=1, trigger_event_count=1)
        cfg['retrieval'].update(mode='basic', embedding_provider_id='', rerank_provider_id='')
        self.config = cfg
        self.host = SimpleNamespace(get_provider_by_id=lambda _id: self.provider,
                                    get_using_provider=lambda _sid: self.provider)
        self.service = NativeService(context=self.host, config=cfg,
                                     plugin_root=UPSTREAM, data_dir=self.path)
        self.co = MemoryCoordinator(self.service, self.path / 'phase3-controls.sqlite3', {'synthetic-user'}, enabled=True)
        self.ctx = self.co.context(session_id='fixture:FriendMessage:synthetic-user',
            user_id='synthetic-user', bot_id='synthetic-bot', platform='fixture')
        self.now = datetime.now(timezone.utc).isoformat()

    async def asyncTearDown(self):
        await self.co.close()
        await self.service.aclose()

    async def observe(self, text, source='m1', **kwargs):
        return await self.co.observe(self.ctx, source_id=source, content=text,
                                     occurred_at=self.now, **kwargs)

    async def summary(self, text='我喜欢喝清茶。', source='m1'):
        original = await self.observe(text, source)
        outcome = await self.co.summarize(self.ctx)
        self.assertEqual(outcome['state'], 'completed', outcome)
        return original, await self.service.store.get_memory(outcome['memory_id'])

    async def test_native_extracts_four_categories_with_source_and_time(self):
        texts = ['我的职业是软件工程师。', '我明天下午要参加面试。',
                 '我最喜欢喝清茶。', '我还没有整理完旅行相册。']
        rows = [await self.observe(text, f'm{i}') for i, text in enumerate(texts)]
        result = await self.co.summarize(self.ctx)
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(len(self.provider.calls), 1)
        memory = await self.service.store.get_memory(result['memory_id'])
        for term in ['软件工程师', '参加面试', '清茶', '旅行相册']: self.assertIn(term, memory.content)
        self.assertEqual(memory.reality_level, 'llm_summary')
        self.assertEqual(memory.session_id, self.ctx.session_id)
        sources = await self.service.store.get_timeline_by_ids([r['timeline_id'] for r in rows])
        self.assertEqual(len(sources), 4)
        self.assertTrue(all(r['occurred_at'] for r in sources.values()))
        serialized = json.dumps(memory.metadata)
        self.assertTrue(all(r['timeline_id'] in serialized for r in rows))

    def event(self, text='我之前说错了，我现在更喜欢白水。', message_id='correction-1'):
        return SimpleNamespace(unified_msg_origin=self.ctx.session_id,
            message_str=text, persona_id=self.ctx.persona_id,
            message_obj=SimpleNamespace(message_id=message_id),
            get_platform_name=lambda: self.ctx.platform,
            get_sender_id=lambda: self.ctx.user_id, get_self_id=lambda: self.ctx.bot_id)

    async def test_native_correction_preserves_history_and_survives_reopen(self):
        record = self.co.models.MemoryRecord(id='synthetic-fact', memory_type='observation',
            subject=self.co.models.EntityRef(kind='user', id=self.ctx.user_id),
            object=self.co.models.EntityRef.bot_self(self.ctx.bot_id),
            owner_bot_id=self.ctx.bot_id, scope='private', platform=self.ctx.platform,
            session_id=self.ctx.session_id, visibility='private_pair', lifecycle='stable_memory',
            reality_level='real_user_fact', content='用户喜欢清茶。', evidence='我喜欢清茶。',
            metadata={'persona_id':self.ctx.persona_id,'owner_bot_id':self.ctx.bot_id})
        await self.service.store.insert_memory(record)
        old = await self.service.store.get_memory(record.id)
        event = self.event()
        kwargs = dict(memory_id=old.id, expected_version=self.co.revision.memory_ref(old)['version'],
                      correction_id='fix-1', content='用户现在更喜欢白水。')
        denied = await self.co.correct(event, critical=True, **kwargs)
        self.assertEqual(denied['reason_code'], 'explicit_confirmation_required')
        result = await self.co.correct(event, **kwargs)
        self.assertTrue(result['ok'], result)
        self.assertFalse(self.co.revision.current_memory(await self.service.store.get_memory(old.id)))
        new_id = result['new_ref']['id']
        await self.co.close(); await self.service.aclose()
        self.service = NativeService(context=self.host, config=self.config, plugin_root=UPSTREAM, data_dir=self.path)
        self.co = MemoryCoordinator(self.service, self.path / 'phase3-controls.sqlite3', {'synthetic-user'}, enabled=True)
        new = await self.service.store.get_memory(new_id)
        self.assertEqual(new.supersedes_id, old.id)
        self.assertEqual(new.evidence, event.message_str)
        self.assertTrue(self.co.revision.current_memory(new))
        results = await self.co.recall(self.ctx, '白水')
        self.assertIn(new_id, [x.memory.id for x in results])
        self.assertNotIn(old.id, [x.memory.id for x in results])

    async def test_native_source_withdrawal_cannot_reextract_replayed_source(self):
        source, memory = await self.summary()
        event = self.event('这条资料不要再用了。', 'withdraw-1')
        row = (await self.service.store.get_timeline_by_ids([source['timeline_id']]))[source['timeline_id']]
        source_ref = 'timeline:' + source['timeline_id']
        version = self.co.source_evidence.message_source_version(row)
        # The native withdrawal requires a real prior source-read receipt.
        denied = await self.co.withdraw_source(event, source_ref=source_ref,
            expected_version=version, operation_id='withdraw-1', confirmed=True)
        self.assertFalse(denied['ok'])
        self.co.source_evidence.remember_source_reads(event, self.ctx,
            [{'source_ref':source_ref,'source_version':version}])
        result = await self.co.withdraw_source(event, source_ref=source_ref,
            expected_version=version, operation_id='withdraw-1', confirmed=True)
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['raw_archive_retained'])
        self.assertIsNone(await self.service.store.get_memory(memory.id))
        replay = await self.service.record_visible_turn(role='user',content='我喜欢喝清茶。',
            scope='private',session_id=self.ctx.session_id,platform=self.ctx.platform,
            user_id=self.ctx.user_id,message_id='m1',source='phase3_confirmed_archive',
            metadata={'owner_bot_id':self.ctx.bot_id,'bot_id':self.ctx.bot_id,
                      'platform':self.ctx.platform,'persona_id':self.ctx.persona_id},occurred_at=self.now)
        self.assertEqual(replay, '')
        self.assertEqual(await self.co.recall(self.ctx,'清茶'), [])

    async def test_duplicate_rejected_and_conflicting_source_fails(self):
        a = await self.observe('我喜欢清茶。')
        b = await self.observe('我喜欢清茶。')
        self.assertEqual(a['timeline_id'], b['timeline_id'])
        self.assertEqual(b['state'], 'deduplicated')
        with self.assertRaises(MemoryDenied): await self.observe('变更过的原文。')

    async def test_unknown_and_progress_never_become_user_facts(self):
        for status in ['unknown', 'failed', 'pending']:
            result = await self.observe('未确认投递的回复', status, role='assistant', delivery_status=status)
            self.assertEqual(result['state'], 'excluded')
        result = await self.observe('我去查一下', 'progress-1', role='assistant', kind='progress')
        self.assertEqual(result['state'], 'excluded')
        self.assertEqual(self.co.status()['source_count'], 0)

    async def test_native_recall_after_reopen_without_recent_context(self):
        _, memory = await self.summary('我最喜欢的饮品是清茶。')
        await self.co.close(); await self.service.aclose()
        self.service = NativeService(context=self.host, config=self.config, plugin_root=UPSTREAM, data_dir=self.path)
        self.co = MemoryCoordinator(self.service, self.path / 'phase3-controls.sqlite3', {'synthetic-user'}, enabled=True)
        results = await self.co.recall(self.ctx, '清茶')
        self.assertIn(memory.id, [x.memory.id for x in results])
        self.assertEqual(len(self.provider.calls), 1)

    async def test_bad_extraction_is_bounded_and_raw_retained(self):
        self.provider.bad = True
        source = await self.observe('我喜欢茶。')
        for _ in range(4): await self.co.summarize(self.ctx)
        self.assertEqual(len(self.provider.calls), 1)
        self.assertTrue(await self.service.store.get_timeline_by_ids([source['timeline_id']]))
        progress = await self.service.store.summary_progress()
        self.assertEqual(progress['quarantined_batches'], 1)

    async def test_hourly_budget_persists_across_batches(self):
        self.config['memory_summary']['max_calls_per_session_hour'] = 1
        await self.summary()
        await self.observe('我喜欢蓝色。', 'm2')
        await self.co.summarize(self.ctx)
        self.assertEqual(len(self.provider.calls), 1)

    async def test_idle_archive_tick_resumes_native_budget_after_reopen(self):
        self.config['memory_summary']['max_calls_per_session_hour'] = 1
        await self.summary()
        await self.observe('我喜欢蓝色。', 'budget-later')
        await self.co.summarize(self.ctx)
        self.co.remember_context(self.ctx)
        store = self.archive()
        self.co.bind_archive(store)
        await self.co.close();await self.service.aclose()
        self.config['memory_summary']['enabled'] = True
        self.service = NativeService(context=self.host, config=self.config,
                                    plugin_root=UPSTREAM, data_dir=self.path)
        self.co = MemoryCoordinator(self.service, self.path/'phase3-controls.sqlite3',
                                    {'synthetic-user'}, enabled=True)
        self.assertEqual(await self.co.drain_archive(store), 0)
        self.assertEqual(self.co.summary_tasks, {})
        batches = importlib.import_module(PACKAGE+'.core.summary_batches')
        future = datetime.now(timezone.utc)+timedelta(hours=1, seconds=2)
        class Later(datetime):
            @classmethod
            def now(cls, tz=None):return future if tz else future.replace(tzinfo=None)
        with patch.object(batches, 'datetime', Later), patch.object(batches, 'utc_now', lambda:future.isoformat(timespec='seconds')):
            self.assertEqual(await self.co.drain_archive(store), 0)
            await asyncio.gather(*self.co.summary_tasks.values())
        self.assertEqual(len(self.provider.calls), 2)
        self.assertEqual((await self.service.store.summary_progress())['conversation_memories'], 2)
        self.assertEqual(await self.co.drain_archive(store), 0)
        self.assertEqual(len(self.provider.calls), 2)

    async def test_idle_due_check_respects_quiet_trigger_and_pause(self):
        self.config['memory_summary'].update(trigger_event_count=12, trigger_interval_minutes=30)
        self.assertFalse(await self.co.resume_due_summary(self.ctx))
        await self.observe('我喜欢安静地听纯音乐。', 'quiet-window')
        self.assertFalse(await self.co.resume_due_summary(self.ctx))
        summarizer = importlib.import_module(PACKAGE+'.core.summarizer')
        future = datetime.now(timezone.utc)+timedelta(minutes=31)
        class Later(datetime):
            @classmethod
            def now(cls, tz=None):return future if tz else future.replace(tzinfo=None)
        with patch.object(summarizer, 'datetime', Later):
            self.assertTrue(await self.co.resume_due_summary(self.ctx))
            await asyncio.gather(*self.co.summary_tasks.values())
        self.assertEqual(len(self.provider.calls), 1)
        await self.observe('我喜欢蓝色。', 'paused-window')
        await self.co.pause(self.ctx)
        self.assertFalse(await self.co.resume_due_summary(self.ctx))
        self.assertEqual(len(self.provider.calls), 1)

    async def test_background_notification_is_coalesced(self):
        self.provider.wait = asyncio.Event()
        await self.observe('我喜欢清茶。')
        tasks = [self.co.schedule(self.ctx) for _ in range(10)]
        self.assertEqual(len({id(t) for t in tasks}), 1)
        await asyncio.sleep(.05)
        self.provider.wait.set()
        await asyncio.wait_for(tasks[0], 5)
        self.assertEqual(len(self.provider.calls), 1)

    async def test_pause_cancels_inflight_summary_without_saving_late_output(self):
        self.provider.wait = asyncio.Event()
        await self.observe('我的合成偏好是安静地听纯音乐。')
        task = self.co.schedule(self.ctx)
        await asyncio.wait_for(self.provider.started.wait(), 10)
        self.assertEqual(len(self.provider.calls), 1)
        await self.co.pause(self.ctx)
        self.provider.wait.set()
        self.assertTrue(task.cancelled())
        self.assertEqual(await self.co.recall(self.ctx, '信息'), [])
        self.assertEqual(self.co.status()['job_counts'].get('interrupted'), 1)

    async def test_native_configuration_disables_extra_models_and_raw_cleanup(self):
        cfg = native_config({}, enabled=True, provider_id='synthetic')
        self.assertEqual(cfg['retrieval']['mode'], 'basic')
        self.assertFalse(cfg['retrieval']['embedding_enabled'])
        self.assertFalse(cfg['portrait']['daily_dispatch_enabled'])
        self.assertFalse(cfg['maintenance']['auto_schedule_enabled'])
        self.assertEqual(cfg['maintenance']['retention_summarized_timeline_days'], 0)
        self.assertFalse(cfg['memory_capture']['capture_bot_responses'])
        self.assertEqual(cfg['memory_summary']['max_retries'], 1)

    async def test_pause_blocks_native_capture_and_recall_after_reopen(self):
        await self.summary()
        await self.co.pause(self.ctx)
        self.assertEqual(await self.co.recall(self.ctx, '清茶'), [])
        self.assertFalse(self.service._scope_feature_enabled(self.ctx, 'recall'))
        self.assertFalse(self.service._scope_feature_enabled(self.ctx, 'capture'))
        self.assertEqual((await self.observe('不能再次提取', 'm2'))['state'], 'paused')
        await self.co.close(); await self.service.aclose()
        self.service = NativeService(context=self.host, config=self.config, plugin_root=UPSTREAM, data_dir=self.path)
        self.co = MemoryCoordinator(self.service, self.path / 'phase3-controls.sqlite3', {'synthetic-user'}, enabled=True)
        self.assertEqual(await self.co.recall(self.ctx, '清茶'), [])
        self.assertFalse(self.service._scope_feature_enabled(self.ctx, 'capture'))

    async def test_event_reschedule_updates_same_identity_and_closes(self):
        _, memory = await self.summary('我明天下午要参加面试。')
        first = await self.co.event_update(self.ctx, event_id='interview', memory_id=memory.id,
            state='scheduled', source_id='m1', due_at=self.now)
        self.assertEqual(first['revision'], 1)
        await self.observe('面试改到后天。', 'm2')
        second = await self.co.event_update(self.ctx, event_id='interview', memory_id=memory.id,
            state='awaiting_result', source_id='m2', due_at=self.now, expected_revision=1)
        self.assertEqual(second['revision'], 2)
        self.assertEqual(len(self.co.due_events(self.ctx, now=self.now)), 1)
        await self.co.event_update(self.ctx, event_id='interview', memory_id=memory.id,
            state='completed', source_id='m2', expected_revision=2)
        self.assertEqual(self.co.due_events(self.ctx, now=self.now), [])
        with self.assertRaises(MemoryDenied):
            await self.co.event_update(self.ctx, event_id='interview', memory_id=memory.id,
                state='scheduled', source_id='m2', expected_revision=3)
        self.assertEqual(self.co.db.execute('SELECT COUNT(*) FROM event_history').fetchone()[0], 3)

    async def test_event_expiry_and_cancel_do_not_produce_candidates(self):
        _, memory = await self.summary('旅行安排尚待确定。')
        for state in ['cancelled', 'do_not_discuss', 'expired', 'responded']:
            await self.co.event_update(self.ctx, event_id=state, memory_id=memory.id,
                state=state, source_id='m1', due_at=self.now)
        self.assertEqual(self.co.due_events(self.ctx, now=self.now), [])

    async def test_allowlist_and_disabled_are_fail_closed(self):
        with self.assertRaises(MemoryDenied):
            await self.co.recall(replace(self.ctx, user_id='other'), '茶')
        with self.assertRaises(MemoryDenied):
            await self.co.recall(replace(self.ctx, scope='group', group_id='group'), '茶')
        self.co.enabled = False
        with self.assertRaises(MemoryDenied): await self.co.recall(self.ctx, '茶')

    async def test_native_assertion_domain_revision_archives_stale_summary(self):
        _, old_summary = await self.summary('我住在苏州。', 'address-1')
        old_assertions = await self.service.store.list_memories(memory_type='assertion', session_id=self.ctx.session_id)
        self.assertEqual(len(old_assertions), 1)
        _, new_summary = await self.summary('我现在住在杭州。', 'address-2')
        old = await self.service.store.get_memory(old_assertions[0].id)
        self.assertEqual(old.validity_status, 'superseded')
        new = await self.service.store.get_memory(old.supersedes_id)
        self.assertIn('杭州', new.content)
        self.assertEqual((await self.service.store.get_memory(old_summary.id)).lifecycle, 'archived')
        results = await self.co.recall(self.ctx, '住在')
        self.assertFalse(any(r.memory.id in {old.id, old_summary.id} for r in results))
        await self.co.close(); await self.service.aclose()
        self.service = NativeService(context=self.host, config=self.config, plugin_root=UPSTREAM, data_dir=self.path)
        self.co = MemoryCoordinator(self.service, self.path / 'phase3-controls.sqlite3', {'synthetic-user'}, enabled=True)
        results = await self.co.recall(self.ctx, '杭州')
        self.assertTrue(any('杭州' in r.memory.content for r in results))
        self.assertEqual((await self.service.store.get_memory(old.id)).supersedes_id, new.id)

    async def test_profile_review_consent_import_and_rollback(self):
        entries = [{'id': 'fixture-1', 'layer': 'L2', 'text': '合成用户喜欢天文。',
                    'source_ref': 'fixture:line1', 'confidence_status': 'user_confirmed'}]
        preview = self.co.preview_profile(entries)
        approval = {'manifest_hash': preview['manifest_hash'], 'layers': ['L2'],
                    'isolated_import': True, 'allow_deepseek_use': True}
        with self.assertRaises(MemoryDenied):
            await self.co.import_profile(self.ctx, entries, batch_id='batch-fixture', approval=approval, environment='production')
        result = await self.co.import_profile(self.ctx, entries, batch_id='batch-fixture', approval=approval)
        self.assertEqual(result['state'], 'isolated_imported')
        record = await self.service.store.get_memory(result['memory_ids'][0])
        self.assertFalse(record.metadata['shared_experience'])
        self.assertEqual(record.metadata['source_ref'], 'fixture:line1')
        self.assertEqual(record.metadata['owner_bot_id'], self.ctx.bot_id)
        # Simulate interruption after the native write, before the reference
        # ledger commits completion. Native content is not edited for the test.
        with self.co.db:
            self.co.db.execute("UPDATE imports SET state='pending' WHERE batch_id='batch-fixture'")
        replay = await self.co.import_profile(self.ctx, entries, batch_id='batch-fixture', approval=approval)
        self.assertEqual(replay['memory_ids'], result['memory_ids'])
        with self.assertRaises(MemoryDenied):
            await self.co.import_profile(replace(self.ctx, bot_id='other-bot'), entries,
                batch_id='batch-fixture', approval=approval)
        self.assertEqual((await self.co.rollback_profile(batch_id='batch-fixture'))['count'], 1)
        self.assertIsNone(await self.service.store.get_memory(record.id))
        self.assertEqual(len(self.provider.calls), 0)

    async def test_unreviewed_profile_and_unbounded_temporal_entries_rejected(self):
        for row in [
            {'id':'x','layer':'L1','text':'推断','source_ref':'fixture','confidence_status':'inferred'},
            {'id':'x','layer':'L3','text':'近期','source_ref':'fixture','confidence_status':'user_confirmed'},
            {'id':'x','layer':'L2','text':'外部经历','source_ref':'fixture','confidence_status':'user_confirmed','shared_experience':True},
        ]:
            with self.assertRaises(MemoryDenied): self.co.preview_profile([row])

    def archive(self):
        store = GuardStore(self.path / 'guard.sqlite3')
        store.enable_phase3()
        self.addCleanup(store.close)
        return store

    async def test_archive_boundary_burst_cursor_and_restart_identity(self):
        store = self.archive()
        store.claim('fixture', self.ctx.session_id, 'before-activation', self.ctx.user_id, '旧资料不擅自导入。', [])
        self.co.bind_archive(store)
        self.co.remember_context(self.ctx)
        for i in range(35):
            store.claim('fixture', self.ctx.session_id, f'archive-{i}', self.ctx.user_id, f'合成原文条目 {i}', [])
        self.assertEqual(await self.co.drain_archive(store, limit=32), 32)
        await self.co.close()
        self.co = MemoryCoordinator(self.service, self.path / 'phase3-controls.sqlite3', {'synthetic-user'}, enabled=True)
        self.co.bind_archive(store)
        recovered = self.co.context_for_session(self.ctx.session_id)
        self.assertEqual(recovered.bot_id, self.ctx.bot_id)
        self.assertEqual(recovered.message_text, '')
        self.assertEqual(await self.co.drain_archive(store, limit=32), 3)
        self.assertEqual(await self.co.drain_archive(store), 0)
        self.assertEqual(self.co.status()['source_count'], 35)
        self.assertFalse(self.co.db.execute("SELECT 1 FROM sources WHERE source_id='before-activation'").fetchone())
        with self.assertRaises(MemoryDenied):
            self.co.remember_context(replace(self.ctx, bot_id='different-bot'))

    async def test_archive_pending_delivery_recovery_excludes_nonfacts(self):
        store = self.archive()
        self.co.bind_archive(store)
        self.co.remember_context(self.ctx)
        store.create_turn('turn', 'fixture', self.ctx.session_id, proactive=True)
        for idx, kind in enumerate(('normal', 'progress', 'final')):
            store.begin_delivery(f'delivery-{idx}', 'turn', kind, idx, f'合成输出{idx}', [])
        store.finish_delivery('delivery-1', 'sent', receipt_source='synthetic_ack')
        store.finish_delivery('delivery-2', 'sent', receipt_source='synthetic_ack')
        self.assertEqual(await self.co.drain_archive(store), 0)
        store.finish_delivery('delivery-0', 'unknown', 'Interrupted')
        self.assertEqual(await self.co.drain_archive(store), 3)
        self.assertEqual([r[0] for r in self.co.db.execute('SELECT source_id FROM sources')], ['delivery-2'])
        self.assertEqual(await self.co.drain_archive(store), 0)

    async def test_archive_failure_keeps_cursor_and_native_pause_discards_backlog(self):
        store = self.archive()
        self.co.bind_archive(store)
        self.co.remember_context(self.ctx)
        store.claim('fixture', self.ctx.session_id, 'm1', self.ctx.user_id, '相同ID但不同内容', [])
        await self.observe('已收录原始内容', 'm1')
        with self.assertRaises(MemoryDenied):
            await self.co.drain_archive(store)
        self.assertIsNone(self.co.db.execute('SELECT row_id FROM archive_cursors').fetchone())
        await self.co.pause(self.ctx)
        self.assertEqual(await self.co.drain_archive(store), 1)
        self.assertEqual(self.co.status()['source_count'], 1)

    async def test_event_prompt_receipt_is_not_a_user_response(self):
        _, memory = await self.summary('我明天下午有面试。')
        await self.co.event_update(self.ctx, event_id='interview', memory_id=memory.id,
            state='awaiting_result', source_id='m1', due_at=self.now)
        self.assertTrue(self.co.proactive_event(self.ctx, 'interview', now=self.now)['eligible'])
        with self.assertRaises(MemoryDenied):
            self.co.mark_event_prompted(self.ctx, 'interview', expected_revision=1,
                                       delivery={'state':'unknown','delivery_id':'d1'})
        result = self.co.mark_event_prompted(self.ctx, 'interview', expected_revision=1,
                                             delivery={'state':'sent','delivery_id':'d1'})
        self.assertEqual(result['event_state'], 'awaiting_result')
        self.assertFalse(self.co.proactive_event(self.ctx, 'interview', now=self.now)['eligible'])
        await self.co.close()
        self.co = MemoryCoordinator(self.service, self.path / 'phase3-controls.sqlite3', {'synthetic-user'}, enabled=True)
        self.assertTrue(self.co.proactive_event(self.ctx, 'interview', now=self.now)['prompted'])
        self.assertFalse(self.co.proactive_event(self.ctx, 'missing', now=self.now)['eligible'])

    async def test_source_bound_native_event_create_reschedule_complete(self):
        quote = '2030-10-22 15:00 的面试，记录为待发生。'
        await self.observe(quote, 'event-original')
        created = await self.co.user_event(self.ctx, event_id='interview', state='scheduled',
            quote=quote, original_text=quote, source_id='event-original', occurred_at=self.now,
            due_at='2030-10-22T15:00:00+08:00')
        self.assertEqual(created['revision'], 1)
        old_memory = self.co.db.execute('SELECT memory_id FROM events').fetchone()[0]
        updated = '面试改到 2030-10-23 15:00，等待结果。'
        await self.observe(updated, 'event-update')
        await self.co.user_event(self.ctx, event_id='interview', state='awaiting_result',
            quote=updated, original_text=updated, source_id='event-update', occurred_at=self.now,
            due_at='2030-10-23T15:00:00+08:00', expected_revision=1)
        self.assertEqual((await self.service.store.get_memory(old_memory)).lifecycle, 'archived')
        completed = '面试完成，通过了。'
        await self.observe(completed, 'event-completed')
        await self.co.user_event(self.ctx, event_id='interview', state='completed',
            quote=completed, original_text=completed, source_id='event-completed', occurred_at=self.now,
            expected_revision=2)
        rows = await self.co.list_events(self.ctx)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['state'], rows[0]['revision']), ('completed',3))
        self.assertEqual(self.co.due_events(self.ctx, now='2030-10-24T00:00:00Z'), [])

    async def test_event_tool_evidence_rejects_fabricated_time_and_status(self):
        text = '明天有一场面试。'
        await self.observe(text)
        base = dict(event_id='interview', state='scheduled', quote=text, original_text=text,
                    source_id='m1', occurred_at=self.now)
        with self.assertRaises(MemoryDenied):
            await self.co.user_event(self.ctx, **(base | {'due_at':'2030-10-23T15:00:00+08:00'}))
        with self.assertRaises(MemoryDenied):
            await self.co.user_event(self.ctx, **(base | {'state':'completed'}))
        with self.assertRaises(MemoryDenied):
            await self.co.user_event(self.ctx, **(base | {'original_text':'不同来源内容'}))
        self.assertEqual(await self.co.list_events(self.ctx), [])

    async def test_critical_identity_change_needs_archived_confirmation(self):
        store = self.archive()
        text = '更正：我住在杭州。'
        store.claim('fixture', self.ctx.session_id, 'identity-change', self.ctx.user_id, text, [])
        result = await self.observe(text, 'identity-change')
        self.assertEqual(result['state'], 'identity_confirmation_required')
        self.assertEqual(self.co.status()['source_count'], 0)
        with self.assertRaises(MemoryDenied):
            await self.co.confirm_identity_change(self.ctx, store, confirmation_id='not-real', text='确认这次身份变更')
        store.claim('fixture', self.ctx.session_id, 'confirmed', self.ctx.user_id, '确认这次身份变更', [])
        result = await self.co.confirm_identity_change(self.ctx, store, confirmation_id='confirmed', text='确认这次身份变更')
        self.assertEqual(result['state'], 'approved')
        await self.co.summary_tasks[self.co._scope(self.ctx)]
        self.assertEqual(self.co.status()['source_count'], 1)
        self.assertTrue(any('杭州' in r.memory.content for r in await self.co.recall(self.ctx,'杭州')))
        self.assertEqual(self.co.db.execute('SELECT confirmation_id FROM identity_reviews').fetchone()[0], 'confirmed')

    async def test_natural_control_uses_native_source_read_and_withdrawal(self):
        await self.summary('我的合成保密标签是紫藤27。')
        command = '停止使用关于紫藤27的整条来源，保留原始档案。'
        event = self.event(command, 'control-1')
        found = await self.co.control_memory(event, action='search', query='紫藤27')
        self.assertTrue(found['ok'], found)
        source = found['sources'][0]
        with self.assertRaises(MemoryDenied):
            await self.co.control_memory(event, action='stop_use', query='紫藤27', quote='不相关资料',
                source_ref=source['source_ref'], expected_version=source['source_version'])
        result = await self.co.control_memory(event, action='stop_use', query='紫藤27', quote=command,
            source_ref=source['source_ref'], expected_version=source['source_version'])
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['raw_archive_retained'])
        self.assertEqual((await self.observe(command, 'control-1'))['state'], 'control_not_a_memory_fact')
        self.assertEqual(await self.co.recall(self.ctx, '紫藤27'), [])

    async def test_no_mention_suppresses_native_recall_and_survives_reopen(self):
        await self.summary('我的合成保密标签是月白31。')
        command = '别再提关于月白31的资料，停止使用整条来源，保留原始档案。'
        event = self.event(command, 'no-mention-1')
        found = await self.co.control_memory(event, action='search', query='月白31')
        source = found['sources'][0]
        result = await self.co.control_memory(event, action='no_mention', query='月白31', quote=command,
            source_ref=source['source_ref'], expected_version=source['source_version'])
        self.assertTrue(result['ok'], result)
        self.assertEqual(await self.co.recall(self.ctx, '月白31'), [])
        await self.co.close()
        await self.service.aclose()
        self.service = NativeService(context=self.host, config=self.config, plugin_root=UPSTREAM, data_dir=self.path)
        self.co = MemoryCoordinator(self.service, self.path / 'phase3-controls.sqlite3', {'synthetic-user'}, enabled=True)
        self.assertEqual(await self.co.recall(self.ctx, '月白31'), [])
        replay = await self.observe('我的合成保密标签是月白31。')
        self.assertIn(replay['state'], {'deduplicated', 'suppressed'})

    async def test_cold_start_rejects_unapproved_native_backlog(self):
        self.co.disable_runtime()
        self.co.remember_context(self.ctx)
        await self.service.record_visible_turn(role='user', content='未经批准的旧原生记录。',
            scope='private', session_id=self.ctx.session_id, platform=self.ctx.platform,
            user_id=self.ctx.user_id, message_id='legacy-unapproved', occurred_at=self.now)
        with self.assertRaisesRegex(MemoryDenied, 'old_native_timeline_requires_approval'):
            await self.co.activate_runtime()
        self.assertFalse(self.service.config.bool('memory_summary.enabled', True))
        self.assertEqual(len(self.provider.calls), 0)
        self.assertEqual((await self.service.store.unsummarized_timeline_backlog())['unsummarized_events'], 1)

    async def test_cold_start_allows_only_previously_authorized_sources(self):
        self.co.disable_runtime()
        self.co.remember_context(self.ctx)
        await self.observe('这是一条已授权但尚未提取的合成偏好。')
        self.assertEqual(len(self.provider.calls), 0)
        await self.co.activate_runtime()
        self.assertTrue(self.service.config.bool('memory_summary.enabled', False))
        result = await self.co.summarize(self.ctx)
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(len(self.provider.calls), 1)

    async def test_reviewed_backlog_exact_consent_and_later_restart(self):
        self.co.disable_runtime()
        await self.service.record_visible_turn(role='user', content='已审核的旧合成事实。',
            scope='private', session_id=self.ctx.session_id, platform=self.ctx.platform,
            user_id=self.ctx.user_id, message_id='legacy-reviewed', occurred_at=self.now)
        manifest = await self.co.backlog_manifest(self.ctx.session_id)
        self.assertEqual(manifest['count'], 1)
        self.assertNotIn('旧合成事实', json.dumps(manifest,ensure_ascii=False))
        with self.assertRaisesRegex(MemoryDenied,'explicit_backlog_model_consent_required'):
            await self.co.activate_runtime(approved_backlog={**manifest,'approved':True})
        approval = {**manifest,'approved':True,'allow_model_use':True}
        await self.co.activate_runtime(approved_backlog=approval)
        self.assertEqual((await self.co.summarize(self.ctx))['state'], 'completed')
        self.co.disable_runtime()
        # Already approved IDs remain auditable after the native queue drains.
        await self.co.activate_runtime(approved_backlog=approval)
        self.assertTrue(self.service.config.bool('memory_summary.enabled',False))

    async def test_reviewed_backlog_rejects_new_unknown_record(self):
        self.co.disable_runtime()
        for i in range(2):
            await self.service.record_visible_turn(role='user', content=f'旧合成事实{i}。',
                scope='private', session_id=self.ctx.session_id, platform=self.ctx.platform,
                user_id=self.ctx.user_id, message_id=f'legacy{i}', occurred_at=self.now)
            if i==0: manifest=await self.co.backlog_manifest(self.ctx.session_id)
        with self.assertRaisesRegex(MemoryDenied,'backlog_manifest_changed_review_again'):
            await self.co.activate_runtime(approved_backlog={**manifest,'approved':True,'allow_model_use':True})
        self.assertEqual(len(self.provider.calls),0)

    async def test_source_with_multiple_facts_requires_whole_source_confirmation(self):
        await self.summary('我的合成标签是木棉19。我喜欢听纯音乐。')
        command='别再提木棉19。'
        event=self.event(command,'privacy-review')
        source=(await self.co.control_memory(event,action='search',query='木棉19'))['sources'][0]
        result=await self.co.control_memory(event,action='no_mention',query='木棉19',quote=command,
            source_ref=source['source_ref'],expected_version=source['source_version'])
        self.assertFalse(result['ok'])
        self.assertEqual(result['state'],'confirmation_required')
        self.assertEqual(result['impact'],'whole_source_and_dependent_memories')
        self.assertTrue(await self.co.recall(self.ctx,'纯音乐'))

    async def test_read_only_backlog_export_matches_native_manifest(self):
        spec=importlib.util.spec_from_file_location('backlog_review',ROOT/'scripts/phase3-review-native-backlog.py')
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.co.disable_runtime()
        await self.observe('只读审批的合成原文不能写进manifest。')
        expected=await self.co.backlog_manifest(self.ctx.session_id)
        exported=module.export_manifest(self.service.store.db_path,self.ctx.session_id)
        self.assertEqual({k:v for k,v in exported.items() if k not in {'approved','allow_model_use'}},expected)
        self.assertFalse(exported['approved'])
        self.assertFalse(exported['allow_model_use'])
        output=self.path/'review.json'
        module.save_private(exported,output)
        self.assertEqual(output.stat().st_mode & 0o777,0o600)
        self.assertNotIn('合成原文',output.read_text())
        with self.assertRaises(FileExistsError): module.save_private(exported,output)

    def runtime_fixture(self):
        sys.path.insert(0,str(ROOT/'.runtime/upstream/AstrBot'))
        os.environ.setdefault('ASTRBOT_DISABLE_METRICS','1')
        os.environ.setdefault('ASTRBOT_ROOT',str(self.path/'host'))
        from integrations.phase3_runtime import main as runtime_module
        obj=runtime_module.Phase3Runtime.__new__(runtime_module.Phase3Runtime)
        obj.ready=False
        obj.prepared=True
        obj.memory=self.co
        obj.proactive=obj.proactive_bridge=None
        obj.tasks=set()
        obj.memory_wake=asyncio.Event()
        obj.metrics={'memory_observations':0,'memory_errors':0}
        store=GuardStore(self.path/'runtime-archive.sqlite3')
        store.enable_phase3()
        self.addCleanup(store.close)
        obj.guard=SimpleNamespace(store=store,unregister_delivery_observer=lambda callback:None)
        self.co.bind_archive(store)
        return obj,runtime_module

    async def test_runtime_reprepare_denied_until_idempotent_terminate(self):
        obj,module=self.runtime_fixture()
        with patch.object(module,'get_astrbot_data_path',return_value=str(self.path/'host/data')):
            await obj.activate()
        original_memory=obj.memory
        with self.assertRaisesRegex(ValueError,'terminate_required'):
            await obj.prepare('synthetic-user','fixture')
        self.assertIs(obj.memory,original_memory)
        self.assertEqual(len(obj.tasks),1)
        await obj.terminate()
        await obj.terminate()
        self.assertFalse(obj.ready)
        self.assertFalse(obj.prepared)
        self.assertIsNone(obj.memory)
        self.assertFalse(self.service.config.bool('memory_summary.enabled',True))

    async def test_l3_preview_does_not_authorize_unverified_expiry_import(self):
        entries=[{'id':'temporary','layer':'L3','text':'近期合成资料。','source_ref':'synthetic:1',
            'confidence_status':'user_confirmed','occurred_at':self.now,'expires_at':'2030-01-01T00:00:00+00:00'}]
        preview=self.co.preview_profile(entries)
        with self.assertRaisesRegex(MemoryDenied,'expiry_not_verified'):
            await self.co.import_profile(self.ctx,entries,batch_id='temporal',approval={
                'manifest_hash':preview['manifest_hash'],'layers':['L3'],'isolated_import':True,'allow_deepseek_use':True})
        self.assertEqual(self.co.db.execute('SELECT COUNT(*) FROM imports').fetchone()[0],0)

    async def test_runtime_activation_failure_disables_native_memory(self):
        obj,module=self.runtime_fixture()
        class BrokenBridge:
            def attach(self): self.attached=True
            def activate_native_config(self): raise PermissionError('synthetic_approval_failure')
            def detach(self): self.attached=False
        obj.proactive=SimpleNamespace(enabled=False,close=lambda:None)
        obj.proactive_bridge=BrokenBridge()
        with patch.object(module,'get_astrbot_data_path',return_value=str(self.path/'host/data')):
            with self.assertRaises(PermissionError): await obj.activate()
        self.assertFalse(obj.ready)
        self.assertFalse(obj.proactive.enabled)
        self.assertFalse(obj.proactive_bridge.attached)
        self.assertFalse(self.service.config.bool('memory_summary.enabled',True))
        self.assertEqual(len(self.provider.calls),0)
        await obj.terminate()


if __name__ == '__main__': unittest.main()
