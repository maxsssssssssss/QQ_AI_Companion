"""Instance-local concurrency checks around the pinned native summary writer.

No extraction, queue, database or model is replaced. Native batch rows are
fingerprinted before the existing provider call; native transactions recheck
them before writing. Only the short write phase shares a gate with explicit
correction/withdrawal. Network generation never holds that gate or a DB lock.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json


class SummarySnapshotChanged(RuntimeError):
    """The original evidence/derived revision is no longer current."""


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


@dataclass
class Run:
    ctx: object
    snapshot: dict | None = None
    writing: bool = False
    gate_held: bool = False


class Consistency:
    def __init__(self, service):
        self.service, self.store = service, service.store
        self.gate = asyncio.Lock()
        self.run = ContextVar('phase3_native_summary_run', default=None)
        self.originals = []
        self.active = True
        self._install()

    def replace(self, obj, name, value):
        old = getattr(obj, name)
        self.originals.append((obj, name, old, value))
        setattr(obj, name, value)
        return old

    def _domain_revision(self, ctx):
        # Native instance identity comes from its authorized SessionContext.
        # Access counters are intentionally excluded: merely reading memory
        # during slow generation is not a correction and must not starve jobs.
        rows = self.store._conn.execute(
            'SELECT id,content,evidence,metadata,lifecycle,validity_status,'
            'valid_from,valid_to,review_status,supersedes_id '
            'FROM memories WHERE scope=? AND owner_bot_id=? '
            'AND (session_id=? OR subject_id=?) ORDER BY id LIMIT 4097',
            (ctx.scope,ctx.bot_id,ctx.session_id,ctx.user_id)).fetchall()
        if len(rows)>4096:
            raise SummarySnapshotChanged('summary_revision_limit')
        return fingerprint([dict(row) for row in rows])

    @staticmethod
    def _source_value(row):
        # Successful native completion updates this field; that is not a
        # modification of the evidence used by assertion persistence.
        return {key:value for key,value in dict(row).items() if key!='summarized_at'}

    def _sources(self, ids):
        result = {}
        for source_id in ids:
            row = self.store._conn.execute('SELECT * FROM timeline WHERE id=?',(source_id,)).fetchone()
            if row is None:
                raise SummarySnapshotChanged('summary_source_unavailable')
            result[source_id] = fingerprint(self._source_value(row))
        return result

    def capture(self, run, rows):
        if not rows or len(rows)>128:
            raise SummarySnapshotChanged('summary_snapshot_bounds')
        expected={str(row['id']):fingerprint(self._source_value(row)) for row in rows}
        with self.store._lock:
            if self._sources(expected)!=expected:
                raise SummarySnapshotChanged('summary_source_changed')
            return {'sources':expected,'revision':self._domain_revision(run.ctx)}

    def verify(self, run):
        snapshot=run.snapshot
        if snapshot is None:
            raise SummarySnapshotChanged('summary_snapshot_missing')
        if self._sources(snapshot['sources'])!=snapshot['sources']:
            raise SummarySnapshotChanged('summary_source_changed')
        if self._domain_revision(run.ctx)!=snapshot['revision']:
            raise SummarySnapshotChanged('summary_derived_revision_changed')

    async def defer(self, batch_id):
        batch=await self.store.get_summary_batch(batch_id)
        if not batch or batch['state'] in {'completed','no_memory'}:
            return
        exhausted=int(batch['automatic_calls'])>=max(1,self.service.config.int('memory_summary.max_retries',1))
        await self.store.defer_summary_batch(batch_id,'summary_snapshot_changed',
            quarantine=exhausted,
            delay=self.service._summary_retry_backoff_seconds(int(batch['automatic_calls'])))

    @asynccontextmanager
    async def control(self):
        """Join only the native commit phase, never wait for its model call."""
        async with self.gate:
            yield

    @staticmethod
    async def settle_write(awaitable):
        # Cancelling asyncio.to_thread does not stop a SQLite write. Keep the
        # gate and transaction verifier alive until that local operation has
        # settled; model generation never uses this cancellation barrier.
        operation=asyncio.create_task(awaitable)
        try:
            return await asyncio.shield(operation)
        except asyncio.CancelledError:
            while not operation.done():
                try:
                    await asyncio.shield(operation)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not operation.cancelled():
                operation.exception()
            raise

    def _install(self):
        service, store = self.service, self.store
        original_run = service.maybe_summarize_session
        async def summarize(ctx, *, force=False):
            if not self.active:
                return await original_run(ctx,force=force)
            current=Run(ctx)
            token=self.run.set(current)
            try:
                return await original_run(ctx,force=force)
            finally:
                if current.gate_held:
                    self.gate.release()
                self.run.reset(token)
        self.replace(service,'maybe_summarize_session',summarize)

        original_provider = service.summarizer.summarize_with_provider
        async def provider(*args,**kwargs):
            current=self.run.get()
            if current is not None and self.active:
                # Native already copied batch rows; capture verifies that exact
                # input before sending it to the configured summary provider.
                if current.snapshot is None:
                    current.snapshot=self.capture(current,kwargs.get('rows') or [])
                else:
                    with store._lock:
                        self.verify(current)
            return await original_provider(*args,**kwargs)
        self.replace(service.summarizer,'summarize_with_provider',provider)

        original_finish=store.finish_summary_batch
        async def finish(batch_id,event_ids,**kwargs):
            current=self.run.get()
            if current is None or not self.active:
                return await original_finish(batch_id,event_ids,**kwargs)
            if not current.gate_held:
                await self.gate.acquire()
                current.gate_held=True
            current.writing=True
            async def commit():
                try:
                    return await original_finish(batch_id,event_ids,**kwargs)
                except SummarySnapshotChanged:
                    # Queue bookkeeping is not a fact commit. The rejected old
                    # revision must not prevent persisting its bounded failure.
                    current.writing=False
                    await self.defer(batch_id)
                    raise
            try:
                return await self.settle_write(commit())
            finally:
                current.writing=False
        self.replace(store,'finish_summary_batch',finish)

        original_assertions=service._persist_assertions
        async def assertions(*args,**kwargs):
            current=self.run.get()
            if current is None or not self.active:
                return await original_assertions(*args,**kwargs)
            current.writing=True
            try:
                return await self.settle_write(original_assertions(*args,**kwargs))
            finally:
                current.writing=False
        self.replace(service,'_persist_assertions',assertions)

        original_transaction=store._transaction_sync
        @contextmanager
        def transaction():
            current=self.run.get()
            with original_transaction():
                if current is not None and current.writing and self.active:
                    # Called inside the native store lock and transaction,
                    # including asyncio.to_thread's propagated ContextVar.
                    self.verify(current)
                yield
                if current is not None and current.writing and self.active:
                    current.snapshot['revision']=self._domain_revision(current.ctx)
        self.replace(store,'_transaction_sync',transaction)

        original_correct=service.correct_user_memory
        async def correct(*args,**kwargs):
            async with self.control():
                return await original_correct(*args,**kwargs)
        self.replace(service,'correct_user_memory',correct)

    def restore(self):
        if not self.active:return
        self.active=False
        for obj,name,original,wrapper in reversed(self.originals):
            if getattr(obj,name,None) is wrapper:setattr(obj,name,original)


def install(service):
    # Small provider-only fixtures do not pretend to be the native store.
    if not all(hasattr(service,key) for key in ('store','summarizer','maybe_summarize_session')):
        return None
    return Consistency(service)
