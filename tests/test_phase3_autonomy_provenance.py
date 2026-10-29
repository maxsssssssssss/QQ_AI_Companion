"""Provenance proves an actual retrieval, never that model prose is true."""
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace as NS
import unittest

from integrations.phase3_runtime.autonomy import AutonomousProactiveBridge
from integrations.phase3_runtime.autonomy_state import AutonomyState


class DecisionProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=sqlite3.connect(Path(self.tmp.name)/'active.db');self.addCleanup(self.db.close)
        self.now=100000.0
        self.state=AutonomyState(self.db,clock=lambda:self.now)
        self.scope={'session':'test:FriendMessage:10001','opportunity_id':'current'}
        self.bridge=NS(state=self.state)
        self.begin('current')

    def begin(self,name,session=None):
        self.state.begin_opportunity(name,evaluation_id=name,candidate_id=name,
            session=session or self.scope['session'],deadline=self.now+600,input_version='0')

    def receipt(self,name='current',url='https://science.nasa.gov/titan/',state='succeeded'):
        call=self.state.reserve_tool_attempt(name,'search')
        source={'url':url,'title':'Synthetic source','fetched_at':self.now,
            'attempt_id':call['id'],'content_sha256':'a'*64,'verification':'retrieved_not_fact_verified'}
        self.state.finish_call(call['id'],state,metadata={'io_started':True,'sources':[source]})
        return source

    def decide(self,**kwargs):
        return {'kind':'external_fact','source_refs':[],**kwargs}

    def check(self,decision):
        return AutonomousProactiveBridge.decision_provenance(self.bridge,self.scope,decision)

    def pocket(self,source,*,finding='saved',expires=100):
        self.state.save_finding(finding,session=self.scope['session'],question='Synthetic research',
            content='Unshared synthetic observation',kind='external_fact',sources=[source],
            expires_at=self.now+expires,opportunity_id='current')

    def test_opinions_need_no_external_authority(self):
        self.assertEqual(self.check({'kind':'opinion'}),'allowed')

    def test_declared_refs_must_be_verified_for_opinion_and_observation_too(self):
        source=self.receipt(url='https://example.org/fact?inline-read-more')
        for kind in ('opinion','observation','hypothesis','creative_work'):
            with self.subTest(kind=kind):
                self.assertEqual(self.check({'kind':kind,'source_refs':[source['url']]}),'allowed')
                self.assertEqual(self.check({'kind':kind,'source_refs':['https://example.org/fact']}),
                                 'external_fact_source_not_retrieved')

    def test_source_exists_but_model_did_not_identify_it_is_not_bound(self):
        self.receipt()
        self.assertEqual(self.check(self.decide()),'external_fact_without_provenance')

    def test_identified_actual_source_passes_without_claiming_truth(self):
        source=self.receipt()
        self.assertEqual(self.check(self.decide(source_refs=[source['url']])),'allowed')
        self.assertNotIn('verified',source)

    def test_unrelated_or_invented_source_cannot_use_other_retrieval_as_magic_boolean(self):
        self.receipt()
        self.assertEqual(self.check(self.decide(source_refs=['https://example.org/different'])),
                         'external_fact_source_not_retrieved')

    def test_failed_tool_is_not_a_source_even_when_metadata_contains_url(self):
        source=self.receipt(state='failed')
        self.assertEqual(self.check(self.decide(source_refs=[source['url']])),
                         'external_fact_source_not_retrieved')

    def test_current_selected_pocket_can_reference_same_session_earlier_retrieval(self):
        self.begin('earlier');source=self.receipt('earlier');self.pocket(source)
        self.assertEqual(self.check(self.decide(pocket_id='saved',source_refs=[source['url']])),'allowed')

    def test_unselected_expired_and_revoked_pockets_are_not_authority(self):
        self.begin('earlier');source=self.receipt('earlier');self.pocket(source)
        decision=self.decide(source_refs=[source['url']])
        self.assertEqual(self.check(decision),'external_fact_source_not_retrieved')
        self.state.discard_finding('saved',session=self.scope['session'])
        decision['pocket_id']='saved'
        self.assertEqual(self.check(decision),'external_fact_source_not_retrieved')
        self.pocket(source,finding='expires',expires=1)
        self.now+=2;decision['pocket_id']='expires'
        self.assertEqual(self.check(decision),'external_fact_source_not_retrieved')

    def test_pocket_verified_boolean_without_actual_tool_receipt_does_not_count(self):
        source={'url':'https://example.org/page','verified':True,'fetched_at':self.now,
            'attempt_id':'invented','content_sha256':'a'*64,'verification':'retrieved_not_fact_verified'}
        self.pocket(source)
        self.assertEqual(self.check(self.decide(pocket_id='saved',source_refs=[source['url']])),
                         'external_fact_source_not_retrieved')

    def test_different_session_receipt_cannot_be_laundered_through_local_pocket(self):
        self.begin('elsewhere',session='test:FriendMessage:10002')
        source=self.receipt('elsewhere');self.pocket(source)
        self.assertEqual(self.check(self.decide(pocket_id='saved',source_refs=[source['url']])),
                         'external_fact_source_not_retrieved')

    def test_pocket_digest_must_still_match_persisted_receipt(self):
        self.begin('earlier');source=self.receipt('earlier')
        source['content_sha256']='b'*64;self.pocket(source)
        self.assertEqual(self.check(self.decide(pocket_id='saved',source_refs=[source['url']])),
                         'external_fact_source_not_retrieved')


if __name__=='__main__':unittest.main()
