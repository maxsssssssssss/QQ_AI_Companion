import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
s=importlib.util.spec_from_file_location('qq_audit',ROOT/'scripts/phase3-qq-audit.py')
audit=importlib.util.module_from_spec(s);s.loader.exec_module(audit)


class AuditTests(unittest.TestCase):
    def test_native_publication_exports_only_verified_shape_not_private_receipts(self):
        raw=json.dumps({'profile_recall':{'publication_status':'published',
            'native_published':True,'native_snapshot_present':True,'statistics_deferred':False,
            'native_ref_count':4,'native_context_chars':4100,'snapshot_id':'PRIVATE-SNAPSHOT',
            'continuity_snapshot':{'private_text':'PRIVATE-BODY'},'native_body':'PRIVATE-BODY'}})
        row=audit.diagnostic_projection(raw)['profile_recall']
        self.assertEqual(row['publication_status'],'published')
        self.assertTrue(row['native_published'] and row['native_snapshot_present'])
        self.assertEqual((row['native_ref_count'],row['native_context_chars']),(4,4100))
        self.assertNotIn('PRIVATE',json.dumps(row))
        bad=audit.diagnostic_projection(json.dumps({'profile_recall':{
            'publication_status':'PRIVATE-ERROR','native_published':'SECRET'}}))
        self.assertEqual(bad['profile_recall']['publication_status'],'unavailable')
        self.assertNotIn('native_published',bad['profile_recall'])

    def test_turn_diagnostics_export_fixed_shapes_only(self):
        raw=json.dumps({'profile_recall':{'at':'2026-10-23T10:00:00+08:00','status':'found',
            'reason':'SECRET','text':'SECRET','elapsed_ms':25,'memory_refs':[{'id':'PRIVATE-ID',
            'version':'PRIVATE-VERSION','chars':452,'text':'SECRET'}]},'secret_stage':{'token':'SECRET'}})
        result=audit.diagnostic_projection(raw)
        self.assertEqual(result['profile_recall']['status'],'found')
        self.assertEqual(result['profile_recall']['memory_refs'][0]['chars'],452)
        for text in ('SECRET','PRIVATE-ID','PRIVATE-VERSION','secret_stage'):
            self.assertNotIn(text,json.dumps(result))

    def test_empty_recovery_usage_is_separate_and_never_exports_turn_identity(self):
        from integrations.phase1_guard.store import GuardStore
        from integrations.phase1_guard import recovery
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'guard.sqlite3';store=GuardStore(path);store.enable_phase3()
            store.claim('fixture','fixture:FriendMessage:10001','m1','10001','synthetic',[])
            store.create_turn('private-turn','fixture','fixture:FriendMessage:10001',['m1'])
            recovery.initialize(store.db);recovery.claim(store.db,'private-turn')
            recovery.finish(store.db,'private-turn','recovered','nonempty',SimpleNamespace(prompt_tokens=17,completion_tokens=8))
            result=audit.recovery_evidence(path,'2020-01-01T00:00:00+00:00')
            self.assertEqual((result['attempts'],result['input_tokens'],result['output_tokens']),(1,17,8))
            self.assertNotIn('private-turn',json.dumps(result));self.assertNotIn('synthetic',json.dumps(result))
            store.close()

    def test_component_projection_never_exports_payload(self):
        text=json.dumps([{'type':'image','data':{'url':'https://secret.invalid/token'}},
                         {'type':'PRIVATE-ID','text':'private'}])
        self.assertEqual(audit.chain_types(text),{'image':1,'other':1})
        self.assertNotIn('secret',json.dumps(audit.chain_types(text)))

    def test_provider_cost_and_since_excludes_old_calls(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'test.db'
            db=sqlite3.connect(path)
            db.execute('CREATE TABLE provider_stats(start_time REAL,token_input_other INTEGER,token_input_cached INTEGER,token_output INTEGER)')
            db.executemany('INSERT INTO provider_stats VALUES (?,?,?,?)',[(0,999999,999999,999999),(1792629000,100,20,10)])
            db.commit();db.close();before=path.read_bytes()
            result=audit.model_evidence(path,'2026-10-22T00:19:13+00:00')
            self.assertEqual(result['internal_agent_recorded_runs'],1)
            self.assertIsNone(result['http_request_count'])
            self.assertEqual(result['peak_uncached_estimate_cny'],.00032)
            self.assertEqual(before,path.read_bytes())

    def test_timezone_required(self):
        with self.assertRaisesRegex(ValueError,'timezone_required'):
            audit.report(Path('/tmp/not-read'),'2026-10-22T00:00:00')

    def test_bubble_projection_includes_unattempted_parts_without_content(self):
        from integrations.phase1_guard.store import GuardStore
        with tempfile.TemporaryDirectory() as folder:
            store=GuardStore(Path(folder)/'guard.sqlite3')
            store.enable_phase3();store.enable_bubbles()
            store.claim('fixture','fixture:FriendMessage:10001','m1','10001','synthetic-input',[])
            store.create_turn('private-turn','fixture','fixture:FriendMessage:10001',['m1'])
            store.plan_bubbles('private-turn','normal',['SECRET_ONE','SECRET_TWO'],['private-id-one','private-id-two'],'a'*64)
            store.mark_bubble_transport('private-id-one')
            store.finish_bubble('private-turn','normal',0,'sent')
            store.cancel_remaining_bubbles('private-turn','normal','interrupted_during_interval')
            result=audit.bubble_evidence(store.db,'private-turn')
            self.assertEqual([(r['state'],r['adapter_attempts']) for r in result],[('sent',1),('cancelled',0)])
            serialized=json.dumps(result)
            for secret in ('SECRET_ONE','SECRET_TWO','private-id','private-turn','10001'):
                self.assertNotIn(secret,serialized)
            store.close()

    def test_search_timing_correlates_exact_turn_and_does_not_claim_http_success(self):
        with tempfile.TemporaryDirectory() as folder:
            web,guard=Path(folder)/'web.db',Path(folder)/'guard.db'
            with sqlite3.connect(web) as db:
                db.execute('CREATE TABLE web_tool_attempts(id,turn_id,ordinal,kind,progress_delivery_id,progress_state,started_at,finished_at,state)')
                db.execute('INSERT INTO web_tool_attempts VALUES (?,?,?,?,?,?,?,?,?)',
                    ('private-attempt','private-turn',1,'search','private-progress','sent','2026-10-22T10:00:02+00:00','2026-10-22T10:00:03+00:00','returned'))
            with sqlite3.connect(guard) as db:
                db.execute('CREATE TABLE deliveries(delivery_id,turn_id,kind,state,receipt_source,finished_at,created_at,part_index)')
                db.executemany('INSERT INTO deliveries VALUES (?,?,?,?,?,?,?,?)',[
                    ('private-progress','private-turn','progress','sent','AstrBot4.29.1_event_send_ack','2026-10-22T10:00:01+00:00','2026-10-22T10:00:00+00:00',0),
                    ('private-final','private-turn','final','sent','AstrBot4.29.1_event_send_ack','2026-10-22T10:00:05+00:00','2026-10-22T10:00:04+00:00',0)])
            before=(web.read_bytes(),guard.read_bytes())
            row=audit.search_evidence(web,guard,'2026-10-22T10:00:00+00:00')[0]
            self.assertTrue(row['progress_before_tool']);self.assertTrue(row['all_finals_created_after_tool'])
            self.assertTrue(row['returned_is_not_http_success']);self.assertEqual(row['final_sent_parts'],1)
            self.assertNotIn('private-',json.dumps(row));self.assertEqual(before,(web.read_bytes(),guard.read_bytes()))
            with sqlite3.connect(guard) as db:db.execute("UPDATE deliveries SET state='unknown' WHERE kind='progress'")
            self.assertFalse(audit.search_evidence(web,guard,'2026-10-22T10:00:00+00:00')[0]['progress_before_tool'])
            with sqlite3.connect(guard) as db:
                db.execute("UPDATE deliveries SET state='sent',turn_id='other' WHERE kind='progress'")
                db.execute("DELETE FROM deliveries WHERE kind='final'")
            row=audit.search_evidence(web,guard,'2026-10-22T10:00:00+00:00')[0]
            self.assertFalse(row['progress_before_tool']);self.assertFalse(row['all_finals_created_after_tool'])


if __name__=='__main__':unittest.main()
