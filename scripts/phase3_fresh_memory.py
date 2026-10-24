#!/usr/bin/env python3
"""Forget chat-derived native memory on an explicitly marked, stopped copy.

This is an offline release helper, not a runtime plugin or production reset
endpoint. It never imports a provider, accesses a network, or modifies a live
data directory. The release controller owns stopping writers, copying SQLite
consistently, cleaning the source archives/queues, installing checked copies,
and deleting old backups. Only approved L1/L2 IDs from the existing release
ledger survive; their complete rows, provenance, and ledger remain unchanged.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import stat
import time


MARKER = '.fresh-start-candidate.json'
MARKER_VALUE = {'schema': 'qq-companion.fresh-start-candidate.v1', 'stopped_copy': True}
NATIVE = 'plugin_data/astrbot_plugin_memory_companion/memory_companion.db'
LEDGER = 'plugin_data/phase3_profile_release/ledger.sqlite3'
SCOPED = 'plugin_data/astrbot_plugin_memory_companion/req041_scoped.db'

# Names come from the pinned MemoryCompanion schema. Unknown tables fail closed
# so a new content store cannot silently escape this one-time cleanup.
PRESERVE = frozenset({
    'schema_metadata', 'identities', 'memory_acl_rules', 'memory_acl_policies',
    'portrait_people', 'portrait_scope_capabilities', 'portrait_daily_runs',
    'capture_runtime', 'retrieval_revision', 'source_query_revision',
    'source_semantic_revision', 'role_input_meta', 'summary_batch_calls',
})
EMPTY = (
    'summary_batch_events', 'timeline', 'summary_failures', 'chat_import_segments',
    'chat_import_batches', 'relationship_edges', 'knowledge_edges', 'knowledge_nodes',
    'cross_window_threads', 'review_queue', 'injection_logs', 'import_batches',
    'emotion_event_deliveries', 'emotion_events', 'portrait_learning_queue',
    'portrait_facts', 'portrait_evidence', 'portrait_operations',
    'profile_repair_operations', 'memory_correction_receipts',
    'capture_chunks', 'capture_sources', 'capture_operations', 'capture_changes',
    'source_semantic_dependencies', 'source_semantic_documents',
    'source_semantic_dirty', 'source_semantic_order_changes',
    'source_semantic_generations', 'life_source_watches',
    'life_message_source_watches', 'role_input_versions', 'role_input_contexts',
    'role_input_registrations',
)
SPECIAL = frozenset({
    'memories', 'memory_embeddings', 'summary_batches', 'portrait_suppressions',
    'capture_suppression', 'message_source_tombstones', 'life_message_withdrawals',
})
FTS = frozenset({'memory_fts', 'timeline_source_fts', 'knowledge_label_trgm'})
SCOPED_TABLES = frozenset({
    'scoped_records', 'scoped_operations', 'scoped_epoch_state',
    'scoped_epoch_operations', 'scoped_namespace_operations',
})


class FreshMemoryError(ValueError):
    """Safe error code only; never include original SQL rows or private text."""


def _digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _hash_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _candidate(root):
    root = Path(root).absolute()
    if root.resolve() != root or not root.is_dir():
        raise FreshMemoryError('real_candidate_directory_required')
    # A marker in a live mount is insufficient authorization.
    parts = root.parts
    if any(parts[i:i + 2] == ('deployment', 'state') for i in range(len(parts) - 1)):
        raise FreshMemoryError('live_state_directory_forbidden')
    marker = _file(root, MARKER)
    if json.loads(marker.read_text(encoding='utf-8')) != MARKER_VALUE:
        raise FreshMemoryError('stopped_candidate_marker_required')
    return root


def _file(root, relative, *, optional=False):
    rel = Path(relative)
    if rel.is_absolute() or '..' in rel.parts:
        raise FreshMemoryError('candidate_relative_path_required')
    path = root / rel
    if optional and not path.exists():
        return None
    if not path.is_file() or path.resolve() != path:
        raise FreshMemoryError('regular_candidate_file_required')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise FreshMemoryError('independent_candidate_file_required')
    for suffix in ('-wal', '-shm', '-journal'):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() and (sidecar.is_symlink() or sidecar.stat().st_nlink != 1):
            raise FreshMemoryError('unsafe_sqlite_sidecar')
    return path


@contextmanager
def _connect(path, *, write=False):
    db = sqlite3.connect(path.as_uri() + ('?mode=rw' if write else '?mode=ro'),
                         uri=True, timeout=3)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    if not write:
        db.execute('PRAGMA query_only=ON')
    try:
        yield db
        if write:
            db.commit()
    except BaseException:
        if write:
            db.rollback()
        raise
    finally:
        db.close()


def _tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if not r[0].startswith('sqlite_')}


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _counts(db, tables):
    return {name: db.execute(f'SELECT COUNT(*) FROM {_quote(name)}').fetchone()[0]
            for name in sorted(tables)}


def _rows_digest(db, table):
    # Hashing, not exporting, control rows lets us verify exact preservation.
    rows = [dict(r) for r in db.execute(f'SELECT * FROM {_quote(table)}')]
    return _digest(sorted(rows, key=lambda r: json.dumps(r, sort_keys=True)))


def _approved(db, ledger):
    with _connect(ledger) as control:
        if _tables(control) != {'entries'}:
            raise FreshMemoryError('unexpected_profile_ledger_schema')
        entries = [dict(r) for r in control.execute("SELECT * FROM entries WHERE state='applied'")]
    if len(entries) != 15 or len({r['memory_id'] for r in entries}) != 15:
        raise FreshMemoryError('approved_three_l1_twelve_l2_required')
    if any(len({r[key] for r in entries}) != 1 for key in ('batch_id', 'manifest_hash', 'identity_hash')):
        raise FreshMemoryError('single_approved_batch_required')
    records, layers = {}, Counter()
    for entry in entries:
        row = db.execute('SELECT * FROM memories WHERE id=?', (entry['memory_id'],)).fetchone()
        if row is None:
            raise FreshMemoryError('approved_record_missing')
        record = dict(row)
        meta = json.loads(record['metadata'])
        layer = meta.get('profile_layer')
        if (record['source_plugin'] != 'phase3_profile_release'
                or record['validity_status'] != 'active'
                or record['lifecycle'] == 'archived'
                or record['scope'] != 'private'
                or _digest(record['content']) != entry['content_hash']
                or meta.get('manifest_hash') != entry['manifest_hash']
                or meta.get('import_batch') != entry['batch_id']
                or meta.get('confidence_status') != 'user_confirmed'
                or not meta.get('source_refs')
                or not meta.get('source_document_sha256')
                or layer not in {'L1', 'L2'}):
            raise FreshMemoryError('approved_record_changed_or_unavailable')
        if layer == 'L1' and (record['memory_type'] != 'core_memory'
                             or meta.get('core_enabled', True) is not True):
            raise FreshMemoryError('approved_l1_must_remain_native_core')
        if layer == 'L2' and record['memory_type'] != 'external_memory':
            raise FreshMemoryError('approved_l2_native_type_changed')
        records[record['id']] = record
        layers[layer] += 1
    if dict(layers) != {'L1': 3, 'L2': 12}:
        raise FreshMemoryError('approved_three_l1_twelve_l2_required')
    return records


def _check_schema(db):
    names = _tables(db)
    known = PRESERVE | set(EMPTY) | SPECIAL | FTS
    unknown = {n for n in names if n not in known and
               not any(n.startswith(fts + '_') for fts in FTS)}
    if unknown:
        raise FreshMemoryError('unreviewed_native_table')
    if not {'memories', 'timeline', 'summary_batches', 'summary_batch_calls'} <= names:
        raise FreshMemoryError('pinned_native_schema_required')
    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise FreshMemoryError('native_integrity_failed')
    if db.execute('PRAGMA foreign_key_check').fetchall():
        raise FreshMemoryError('native_foreign_keys_failed')
    return names


def _scoped_plan(path):
    if path is None:
        return None
    with _connect(path) as db:
        if _tables(db) != SCOPED_TABLES:
            raise FreshMemoryError('unreviewed_scoped_schema')
        if db.execute("SELECT COUNT(*) FROM scoped_records WHERE record_kind='rule'").fetchone()[0]:
            raise FreshMemoryError('scoped_rules_require_review')
        if db.execute("SELECT COUNT(*) FROM scoped_records WHERE record_kind NOT IN ('profile_fact','memory','evidence','summary')").fetchone()[0]:
            raise FreshMemoryError('unreviewed_scoped_kind')
        return {'records': db.execute('SELECT COUNT(*) FROM scoped_records').fetchone()[0],
                'control_hashes': {t: _rows_digest(db, t) for t in SCOPED_TABLES - {'scoped_records'}}}


def _vacuum(db):
    db.commit()
    if db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] != 0:
        raise FreshMemoryError('candidate_writer_or_reader_still_active')
    db.execute('VACUUM')
    if db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] != 0:
        raise FreshMemoryError('candidate_checkpoint_failed')


def clean_memory_candidate(candidate_root, *, native_relative=NATIVE,
                           ledger_relative=LEDGER, scoped_relative=SCOPED, apply=False):
    """Plan/apply only on a stopped independent data copy; return no content.

    Copy consistency and absence of processes using the copy are the caller's
    responsibility. Each DB transaction fails closed. The caller must discard
    the entire candidate after any failure and never partially install it.
    """
    root = _candidate(candidate_root)
    native = _file(root, native_relative)
    ledger = _file(root, ledger_relative)
    scoped = _file(root, scoped_relative, optional=True)
    ledger_hash = _hash_file(ledger)
    scoped_plan = _scoped_plan(scoped)
    with _connect(native) as db:
        names = _check_schema(db)
        approved = _approved(db, ledger)
        counts = _counts(db, names - {n for n in names if any(n.startswith(f + '_') for f in FTS)})
        exact_controls = PRESERVE - {'retrieval_revision', 'source_query_revision', 'source_semantic_revision'}
        controls = {t: _rows_digest(db, t) for t in exact_controls & names}
        approved_digest = _digest(approved)
    report = {'state': 'PLAN', 'approved_layers': {'L1': 3, 'L2': 12},
              'approved_full_rows_preserved': True, 'profile_ledger_preserved': True,
              'memories_before': counts['memories'], 'memories_to_remove': counts['memories'] - 15,
              'summary_calls_preserved': counts['summary_batch_calls'],
              'table_counts_before': counts, 'scoped_records_to_redact': scoped_plan['records'] if scoped_plan else 0,
              'model_calls': 0, 'private_bodies_exported': False}
    if not apply:
        return report
    with _connect(native, write=True) as db:
        db.execute('PRAGMA secure_delete=ON')
        db.execute('BEGIN IMMEDIATE')
        if _digest(_approved(db, ledger)) != approved_digest:
            raise FreshMemoryError('candidate_changed_before_cleanup')
        db.execute('CREATE TEMP TABLE keep_profile(id TEXT PRIMARY KEY)')
        db.executemany('INSERT INTO keep_profile VALUES(?)', [(key,) for key in approved])
        # Preserve native indexed text for the same immutable approved records.
        indexed = []
        if 'memory_fts' in names:
            indexed = db.execute('SELECT search_text,memory_id FROM memory_fts WHERE memory_id IN (SELECT id FROM keep_profile)').fetchall()
            if len(indexed) != 15 or len({r['memory_id'] for r in indexed}) != 15:
                raise FreshMemoryError('approved_native_index_incomplete')
        for table in EMPTY:
            if table in names:
                db.execute(f'DELETE FROM {_quote(table)}')
        db.execute('DELETE FROM memories WHERE id NOT IN (SELECT id FROM keep_profile)')
        if 'memory_embeddings' in names:
            db.execute('DELETE FROM memory_embeddings WHERE memory_id NOT IN (SELECT id FROM keep_profile)')
        # Parent rows must survive while summary call rows refer to them. This
        # preserves hourly attempt accounting without a resumable batch or text.
        db.execute('DELETE FROM summary_batches WHERE id NOT IN (SELECT batch_id FROM summary_batch_calls)')
        db.execute("UPDATE summary_batches SET state='cancelled',next_retry_at=NULL,retry_reason='',last_error='',metadata='{}',memory_id=''")
        # Suppression/withdrawal keys remain to reject delayed replay. Notes are
        # redacted rather than restoring access by deleting deny-state records.
        for table in ('portrait_suppressions', 'capture_suppression', 'message_source_tombstones', 'life_message_withdrawals'):
            if table in names:
                cols = {r['name'] for r in db.execute(f'PRAGMA table_info({_quote(table)})')}
                for col in ('reason', 'context', 'metadata'):
                    if col in cols:
                        value = '{}' if col in {'context', 'metadata'} else 'fresh_start'
                        db.execute(f'UPDATE {_quote(table)} SET {_quote(col)}=?', (value,))
        if 'memory_fts' in names:
            db.execute('DELETE FROM memory_fts')
            db.executemany('INSERT INTO memory_fts(search_text,memory_id) VALUES(?,?)', [tuple(r) for r in indexed])
            db.execute("INSERT INTO memory_fts(memory_fts) VALUES('rebuild')")
        if 'timeline_source_fts' in names:
            db.execute("INSERT INTO timeline_source_fts(timeline_source_fts) VALUES('rebuild')")
        if 'knowledge_label_trgm' in names:
            db.execute('DELETE FROM knowledge_label_trgm')
            db.execute("INSERT INTO knowledge_label_trgm(knowledge_label_trgm) VALUES('rebuild')")
        if _digest(_approved(db, ledger)) != approved_digest:
            raise FreshMemoryError('approved_profile_modified')
        if any(_rows_digest(db, table) != value for table, value in controls.items()):
            raise FreshMemoryError('protected_control_or_budget_modified')
        _check_schema(db)
        report['table_counts_after'] = _counts(db, set(counts))
        if any(report['table_counts_after'].get(t, 0) for t in EMPTY):
            raise FreshMemoryError('old_derived_payload_remains')
        _vacuum(db)
    if scoped is not None:
        with _connect(scoped, write=True) as db:
            db.execute('PRAGMA secure_delete=ON')
            db.execute('BEGIN IMMEDIATE')
            # Matches the native erased-payload tombstone format; existing
            # revisions/idempotency/epoch remain so an old upsert cannot revive it.
            erased = '{}'
            erased_hash = hashlib.sha256(erased.encode('utf-8')).hexdigest()
            db.execute('''UPDATE scoped_records SET payload_json=?,payload_hash=?,deleted=1,
                revision=revision+CASE WHEN deleted=0 THEN 1 ELSE 0 END,updated_at=?''',
                       (erased, erased_hash, time.time()))
            if any(_rows_digest(db, t) != value for t, value in scoped_plan['control_hashes'].items()):
                raise FreshMemoryError('scoped_epoch_or_idempotency_changed')
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise FreshMemoryError('scoped_integrity_failed')
            _vacuum(db)
    if _hash_file(ledger) != ledger_hash:
        raise FreshMemoryError('profile_ledger_changed')
    report['state'] = 'CLEANED_CANDIDATE'
    report['scoped_active_records_after'] = 0 if scoped else None
    report['no_native_summary_jobs_remain'] = True
    report['caller_must_clear_archives_and_runtime_queues'] = True
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate_root', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        result = clean_memory_candidate(args.candidate_root, apply=args.apply)
    except (FreshMemoryError, sqlite3.Error, OSError, ValueError, KeyError) as exc:
        # Exception messages from SQLite or JSON may contain input. Export only
        # controlled codes for our own exception class, otherwise the type.
        code = str(exc) if isinstance(exc, FreshMemoryError) else type(exc).__name__
        print(json.dumps({'state': 'REFUSED', 'reason': code}))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
