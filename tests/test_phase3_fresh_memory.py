"""Fresh-start cleanup against the fixed native SQLite schema, synthetic only."""
import asyncio
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest


ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / '.runtime/upstream/memory_companion'
spec = importlib.util.spec_from_file_location('fresh_memory', ROOT / 'scripts/phase3_fresh_memory.py')
fresh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fresh)
PACKAGE = 'phase3_fresh_pinned_native'
if UPSTREAM.is_dir():
    package = ModuleType(PACKAGE)
    package.__path__ = [str(UPSTREAM)]
    sys.modules.setdefault(PACKAGE, package)
    NativeStore = importlib.import_module(PACKAGE + '.core.store').MemoryStore
    ScopedStore = importlib.import_module(PACKAGE + '.core.scoped_store').ScopedStore
else:
    NativeStore = None

OLD = 'CHAT_ERASE_SENTINEL_71AQ'
SESSION = 'fixture:FriendMessage:synthetic-user'


@unittest.skipIf(NativeStore is None, 'pinned native source absent; not a PASS')
class FreshMemoryTests(unittest.TestCase):
    def setUp(self):
        commit = subprocess.check_output(['git', '-C', str(UPSTREAM), 'rev-parse', 'HEAD'], text=True).strip()
        self.assertEqual(commit, 'e40a83005b0a2e0817e5680c7d14398c9ab49790')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / fresh.MARKER).write_text(json.dumps(fresh.MARKER_VALUE))
        self.db_path = self.root / fresh.NATIVE
        self.ledger = self.root / fresh.LEDGER
        self.ledger.parent.mkdir(parents=True)
        native = NativeStore(self.db_path)
        native.initialize()
        self.approved = {}
        with sqlite3.connect(self.ledger) as ledger:
            ledger.execute('CREATE TABLE entries(batch_id TEXT,entry_id TEXT,manifest_hash TEXT,identity_hash TEXT,memory_id TEXT,content_hash TEXT,state TEXT,PRIMARY KEY(batch_id,entry_id))')
            for i in range(15):
                key = f'approved-{i:02d}'
                layer = 'L1' if i < 3 else 'L2'
                content = f'合成批准资料 syntheticprofile{i:02d} 保留。'
                metadata = {'profile_layer': layer, 'source_document_sha256': 'a' * 64,
                            'source_refs': [f'synthetic:section:{i}'], 'manifest_hash': 'b' * 64,
                            'import_batch': 'approved-batch', 'confidence_status': 'user_confirmed',
                            'core_enabled': True, 'owner_bot_id': 'synthetic-bot',
                            'persona_id': 'synthetic-persona'}
                native._conn.execute('''INSERT INTO memories(id,memory_type,source_plugin,scope,
                    validity_status,lifecycle,content,metadata) VALUES(?,?,?,?,?,?,?,?)''',
                    (key, 'core_memory' if i < 3 else 'external_memory', 'phase3_profile_release',
                     'private', 'active', 'stable_memory', content, json.dumps(metadata)))
                row = native._conn.execute('SELECT * FROM memories WHERE id=?', (key,)).fetchone()
                native._upsert_memory_fts_row(row)
                self.approved[key] = dict(row)
                ledger.execute('INSERT INTO entries VALUES(?,?,?,?,?,?,?)',
                               ('approved-batch', key, 'b' * 64, 'c' * 64, key, fresh._digest(content), 'applied'))
        db = native._conn
        db.execute("INSERT INTO memories(id,memory_type,content,metadata,source_plugin) VALUES('old-memory','user_preference',?,?, 'memory_summary')", (OLD, json.dumps({'old': OLD})))
        native._upsert_memory_fts_row(db.execute("SELECT * FROM memories WHERE id='old-memory'").fetchone())
        db.execute("INSERT INTO timeline(id,event_type,session_id,content) VALUES('old-timeline','user_message',?,?)", (SESSION, OLD))
        db.execute("INSERT INTO summary_batches(id,session_id,scope,state,last_error,metadata,memory_id,created_at,updated_at) VALUES('old-batch',?,'private','retry_pending',?,?,'old-memory','2026-10-24T00:00:00+00:00','2026-10-24T00:00:00+00:00')", (SESSION, OLD, json.dumps({'text': OLD})))
        db.execute("INSERT INTO summary_batch_events VALUES('old-timeline','old-batch')")
        db.execute("INSERT INTO summary_batch_calls VALUES(1,'old-batch',?,'2026-10-24T00:00:00+00:00',1)", (SESSION,))
        db.execute("INSERT INTO summary_batches(id,session_id,scope,created_at,updated_at) VALUES('unattempted',?,'private','2026-10-24','2026-10-24')", (SESSION,))
        db.execute("INSERT INTO identities(id,entity_id,profile) VALUES('identity','synthetic-user',?)", (json.dumps({'last_session': SESSION, 'last_scope': 'private'}),))
        db.execute("INSERT INTO memory_acl_rules(id,effect,note) VALUES('acl','deny','preserve-config')")
        db.execute("INSERT INTO memory_acl_policies(id,read_mode) VALUES('policy','whitelist')")
        db.execute("INSERT INTO portrait_people(person_id,resolved_identity_key) VALUES('person','synthetic-identity')")
        db.execute("INSERT INTO portrait_scope_capabilities(person_id,source_scope,capability_summary) VALUES('person','private','{\"capture\":true}')")
        db.execute("INSERT INTO portrait_daily_runs(person_id,run_day,attempts,successes) VALUES('person','2026-10-24',2,1)")
        db.execute("INSERT INTO portrait_facts(id,claim_summary) VALUES('old-fact',?)", (OLD,))
        db.execute("INSERT INTO portrait_evidence(evidence_hash,context_refs) VALUES('old-evidence',?)", (json.dumps([OLD]),))
        db.execute("INSERT INTO portrait_learning_queue(queue_id,person_id,fact_id,evidence_hash) VALUES('learn','person','old-fact','old-evidence')")
        db.execute("INSERT INTO portrait_operations(operation_id,snapshot) VALUES('old-operation',?)", (json.dumps({'text': OLD}),))
        db.execute("INSERT INTO injection_logs(id,query) VALUES('old-injection',?)", (OLD,))
        db.execute("INSERT INTO cross_window_threads(id,topic,content) VALUES('old-thread',?,?)", (OLD, OLD))
        db.execute("INSERT INTO knowledge_nodes(id,node_key,label) VALUES('old-node','old-node',?)", (OLD,))
        db.execute("INSERT INTO knowledge_edges(id,source_node_id,target_node_id,source_memory_id,evidence) VALUES('old-edge','old-node','old-node','old-memory',?)", (OLD,))
        db.execute("INSERT INTO capture_sources VALUES('source',?,'old-timeline',1,?,'committed')", (json.dumps({'old': OLD}), json.dumps({'text': OLD})))
        db.execute("INSERT INTO capture_chunks VALUES('source',0,0,0,?,'digest')", (OLD,))
        db.execute("INSERT INTO capture_operations(operation,source_key,payload_digest,envelope,state,created,due,expires) VALUES('capture','source','digest',?,'prepared',1,1,100)", (json.dumps({'text': OLD}),))
        db.execute("INSERT INTO capture_suppression VALUES('existing-deny',?,1)", (OLD,))
        db.execute("INSERT INTO portrait_suppressions(suppression_key,reason) VALUES('deny',?)", (OLD,))
        db.commit()
        native.close()
        self.scoped_path = self.root / fresh.SCOPED
        scoped = ScopedStore(self.scoped_path)
        del scoped
        with sqlite3.connect(self.scoped_path) as db:
            db.execute("INSERT INTO scoped_epoch_state VALUES(1,'epoch','policy',7,1)")
            db.execute("INSERT INTO scoped_records VALUES('scope','profile_fact','old-scoped',1,?,'digest','policy','epoch',0,1)", (json.dumps({'content': OLD}),))
            db.execute("INSERT INTO scoped_operations VALUES('old-event','epoch','request-hash','applied',1)")
        self.ledger_hash = fresh._hash_file(self.ledger)

    def clean(self, **kwargs):
        return fresh.clean_memory_candidate(self.root, **kwargs)

    def count(self, table):
        with sqlite3.connect(self.db_path) as db:
            return db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]

    def test_plan_does_not_modify_database_or_export_content(self):
        before = fresh._hash_file(self.db_path)
        result = self.clean()
        self.assertEqual(result['state'], 'PLAN')
        self.assertEqual(result['memories_to_remove'], 1)
        self.assertEqual(fresh._hash_file(self.db_path), before)
        self.assertEqual(fresh._hash_file(self.ledger), self.ledger_hash)
        self.assertNotIn(OLD, json.dumps(result))

    def test_approved_full_rows_identity_acl_capabilities_and_budget_preserved(self):
        with fresh._connect(self.db_path) as db:
            protected = {t: fresh._rows_digest(db, t) for t in fresh.PRESERVE & fresh._tables(db)
                         if t not in {'retrieval_revision', 'source_query_revision', 'source_semantic_revision'}}
        result = self.clean(apply=True)
        self.assertEqual(result['state'], 'CLEANED_CANDIDATE')
        self.assertEqual(result['approved_layers'], {'L1': 3, 'L2': 12})
        with fresh._connect(self.db_path) as db:
            self.assertEqual(fresh._approved(db, self.ledger), self.approved)
            for table, before in protected.items():
                self.assertEqual(fresh._rows_digest(db, table), before, table)
        self.assertEqual(fresh._hash_file(self.ledger), self.ledger_hash)

    def test_derived_content_tables_and_pending_native_summary_are_empty(self):
        self.clean(apply=True)
        with fresh._connect(self.db_path) as db:
            for table in fresh.EMPTY:
                if table in fresh._tables(db):
                    self.assertEqual(db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0, table)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM memories').fetchone()[0], 15)
            row = db.execute('SELECT * FROM summary_batches').fetchone()
            self.assertEqual((row['state'], row['metadata'], row['last_error'], row['memory_id']), ('cancelled', '{}', '', ''))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM summary_batches').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM summary_batch_calls').fetchone()[0], 1)
        native = NativeStore(self.db_path)
        native.initialize()
        self.addCleanup(native.close)
        self.assertIsNone(asyncio.run(native.next_summary_batch(SESSION, force=True)))

    def test_indexes_keep_profiles_remove_old_terms_and_purge_raw_database_bytes(self):
        self.clean(apply=True)
        with fresh._connect(self.db_path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM memory_fts WHERE memory_fts MATCH 'syntheticprofile04'").fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM memory_fts WHERE memory_fts MATCH ?', (OLD,)).fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM knowledge_label_trgm').fetchone()[0], 0)
        for path in self.root.rglob('*'):
            if path.is_file():
                self.assertNotIn(OLD.encode(), path.read_bytes(), path.name)

    def test_scoped_payload_tombstone_keeps_epoch_and_idempotency(self):
        result = self.clean(apply=True)
        self.assertEqual(result['scoped_records_to_redact'], 1)
        with fresh._connect(self.scoped_path) as db:
            row = db.execute('SELECT * FROM scoped_records').fetchone()
            self.assertEqual((row['payload_json'], row['deleted'], row['revision']), ('{}', 1, 2))
            self.assertEqual(row['payload_hash'], hashlib.sha256(b'{}').hexdigest())
            self.assertEqual(db.execute('SELECT revision FROM scoped_epoch_state').fetchone()[0], 7)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM scoped_operations').fetchone()[0], 1)

    def test_repeat_does_not_duplicate_profiles_or_reset_summary_attempts(self):
        self.clean(apply=True)
        second = self.clean(apply=True)
        self.assertEqual(second['memories_to_remove'], 0)
        self.assertEqual(self.count('memories'), 15)
        self.assertEqual(self.count('summary_batch_calls'), 1)
        with fresh._connect(self.scoped_path) as db:
            self.assertEqual(db.execute('SELECT revision FROM scoped_records').fetchone()[0], 2)

    def test_new_native_timeline_and_summary_are_allowed_after_reopen(self):
        self.clean(apply=True)
        native = NativeStore(self.db_path)
        native.initialize()
        self.addCleanup(native.close)
        with native._conn:
            native._conn.execute("INSERT INTO timeline(id,event_type,session_id,content) VALUES('new-event','user_message',?,'new allowed chat')", (SESSION,))
            native._conn.execute("INSERT INTO summary_batches(id,session_id,scope,created_at,updated_at) VALUES('new-batch',?,'private','2026-10-24','2026-10-24')", (SESSION,))
            native._conn.execute("INSERT INTO summary_batch_events VALUES('new-event','new-batch')")
        batch = asyncio.run(native.next_summary_batch(SESSION, force=True))
        self.assertEqual(batch['id'], 'new-batch')
        self.assertEqual(len(asyncio.run(native.summary_batch_rows('new-batch'))), 1)
        self.assertEqual(self.count('summary_batch_calls'), 1)

    def test_missing_marker_and_live_path_are_rejected(self):
        (self.root / fresh.MARKER).unlink()
        with self.assertRaises(fresh.FreshMemoryError):
            self.clean(apply=True)
        live = self.root / 'deployment/state/data'
        live.mkdir(parents=True)
        (live / fresh.MARKER).write_text(json.dumps(fresh.MARKER_VALUE))
        with self.assertRaisesRegex(fresh.FreshMemoryError, 'live_state'):
            fresh.clean_memory_candidate(live, apply=True)

    def test_hardlinked_copy_is_rejected(self):
        os.link(self.db_path, self.root / 'other.db')
        with self.assertRaisesRegex(fresh.FreshMemoryError, 'independent'):
            self.clean(apply=True)

    def test_changed_approved_content_fails_before_any_deletion(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE memories SET content='unreviewed' WHERE id='approved-01'")
        before = fresh._hash_file(self.db_path)
        with self.assertRaisesRegex(fresh.FreshMemoryError, 'approved_record_changed'):
            self.clean(apply=True)
        self.assertEqual(fresh._hash_file(self.db_path), before)
        self.assertEqual(self.count('memories'), 16)

    def test_unknown_native_table_refuses_without_partial_deletion(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute('CREATE TABLE surprise_payload(text TEXT)')
        with self.assertRaisesRegex(fresh.FreshMemoryError, 'unreviewed_native_table'):
            self.clean(apply=True)
        self.assertEqual(self.count('timeline'), 1)

    def test_scoped_rule_requires_review_before_main_mutation(self):
        with sqlite3.connect(self.scoped_path) as db:
            db.execute("UPDATE scoped_records SET record_kind='rule'")
        with self.assertRaisesRegex(fresh.FreshMemoryError, 'scoped_rules_require_review'):
            self.clean(apply=True)
        self.assertEqual(self.count('timeline'), 1)

    def test_withdrawn_or_incomplete_approved_batch_refused(self):
        with sqlite3.connect(self.ledger) as db:
            db.execute("UPDATE entries SET state='withdrawn' WHERE entry_id='approved-01'")
        with self.assertRaisesRegex(fresh.FreshMemoryError, 'approved_three'):
            self.clean(apply=True)
        self.assertEqual(self.count('memories'), 16)


if __name__ == '__main__':
    unittest.main()
