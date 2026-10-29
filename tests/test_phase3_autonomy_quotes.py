"""URL-bound, display-normalized quotations from actual visible tool bodies."""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from integrations.phase3_runtime.autonomy_content import review_reason
from integrations.phase3_runtime.autonomy_state import AutonomyState
import test_phase3_proactive_search as search_fixture
from integrations.phase3_search.policy import FETCH


class QuoteBindingTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close);self.now=1800000000.
        self.state=AutonomyState(self.db,clock=lambda:self.now)
        self.scope={'session':'test:FriendMessage:10001','opportunity_id':'op'}
        self.state.begin_opportunity('op',evaluation_id='eval',candidate_id='op',
            session=self.scope['session'],deadline=self.now+600,input_version='v1')

    def receipt(self,kind,body,urls,**kwargs):
        call=self.state.reserve_tool_attempt('eval',kind)
        sources=[{'url':url,'attempt_id':call['id'],'content_sha256':hashlib.sha256(body.encode()).hexdigest(),
                  'verification':'retrieved_not_fact_verified','fetched_at':self.now,**kwargs} for url in urls]
        self.state.finish_call(call['id'],'succeeded',metadata={'io_started':True,'sources':sources,'result_excerpt':body})
        return sources

    def check(self,url,quote):
        meta={'topic':'A sourced observation','kind':'external_fact','source_refs':[url],
              'content_check':{'novelty':'new','basis':'A concrete new observation','unsupported_self_episode':False,
                               'claims':[{'claim':'A sourced observation','url':url,'quote':quote}]}}
        text='[PHASE3_PROACTIVE:send]\n[PHASE3_TOPIC:'+json.dumps(meta)+']\nA sourced observation.'
        return review_reason(text,self.state,'eval',self.scope)

    def test_cannot_use_other_url_snippet_from_same_search_receipt(self):
        a,b='https://example.org/a','https://example.org/b'
        data=json.dumps({'results':[{'url':a,'title':'A','snippet':'A has twelve moons.'},
                                    {'url':b,'title':'B','snippet':'B is a synthetic paper.'}]})
        self.receipt('search',data,[a,b])
        self.assertEqual(self.check(a,'A has twelve moons.'),'allowed')
        self.assertEqual(self.check(b,'A has twelve moons.'),'claim_quote_not_in_retrieved_evidence')
        self.assertEqual(self.check(b,'B is a synthetic paper.'),'allowed')

    def test_headers_and_json_fields_do_not_masquerade_as_body_evidence(self):
        a='https://example.org/a'
        self.receipt('search',json.dumps({'results':[{'url':a,'title':'Invented headline','snippet':'Actual snippet.'}]}),[a])
        self.assertEqual(self.check(a,'Invented headline'),'claim_quote_not_in_retrieved_evidence')
        self.assertEqual(self.check(a,a),'claim_quote_not_in_retrieved_evidence')

    def test_fetch_quote_is_bound_to_exact_header_url_and_body(self):
        a,b='https://example.org/a','https://example.org/b'
        self.receipt('fetch','URL: '+a+'\nTitle: Header only\nContent: Actual body.',[a,b])
        self.assertEqual(self.check(a,'Actual body.'),'allowed')
        self.assertEqual(self.check(a,'Header only'),'claim_quote_not_in_retrieved_evidence')
        self.assertEqual(self.check(b,'Actual body.'),'claim_quote_not_in_retrieved_evidence')

    def test_markdown_escapes_and_whitespace_normalize_without_rewriting_prose(self):
        a='https://example.org/a'
        body="No Man\\'s Sky\\'s update brings asteroid belts\\, anomalies\\, and missions\\.\n\nRead \\(the full details\\)."
        self.receipt('fetch','URL: '+a+'\nTitle: Heading\nContent: '+body,[a])
        self.assertEqual(self.check(a,"No Man's Sky's update brings asteroid belts, anomalies, and missions. Read (the full details)."),'allowed')

    def test_numeric_spaces_and_noncontiguous_quotes_are_not_silently_rewritten(self):
        a='https://example.org/a'
        self.receipt('fetch','URL: '+a+'\nTitle: Heading\nContent: Value 1 2 is not twelve. A separate result follows.',[a])
        self.assertEqual(self.check(a,'Value 12'),'claim_quote_not_in_retrieved_evidence')
        self.assertEqual(self.check(a,'Value ... result follows.'),'claim_quote_not_in_retrieved_evidence')

    def test_truncated_search_keeps_complete_rows_but_not_cross_row_claims(self):
        a,b='https://example.org/a','https://example.org/b'
        body=json.dumps({'results':[{'url':a,'snippet':'Complete first source.'},
                                    {'url':b,'snippet':'Second source that gets cut.'}]})
        self.receipt('search',body[:-12],[a,b])
        self.assertEqual(self.check(a,'Complete first source.'),'allowed')
        self.assertEqual(self.check(b,'Complete first source.'),'claim_quote_not_in_retrieved_evidence')
        self.assertEqual(self.check(b,'Second source'),'allowed')

    def test_truncation_inside_a_url_cannot_create_source_authority(self):
        a='https://example.org/complete-path'
        self.receipt('search','{"results":[{"snippet":"Visible text","url":"https://example.org/',[a])
        self.assertEqual(self.check(a,'Visible text'),'claim_quote_not_in_retrieved_evidence')

    def test_explicit_revocation_not_reintroduced_by_valid_quote(self):
        a='https://example.org/a'
        self.receipt('fetch','URL: '+a+'\nTitle: Heading\nContent: Revoked evidence.',[a],revoked=True)
        self.assertEqual(self.check(a,'Revoked evidence.'),'claim_quote_not_in_retrieved_evidence')

    def test_aging_source_with_accurate_historical_date_is_not_blanket_rejected(self):
        a='https://example.org/history'
        self.receipt('fetch','URL: '+a+'\nTitle: A dated source\nContent: In 2019, the project began.',
                     [a],fetched_at=self.now-4*86400)
        self.assertEqual(self.check(a,'In 2019, the project began.'),'allowed')

    def test_only_selected_receipts_are_scanned(self):
        self.state.begin_opportunity('unrelated',evaluation_id='old',candidate_id='old',session=self.scope['session'],
                                     deadline=self.now+600,input_version='v1')
        old=self.state.reserve_tool_attempt('old','search')
        self.state.finish_call(old['id'],'succeeded',metadata={'io_started':True})
        # A corrupt unrelated historical receipt cannot affect this opportunity.
        self.db.execute('UPDATE autonomy_calls SET metadata=? WHERE id=?',('{malformed',old['id']));self.db.commit()
        a='https://example.org/a';self.receipt('fetch','URL: '+a+'\nTitle: H\nContent: Current body.',[a])
        self.assertEqual(self.check(a,'Current body.'),'allowed')


