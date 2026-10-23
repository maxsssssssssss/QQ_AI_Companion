"""Reference-only governance around MemoryCompanion 2.3.1 public service APIs.

Content, extraction, retrieval and correction remain in MemoryCompanion. This
SQLite file contains IDs, consent, lifecycle state and job observations only;
Guard/Recorder remain the independent original-message archive. No background
Agent, vector store or extraction prompt is introduced here.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone, timedelta
import hashlib
import importlib
import json
import os
import re
from pathlib import Path
import sqlite3
import time


class MemoryDenied(ValueError):
    """An operation did not have the required scope, source or consent."""


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def instant(value):
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise MemoryDenied('timezone_required')
    return parsed.astimezone(timezone.utc).isoformat()


def resolve_memory_service(context):
    found = [p.star_cls.service for p in context.get_all_stars()
             if str(p.module_path).endswith('.astrbot_plugin_memory_companion.main')
             and p.activated and p.star_cls is not None and str(p.version) == '2.3.1']
    if len(found) != 1:
        raise MemoryDenied('pinned_memory_service_required')
    return found[0]


def native_config(original, *, enabled=False, provider_id=''):
    """Return a candidate configuration; never mutate a running installation.

    The confirmed delivery owner calls observe(). Disable the parallel native
    before-send capture path so unknown/failed output is never shared history.
    Native summaries use their own persisted batch/hour budgets and workers.
    """
    cfg = deepcopy(original)
    cfg.setdefault('memory_capture', {}).update(enabled=False,
        capture_user_messages=False, capture_bot_responses=False,
        extract_stable_facts=False, proposal_mode='summary_first')
    cfg.setdefault('memory_summary', {}).update(enabled=bool(enabled),
        private_provider_id=provider_id, fallback_provider_id='',
        private_fallback_provider_id='', max_retries=1,
        max_calls_per_session_hour=4, max_concurrent_calls=1,
        min_events=4, trigger_event_count=12, trigger_interval_minutes=30,
        max_events_per_summary=24, max_input_chars=6000,
        max_summary_chars=1200, provider_timeout_seconds=30)
    cfg.setdefault('maintenance', {}).update(retention_raw_event_days=0,
        retention_unsummarized_timeline_days=0, retention_summarized_timeline_days=0,
        auto_schedule_enabled=False)
    cfg.setdefault('retrieval', {}).update(embedding_enabled=False,
        mode='basic', embedding_backfill_enabled=False)
    cfg.setdefault('source_semantic', {})['enabled'] = False
    cfg.setdefault('portrait', {})['daily_dispatch_enabled'] = False
    cfg.setdefault('memory_injection', {}).update(debug_log_injection_enabled=False,
                                                 debug_stage_timing_enabled=False)
    cfg.setdefault('scope_control', {}).update(group_capture_enabled=False,
        group_recall_enabled=False, group_topology_enabled=False)
    cfg.setdefault('private_companion_bridge', {})['accept_external_records'] = bool(enabled)
    return cfg


class MemoryCoordinator:
    STATES = {'scheduled', 'awaiting_result', 'responded', 'completed', 'cancelled',
              'do_not_discuss', 'expired'}
    TERMINAL = {'completed', 'cancelled', 'do_not_discuss', 'expired'}

    def __init__(self, service, state_path, allowed_user_ids, *, enabled=False, runtime_managed=False):
        self.service = service
        self.closed = False
        self.enabled = bool(enabled)
        self.runtime_managed = runtime_managed
        self.summary_installation = None
        if runtime_managed:
            self.disable_runtime()
        self.allowed = frozenset(str(v) for v in allowed_user_ids)
        if self.enabled and not self.allowed:
            raise MemoryDenied('allowlist_required')
        native = service.__class__.__module__.rsplit('.', 1)[0]
        self.models = importlib.import_module(native + '.models')
        self.revision = importlib.import_module(native + '.memory_revision')
        self.source_api = importlib.import_module(native + '.message_sources')
        self.source_evidence = importlib.import_module(native + '.source_evidence')
        self.state_path = Path(state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        if self.state_path.is_symlink():
            raise MemoryDenied('state_symlink')
        fd = os.open(self.state_path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self.db = sqlite3.connect(self.state_path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS sources (
                scope TEXT NOT NULL, source_id TEXT NOT NULL, role TEXT NOT NULL,
                content_hash TEXT NOT NULL, timeline_id TEXT NOT NULL,
                occurred_at TEXT NOT NULL, PRIMARY KEY(scope,source_id,role));
            CREATE TABLE IF NOT EXISTS controls (
                scope TEXT PRIMARY KEY, capture_paused INTEGER NOT NULL DEFAULT 0,
                recall_paused INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY, scope TEXT NOT NULL, state TEXT NOT NULL,
                elapsed_ms INTEGER NOT NULL, result_ref TEXT NOT NULL, error_type TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                scope TEXT NOT NULL, event_id TEXT NOT NULL, memory_id TEXT NOT NULL,
                state TEXT NOT NULL, due_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                revision INTEGER NOT NULL, source_id TEXT NOT NULL,
                PRIMARY KEY(scope,event_id));
            CREATE TABLE IF NOT EXISTS event_history (
                id INTEGER PRIMARY KEY, scope TEXT NOT NULL, event_id TEXT NOT NULL,
                state TEXT NOT NULL, due_at TEXT NOT NULL, revision INTEGER NOT NULL,
                source_id TEXT NOT NULL, recorded_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS imports (
                batch_id TEXT NOT NULL, entry_id TEXT NOT NULL, memory_id TEXT NOT NULL,
                manifest_hash TEXT NOT NULL, state TEXT NOT NULL,
                PRIMARY KEY(batch_id,entry_id));
            CREATE TABLE IF NOT EXISTS import_scopes (batch_id TEXT PRIMARY KEY, scope TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS identities (
                session_id TEXT PRIMARY KEY, scope TEXT NOT NULL, user_id TEXT NOT NULL,
                bot_id TEXT NOT NULL, platform TEXT NOT NULL, persona_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS archive_baselines (stream TEXT PRIMARY KEY, row_id INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS archive_cursors (
                scope TEXT NOT NULL, stream TEXT NOT NULL, row_id INTEGER NOT NULL,
                PRIMARY KEY(scope,stream));
            CREATE TABLE IF NOT EXISTS event_prompts (
                scope TEXT NOT NULL, event_id TEXT NOT NULL, revision INTEGER NOT NULL,
                delivery_id TEXT NOT NULL, recorded_at TEXT NOT NULL,
                PRIMARY KEY(scope,event_id,revision));
            CREATE TABLE IF NOT EXISTS identity_reviews (
                scope TEXT NOT NULL, source_id TEXT NOT NULL, content_hash TEXT NOT NULL,
                state TEXT NOT NULL, confirmation_id TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(scope,source_id));
            CREATE TABLE IF NOT EXISTS approved_history (
                session_id TEXT NOT NULL, timeline_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                manifest_hash TEXT NOT NULL, PRIMARY KEY(session_id,timeline_id));
        ''')
        self.lock = asyncio.Lock()
        self.summary_tasks = {}
        if self.enabled:
            from .summary_provider import install
            self.summary_installation = install(service)

    def disable_runtime(self):
        """Synchronous stop gate, before asynchronous shutdown or failure cleanup."""
        self.service.config.raw.setdefault('memory_summary', {})['enabled'] = False
        for task in getattr(self.service, '_summary_workers', {}).values():
            if not task.done(): task.cancel()

    @staticmethod
    def _backlog_fingerprint(row):
        return digest({k:v for k,v in row.items() if k != 'summarized_at'})

    async def backlog_manifest(self, session_id):
        """Read-only review material: exact native IDs/hashes, never raw text."""
        if not isinstance(session_id, str) or session_id.split(':')[-1] not in self.allowed or ':FriendMessage:' not in session_id:
            raise MemoryDenied('private_allowlist_required')
        records, after = [], ''
        while True:
            window = await self.service.store.unsummarized_timeline_window(
                session_id=session_id, scope='private', limit=100, after_timeline_id=after)
            rows = window['rows']
            records.extend({'timeline_id':row['id'],'fingerprint':self._backlog_fingerprint(row)} for row in rows)
            if len(records)>10000: raise MemoryDenied('backlog_review_limit')
            if len(rows)<100: break
            after=rows[-1]['id']
        manifest = {'schema':'phase3.native-backlog.v1','session_id':session_id,
                    'count':len(records),'records':records}
        return {**manifest,'manifest_hash':digest(manifest)}

    async def activate_runtime(self, *, approved_backlog=None):
        """No old native backlog is silently included in newly approved extraction.

        Native window APIs have no persistent consent watermark. Audit every
        pending row against durable source IDs created by this adapter; unknown
        history requires separate migration approval, not fake summarized marks.
        """
        self.disable_runtime()
        backlog = await self.service.store.unsummarized_timeline_backlog()
        total = int(backlog['unsummarized_events'])
        if total > 10000:
            raise MemoryDenied('old_native_timeline_requires_approval')
        if approved_backlog is not None:
            if approved_backlog.get('approved') is not True or approved_backlog.get('allow_model_use') is not True:
                raise MemoryDenied('explicit_backlog_model_consent_required')
            supplied = {k:v for k,v in approved_backlog.items() if k not in {'approved','allow_model_use'}}
            payload = {k:v for k,v in supplied.items() if k!='manifest_hash'}
            if supplied.get('manifest_hash') != digest(payload):
                raise MemoryDenied('backlog_manifest_changed_review_again')
            remembered = {r['timeline_id']:r['fingerprint'] for r in self.db.execute(
                'SELECT timeline_id,fingerprint FROM approved_history WHERE session_id=? AND manifest_hash=?',
                (supplied.get('session_id',''),supplied['manifest_hash']))}
            exact = {r['timeline_id']:r['fingerprint'] for r in supplied.get('records',[])}
            if not remembered or remembered != exact:
                manifest = await self.backlog_manifest(approved_backlog.get('session_id',''))
                if supplied != manifest:
                    raise MemoryDenied('backlog_manifest_changed_review_again')
                with self.db:
                    for row in manifest['records']:
                        self.db.execute('INSERT OR REPLACE INTO approved_history VALUES (?,?,?,?)',
                            (manifest['session_id'],row['timeline_id'],row['fingerprint'],manifest['manifest_hash']))
        reviewed = 0
        sessions = {r[0] for r in self.db.execute('SELECT session_id FROM identities')}
        sessions.update(r[0] for r in self.db.execute('SELECT DISTINCT session_id FROM approved_history'))
        for session_id in sessions:
            identity = self.db.execute('SELECT scope FROM identities WHERE session_id=?',(session_id,)).fetchone()
            known = {r[0] for r in self.db.execute('SELECT timeline_id FROM sources WHERE scope=?', (identity['scope'],))} if identity else set()
            approved = {r['timeline_id']:r['fingerprint'] for r in self.db.execute('SELECT * FROM approved_history WHERE session_id=?',(session_id,))}
            after = ''
            while True:
                window = await self.service.store.unsummarized_timeline_window(
                    session_id=session_id, scope='private', limit=100, after_timeline_id=after)
                rows = window['rows']
                if any(row['id'] not in known and approved.get(row['id'])!=self._backlog_fingerprint(row) for row in rows):
                    raise MemoryDenied('old_native_timeline_requires_approval')
                reviewed += len(rows)
                if len(rows) < 100: break
                after = rows[-1]['id']
            batch = await self.service.store.next_summary_batch(session_id, force=True)
            if batch:
                rows = await self.service.store.summary_batch_rows(batch['id'])
                if any(row['id'] not in known and row['id'] not in approved for row in rows):
                    raise MemoryDenied('old_native_timeline_requires_approval')
        if reviewed != total:
            raise MemoryDenied('old_native_timeline_requires_approval')
        self.service.config.raw.setdefault('memory_summary', {})['enabled'] = True

    def context(self, *, session_id, user_id, bot_id, platform='aiocqhttp',
                persona_id='qq_companion', message_id='', message_text=''):
        return self.models.SessionContext(scope='private', session_id=str(session_id),
            user_id=str(user_id), bot_id=str(bot_id), platform=platform,
            persona_id=persona_id, message_id=str(message_id), message_text=message_text)

    def _scope(self, ctx):
        if not self.enabled:
            raise MemoryDenied('memory_disabled')
        if ctx.scope != 'private' or ctx.group_id or str(ctx.user_id) not in self.allowed:
            raise MemoryDenied('private_allowlist_required')
        if not all((ctx.session_id, ctx.user_id, ctx.bot_id, ctx.persona_id, ctx.platform)):
            raise MemoryDenied('complete_identity_required')
        return digest([ctx.platform, ctx.session_id, ctx.user_id, ctx.bot_id, ctx.persona_id])

    def _paused(self, scope, column):
        assert column in {'capture_paused', 'recall_paused'}
        row = self.db.execute(f'SELECT {column} FROM controls WHERE scope=?', (scope,)).fetchone()
        return bool(row and row[0])

    @staticmethod
    def identity_confirmation_needed(text):
        # Conservative high-impact change boundary, not a per-turn classifier.
        # Ambiguous/unknown linguistic forms remain a stated limitation.
        if re.search(r'确认更正|我确认|确定更正', text):
            return False
        return bool(re.search(r'更正|改正|之前.{0,10}错|其实|不是|改名|改成|以后叫', text)
                    and re.search(r'姓名|名字|我叫|叫我|生日|出生|父亲|母亲|爸爸|妈妈|姐姐|妹妹|哥哥|弟弟|亲友|身份|职业|住在|居住', text))

    @staticmethod
    def memory_control_request(text):
        return bool(re.search(r'停止使用|不再使用|不要再用|删除.{0,12}(?:记忆|资料)|不再(?:主动)?提|不要(?:再|主动)?提|别再提|别记了', text))

    def defer_identity_change(self, ctx, source_id, text):
        scope = self._scope(ctx)
        if not self.identity_confirmation_needed(text):
            return False
        old = self.db.execute('SELECT * FROM identity_reviews WHERE scope=? AND source_id=?', (scope, source_id)).fetchone()
        if old and old['content_hash'] != digest(text):
            raise MemoryDenied('identity_source_conflict')
        if old and old['state'] == 'approved':
            return False
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO identity_reviews VALUES (?,?,?,'pending','')", (scope, source_id, digest(text)))
        return True

    async def confirm_identity_change(self, ctx, archive, *, confirmation_id, text):
        scope = self._scope(ctx)
        if text not in {'确认更正', '确认这次身份变更', '取消这次身份变更'}:
            raise MemoryDenied('explicit_identity_confirmation_required')
        confirmation = archive.db.execute('SELECT 1 FROM inbound WHERE session=? AND sender_id=? AND message_id=? AND text=?',
            (ctx.session_id, ctx.user_id, confirmation_id, text)).fetchone()
        if not confirmation:
            raise MemoryDenied('archived_confirmation_required')
        pending = self.db.execute("SELECT * FROM identity_reviews WHERE scope=? AND state='pending'", (scope,)).fetchall()
        if len(pending) != 1:
            return {'state':'clarification_required', 'pending_count':len(pending)}
        item = pending[0]
        source = archive.db.execute('SELECT * FROM inbound WHERE session=? AND sender_id=? AND message_id=?',
            (ctx.session_id, ctx.user_id, item['source_id'])).fetchone()
        if source is None or digest(source['text']) != item['content_hash']:
            raise MemoryDenied('identity_original_unavailable')
        state = 'cancelled' if text.startswith('取消') else 'approved'
        with self.db:
            self.db.execute('UPDATE identity_reviews SET state=?,confirmation_id=? WHERE scope=? AND source_id=?',
                (state, confirmation_id, scope, item['source_id']))
        if state == 'approved':
            await self.observe(ctx, source_id=item['source_id'], content=source['text'], occurred_at=source['received_at'])
            self.schedule(ctx)
        return {'state':state, 'source_id':item['source_id']}

    def remember_context(self, ctx):
        """Persist routing identity only, never the recent message/context text."""
        scope = self._scope(ctx)
        old = self.db.execute('SELECT scope FROM identities WHERE session_id=?', (ctx.session_id,)).fetchone()
        if old and old['scope'] != scope:
            raise MemoryDenied('session_identity_changed')
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO identities VALUES (?,?,?,?,?,?)',
                (ctx.session_id, scope, ctx.user_id, ctx.bot_id, ctx.platform, ctx.persona_id))

    def context_for_session(self, session_id):
        row = self.db.execute('SELECT * FROM identities WHERE session_id=?', (session_id,)).fetchone()
        if row is None:
            return None
        ctx = self.context(**{k: row[k] for k in ('session_id', 'user_id', 'bot_id', 'platform', 'persona_id')})
        self._scope(ctx)
        return ctx

    def bind_archive(self, store):
        """Set a first-activation boundary; existing Phase 2 history is not imported.

        The Guard is the durable work source, so no copied-content queue exists.
        Subsequent binds retain the boundary/cursors for restart recovery.
        """
        with self.db:
            for stream, table in (('inbound', 'inbound'), ('delivery', 'deliveries')):
                maximum = store.db.execute(f'SELECT COALESCE(MAX(rowid),0) FROM {table}').fetchone()[0]
                self.db.execute('INSERT OR IGNORE INTO archive_baselines VALUES (?,?)', (stream, maximum))

    async def drain_archive(self, store, *, limit=32):
        """Read at most limit source rows per known session; commit per-source cursor.

        Failed writes leave their cursor unchanged. A native write interrupted
        before cursor commit is replayed through the native source identity and
        this bridge's hash/ID deduplication. Pending delivery blocks its own
        stream until Guard classifies it; unknown/failed/progress are never facts.
        """
        processed = 0
        for identity in self.db.execute('SELECT session_id FROM identities').fetchall():
            ctx = self.context_for_session(identity['session_id'])
            scope = self._scope(ctx)
            for stream in ('inbound', 'delivery'):
                row = self.db.execute('SELECT row_id FROM archive_cursors WHERE scope=? AND stream=?', (scope, stream)).fetchone()
                if row is None:
                    row = self.db.execute('SELECT row_id FROM archive_baselines WHERE stream=?', (stream,)).fetchone()
                if row is None:
                    raise MemoryDenied('archive_boundary_required')
                cursor = row[0]
                if stream == 'inbound':
                    rows = store.db.execute('SELECT rowid AS archive_row,* FROM inbound WHERE session=? AND sender_id=? AND rowid>? ORDER BY rowid LIMIT ?',
                        (ctx.session_id, ctx.user_id, cursor, max(1, min(64, limit)))).fetchall()
                else:
                    rows = store.db.execute('SELECT d.rowid AS archive_row,d.* FROM deliveries d JOIN turns t ON t.turn_id=d.turn_id WHERE t.session=? AND d.rowid>? ORDER BY d.rowid LIMIT ?',
                        (ctx.session_id, cursor, max(1, min(64, limit)))).fetchall()
                for source in rows:
                    if stream == 'delivery' and source['state'] == 'pending':
                        break
                    if stream == 'inbound' and source['text'].strip():
                        await self.observe(ctx, source_id=source['message_id'], content=source['text'], occurred_at=source['received_at'])
                    elif stream == 'delivery' and source['state'] == 'sent' and source['kind'] != 'progress' and source['text'].strip():
                        originals = store.db.execute('SELECT i.text FROM turn_sources s JOIN inbound i ON i.platform=s.platform AND i.session=s.session AND i.message_id=s.message_id WHERE s.turn_id=?', (source['turn_id'],)).fetchall()
                        if not any(self.memory_control_request(item[0]) or self.identity_confirmation_needed(item[0]) for item in originals):
                            await self.observe(ctx, source_id=source['delivery_id'], content=source['text'],
                                occurred_at=source['finished_at'] or source['created_at'], role='assistant', kind=source['kind'], delivery_status='sent')
                    with self.db:
                        self.db.execute('INSERT INTO archive_cursors VALUES (?,?,?) ON CONFLICT(scope,stream) DO UPDATE SET row_id=excluded.row_id',
                            (scope, stream, source['archive_row']))
                    processed += 1
            if processed:
                self.schedule(ctx)
            else:
                await self.resume_due_summary(ctx)
        return processed

    async def resume_due_summary(self, ctx):
        """Use the existing archive tick and native queue; never force a call.

        With no new messages, budget-deferred batches and quiet-period windows
        still need a wakeup. Eligibility and every model reservation remain
        native. Empty/future/quarantined work creates no job or model request.
        """
        scope = self._scope(ctx)
        current = self.summary_tasks.get(scope)
        if (self.closed or self._paused(scope, 'capture_paused')
                or not self.service.config.bool('memory_summary.enabled', False)
                or (current is not None and not current.done())):
            return False
        batch = await self.service.store.next_summary_batch(ctx.session_id, force=False)
        if batch is None:
            window = await self.service.store.unsummarized_timeline_window(
                session_id=ctx.session_id, scope=ctx.scope,
                limit=self.service.config.int('memory_summary.max_events_per_summary', 24),
                exclude_assigned=True)
            if not self.service._summary_window_ready(window, force=False):
                return False
        self.schedule(ctx)
        return True

    async def observe(self, ctx, *, source_id, content, occurred_at, role='user',
                      kind='normal', delivery_status='sent'):
        scope = self._scope(ctx)
        if role not in {'user', 'assistant'} or kind not in {'normal', 'final', 'proactive', 'progress'}:
            raise MemoryDenied('invalid_observation_kind')
        if role == 'assistant' and (delivery_status != 'sent' or kind == 'progress'):
            return {'state': 'excluded', 'timeline_id': ''}
        if role == 'user' and self.memory_control_request(content):
            return {'state': 'control_not_a_memory_fact', 'timeline_id': ''}
        if self._paused(scope, 'capture_paused'):
            return {'state': 'paused', 'timeline_id': ''}
        if not source_id or not isinstance(content, str) or not content.strip():
            raise MemoryDenied('source_and_content_required')
        stamp = instant(occurred_at)
        body_hash = digest(content)
        if role == 'user' and self.defer_identity_change(ctx, str(source_id), content):
            return {'state':'identity_confirmation_required', 'timeline_id':''}
        async with self.lock:
            old = self.db.execute('SELECT * FROM sources WHERE scope=? AND source_id=? AND role=?',
                                  (scope, str(source_id), role)).fetchone()
            if old:
                if old['content_hash'] != body_hash:
                    raise MemoryDenied('source_content_conflict')
                return {'state': 'deduplicated', 'timeline_id': old['timeline_id']}
            # Original source IDs are from Guard, not fabricated user messages.
            timeline_id = await self.service.record_visible_turn(role=role, content=content,
                scope='private', session_id=ctx.session_id, platform=ctx.platform,
                user_id=ctx.user_id, user_name=ctx.user_name, message_id=str(source_id),
                source='phase3_confirmed_archive', occurred_at=stamp,
                metadata={'bot_id': ctx.bot_id, 'owner_bot_id': ctx.bot_id,
                          'persona_id': ctx.persona_id, 'platform': ctx.platform,
                          'source_message_id': str(source_id), 'kind': kind,
                          'epistemic_status': 'user_statement' if role == 'user' else 'observed_bot_action',
                          'conversation_observed': True, 'shared_experience': False,
                          'delivery_confirmed': role == 'assistant'})
            if not timeline_id:
                return {'state': 'suppressed', 'timeline_id': ''}
            with self.db:
                self.db.execute('INSERT INTO sources VALUES (?,?,?,?,?,?)',
                    (scope, str(source_id), role, body_hash, timeline_id, stamp))
            return {'state': 'recorded', 'timeline_id': timeline_id}

    async def summarize(self, ctx):
        scope = self._scope(ctx)
        if self._paused(scope, 'capture_paused'):
            return {'state': 'paused', 'memory_id': ''}
        start, memory_id, error = time.monotonic(), '', ''
        try:
            memory_id = await self.service.maybe_summarize_session(ctx, force=False)
            await self.reconcile_assertions(ctx)
            state = 'completed' if memory_id else 'idle_or_budgeted'
        except asyncio.CancelledError:
            state, error = 'interrupted', 'CancelledError'
            raise
        except Exception as exc:
            state, error = 'failed', type(exc).__name__
        finally:
            elapsed = round((time.monotonic() - start) * 1000)
            with self.db:
                self.db.execute('INSERT INTO jobs(scope,state,elapsed_ms,result_ref,error_type) VALUES (?,?,?,?,?)',
                    (scope, state, elapsed, memory_id, error))
        return {'state': state, 'memory_id': memory_id, 'error_type': error, 'elapsed_ms': elapsed}

    async def reconcile_assertions(self, ctx):
        """Honor native domain revisions without leaving stale summaries active.

        Native upsert_assertion owns comparison, polarity and historical links.
        We archive the old source summary only when a real native assertion
        explicitly supersedes it; raw sources and historical rows remain.
        """
        self._scope(ctx)
        records = await self.service.store.list_memories(memory_type='assertion',
            session_id=ctx.session_id, scope='private', limit=128)
        archived = 0
        for old in records:
            if old.validity_status != 'superseded' or not self.revision.correction_owner(ctx, old):
                continue
            replacement = await self.service.store.get_memory(old.supersedes_id)
            if replacement is None or replacement.validity_status != 'active' or not self.revision.correction_owner(ctx, replacement):
                continue
            old_summary = (old.metadata or {}).get('assertion_source_summary_id')
            new_summary = (replacement.metadata or {}).get('assertion_source_summary_id')
            if old_summary and old_summary != new_summary:
                original = await self.service.store.get_memory(old_summary)
                if original and original.lifecycle != 'archived' and self.revision.correction_owner(ctx, original):
                    archived += await self.service.store.archive_memories([old_summary],
                        reason='phase3_native_assertion_superseded', supersedes_id=new_summary or replacement.id)
        if archived:
            self.service.store.invalidate_registered_caches(reason='phase3_native_assertion_superseded')
        return archived

    def schedule(self, ctx):
        """Coalesce notification only; persisted batching/budget is upstream."""
        scope = self._scope(ctx)
        current = self.summary_tasks.get(scope)
        if current is None or current.done():
            current = asyncio.create_task(self.summarize(replace(ctx)))
            self.summary_tasks[scope] = current
        return current

    async def recall(self, ctx, query, *, top_k=6):
        scope = self._scope(ctx)
        if self._paused(scope, 'recall_paused'):
            return []
        return await self.service.search(query, ctx, top_k=min(10, max(1, int(top_k))))

    async def correct(self, event, *, critical=False, confirmed=False, **kwargs):
        ctx = await self.service.identity.resolve_event_context(event)
        self._scope(ctx)
        if critical and not confirmed:
            return {'ok': False, 'reason_code': 'explicit_confirmation_required'}
        return await self.service.correct_user_memory(event=event, **kwargs)

    async def withdraw_source(self, event, *, source_ref, expected_version, operation_id,
                              mode='stop_use', confirmed=False):
        """Require native exact-source read receipt. Never erase Guard originals.

        no_mention, stop_use and delete_derived suppress ordinary recall and
        proactive selection through the same native source withdrawal.
        erase_originals requires a separately reviewed archive erasure job.
        """
        ctx = await self.service.identity.resolve_event_context(event)
        scope = self._scope(ctx)
        if mode == 'erase_originals':
            return {'ok': False, 'state': 'approval_required_for_archive_erasure'}
        if mode not in {'stop_use', 'delete_derived', 'no_mention'}:
            raise MemoryDenied('explicit_operation_required')
        if not confirmed:
            return {'ok': False, 'state': 'confirmation_required'}
        # The model runs outside this short gate. Native summary completion and
        # its following assertion writes cannot race a committed withdrawal.
        if self.summary_installation:
            async with self.summary_installation.control():
                result = await self.source_api.manage(self.service, event, action='withdraw',
                    operation_id=operation_id, source_ref=source_ref, expected_version=expected_version)
        else:
            result = await self.source_api.manage(self.service, event, action='withdraw',
                operation_id=operation_id, source_ref=source_ref, expected_version=expected_version)
        if result.get('ok'):
            # Remove the source projection, not the independent raw archive.
            # Native tombstone prevents replay of this exact source after restart.
            result = {**result, 'raw_archive_retained': True, 'scope': mode}
            source_id = source_ref.removeprefix('timeline:')
            with self.db:
                self.db.execute("UPDATE events SET state='do_not_discuss',revision=revision+1 WHERE scope=? AND source_id IN (SELECT source_id FROM sources WHERE scope=? AND timeline_id=?)",
                                (scope, scope, source_id))
        return result

    async def control_memory(self, event, *, action, query, quote='', source_ref='', expected_version=''):
        ctx = await self.service.identity.resolve_event_context(event)
        self._scope(ctx)
        if not isinstance(query, str) or not 2 <= len(query) <= 80 or query in {'那个','这条','记忆','资料','全部','所有'}:
            raise MemoryDenied('specific_memory_query_required')
        if action == 'search':
            return await self.service.tool_sources(event, action='search', terms=[query], limit=5)
        if action not in {'stop_use', 'delete_derived', 'no_mention'}:
            raise MemoryDenied('unsupported_memory_operation')
        if not quote or quote not in ctx.message_text or query not in quote or not self.memory_control_request(quote):
            raise MemoryDenied('current_explicit_user_instruction_required')
        # A real native read verifies scope/version and issues the exact source
        # receipt. Never manufacture an observed-source receipt for the Agent.
        read = await self.service.tool_sources(event, action='read', source_ref=source_ref, limit=1)
        matches = [r for r in read.get('sources', []) if r.get('source_ref') == source_ref
                   and r.get('source_version') == expected_version and query in r.get('excerpt', '')]
        if len(matches) != 1:
            raise MemoryDenied('exact_source_and_query_must_match')
        if not re.search(r'整条(?:原始消息|来源|消息)|整段(?:原始消息|来源|消息)', quote):
            return {'ok':False,'state':'confirmation_required','source_ref':source_ref,
                'source_version':expected_version,'impact':'whole_source_and_dependent_memories',
                'raw_archive_retained':True,
                'reason':'原生撤回以整条来源为单位，可能同时移除该消息支持的其他派生事实。请明确确认停止使用整条来源。'}
        return await self.withdraw_source(event, source_ref=source_ref, expected_version=expected_version,
            operation_id='phase3_control:' + str(ctx.message_id) + ':' + source_ref,
            mode=action, confirmed=True)

    async def pause(self, ctx, *, capture=True, recall=True, reason='user_stop_use'):
        """Conservative whole-session stop when user has not named exact records."""
        scope = self._scope(ctx)
        # Native ACL first: if the sidecar fails, the upstream path stays closed.
        await self.service.store.upsert_acl_policy(window_scope='private', window_id=ctx.user_id,
            capture_enabled=not capture, recall_enabled=not recall)
        if capture:
            tasks = [self.summary_tasks.get(scope),
                     getattr(self.service, '_summary_workers', {}).get(ctx.session_id)]
            active = [t for t in tasks if t is not None and t is not asyncio.current_task() and not t.done()]
            for task in active: task.cancel()
            if active: await asyncio.gather(*active, return_exceptions=True)
        with self.db:
            self.db.execute('INSERT INTO controls VALUES (?,?,?,?) ON CONFLICT(scope) DO UPDATE SET capture_paused=excluded.capture_paused,recall_paused=excluded.recall_paused,reason=excluded.reason',
                            (scope, int(capture), int(recall), reason))
        return {'state': 'paused', 'capture_paused': capture, 'recall_paused': recall}

    async def event_update(self, ctx, *, event_id, memory_id, state, source_id,
                           due_at='', expires_at='', expected_revision=0):
        scope = self._scope(ctx)
        if state not in self.STATES or not event_id:
            raise MemoryDenied('event_state_invalid')
        source = self.db.execute('SELECT 1 FROM sources WHERE scope=? AND source_id=?', (scope, source_id)).fetchone()
        record = await self.service.store.get_memory(memory_id)
        if (not source or record is None or record.session_id != ctx.session_id
                or not self.revision.correction_owner(ctx, record)):
            raise MemoryDenied('event_source_or_memory_missing')
        due = instant(due_at) if due_at else ''
        expires = instant(expires_at) if expires_at else ''
        if expires and due and expires < due:
            raise MemoryDenied('expiry_before_event')
        with self.db:
            old = self.db.execute('SELECT * FROM events WHERE scope=? AND event_id=?', (scope, event_id)).fetchone()
            if (old['revision'] if old else 0) != expected_revision:
                raise MemoryDenied('event_revision_conflict')
            if old and old['state'] in self.TERMINAL and state not in self.TERMINAL:
                raise MemoryDenied('closed_event_requires_explicit_new_event')
            revision = expected_revision + 1
            self.db.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(scope,event_id) DO UPDATE SET memory_id=excluded.memory_id,state=excluded.state,due_at=excluded.due_at,expires_at=excluded.expires_at,revision=excluded.revision,source_id=excluded.source_id',
                (scope, event_id, memory_id, state, due, expires, revision, source_id))
            self.db.execute('INSERT INTO event_history(scope,event_id,state,due_at,revision,source_id,recorded_at) VALUES (?,?,?,?,?,?,?)',
                (scope, event_id, state, due, revision, source_id, datetime.now(timezone.utc).isoformat()))
        return {'event_id': event_id, 'state': state, 'revision': revision}

    async def user_event(self, ctx, *, event_id, state, source_id, original_text,
                         quote, occurred_at, due_at='', expires_at='', expected_revision=0):
        """Native Agent event bridge: quoted, already archived user evidence only."""
        scope = self._scope(ctx)
        if self._paused(scope, 'capture_paused') or self._paused(scope, 'recall_paused'):
            raise MemoryDenied('memory_paused')
        if not re.fullmatch(r'[\w\-]{1,80}', event_id) or not quote or quote not in original_text or len(quote) > 1000:
            raise MemoryDenied('exact_current_user_quote_required')
        source = self.db.execute("SELECT * FROM sources WHERE scope=? AND source_id=? AND role='user'", (scope, source_id)).fetchone()
        if source is None or source['content_hash'] != digest(original_text):
            raise MemoryDenied('archived_current_user_source_required')
        signals = {'completed': r'完成|结束|做完|搞定', 'cancelled': r'取消|不去了|作废',
                   'do_not_discuss': r'别.*问|不想.*聊|不要.*提|别再提',
                   'responded': r'结果|成功|失败|通过|没过|进展'}
        if state in signals and not re.search(signals[state], quote):
            raise MemoryDenied('explicit_event_status_evidence_required')
        if state in {'scheduled', 'awaiting_result'} and not re.search(r'面试|考试|约|会议|计划|安排|等待|结果|待发生|待结果|改到|改期', quote):
            raise MemoryDenied('event_evidence_required')
        if state not in self.STATES - {'expired'}:
            raise MemoryDenied('event_state_invalid')
        if type(expected_revision) is not int or expected_revision < 0:
            raise MemoryDenied('event_revision_invalid')
        if due_at:
            # No invented deadline: first release accepts explicit absolute
            # ISO dates in the quote. Relative/ambiguous times ask clarification.
            from zoneinfo import ZoneInfo
            local = datetime.fromisoformat(instant(due_at)).astimezone(ZoneInfo('Asia/Shanghai'))
            if local.strftime('%Y-%m-%d') not in quote or local.strftime('%H:%M') not in quote:
                raise MemoryDenied('explicit_date_and_time_required')
        if expires_at:
            instant(expires_at)
        elif due_at:
            expires_at = (datetime.fromisoformat(instant(due_at)) + timedelta(days=7)).isoformat()
        async with self.lock:
            old = self.db.execute('SELECT * FROM events WHERE scope=? AND event_id=?', (scope, event_id)).fetchone()
            if (old['revision'] if old else 0) != expected_revision:
                raise MemoryDenied('event_revision_conflict')
            if old and old['state'] in self.TERMINAL:
                raise MemoryDenied('closed_event_requires_explicit_new_event')
            memory_id = self.service.stable_id('phase3_user_event', scope, event_id, str(expected_revision + 1))
            await self.service.record_external_event(memory_id=memory_id, memory_type='user_event',
                content=quote, subject=self.models.EntityRef(kind='user', id=ctx.user_id),
                object=self.models.EntityRef(kind='bot', id=ctx.bot_id), scope='private',
                session_id=ctx.session_id, platform=ctx.platform, message_id=source_id,
                visibility='private_pair', reality_level='real_user_fact', occurred_at=occurred_at,
                source_plugin='phase3_confirmed_user_event',
                metadata={'owner_bot_id': ctx.bot_id, 'persona_id': ctx.persona_id,
                          'event_id': event_id, 'source_message_id': source_id,
                          'source_timeline_id': source['timeline_id'], 'epistemic_status': 'user_statement',
                          'shared_experience': False})
            result = await self.event_update(ctx, event_id=event_id, memory_id=memory_id,
                state=state, source_id=source_id, due_at=due_at or (old['due_at'] if old else ''),
                expires_at=expires_at or (old['expires_at'] if old else ''), expected_revision=expected_revision)
            if old and old['memory_id'] != memory_id:
                await self.service.store.archive_memories([old['memory_id']],
                    reason='phase3_user_event_revision', supersedes_id=memory_id)
                self.service.store.invalidate_registered_caches(reason='phase3_user_event_revision')
            return result

    async def list_events(self, ctx):
        scope = self._scope(ctx)
        if self._paused(scope, 'recall_paused'):
            return []
        rows = self.db.execute('SELECT * FROM events WHERE scope=? ORDER BY revision DESC,event_id LIMIT 20', (scope,)).fetchall()
        result = []
        for row in rows:
            record = await self.service.store.get_memory(row['memory_id'])
            if record and self.revision.correction_owner(ctx, record):
                result.append({k: row[k] for k in ('event_id', 'state', 'revision', 'due_at', 'expires_at')} | {'user_quote': record.content})
        return result

    def due_events(self, ctx, *, now):
        scope = self._scope(ctx)
        now = instant(now)
        with self.db:
            self.db.execute("UPDATE events SET state='expired',revision=revision+1 WHERE scope=? AND expires_at!='' AND expires_at<=? AND state IN ('scheduled','awaiting_result','responded')", (scope, now))
        return [dict(r) for r in self.db.execute("SELECT event_id,memory_id,state,revision,source_id,expires_at FROM events WHERE scope=? AND state IN ('scheduled','awaiting_result') AND due_at!='' AND due_at<=? ORDER BY due_at,event_id", (scope, now))]

    def proactive_event(self, ctx, event_id, *, now):
        scope = self._scope(ctx)
        self.due_events(ctx, now=now)
        row = self.db.execute('SELECT * FROM events WHERE scope=? AND event_id=?', (scope, event_id)).fetchone()
        if row is None:
            return {'event_id': event_id, 'state': 'missing', 'eligible': False, 'due': False, 'revision': 0}
        due = bool(row['due_at'] and row['due_at'] <= instant(now))
        prompted = self.db.execute('SELECT 1 FROM event_prompts WHERE scope=? AND event_id=? AND revision=?',
                                  (scope, event_id, row['revision'])).fetchone() is not None
        return {'event_id': event_id, 'state': row['state'], 'due': due, 'revision': row['revision'],
                'eligible': row['state'] == 'awaiting_result' and due and not prompted
                    and not self._paused(scope, 'recall_paused'), 'prompted': prompted}

    def mark_event_prompted(self, ctx, event_id, *, expected_revision, delivery):
        """Record a Guard receipt; a sent question is NOT a user's response."""
        scope = self._scope(ctx)
        row = self.db.execute('SELECT * FROM events WHERE scope=? AND event_id=?', (scope, event_id)).fetchone()
        if not row or row['revision'] != expected_revision or row['state'] != 'awaiting_result':
            raise MemoryDenied('event_revision_conflict')
        if delivery.get('state') != 'sent' or not delivery.get('delivery_id'):
            raise MemoryDenied('confirmed_guard_delivery_required')
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO event_prompts VALUES (?,?,?,?,?)',
                (scope, event_id, expected_revision, delivery['delivery_id'], datetime.now(timezone.utc).isoformat()))
        return {'state': 'prompted', 'event_state': row['state'], 'revision': expected_revision}

    def preview_profile(self, entries):
        allowed = {'L1', 'L2', 'L3'}
        ids = set()
        for row in entries:
            if row.get('layer') not in allowed or row.get('confidence_status') != 'user_confirmed':
                raise MemoryDenied('profile_entry_unreviewed')
            if row.get('shared_experience') or not row.get('source_ref') or not row.get('id') or not row.get('text'):
                raise MemoryDenied('invalid_profile_source')
            if not isinstance(row['text'], str) or len(row['text']) > 4000:
                raise MemoryDenied('profile_entry_exceeds_native_limit')
            if row['id'] in ids:
                raise MemoryDenied('duplicate_profile_entry')
            ids.add(row['id'])
            if row['layer'] == 'L3' and (not row.get('occurred_at') or not row.get('expires_at')):
                raise MemoryDenied('temporal_profile_needs_expiry')
            if row.get('occurred_at'):
                instant(row['occurred_at'])
            if row.get('expires_at'):
                instant(row['expires_at'])
        return {'manifest_hash': digest(entries), 'counts': {layer: sum(r['layer'] == layer for r in entries) for layer in sorted(allowed)},
                'shared_experiences': 0, 'state': 'reviewed_manifest_only'}

    async def import_profile(self, ctx, entries, *, batch_id, approval, environment='isolated'):
        scope = self._scope(ctx)
        preview = self.preview_profile(entries)
        if environment != 'isolated' or not approval.get('isolated_import'):
            raise MemoryDenied('production_import_requires_separate_release')
        if approval.get('manifest_hash') != preview['manifest_hash'] or not approval.get('allow_deepseek_use'):
            raise MemoryDenied('exact_manifest_and_model_consent_required')
        if not batch_id or not set(preview['counts']).issuperset(approval.get('layers', [])):
            raise MemoryDenied('invalid_batch_or_layers')
        if any(row['layer'] not in approval.get('layers', []) for row in entries):
            raise MemoryDenied('layer_not_approved')
        # The pinned record_external_memory API stores arbitrary expires_at
        # metadata but does not enforce it in every native recall path.
        if any(row['layer']=='L3' for row in entries):
            raise MemoryDenied('temporal_profile_import_expiry_not_verified')
        old_scope = self.db.execute('SELECT scope FROM import_scopes WHERE batch_id=?', (batch_id,)).fetchone()
        if old_scope and old_scope[0] != scope:
            raise MemoryDenied('batch_identity_conflict')
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO import_scopes VALUES (?,?)', (batch_id, scope))
        refs = []
        for row in entries:
            old = self.db.execute('SELECT * FROM imports WHERE batch_id=? AND entry_id=?', (batch_id, row['id'])).fetchone()
            if old:
                if old['manifest_hash'] != preview['manifest_hash'] or old['state'] not in {'imported', 'pending'}:
                    raise MemoryDenied('batch_conflict')
                if old['state'] == 'imported':
                    refs.append(old['memory_id']); continue
            memory_id = self.service.stable_id('phase3_profile', scope, batch_id, row['id'])
            # Reserve the exact native ID before the write, so interrupted
            # imports are recoverable or removable without searching content.
            with self.db:
                self.db.execute('INSERT OR IGNORE INTO imports VALUES (?,?,?,?,?)',
                    (batch_id, row['id'], memory_id, preview['manifest_hash'], 'pending'))
            result = await self.service.record_external_memory(user_id=ctx.user_id,
                content=row['text'], source_plugin='phase3_profile', memory_type='external_memory',
                memory_id=memory_id, idempotency_key=memory_id, occurred_at=row.get('occurred_at', ''),
                metadata={'profile_layer': row['layer'], 'source_ref': row['source_ref'],
                          'confidence_status': 'user_confirmed', 'shared_experience': False,
                          'owner_bot_id': ctx.bot_id, 'bot_id': ctx.bot_id, 'persona_id': ctx.persona_id,
                          'expires_at': row.get('expires_at', ''), 'import_batch': batch_id})
            if not result.get('ok'):
                raise MemoryDenied('native_profile_write_rejected')
            with self.db:
                self.db.execute("UPDATE imports SET memory_id=?,state='imported' WHERE batch_id=? AND entry_id=?",
                    (result['memory_id'], batch_id, row['id']))
            refs.append(result['memory_id'])
        return {**preview, 'state': 'isolated_imported', 'memory_ids': refs}

    async def rollback_profile(self, *, batch_id):
        rows = self.db.execute("SELECT * FROM imports WHERE batch_id=? AND state IN ('imported','pending')", (batch_id,)).fetchall()
        for row in rows:
            await self.service.store.delete_memory(row['memory_id'])
            with self.db:
                self.db.execute("UPDATE imports SET state='rolled_back' WHERE batch_id=? AND entry_id=?", (batch_id, row['entry_id']))
        return {'state': 'rolled_back', 'count': len(rows)}

    def status(self):
        return {'enabled': self.enabled,
                'source_count': self.db.execute('SELECT COUNT(*) FROM sources').fetchone()[0],
                'job_counts': {r[0]: r[1] for r in self.db.execute('SELECT state,COUNT(*) FROM jobs GROUP BY state')},
                'native_usage': self.service.token_usage_summary()}

    async def close(self):
        if self.closed:
            return
        self.closed = True
        self.disable_runtime()
        # Native workers can still be unwinding after disable_runtime(). Keep
        # the instance's summary checks installed until every writer has exited.
        tasks = list(set(self.summary_tasks.values()) |
                     set(getattr(self.service, '_summary_workers', {}).values()))
        for task in tasks:
            if not task.done(): task.cancel()
        if tasks: await asyncio.gather(*tasks, return_exceptions=True)
        if self.summary_installation:
            self.summary_installation.restore()
        self.db.close()
