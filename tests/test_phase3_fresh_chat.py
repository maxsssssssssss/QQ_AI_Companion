import importlib.util
import ast
import importlib
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import types
import sys
import unittest

from integrations.phase1_guard.store import GuardStore


SPEC = importlib.util.spec_from_file_location('phase3_fresh_chat', Path(__file__).parents[1]/'scripts/phase3_fresh_chat.py')
fresh = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fresh)


class FreshChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/'copy'
        self.root.mkdir()
        (self.root/fresh.MARKER).write_text(json.dumps({'schema': fresh.MARKER_SCHEMA, 'stopped_copy': True}))

    def db(self, relative, script):
        path = self.root/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.executescript(script)
        return path

    def guard(self):
        path = self.root/fresh.GUARD
        store = GuardStore(path)
        store.enable_phase3()
        store.enable_turn_diagnostics()
        store.enable_bubbles()
        store.claim('p', 's', 'old1', 'u', 'UNIQUE_OLD_TRANSCRIPT', [{'text': 'UNIQUE_OLD_TRANSCRIPT'}])
        store.db.execute("INSERT INTO turns(turn_id,platform,session,state,created_at,diagnostics_json) VALUES ('t','p','s','generating','yesterday','{\"bad\":\"UNIQUE_OLD_TRANSCRIPT\"}')")
        store.db.execute("INSERT INTO turn_sources VALUES ('t',0,'p','s','old1')")
        store.db.execute("INSERT INTO deliveries(delivery_id,turn_id,kind,part_index,text,chain_json,state,created_at) VALUES ('d','t','normal',0,'UNIQUE_OLD_TRANSCRIPT','[]','pending','yesterday')")
        store.db.execute("INSERT INTO bubble_plans VALUES ('t','normal','hash','yesterday')")
        store.db.execute("INSERT INTO bubble_parts(turn_id,kind,part_index,delivery_id,content_sha256,chars,state,transport_attempts) VALUES ('t','normal',0,'d','hash',20,'planned',1)")
        store.db.execute("INSERT INTO bubble_parts(turn_id,kind,part_index,delivery_id,content_sha256,chars,state,transport_attempts) VALUES ('t','normal',1,'d2','hash',20,'planned',0)")
        store.db.commit()
        store.begin_outbound(('p','s','old1'), 'UNIQUE_OLD_TRANSCRIPT', [])
        store.db.close()
        return path

    def runtime(self):
        return self.db(fresh.RUNTIME/'memory-controls.sqlite3', '''
            CREATE TABLE identities(session_id TEXT PRIMARY KEY,scope TEXT,user_id TEXT,bot_id TEXT,platform TEXT,persona_id TEXT);
            INSERT INTO identities VALUES ('s','scope','u','b','p','persona');
            CREATE TABLE archive_baselines(stream TEXT PRIMARY KEY,row_id INTEGER);
            INSERT INTO archive_baselines VALUES ('inbound',0),('delivery',0);
            CREATE TABLE archive_cursors(scope TEXT,stream TEXT,row_id INTEGER,PRIMARY KEY(scope,stream));
            INSERT INTO archive_cursors VALUES ('scope','inbound',0),('scope','delivery',0);
            CREATE TABLE sources(id INTEGER PRIMARY KEY, content TEXT);
            INSERT INTO sources VALUES (1,'UNIQUE_OLD_TRANSCRIPT');
            CREATE TABLE jobs(id INTEGER PRIMARY KEY, content TEXT);
            INSERT INTO jobs VALUES (1,'UNIQUE_OLD_TRANSCRIPT');
            CREATE TABLE approved_history(id INTEGER PRIMARY KEY);
            INSERT INTO approved_history VALUES (1);
            CREATE TABLE controls(scope TEXT PRIMARY KEY,capture_paused INTEGER,recall_paused INTEGER,reason TEXT);
            INSERT INTO controls VALUES ('scope',0,0,'');
            CREATE TABLE imports(batch_id TEXT,entry_id TEXT,memory_id TEXT);
            INSERT INTO imports VALUES ('approved','profile','stable-id');
        ''')

    def test_marker_required_and_live_path_rejected(self):
        (self.root/fresh.MARKER).unlink()
        with self.assertRaisesRegex(ValueError, 'copy_marker'):
            fresh.clean_copy(self.root)
        with self.assertRaisesRegex(ValueError, 'isolated_data_copy'):
            fresh.checked_copy('/opt/qq-ai-companion/deployment/state/data')

    def test_symlink_and_hardlink_rejected_before_writes(self):
        target = self.root/'real'
        target.write_text('untouched')
        (self.root/'data_v4.db').symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'symlink_target'):
            fresh.clean_copy(self.root)
        (self.root/'data_v4.db').unlink()
        (self.root/'data_v4.db').hardlink_to(target)
        with self.assertRaisesRegex(ValueError, 'regular_unshared'):
            fresh.clean_copy(self.root)
        self.assertEqual(target.read_text(), 'untouched')

    def test_astrbot_removes_history_preserves_persona_config_usage(self):
        path = self.db('data_v4.db', '''
            CREATE TABLE conversations(conversation_id TEXT,content TEXT,title TEXT);
            INSERT INTO conversations VALUES ('old','UNIQUE_OLD_TRANSCRIPT','old title');
            CREATE TABLE platform_message_history(content TEXT);
            INSERT INTO platform_message_history VALUES ('UNIQUE_OLD_TRANSCRIPT');
            CREATE TABLE preferences(key TEXT,value TEXT);
            INSERT INTO preferences VALUES ('sel_conv_id','old'),('persona_id','small-whale'),('provider_id','flash');
            CREATE TABLE personas(persona_id TEXT,system_prompt TEXT);
            INSERT INTO personas VALUES ('small-whale','APPROVED_PERSONA');
            CREATE TABLE provider_stats(token_input INTEGER);
            INSERT INTO provider_stats VALUES (1234);
        ''')
        fresh.clean_copy(self.root)
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM conversations').fetchone()[0],0)
            self.assertEqual(dict(db.execute('SELECT * FROM preferences')), {'persona_id':'small-whale','provider_id':'flash'})
            self.assertEqual(db.execute('SELECT system_prompt FROM personas').fetchone()[0],'APPROVED_PERSONA')
            self.assertEqual(db.execute('SELECT token_input FROM provider_stats').fetchone()[0],1234)
        self.assertNotIn(b'UNIQUE_OLD_TRANSCRIPT',path.read_bytes())

    def test_extra_astrbot_task_fails_closed_and_transaction_rolls_back(self):
        path = self.db('data_v4.db', '''
            CREATE TABLE conversations(conversation_id TEXT,content TEXT,title TEXT);
            INSERT INTO conversations VALUES ('old','keep-until-reviewed','x');
            CREATE TABLE cron_jobs(payload TEXT);
            INSERT INTO cron_jobs VALUES ('unreviewed');
        ''')
        with self.assertRaisesRegex(ValueError,'additional_astrbot'):
            fresh.clean_copy(self.root)
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM conversations').fetchone()[0],1)

    def test_recorder_fts_clean_and_new_record_still_searchable(self):
        path = self.db('plugin_data/astrbot_plugin_message_recorder/messages.db', '''
            CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT,message_str TEXT,message_chain TEXT,raw_message TEXT);
            CREATE VIRTUAL TABLE messages_fts USING fts5(message_str,content='messages',content_rowid='id');
            CREATE TRIGGER ai AFTER INSERT ON messages BEGIN INSERT INTO messages_fts(rowid,message_str) VALUES (new.id,new.message_str); END;
            CREATE TRIGGER ad AFTER DELETE ON messages BEGIN INSERT INTO messages_fts(messages_fts,rowid,message_str) VALUES ('delete',old.id,old.message_str); END;
            INSERT INTO messages(message_str,message_chain,raw_message) VALUES ('oldsecret','[]','oldsecret');
        ''')
        fresh.clean_copy(self.root)
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM messages_fts WHERE messages_fts MATCH 'oldsecret'").fetchone()[0],0)
            db.execute("INSERT INTO messages(message_str,message_chain,raw_message) VALUES ('freshchat','[]','freshchat')")
            self.assertEqual(db.execute("SELECT count(*) FROM messages_fts WHERE messages_fts MATCH 'freshchat'").fetchone()[0],1)

    def test_guard_keeps_idempotency_unknown_and_cancels_unsent_tail(self):
        path = self.guard()
        fresh.clean_copy(self.root)
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute('SELECT text,chain_json FROM inbound').fetchone(), ('','[]'))
            self.assertEqual(db.execute('SELECT state FROM deliveries').fetchone()[0], 'unknown')
            self.assertEqual(db.execute('SELECT state FROM outbound').fetchone()[0], 'unknown')
            self.assertEqual(db.execute('SELECT state,diagnostics_json FROM turns').fetchone(), ('interrupted','{}'))
            self.assertEqual([r[0] for r in db.execute('SELECT state FROM bubble_parts ORDER BY part_index')], ['unknown','cancelled'])
        store = GuardStore(path)
        try:
            self.assertFalse(store.claim('p','s','old1','u','replayed',[]))
            self.assertTrue(store.claim('p','s','new','u','NEW_CHAT',[]))
        finally:
            store.db.close()
        self.assertNotIn(b'UNIQUE_OLD_TRANSCRIPT',path.read_bytes())

    def test_runtime_cutoff_after_compaction_preserves_profile_identity_and_new_chat(self):
        guard = self.guard()
        runtime = self.runtime()
        report = fresh.clean_copy(self.root)
        with sqlite3.connect(runtime) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM sources').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM approved_history').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT memory_id FROM imports').fetchone()[0],'stable-id')
            self.assertEqual(db.execute('SELECT count(*) FROM identities').fetchone()[0],1)
            self.assertEqual(dict(db.execute('SELECT stream,row_id FROM archive_baselines')),report['guard_cutoffs'])
            self.assertEqual(dict(db.execute('SELECT stream,row_id FROM archive_cursors')),report['guard_cutoffs'])
        store = GuardStore(guard)
        try:
            store.claim('p','s','new','u','NEW_CHAT',[])
            self.assertEqual(store.db.execute('SELECT text FROM inbound WHERE rowid>?',(report['guard_cutoffs']['inbound'],)).fetchone()[0], 'NEW_CHAT')
        finally:
            store.db.close()

    def test_proactive_does_not_reset_attempt_or_generation_budget(self):
        path = self.db(fresh.RUNTIME/'proactive-controls.sqlite3','''
            CREATE TABLE proactive_attempts(candidate TEXT PRIMARY KEY,day TEXT,attempted REAL,state TEXT,topic_id TEXT);
            INSERT INTO proactive_attempts VALUES ('a','today',123,'pending','OLD_TOPIC'),('b','today',124,'failed','OLD_TOPIC');
            CREATE TABLE proactive_generations(candidate TEXT PRIMARY KEY,day TEXT,started REAL,state TEXT);
            INSERT INTO proactive_generations VALUES ('a','today',120,'running'),('b','today',121,'skipped');
            CREATE TABLE proactive_controls(name TEXT PRIMARY KEY,value TEXT);
            INSERT INTO proactive_controls VALUES ('pause_until','999'),('blocked:id','1'),('spoken:id','123'),('last_topic','OLD_TOPIC');
        ''')
        fresh.clean_copy(self.root)
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM proactive_attempts').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT count(*) FROM proactive_generations').fetchone()[0],2)
            self.assertEqual(db.execute("SELECT state FROM proactive_attempts WHERE candidate='a'").fetchone()[0],'unknown')
            self.assertEqual(dict(db.execute('SELECT * FROM proactive_controls')),{'pause_until':'999','blocked:id':'1'})
            self.assertEqual(db.execute("SELECT count(*) FROM proactive_attempts WHERE topic_id<>''").fetchone()[0],0)

    def test_private_keeps_routing_capability_budget_and_clears_all_old_topics(self):
        original = {'version': 1, 'token_usage': {'today': 17}, 'runtime_settings': {'proactive':False},
            'users': {'u': {'umo':'p:FriendMessage:u','enabled':True,'proactive_private_enabled':False,
                'unified_profile_capabilities': {'private_memory_enabled':True}, 'sent_today':3,'sent_day':'today',
                'proactive_daily_limit':5,'last_user_message':'OLD_TEXT','companion_memory':{'fact':'OLD_TEXT'},
                'dialogue_episodes':[{'text':'OLD_TEXT'}],'planned_proactive_topic':'OLD_TEXT',
                'proactive_impulses':[{'text':'OLD_TEXT'}],'private_delivery_routes':{'p:FriendMessage:u':{'umo':'p:FriendMessage:u','last_error':'OLD_TEXT','failure_count':1}}}},
            'unified_person': {'identity_links':{'id':'u'},'profiles':{'u':{'profile_status':'active'}},'audit_events':[{'note':'OLD_TEXT'}]},
            'proactive_candidate_pool':[{'content':'OLD_TEXT'}], '_req041_private_memory':{'x':'OLD_TEXT'},
            'private_image_vision_cache': {'x':'OLD_TEXT'},'groups':{}}
        result = fresh.clean_private_payload(original)
        self.assertNotIn('OLD_TEXT',json.dumps(result))
        self.assertEqual(result['token_usage'],{'today':17})
        self.assertEqual(result['users']['u']['sent_today'],3)
        self.assertFalse(result['users']['u']['proactive_private_enabled'])
        self.assertEqual(result['users']['u']['umo'],'p:FriendMessage:u')
        self.assertEqual(result['unified_person']['identity_links'],{'id':'u'})
        self.assertEqual(original['users']['u']['last_user_message'],'OLD_TEXT')

    def test_private_sqlite_checksums_revisions_and_json_both_scrubbed(self):
        path = self.db(fresh.PRIVATE/'persona_profiles/default.db','''
            CREATE TABLE store_sections(section_name TEXT PRIMARY KEY,payload_json TEXT,updated_at REAL,checksum TEXT,schema_version INTEGER,revision INTEGER,is_deleted INTEGER);
            CREATE TABLE store_meta(meta_key TEXT PRIMARY KEY,meta_value TEXT);
            INSERT INTO store_meta VALUES ('initialized','1'),('next_revision','99');
        ''')
        with sqlite3.connect(path) as db:
            for i,(key,val) in enumerate({'version':1,'users':{'u':{'umo':'p:s:u','last_user_message':'OLD_TEXT'}},'memo_notes':['OLD_TEXT']}.items()):
                raw=fresh.canonical(val)
                db.execute('INSERT INTO store_sections VALUES (?,?,?,?,?,?,?)',(key,raw,0,fresh.digest(val),2,i+1,0))
        jpath=self.root/fresh.PRIVATE/'companions.json'
        jpath.write_text(json.dumps({'users':{'u':{'umo':'p:s:u','last_user_message':'OLD_TEXT'}}}))
        fresh.clean_copy(self.root)
        self.assertNotIn('OLD_TEXT',jpath.read_text())
        with sqlite3.connect(path) as db:
            for raw,checksum in db.execute('SELECT payload_json,checksum FROM store_sections'):
                self.assertEqual(checksum, fresh.digest(json.loads(raw)))
                self.assertNotIn('OLD_TEXT',raw)
            self.assertEqual(db.execute("SELECT meta_value FROM store_meta WHERE meta_key='next_revision'").fetchone()[0],'99')

    def test_outbox_applied_watermarks_retained_body_removed(self):
        path=self.db(fresh.PRIVATE/'req041_migration_outbox.db','''
            CREATE TABLE outbox(event_id TEXT PRIMARY KEY,payload_json TEXT,payload_hash TEXT,state TEXT,source_revision INTEGER);
            INSERT INTO outbox VALUES ('id','{"body":"OLD_TEXT"}','original-digest','applied',37);
        ''')
        fresh.clean_copy(self.root)
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute('SELECT * FROM outbox').fetchone(),('id','{}','original-digest','applied',37))

    def test_unsettled_outbox_fails_without_faking_applied(self):
        path=self.db(fresh.PRIVATE/'req041_migration_outbox.db','''
            CREATE TABLE outbox(event_id TEXT PRIMARY KEY,payload_json TEXT,payload_hash TEXT,state TEXT,source_revision INTEGER);
            INSERT INTO outbox VALUES ('id','{"body":"OLD_TEXT"}','original-digest','failed',37);
        ''')
        with self.assertRaisesRegex(ValueError,'unsettled_native'):
            fresh.clean_copy(self.root)
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute('SELECT state FROM outbox').fetchone()[0],'failed')

    def test_outbox_keeps_native_identity_checkpoint_and_zeroes_relationship_accumulation(self):
        path=self.db(fresh.PRIVATE/'req041_migration_outbox.db','''
            CREATE TABLE outbox(event_id TEXT PRIMARY KEY,payload_json TEXT,payload_hash TEXT,state TEXT,source_revision INTEGER);
        ''')
        payload={'operation':'identity_baseline','identity_ref':'person-id','projection_revision':5,
                 'projection_checkpoint_hash':'checkpoint','body':'OLD_TEXT'}
        relationship={'operation':'relationship_legacy_snapshot','relationship_role':'owner',
                      'relationship_mode':'normal','relationship_score':86,'daily_totals':{'today':12},
                      'last_effective_at':100,'ledger':['OLD_TEXT']}
        with sqlite3.connect(path) as db:
            db.execute('INSERT INTO outbox VALUES (?,?,?,?,?)',('identity',json.dumps(payload),'old-id-hash','applied',5))
            db.execute('INSERT INTO outbox VALUES (?,?,?,?,?)',('relationship',json.dumps(relationship),'old-rel-hash','applied',20))
        fresh.clean_copy(self.root)
        with sqlite3.connect(path) as db:
            cleaned={r[0]:json.loads(r[1]) for r in db.execute('SELECT event_id,payload_json FROM outbox')}
        self.assertEqual(cleaned['identity']['projection_checkpoint_hash'],'checkpoint')
        self.assertEqual(cleaned['identity']['projection_revision'],5)
        self.assertEqual(cleaned['relationship']['relationship_score'],0)
        self.assertEqual(cleaned['relationship']['daily_totals'],{'day':'','positive':0,'negative':0})
        self.assertNotIn('OLD_TEXT',json.dumps(cleaned))

    def test_native_discarded_relationship_tail_does_not_replay_or_break_new_revision(self):
        root=Path(__file__).parents[1]/'.runtime/upstream/private_companion'
        pkg=types.ModuleType('fresh_native_outbox')
        pkg.__path__=[str(root)]
        sys.modules[pkg.__name__]=pkg
        module=importlib.import_module(pkg.__name__+'.migration_outbox')
        namespace=importlib.import_module(pkg.__name__+'.identity_namespace')
        path=self.root/fresh.PRIVATE/'req041_migration_outbox.db'
        path.parent.mkdir(parents=True)
        native=module.MigrationOutbox(path)
        native.begin_epoch('fixture-epoch',policy_version='req041-v1')
        context=namespace.NamespaceContext(kind='private',persona_id='fixture-persona',identity_id='person-id',
                    group_id='',assurance='verified',profile_status='active',policy_version='req041-v1',migration_epoch='fixture-epoch')
        for i in range(3):
            native.enqueue_next(stream_key='relationship:person-id',event_id='old-'+str(i),namespace=context,
                migration_epoch='fixture-epoch',policy_version='req041-v1',
                payload={'operation':'relationship_legacy_event','identity_ref':'person-id','score_after':i+10,'private_note':'OLD_TEXT'})
        native.mark_failed('old-0','fixture-epoch',error_code='previous_failure')
        jpath=self.root/fresh.PRIVATE/'companions.json'
        jpath.write_text(json.dumps({'users':{'u':{'umo':'p:FriendMessage:u','unified_person_id':'person-id',
                      'req041_relationship_source_revision':3,'relationship_score':12}}}))
        result=fresh.clean_copy(self.root)
        self.assertEqual(result['databases'][str(fresh.PRIVATE/'req041_migration_outbox.db')]['discarded_legacy_events'],3)
        self.assertEqual(native.pending('fixture-epoch'),[])
        self.assertEqual(native.applied_revision('relationship:person-id','fixture-epoch'),0)
        self.assertEqual(native.stream_revision('relationship:person-id','fixture-epoch'),0)
        self.assertIsNone(native.latest_for_stream('relationship:person-id','fixture-epoch'))
        self.assertEqual(json.loads(jpath.read_text())['users']['u']['req041_relationship_source_revision'],0)
        duplicate=native.enqueue_next(stream_key='relationship:person-id',event_id='old-0',namespace=context,
                migration_epoch='fixture-epoch',policy_version='req041-v1',
                payload={'operation':'relationship_legacy_event','identity_ref':'person-id','score_after':10,'private_note':'OLD_TEXT'})
        self.assertEqual(duplicate['status'],'duplicate')
        self.assertEqual(native.pending('fixture-epoch'),[])
        next_item=native.enqueue_next(stream_key='relationship:person-id',event_id='new-baseline',namespace=context,
            migration_epoch='fixture-epoch',policy_version='req041-v1',
            payload={'operation':'relationship_legacy_snapshot','identity_ref':'person-id','relationship_score':0})
        self.assertEqual(next_item['source_revision'],native.applied_revision('relationship:person-id','fixture-epoch')+1)
        self.assertEqual([p.event_id for p in native.pending('fixture-epoch')],['new-baseline'])
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM outbox WHERE state='discarded'").fetchone()[0],3)
            self.assertEqual(db.execute("SELECT count(*) FROM outbox WHERE state='applied'").fetchone()[0],0)
            self.assertNotIn('OLD_TEXT',json.dumps(list(db.execute('SELECT payload_json FROM outbox'))))

    def test_native_replay_worker_recovers_clean_baseline_and_accepts_new_event(self):
        root=Path(__file__).parents[1]/'.runtime/upstream/private_companion'
        pkg=types.ModuleType('fresh_native_replay')
        pkg.__path__=[str(root)]
        sys.modules[pkg.__name__]=pkg
        modules={n:importlib.import_module(pkg.__name__+'.'+n) for n in (
            'migration_coordinator','migration_outbox','migration_replay','migration_dual_write',
            'unified_person_registry','relationship_account_store','identity_namespace')}
        data=self.root/fresh.PRIVATE
        data.mkdir(parents=True)
        source=data/'companions.json'
        source.write_text('{"users":{}}')
        coordinator=modules['migration_coordinator'].MigrationCoordinator(data)
        status=coordinator.start_or_resume(source_files=[source],policy_version='req041-v1',
            source_schema_version='legacy-effective',target_schema_version='req041-v1',
            companion_version='6.7.0',memory_version='2.3.1',reserve_bytes=0)
        coordinator.capture_compatibility({})
        coordinator.transition('S3',checkpoint='outbox_active')
        coordinator.transition('S4',checkpoint='backfill_active')
        epoch=status['migration_epoch']
        path=data/'req041_migration_outbox.db'
        outbox=modules['migration_outbox'].MigrationOutbox(path)
        outbox.begin_epoch(epoch,policy_version='req041-v1')
        registry=modules['unified_person_registry'].UnifiedPersonRegistry({})
        created=registry.create_or_link({'companion_instance_id':'astrbot_plugin_private_companion',
              'bot_account_id':'onebot:bot-1','adapter_instance_id':'onebot:default',
              'subject_namespace':'onebot:user','platform_subject_id':'10001'},operation_id='fixture-create')
        person_id=created['person_id']
        coordinator.register_identity(person_id,assurance='verified')
        context=modules['identity_namespace'].build_namespace_context(registry.formal_namespace_for_person(
            person_id,policy_version='req041-v1',migration_epoch=epoch,purpose='relationship_write')['context'])
        outbox.enqueue_next(stream_key='relationship:'+person_id,event_id='old-revoked',namespace=context,
                migration_epoch=epoch,policy_version='req041-v1',
                payload={'operation':'relationship_legacy_event','identity_ref':person_id,'score_after':80})
        fresh.clean_private_outbox(path)
        accounts=modules['relationship_account_store'].RelationshipAccountStore(data/'req041_relationship.db',active_migration_epoch=epoch)
        live={'relationship_role':'friend','relationship_mode':'normal','relationship_score':0,
              'positive_stage_cap_key':'close','daily_totals':{'day':'','positive':0,'negative':0},'last_effective_at':0.0}
        worker=modules['migration_replay'].MigrationReplayWorker(outbox=outbox,coordinator=coordinator,
            relationship_store=accounts,registry=registry,legacy_relationship_resolver=lambda _:dict(live),
            enable_gap_recovery=True,migration_epoch=epoch,policy_version='req041-v1')
        first=worker.run_batch()
        self.assertEqual(first['status'],'ok',first)
        self.assertEqual(accounts.account(context)['relationship_score'],0)
        self.assertEqual(outbox.applied_revision('relationship:'+person_id,epoch),1)
        second=worker.run_batch()
        self.assertEqual((second['status'],second['count']),('ok',0),second)
        producer=modules['migration_dual_write'].MigrationDualWriteProducer(outbox=outbox,
            coordinator=coordinator,migration_epoch=epoch,policy_version='req041-v1')
        live.update(relationship_score=2,daily_totals={'day':'2026-10-24','positive':2,'negative':0},last_effective_at=1700000000.0)
        user={'user_id':'10001','unified_person_id':person_id,'relationship_role':'friend','relationship_mode':'normal',
              'relationship_score':2,'relationship_positive_stage_cap_key':'close',
              'relationship_daily_totals':live['daily_totals'],'relationship_last_effective_at':1700000000.0}
        producer.emit_relationship(registry=registry,user=user,requested_delta=2,reason_code='inbound',source_revision=2,
            result={'changed':True,'delta':2,'entry':{'event_key':'new-message-key','score_before':0,'score_after':2}})
        third=worker.run_batch()
        self.assertEqual(third['status'],'ok',third)
        self.assertEqual(accounts.account(context)['relationship_score'],2)
        self.assertEqual(outbox.applied_revision('relationship:'+person_id,epoch),2)

    def test_fixed_native_authoritative_root_and_scoped_projection_after_cleanup(self):
        root=Path(__file__).parents[1]/'.runtime/upstream/private_companion'
        pkg=types.ModuleType('fresh_native_projection')
        pkg.__path__=[str(root)]
        sys.modules[pkg.__name__]=pkg
        native=importlib.import_module(pkg.__name__+'.authoritative_private_memory')
        registry_module=importlib.import_module(pkg.__name__+'.unified_person_registry')
        projection=importlib.import_module(pkg.__name__+'.migration_scoped_projection')
        source={}
        person_id=registry_module.UnifiedPersonRegistry(source).create_or_link({
            'companion_instance_id':'astrbot_plugin_private_companion','bot_account_id':'onebot:bot-1',
            'adapter_instance_id':'onebot:default','subject_namespace':'onebot:user',
            'platform_subject_id':'10001'},operation_id='fixture-create')['person_id']
        source['users']={'10001':{'user_id':'10001','identity_subject_id':'10001',
            'unified_person_id':person_id,'companion_memory':{'items':[{'text':'OLD_MEMORY'}]}}}
        source[fresh.PRIVATE_MEMORY_KEY]={}
        with self.assertRaisesRegex(native.AuthoritativePrivateMemoryError,'private_memory_store_invalid'):
            native.AuthoritativePrivateMemoryStore(source).read(person_id)
        calls=[]
        sync=projection.ScopedProjectionSynchronizer(
            read=lambda *a,**k:{'ok':True,'code':'not_found','record':None},
            list_records=lambda *a,**k:{'ok':True,'records':[]},
            upsert=lambda *a,**k:calls.append(k) or {'ok':True},
            tombstone=lambda *a,**k:{'ok':True},migration_epoch='epoch-1',policy_version='req041-v1')
        with self.assertRaisesRegex(projection.ScopedProjectionError,'private_memory_store_invalid'):
            sync.sync_snapshot(source)
        cleaned=fresh.clean_private_payload(source)
        self.assertEqual(native.AuthoritativePrivateMemoryStore(cleaned).read(person_id)['code'],'not_found')
        self.assertTrue(sync.sync_snapshot(cleaned)['ok'])
        self.assertTrue(sync.sync_snapshot(cleaned)['ok'])
        self.assertFalse(any(c['record_kind']=='memory' for c in calls))
        allowed_profile={'nickname','style','profile_origin','auto_profile_created','profile_fact_revision'}
        self.assertTrue(any(c['record_kind']=='profile_fact' for c in calls))
        for call in calls:
            if call['record_kind']=='profile_fact':
                self.assertLessEqual(set(call['payload']['content']),allowed_profile)
        self.assertNotIn('OLD_MEMORY',json.dumps(calls))
        self.assertNotIn('OLD_MEMORY',json.dumps(cleaned))

    def test_narrow_root_repair_preserves_new_chat_other_sections_and_revisions(self):
        directory=self.root/fresh.PRIVATE
        directory.mkdir(parents=True)
        json_path=directory/'companions.json'
        new_user={'10001':{'recent_messages':[{'text':'NEW_CHAT'}],'sent_today':4}}
        json_path.write_text(json.dumps({fresh.PRIVATE_MEMORY_KEY:{},'users':new_user}))
        db_path=self.db(fresh.PRIVATE/'companions.db','''
            CREATE TABLE store_sections(section_name TEXT PRIMARY KEY,payload_json TEXT,checksum TEXT,revision INTEGER,is_deleted INTEGER);
            CREATE TABLE store_meta(meta_key TEXT PRIMARY KEY,meta_value TEXT);
            INSERT INTO store_meta VALUES ('next_revision','20');
        ''')
        with sqlite3.connect(db_path) as db:
            db.execute('INSERT INTO store_sections VALUES (?,?,?,?,0)',(fresh.PRIVATE_MEMORY_KEY,'{}',fresh.digest({}),5))
            db.execute('INSERT INTO store_sections VALUES (?,?,?,?,0)',('users',fresh.canonical(new_user),fresh.digest(new_user),8))
        report=fresh.repair_empty_private_memory_copy(self.root)
        self.assertEqual(report['repaired_roots'],2)
        self.assertEqual(json.loads(json_path.read_text())['users'],new_user)
        with sqlite3.connect(db_path) as db:
            raw,checksum,revision=db.execute('SELECT payload_json,checksum,revision FROM store_sections WHERE section_name=?',
                                            (fresh.PRIVATE_MEMORY_KEY,)).fetchone()
            self.assertEqual(json.loads(raw),{'schema':fresh.PRIVATE_MEMORY_SCHEMA,'records':{}})
            self.assertEqual(checksum,fresh.digest(json.loads(raw)))
            self.assertEqual(revision,9)
            self.assertEqual(db.execute("SELECT meta_value FROM store_meta WHERE meta_key='next_revision'").fetchone()[0],'20')
            self.assertEqual(db.execute("SELECT payload_json,revision FROM store_sections WHERE section_name='users'").fetchone(),
                             (fresh.canonical(new_user),8))
        self.assertEqual(fresh.repair_empty_private_memory_copy(self.root)['repaired_roots'],0)

    def test_narrow_root_repair_rejects_nonempty_without_touching_other_copy(self):
        directory=self.root/fresh.PRIVATE
        directory.mkdir(parents=True)
        json_path=directory/'companions.json'
        original=json.dumps({fresh.PRIVATE_MEMORY_KEY:{},'users':{'x':{'text':'NEW_CHAT'}}})
        json_path.write_text(original)
        profiles=directory/'persona_profiles'
        profiles.mkdir()
        (profiles/'x.json').write_text(json.dumps({fresh.PRIVATE_MEMORY_KEY:{'schema':fresh.PRIVATE_MEMORY_SCHEMA,
            'records':{'new':{'content':{'text':'NEW_MEMORY'}}}}}))
        with self.assertRaisesRegex(ValueError,'repair_requires_exact_empty_private_memory_root'):
            fresh.repair_empty_private_memory_copy(self.root)
        self.assertEqual(json_path.read_text(),original)

    def test_memory_native_and_search_budget_untouched(self):
        native=self.root/'plugin_data/astrbot_plugin_memory_companion/memory_companion.db'
        native.parent.mkdir(parents=True)
        native.write_bytes(b'APPROVED_PROFILE_AND_NATIVE_SCHEMA')
        budget=self.root/'plugin_data/phase3_search/budget.sqlite3'
        budget.parent.mkdir(parents=True)
        budget.write_bytes(b'BUDGET_MUST_NOT_RESET')
        fresh.clean_copy(self.root)
        self.assertEqual(native.read_bytes(),b'APPROVED_PROFILE_AND_NATIVE_SCHEMA')
        self.assertEqual(budget.read_bytes(),b'BUDGET_MUST_NOT_RESET')

    def test_report_does_not_contain_chat_or_credentials(self):
        self.guard()
        self.runtime()
        report=fresh.clean_copy(self.root)
        self.assertNotIn('UNIQUE_OLD_TRANSCRIPT',json.dumps(report))
        self.assertNotIn('scope',report)

    def test_relationship_accumulation_reset_without_altering_permission_or_budget(self):
        source={'relationship_score':80,'relationship_daily_totals':{'today':12},
                'relationship_ledger':[{'text':'OLD_TEXT'}], 'relationship_role':'owner',
                'relationship_mode':'normal','relationship_score_schema_version':2,
                'sent_today':4,'proactive_daily_limit':5,'unified_profile_capabilities':{'proactive_private_enabled':False},
                'unified_profile_capability_audit':[{'actor_id':'admin','reason_code':'user_disable','changed':{'proactive_private_enabled':{'to':False}},'note':'OLD_TEXT'}]}
        actual=fresh.clean_private_user(source)
        self.assertEqual(actual['relationship_score'],0)
        self.assertEqual(actual['relationship_daily_totals'],{'day':'','positive':0,'negative':0})
        self.assertEqual(actual['relationship_role'],'owner')
        self.assertEqual(actual['sent_today'],4)
        self.assertEqual(actual['unified_profile_capability_audit'][0]['changed'],{'proactive_private_enabled':{'to':False}})
        self.assertNotIn('OLD_TEXT',json.dumps(actual))

    def test_fixed_private_native_default_normalization_and_sqlite_loader(self):
        root=Path(__file__).parents[1]/'.runtime/upstream/private_companion'
        if not root.exists():
            self.skipTest('fixed upstream checkout unavailable')
        pkg=types.ModuleType('fresh_native_private')
        pkg.__path__=[str(root)]
        sys.modules[pkg.__name__]=pkg
        const=importlib.import_module(pkg.__name__+'.constants')
        helpers=importlib.import_module(pkg.__name__+'.helpers')
        person=importlib.import_module(pkg.__name__+'.person_context_contract')
        capability=importlib.import_module(pkg.__name__+'.unified_profile_service')
        backend=importlib.import_module(pkg.__name__+'.storage.sqlite_backend')
        # Execute the unchanged, pinned defaults method without importing the
        # unrelated framework startup code or providing a fake normalizer.
        tree=ast.parse((root/'core_store.py').read_text())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='CoreStoreMixin')
        fn=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_ensure_store_defaults')
        fn.decorator_list=[]
        namespace={'Any':object,'DATA_VERSION':const.DATA_VERSION,'time':time,'_safe_int':helpers._safe_int,
                   'ensure_person_store':person.ensure_person_store,
                   'migrate_legacy_capabilities':capability.migrate_legacy_capabilities,
                   'repair_default_closed_capabilities':capability.repair_default_closed_capabilities,
                   'DEFAULT_CLOSED_REPAIR_OPERATION_ID':capability.DEFAULT_CLOSED_REPAIR_OPERATION_ID}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),str(root/'core_store.py'),'exec'),namespace)
        normalize=namespace['_ensure_store_defaults']
        fixture={'version':1,'users':{'u':{**const._DEFAULT_USER_TEMPLATE,'umo':'fixture:FriendMessage:u',
                         'last_user_message':'OLD_TEXT','companion_memory':{'fact':'OLD_TEXT'}}}}
        capability.ensure_new_profile_capabilities(fixture['users']['u'],proactive_private_enabled=False)
        fixture=normalize(fixture)
        clean=fresh.clean_private_payload(fixture)
        normalized=normalize(clean)
        self.assertNotIn('OLD_TEXT',json.dumps(normalized))
        self.assertFalse(normalized['users']['u']['proactive_private_enabled'])
        self.assertEqual(normalized['users']['u']['umo'],'fixture:FriendMessage:u')
        path=self.root/fresh.PRIVATE/'persona_profiles/default.db'
        path.parent.mkdir(parents=True)
        native=backend.SqliteStoreBackend(path,normalize,lambda:normalize({'users':{}}))
        native.save_store(fixture)
        fresh.clean_private_sqlite(path)
        reloaded=native.load_store()
        self.assertNotIn('OLD_TEXT',json.dumps(reloaded))
        self.assertFalse(reloaded['users']['u']['proactive_private_enabled'])


if __name__ == '__main__':
    unittest.main()