class FullVisibleToolEvidenceTests(unittest.IsolatedAsyncioTestCase):
    setUp=search_fixture.ProactiveSearchTests.setUp
    make_runtime=search_fixture.ProactiveSearchTests.make_runtime
    execute=search_fixture.ProactiveSearchTests.execute
    calls=search_fixture.ProactiveSearchTests.calls

    async def test_quote_after_old_5000_limit_matches_actual_tool_body(self):
        await self.execute()
        quote='This exact supported sentence occurs after character five thousand.'
        self.fetch.result='URL: '+self.url+'\nTitle: Heading\nContent: '+'x'*6000+'\n'+quote
        result=await self.execute(self.fetch,{'url':self.url})
        meta=json.loads(self.calls()[-1][2])
        self.assertEqual(meta['result_excerpt'],result['untrusted_public_web_data'])
        self.assertIn(quote,meta['result_excerpt'])

    async def test_maximum_chinese_search_evidence_fits_bounded_web_metadata(self):
        self.runtime.max_result_chars=20000
        self.search.result=json.dumps({'results':[{'url':'https://docs.example.org/'+str(i)+'中'*1600,
            'title':'题'*300,'snippet':'文'*3000} for i in range(10)]},ensure_ascii=False)
        result=await self.execute()
        raw=self.calls()[-1][2];meta=json.loads(raw)
        self.assertGreater(len(raw.encode()),65536);self.assertLessEqual(len(raw.encode()),256*1024)
        self.assertEqual(meta['result_excerpt'],result['untrusted_public_web_data'])
        self.assertEqual(len(meta['result_excerpt']),20000)
        self.assertEqual(self.calls()[-1][1],'succeeded')
        model=self.state.reserve_model_request('evaluation-1')
        with self.assertRaisesRegex(ValueError,'metadata_too_large'):
            self.state.finish_call(model['id'],'returned',metadata={'oversized':'文'*30000})
        self.state.finish_call(model['id'],'failed')


if __name__=='__main__':unittest.main()
