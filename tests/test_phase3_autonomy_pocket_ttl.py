"""Research progress, draft TTL and source age use distinct synthetic clocks."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from integrations.phase3_runtime.autonomy_state import AutonomyState, FINDING_IDLE_SECONDS, FINDING_MAX_SECONDS


class AutonomyPocketTTLTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'existing-gate.sqlite3';self.db=sqlite3.connect(self.path)
        self.addCleanup(lambda:self.db.close());self.now=1800000000.0;self.created=self.now
        self.session='test:FriendMessage:10001';self.state=self.open();self.op=0;self.begin()

    def open(self,**kwargs):return AutonomyState(self.db,clock=lambda:self.now,**kwargs)
    def begin(self):
        self.op+=1;key='op-'+str(self.op)
        self.state.begin_opportunity(key,evaluation_id=key,candidate_id=key,session=self.session,
                                     deadline=self.now+600,input_version='v1')
        return key
    def save(self,**kwargs):
        item=self.item()
        return self.state.save_finding('research',session=self.session,question=kwargs.pop('question','Synthetic question'),
            content=kwargs.pop('content','Initial synthetic finding'),kind=kwargs.pop('kind','hypothesis'),
            sources=kwargs.pop('sources',[]),expires_at=kwargs.pop('expires_at',self.now+FINDING_IDLE_SECONDS),
            opportunity_id='op-'+str(self.op),expected_revision=item['revision'] if item else None,**kwargs)
    def item(self):
        rows=self.state.list_pocket(self.session)
        return rows[0] if rows else None
    def source(self,**kwargs):
        return {'url':'https://example.com/research','title':'Synthetic source','observed_at':self.created,
                'attempt_id':'source-call','content_sha256':'a'*64,'verification':'retrieved_not_fact_verified',**kwargs}

    def test_read_select_and_original_save_do_not_extend_draft(self):
        self.save();original=self.item();self.now+=2*86400;self.begin()
        self.assertTrue(self.state.select_finding('research','op-'+str(self.op)))
        self.assertEqual(self.item(),original)
        result=self.save(research_progress=True)
        self.assertEqual(result['reason'],'unchanged');self.assertFalse(result['ttl_extended'])
        self.assertEqual(self.item(),original)
        self.now=self.created+FINDING_IDLE_SECONDS
        self.assertIsNone(self.item())
        self.assertFalse(self.state.select_finding('research','op-'+str(self.op)))

    def test_whitespace_rewrite_state_and_title_only_cannot_renew(self):
        self.save(sources=[self.source()]);original=self.item();self.now+=86400;self.begin()
        result=self.save(content='Initial  synthetic finding\n',sources=[self.source(title='A changed label')],
                         state='researching',research_progress=True)
        current=self.item();self.assertFalse(result['ttl_extended'])
        self.assertEqual(current['expires_at'],original['expires_at'])
        self.assertEqual(current['last_progress_at'],self.created)
        self.assertEqual(current['updated'],self.now)  # rewritten draft is still observable

    def test_plain_rewrite_without_research_opt_in_does_not_renew(self):
        self.save();self.now+=2*86400;self.begin()
        result=self.save(content='A rewritten share draft, not new research')
        self.assertFalse(result['ttl_extended']);self.assertEqual(self.item()['expires_at'],self.created+3*86400)

    def test_real_progress_can_continue_across_days_but_source_stays_old(self):
        self.save(kind='external_fact',sources=[self.source()]);self.now+=2*86400;self.begin()
        result=self.save(kind='external_fact',content='New comparison and unresolved question',
            sources=[self.source()],progress='Compared two explanations; next inspect counterexample',
            state='researching',research_progress=True)
        self.assertTrue(result['ttl_extended']);current=self.item()
        self.assertEqual(current['created'],self.created);self.assertEqual(current['last_progress_at'],self.now)
        self.assertEqual(current['expires_at'],self.now+3*86400)
        self.now+=2*86400
        self.assertIsNotNone(self.item());self.assertEqual(self.item()['source_status'],{self.source()['url']:'aging'})
        self.assertEqual(self.item()['sources'][0]['observed_at'],self.created)
        self.assertFalse(self.state.source_is_current(self.item()['sources'][0]))
        self.assertTrue(self.state.source_is_usable(self.item()['sources'][0]))
        self.assertEqual(self.item()['source_observed_age_seconds'][self.source()['url']],4*86400)

    def test_receipt_reuse_cannot_change_observation_or_source_expiry(self):
        first=self.source(expires_at=self.created+3600)
        self.save(sources=[first]);self.now+=7200;self.begin()
        result=self.save(sources=[self.source(observed_at=self.now,expires_at=self.now+86400)],research_progress=True)
        current=self.item();self.assertEqual(result['reason'],'unchanged')
        self.assertEqual(current['sources'][0]['observed_at'],self.created)
        self.assertEqual(current['sources'][0]['expires_at'],self.created+3600)
        self.assertEqual(current['source_status'],{first['url']:'expired'})

    def test_new_receipt_refreshes_evidence_but_same_body_not_research_progress(self):
        self.save(sources=[self.source()]);self.now+=2*86400;self.begin()
        result=self.save(sources=[self.source(attempt_id='new-retrieval',observed_at=self.now)],research_progress=True)
        self.assertFalse(result['ttl_extended']);self.assertEqual(self.item()['last_progress_at'],self.created)
        self.now+=86400-1
        self.assertTrue(self.state.source_is_current(self.item()['sources'][0]))
        self.assertEqual(self.item()['expires_at'],self.created+3*86400)

    def test_new_source_body_is_progress_and_explicit_short_source_deadline_wins(self):
        self.save(sources=[self.source()]);self.now+=86400;self.begin()
        result=self.save(sources=[self.source(attempt_id='new',content_sha256='b'*64,
            observed_at=self.now,expires_at=self.now+300)],research_progress=True)
        self.assertTrue(result['ttl_extended']);self.now+=301
        self.assertEqual(self.item()['source_status'],{self.source()['url']:'expired'})
        self.assertGreater(self.item()['expires_at'],self.now)

    def test_restart_preserves_progress_and_expiry_never_reauthorizes_send(self):
        self.save();self.now+=2*86400;self.begin()
        self.save(content='Real new research result',research_progress=True);before=self.item()
        self.state.select_finding('research','op-'+str(self.op));self.db.close();self.db=sqlite3.connect(self.path)
        self.state=self.open();self.assertEqual(self.item(),before)
        self.assertEqual(self.db.execute('SELECT selected_eval FROM autonomy_pocket').fetchone()[0],'')
        self.now=before['expires_at'];self.state=self.open();self.assertIsNone(self.item())

    def test_continual_progress_has_absolute_bound_and_terminal_rows_are_bounded(self):
        self.state=self.open(pocket_limit=1);self.save()
        for day in range(2,30,2):
            self.now=self.created+day*86400;self.begin()
            self.save(content='Concrete synthetic progress '+str(day),research_progress=True)
            self.assertLessEqual(self.item()['expires_at'],self.created+FINDING_MAX_SECONDS)
        self.now=self.created+29*86400;self.begin()
        self.save(content='Last bounded step',research_progress=True)
        self.assertEqual(self.item()['expires_at'],self.created+FINDING_MAX_SECONDS)
        self.now=self.created+FINDING_MAX_SECONDS
        self.assertIsNone(self.item())
        self.assertEqual(self.db.execute("SELECT state FROM autonomy_pocket WHERE id='research'").fetchone()[0],'expired')
        for i in range(6):
            self.state.save_finding('new-'+str(i),session=self.session,question='Synthetic',content='Unshared',
                kind='hypothesis',sources=[],expires_at=self.now+1,opportunity_id='op-'+str(self.op))
            self.now+=2;self.state.prune_pocket()
        self.assertLessEqual(self.db.execute('SELECT count(*) FROM autonomy_pocket').fetchone()[0],3)

    def test_invalid_unknown_future_and_stale_source_states_are_distinct(self):
        for source,expected in [({},'unknown'),({'fetched_at':0},'unknown'),({'observed_at':True},'invalid'),
            ({'fetched_at':self.now+1},'future'),({'fetched_at':self.now-3*86400},'aging'),
            ({'fetched_at':self.now},'current')]:
            with self.subTest(source=source):self.assertEqual(self.state.source_freshness(source),expected)

    def test_aging_evidence_is_usable_but_explicit_expiry_or_revocation_is_not(self):
        old=self.source(observed_at=self.now-20*86400)
        self.assertEqual(self.state.source_freshness(old),'aging')
        self.assertTrue(self.state.source_is_usable(old))
        for extra in ({'expires_at':self.now},{'revoked':True}):
            self.assertFalse(self.state.source_is_usable({**old,**extra}))
        self.save(sources=[self.source(revoked=True)])
        self.now+=86400;self.begin()
        self.save(sources=[self.source(revoked=False)],research_progress=True)
        self.assertTrue(self.item()['sources'][0]['revoked'])
        self.assertEqual(self.item()['source_status'],{old['url']:'revoked'})

    def test_dropping_a_source_or_progress_text_does_not_count_as_new_progress(self):
        self.save(sources=[self.source()],progress='Pending question')
        self.now+=86400;self.begin()
        result=self.save(sources=[],progress='',research_progress=True)
        self.assertFalse(result['ttl_extended'])
        self.assertEqual(self.item()['last_progress_at'],self.created)

    def test_legacy_migration_does_not_use_recent_noop_updated_as_new_progress(self):
        self.save();self.db.execute('ALTER TABLE autonomy_pocket DROP COLUMN last_progress')
        self.db.execute('UPDATE autonomy_pocket SET updated=?,expires=?',(self.now+86400,self.now+40*86400));self.db.commit()
        self.now+=2*86400;self.state=self.open();item=self.item()
        self.assertEqual(item['last_progress_at'],self.created)
        self.assertEqual(item['expires_at'],self.created+3*86400)
        self.state=self.open();self.assertEqual(self.item(),item)

    def test_capacity_not_bypassed_by_noop_or_expired_resurrection(self):
        self.state=self.open(pocket_limit=1);self.save();self.now+=86400;self.begin()
        self.save(research_progress=True)
        denied=self.state.save_finding('new',session=self.session,question='Synthetic',content='Unshared',
            kind='hypothesis',sources=[],expires_at=self.now+86400,opportunity_id='op-'+str(self.op))
        self.assertEqual(denied,{'saved':False,'reason':'pocket_capacity'})
        self.now=self.created+3*86400;self.begin()
        with self.assertRaisesRegex(ValueError,'terminal_state'):
            self.save(content='Resurrection attempt',research_progress=True)


if __name__=='__main__':unittest.main()
