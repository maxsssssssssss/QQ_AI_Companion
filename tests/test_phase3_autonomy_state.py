"""Synthetic durable autonomy accounting, research lifecycle and private audit."""
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from integrations.phase3_runtime.autonomy_state import AutonomyState


def stamp(value):
    return datetime.fromisoformat(value+'+08:00').timestamp()


class AutonomyStateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.path=self.root/'existing-gate.sqlite'
        self.now=stamp('2026-10-28T09:00:00');self.db=sqlite3.connect(self.path)
        self.addCleanup(lambda:self.db.close())
        self.db.execute('CREATE TABLE proactive_attempts(candidate TEXT PRIMARY KEY,state TEXT)')
        self.db.execute("INSERT INTO proactive_attempts VALUES ('previous-send','sent')")
        self.db.execute('CREATE TABLE web_daily_attempts(day TEXT,attempts INTEGER)')
        self.db.execute("INSERT INTO web_daily_attempts VALUES ('2026-10-28',60)")
        self.db.commit();self.store=self.open()

    def open(self,**kwargs):
        return AutonomyState(self.db,clock=lambda:self.now,audit_dir=self.root/'audit',**kwargs)

    def begin(self,key='op',evaluation=None,**kwargs):
        return self.store.begin_opportunity(key,evaluation_id=evaluation or key,candidate_id='candidate-'+key,
            session='test:FriendMessage:10001',deadline=kwargs.pop('deadline',self.now+7200),
            input_version=kwargs.pop('input_version','v1'),prompt_version='synthetic-v1',**kwargs)

    def save(self,key='finding',**kwargs):
        return self.store.save_finding(key,session='test:FriendMessage:10001',
            question='Synthetic research question',content=kwargs.pop('content','Synthetic unshared finding'),
            kind=kwargs.pop('kind','hypothesis'),sources=kwargs.pop('sources',[]),
            expires_at=kwargs.pop('expires_at',self.now+86400),
            opportunity_id=kwargs.pop('opportunity_id','op'),**kwargs)

    def test_uses_existing_ledger_and_does_not_reset_old_passive_or_proactive_counts(self):
        self.begin();self.store.reserve_model_request('op')
        self.store=self.open()
        self.assertEqual(self.db.execute('SELECT * FROM proactive_attempts').fetchall(),[('previous-send','sent')])
        self.assertEqual(self.db.execute('SELECT attempts FROM web_daily_attempts').fetchone()[0],60)
        self.assertEqual(self.store.summary()['calls']['model'],{'interrupted':1})

    def test_small_configured_pocket_accepts_default_read_page(self):
        self.store=self.open(pocket_limit=1);self.begin();self.save()
        self.assertEqual(len(self.store.list_pocket('test:FriendMessage:10001')),1)
        self.assertEqual(self.save('second')['reason'],'pocket_capacity')
        self.assertEqual(len(self.store.list_pocket('test:FriendMessage:10001',limit=8)),1)

    def test_144_actual_requests_consumed_before_io_and_failures_still_count(self):
        self.begin()
        for i in range(144):
            call=self.store.reserve_model_request('op',request_id='call-'+str(i))
            self.assertTrue(call['allowed'])
            self.store.finish_call(call['id'],('returned','failed','timeout','empty')[i%4],
                usage={'prompt_tokens':2,'completion_tokens':3},elapsed=.1)
        self.assertEqual(self.store.reserve_model_request('op')['reason'],'model_request_budget')
        self.assertEqual(self.store.summary()['usage'],{'prompt_tokens':288,'completion_tokens':432})
        self.assertEqual(sum(self.store.summary()['calls']['model'].values()),144)

    def test_tool_budget_three_combined_and_reconsideration_does_not_reset(self):
        self.begin()
        for kind in ('search','fetch','search'):
            call=self.store.reserve_tool_attempt('op',kind)
            self.assertTrue(call['allowed']);self.store.finish_call(call['id'],'failed')
        self.store.update_decision('op',raw_control='[defer:5]',parsed={'decision':'defer'},
            effective_state='deferred',reason='model_decision')
        self.begin('op',evaluation='op:reconsider',input_version='v2')
        self.assertEqual(self.store.evaluation('op:reconsider')['input_version'],'v2')
        self.assertEqual(self.store.reserve_tool_attempt('op:reconsider','fetch')['reason'],'opportunity_tool_budget')
        self.assertTrue(self.store.reserve_model_request('op:reconsider')['allowed'])

    def test_daily288_tools_independent_of_passive60_and_resets_only_by_calendar_day(self):
        for i in range(96):
            key='op-'+str(i);self.begin(key)
            for j in range(3):
                call=self.store.reserve_tool_attempt(key,('search','fetch')[j%2])
                self.assertTrue(call['allowed']);self.store.finish_call(call['id'],'rejected')
        self.begin('extra')
        self.assertEqual(self.store.reserve_tool_attempt('extra','search')['reason'],'proactive_tool_daily_budget')
        self.store=self.open();self.begin('restart')
        self.assertEqual(self.store.reserve_tool_attempt('restart','search')['reason'],'proactive_tool_daily_budget')
        self.now=stamp('2026-10-29T09:00:00');self.begin('next-day')
        self.assertTrue(self.store.reserve_tool_attempt('next-day','fetch')['allowed'])
        self.assertEqual(self.db.execute('SELECT attempts FROM web_daily_attempts').fetchone()[0],60)

    def test_idempotent_call_and_evaluation_are_not_reexecuted(self):
        self.assertTrue(self.begin()['allowed'])
        self.assertEqual(self.begin()['reason'],'evaluation_already_attempted')
        self.assertTrue(self.store.reserve_model_request('op','id')['allowed'])
        self.assertEqual(self.store.reserve_model_request('op','id')['reason'],'call_already_attempted')
        self.assertTrue(self.store.finish_call('id','returned'))
        self.assertFalse(self.store.finish_call('id','failed'))
        self.assertEqual(self.store.summary()['calls']['model'],{'returned':1})

    def test_deadline_bounded_at_ten_minutes_and_candidate_expiry_not_extended(self):
        self.assertEqual(self.begin()['deadline'],self.now+600)
        self.now+=600
        self.assertEqual(self.store.reserve_model_request('op')['reason'],'exploration_deadline')
        before=self.now
        self.assertTrue(self.begin('short',deadline=self.now+30)['allowed'])
        self.now+=31
        self.assertEqual(self.begin('short',evaluation='short:again',deadline=self.now+7200)['reason'],'candidate_expired')
        self.assertEqual(self.db.execute("SELECT deadline FROM autonomy_opportunities WHERE id='short'").fetchone()[0],before+30)

    def test_later_defer_can_reconsider_without_refreshing_old_tool_time_budget(self):
        self.begin();self.store.finalize_evaluation('op','deferred',reason='model_decision')
        self.now+=1200;self.assertTrue(self.begin('op',evaluation='later')['allowed'])
        self.assertTrue(self.store.reserve_model_request('later')['allowed'])
        self.assertEqual(self.store.reserve_tool_attempt('later','search')['reason'],'opportunity_exploration_deadline')
        self.assertTrue(self.begin('new-native-op')['allowed'])
        self.assertTrue(self.store.reserve_tool_attempt('new-native-op','search')['allowed'])

    def test_scope_collision_rejected_without_partial_mutation(self):
        self.begin()
        with self.assertRaises(ValueError):
            self.store.begin_opportunity('op',evaluation_id='hijack',candidate_id='other',
                session='test:FriendMessage:20002',deadline=self.now+10,input_version='v')
        self.assertIsNone(self.store.evaluation('hijack'))
        self.assertFalse(self.db.in_transaction)
        self.assertTrue(self.store.reserve_model_request('op')['allowed'])

    def test_reopen_marks_running_interrupted_never_retries_or_resumes_send(self):
        self.begin();self.save();self.store.select_finding('finding','op')
        self.store.reserve_tool_attempt('op','fetch',request_id='running')
        self.store.reserve_supplement('exchange',parent_attempt='parent',parent_kind='passive',attempt_id='supp')
        self.store=self.open()
        self.assertEqual(self.store.evaluation('op')['effective_state'],'interrupted')
        self.assertEqual(self.store.reserve_tool_attempt('op','fetch')['reason'],'evaluation_not_running')
        self.assertEqual(self.store.summary()['calls']['fetch'],{'interrupted':1})
        self.assertEqual(self.db.execute('SELECT selected_eval FROM autonomy_pocket').fetchone()[0],'')
        self.assertEqual(self.db.execute('SELECT state FROM autonomy_supplements').fetchone()[0],'unknown')
        self.assertTrue(self.store.list_pocket('test:FriendMessage:10001')[0]['requires_reevaluation'])

    def test_raw_defer_and_effective_expiry_retained_separately_no_visible_body_in_ledger(self):
        self.begin()
        self.store.update_decision('op',raw_control='[PHASE3_PROACTIVE:defer:45]',
            parsed={'decision':'defer','delay_minutes':45,'text':'private visible text'},
            effective_state='defer_expired',reason='candidate_lifetime',topic='synthetic topic')
        row=self.db.execute('SELECT raw_control,parsed,effective_state,reason FROM autonomy_evaluations').fetchone()
        self.assertEqual(row[0],'[PHASE3_PROACTIVE:defer:45]')
        self.assertEqual(json.loads(row[1]),{'decision':'defer','delay_minutes':45})
        self.assertEqual(row[2:],('defer_expired','candidate_lifetime'))
        self.assertEqual(self.store.reserve_model_request('op')['reason'],'evaluation_not_running')

    def test_research_cross_day_updates_sources_and_keeps_unshared_state(self):
        self.begin();self.save(kind='external_fact',sources=[{
            'url':'https://example.com/research','title':'Synthetic source','observed_at':self.now,'verified':True}])
        self.now+=86400-1
        self.store=self.open();items=self.store.list_pocket('test:FriendMessage:10001')
        self.assertEqual(len(items),1);self.assertTrue(items[0]['requires_reevaluation'])
        self.begin('new-op');self.save(content='Progress from second opportunity',opportunity_id='new-op',
            expected_revision=1,progress='One unresolved question',state='researching')
        current=self.store.list_pocket('test:FriendMessage:10001')[0]
        self.assertEqual(current['revision'],2);self.assertEqual(current['state'],'researching')
        self.assertEqual(current['progress'],'One unresolved question')
        self.assertEqual(self.store.list_pocket('test:FriendMessage:20002'),[])

    def test_pocket_stale_revision_and_terminal_resurrection_rejected(self):
        self.begin();self.save()
        with self.assertRaises(ValueError):self.save(expected_revision=0)
        self.assertTrue(self.store.discard_finding('finding',session='test:FriendMessage:10001'))
        with self.assertRaises(ValueError):self.save(expected_revision=1)
        self.assertEqual(self.store.list_pocket('test:FriendMessage:10001'),[])

    def test_pocket_expiration_and_capacity_do_not_evict_live_findings(self):
        self.store=self.open(pocket_limit=2);self.begin()
        self.save('a',expires_at=self.now+1);self.save('b')
        self.assertEqual(self.save('c'),{'saved':False,'reason':'pocket_capacity'})
        self.now+=2
        self.assertTrue(self.save('c')['saved'])
        self.assertEqual({r['id'] for r in self.store.list_pocket('test:FriendMessage:10001',limit=2)},{'b','c'})
        self.assertEqual(self.db.execute("SELECT state FROM autonomy_pocket WHERE id='a'").fetchone()[0],'expired')

    def test_partial_failed_unknown_not_shared_and_revision_invalidates_selection(self):
        self.begin();self.save(content='Full finding content')
        self.assertFalse(self.store.mark_shared('finding',attempt_id='a',confirmed_text='Full finding content',evaluation_id='op'))
        self.assertTrue(self.store.select_finding('finding','op'))
        for text in ('','Full finding'):
            self.assertFalse(self.store.mark_shared('finding',attempt_id='a',confirmed_text=text,evaluation_id='op'))
        self.save(content='New finding version',expected_revision=1)
        self.assertFalse(self.store.mark_shared('finding',attempt_id='a',confirmed_text='New finding version',evaluation_id='op'))
        self.store.select_finding('finding','op')
        self.assertTrue(self.store.mark_shared('finding',attempt_id='a',confirmed_text='New finding version',evaluation_id='op'))
        self.assertEqual(self.store.list_pocket('test:FriendMessage:10001'),[])

    def test_whole_guard_plan_can_confirm_paraphrased_selection_but_empty_never_can(self):
        self.begin();self.save();self.store.select_finding('finding','op')
        self.assertFalse(self.store.mark_shared('finding',attempt_id='a',confirmed_text='',evaluation_id='op',whole_plan_confirmed=True))
        self.assertTrue(self.store.mark_shared('finding',attempt_id='a',confirmed_text='Actual paraphrased visible output',evaluation_id='op',whole_plan_confirmed=True))

    def test_pocket_sources_and_scope_have_validation_not_user_fact_writes(self):
        self.begin()
        for source in ('file:///etc/passwd','https://token:password@example.com/','https://example.com/#secret'):
            with self.subTest(source=source),self.assertRaises(ValueError):
                self.save(kind='external_fact',sources=[{'url':source}])
        with self.assertRaises(ValueError):self.save(kind='external_fact',state='ready')
        with self.assertRaises(ValueError):self.save(opportunity_id='missing')
        with self.assertRaises(ValueError):self.save(kind='user_fact')
        names={row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertFalse(any('user_facts' in name or 'preferences' in name for name in names))

    def test_unverified_external_assertion_is_retained_without_becoming_fact_or_shared(self):
        self.begin();self.save(kind='external_fact')
        item=self.store.list_pocket('test:FriendMessage:10001')[0]
        self.assertEqual((item['kind'],item['state'],item['evidence_status']),
                         ('external_fact','unshared','unverified'))
        self.assertTrue(self.store.select_finding('finding','op'))
        self.assertFalse(self.store.mark_shared('finding',attempt_id='a',confirmed_text=item['content'],
            evaluation_id='op',whole_plan_confirmed=True))
        self.store=self.open()
        self.assertEqual(self.store.list_pocket('test:FriendMessage:10001')[0]['evidence_status'],'unverified')
        self.save(kind='external_fact',sources=[{'url':'https://example.com/observed','verification':'retrieved_not_fact_verified'}],
                  expected_revision=1,state='ready')
        item=self.store.list_pocket('test:FriendMessage:10001')[0]
        self.assertEqual(item['evidence_status'],'retrieved_not_fact_verified')

    def test_tool_sources_only_successful_structured_metadata_are_returned(self):
        self.begin()
        for index,state in enumerate(('succeeded','failed','malformed')):
            call=self.store.reserve_tool_attempt('op','search')
            self.store.finish_call(call['id'],state,metadata={'sources':[{'url':'https://example.com/'+str(index),'title':'Source'}]})
        self.assertEqual(self.store.tool_sources('op'),[{'url':'https://example.com/0','title':'Source'}])

    def test_one_supplement_per_exchange_and_no_supplement_of_supplement(self):
        result=self.store.reserve_supplement('exchange',parent_attempt='chat-parent',parent_kind='passive',attempt_id='supp')
        self.assertTrue(result['allowed'])
        self.store.finish_supplement('supp','failed')
        self.assertEqual(self.store.reserve_supplement('exchange',parent_attempt='chat-parent',parent_kind='passive',attempt_id='retry')['reason'],'supplement_already_attempted')
        self.assertEqual(self.store.reserve_supplement('new',parent_attempt='supp',parent_kind='proactive',attempt_id='supp2')['reason'],'supplement_cannot_parent_supplement')
        self.assertEqual(self.store.reserve_supplement('new',parent_attempt='parent2',parent_kind='supplement',attempt_id='supp2')['reason'],'supplement_cannot_parent_supplement')
        self.assertTrue(self.store.reserve_supplement('genuinely-new-exchange',parent_attempt='parent3',parent_kind='interest',attempt_id='supp3')['allowed'])

    def test_supplement_eligibility_does_not_consume_and_failed_reservation_stays_atomic(self):
        for _ in range(2):
            self.assertTrue(self.store.supplement_eligibility('exchange',parent_attempt='parent',parent_kind='passive',attempt_id='supp')['allowed'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM autonomy_supplements').fetchone()[0],0)
        self.assertTrue(self.store.reserve_supplement('exchange',parent_attempt='parent',parent_kind='passive',attempt_id='supp')['allowed'])
        self.assertFalse(self.store.reserve_supplement('exchange',parent_attempt='parent',parent_kind='passive',attempt_id='supp2')['allowed'])
        self.assertFalse(self.db.in_transaction)

    def test_supplement_parent_identity_cannot_be_retried_under_new_exchange(self):
        for index,status in enumerate(('sent','failed','unknown','cancelled')):
            parent='interest-'+str(index);attempt='supp-'+str(index)
            self.assertTrue(self.store.reserve_supplement('candidate-'+str(index),
                parent_attempt=parent,parent_kind='interest',attempt_id=attempt)['allowed'])
            self.store.finish_supplement(attempt,status)
            retry=dict(parent_attempt=parent,parent_kind='proactive',attempt_id=attempt+'-retry')
            self.assertEqual(self.store.supplement_eligibility('exchange:'+parent,**retry)['reason'],
                'supplement_already_attempted')
            self.assertFalse(self.store.reserve_supplement('exchange:'+parent,**retry)['allowed'])
        self.store=self.open()
        self.assertFalse(self.store.reserve_supplement('after-restart',parent_attempt='interest-2',
            parent_kind='interest',attempt_id='after-restart-attempt')['allowed'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM autonomy_supplements').fetchone()[0],4)
        self.assertFalse(self.db.in_transaction)

    def test_delivery_finalization_preserves_model_decision_and_terminal_outcomes(self):
        for status in ('sent','failed','unknown','cancelled'):
            self.begin(status)
            self.assertFalse(self.store.update_delivery(status,'premature',status))
            self.store.update_decision(status,raw_control='[PHASE3_PROACTIVE:send]',
                parsed={'decision':'send'},effective_state='ready_to_send',reason='model_decision')
            self.assertTrue(self.store.update_delivery(status,'guard-'+status,status,'guard_result'))
            current=self.store.evaluation(status)
            self.assertEqual(current['effective_state'],status)
            self.assertEqual(current['delivery_attempt'],'guard-'+status)
            row=self.db.execute('SELECT raw_control,parsed FROM autonomy_evaluations WHERE id=?',(status,)).fetchone()
            self.assertEqual(row,('[PHASE3_PROACTIVE:send]',json.dumps({'decision':'send'},ensure_ascii=False,separators=(',',':'))))
            self.assertFalse(self.store.update_delivery(status,'different','sent','cannot_relabel_unknown'))
        self.begin('skipped')
        self.store.update_decision('skipped',raw_control='[PHASE3_PROACTIVE:skip]',parsed={'decision':'skip'},
            effective_state='skipped',reason='model_decision')
        self.assertFalse(self.store.update_delivery('skipped','wrong','sent'))
        with self.assertRaises(ValueError):self.store.update_delivery('skipped','wrong','interest')

    def test_private_audit_keeps_actual_visible_payload_but_removes_keys_and_reasoning(self):
        self.begin();payload={'messages':[{'role':'user','content':'Synthetic private prompt'}],
            'max_tokens':8192,'thinking':{'type':'disabled'},'api_key':'synthetic-key',
            'reasoning_content':'not-visible','nested':{'Authorization':'Bearer synthetic'}}
        audit=self.store.write_audit('op','request',payload)
        self.assertTrue(audit['complete'])
        saved=json.loads((self.root/'audit'/audit['ref']).read_text())
        self.assertEqual(saved['messages'],payload['messages']);self.assertEqual(saved['max_tokens'],8192)
        self.assertEqual(saved['api_key'],'[REDACTED]');self.assertEqual(saved['reasoning_content'],'[REDACTED]')
        self.assertEqual(saved['nested']['Authorization'],'[REDACTED]')
        self.assertNotIn('Synthetic private prompt',json.dumps(self.store.summary()))
        if os.name=='posix':
            self.assertEqual((self.root/'audit'/audit['ref']).stat().st_mode&0o777,0o600)
            self.assertEqual((self.root/'audit').stat().st_mode&0o777,0o700)

    def test_audit_retention_and_capacity_evictions_are_explicit(self):
        self.store=self.open(audit_capacity_bytes=4096,audit_entry_bytes=2048)
        self.begin();refs=[]
        for i in range(4):
            refs.append(self.store.write_audit('op','output',{'content':'x'*1400,'index':i}))
            self.now+=1
        retained=list((self.root/'audit').glob('*.json'))
        self.assertLessEqual(sum(p.stat().st_size for p in retained),4096)
        self.assertEqual(self.db.execute("SELECT count(*) FROM autonomy_audits WHERE state='capacity_evicted'").fetchone()[0],2)
        self.now+=14*86400+1;self.store.prune_audits()
        self.assertEqual(list((self.root/'audit').glob('*.json')),[])
        self.assertEqual(self.db.execute("SELECT count(*) FROM autonomy_audits WHERE state='retention_expired'").fetchone()[0],2)

    def test_oversized_audit_is_not_truncated_and_claimed_complete(self):
        self.store=self.open(audit_capacity_bytes=4096,audit_entry_bytes=1024);self.begin()
        result=self.store.write_audit('op','request',{'content':'x'*1500})
        self.assertFalse(result['complete']);self.assertEqual(result['reason'],'audit_entry_capacity')
        self.assertEqual(list((self.root/'audit').glob('*.json')),[])

    def test_private_audit_symlink_rejected_without_touching_target(self):
        self.begin();result=self.store.write_audit('op','output',{'content':'synthetic'})
        path=self.root/'audit'/result['ref'];path.unlink()
        target=self.root/'important';target.write_text('must remain')
        try:path.symlink_to(target)
        except OSError:self.skipTest('symlinks unavailable')
        self.now+=15*86400
        with self.assertRaises(ValueError):self.store.prune_audits()
        self.assertEqual(target.read_text(),'must remain')

    def test_uncommitted_caller_work_is_not_accidentally_committed(self):
        self.begin();self.db.execute("INSERT INTO proactive_attempts VALUES ('uncommitted','pending')")
        with self.assertRaises(RuntimeError):self.store.reserve_model_request('op')
        self.db.rollback()
        self.assertIsNone(self.db.execute("SELECT * FROM proactive_attempts WHERE candidate='uncommitted'").fetchone())


if __name__=='__main__':unittest.main()
