"""Typographic quote equivalence must not weaken URL or factual boundaries.

Synthetic source text only. Real API outputs remain in ignored private evidence.
"""
import json
import unittest

import test_phase3_autonomy_quotes as fixtures


class QuoteTypographyTests(unittest.TestCase):
    setUp = fixtures.QuoteBindingTests.setUp
    receipt = fixtures.QuoteBindingTests.receipt
    check = fixtures.QuoteBindingTests.check

    def fetched(self, body, url='https://example.org/synthetic'):
        self.receipt('fetch', 'URL: '+url+'\nTitle: Example\nContent: '+body, [url])
        return url

    def test_curly_source_accepts_straight_single_and_double_quotes(self):
        url = self.fetched('It’s labelled “ready”; they’ll call it ‘finished’.')
        self.assertEqual(self.check(url, 'It\'s labelled "ready"; they\'ll call it \'finished\'.'), 'allowed')

    def test_straight_source_accepts_curly_single_and_double_quotes(self):
        url = self.fetched('The team\'s label is "ready"; don\'t rename it.')
        self.assertEqual(self.check(url, 'The team’s label is “ready”; don’t rename it.'), 'allowed')

    def test_search_typography_composes_with_existing_display_normalization(self):
        url = 'https://example.org/search-result'
        snippet = 'The team\\’s “ready” flag\\,\n\nnot the count\\, changed\\.'
        self.receipt('search', json.dumps({'results': [{'url': url, 'snippet': snippet}]}), [url])
        self.assertEqual(self.check(url, 'The team\'s "ready" flag, not the count, changed.'), 'allowed')

    def test_typographic_match_cannot_borrow_from_another_url(self):
        first, second = 'https://example.org/first', 'https://example.org/second'
        data = json.dumps({'results': [
            {'url': first, 'snippet': 'They’ll use “twelve” as the label.'},
            {'url': second, 'snippet': 'No count is provided.'}]})
        self.receipt('search', data, [first, second])
        quote = 'They\'ll use "twelve" as the label.'
        self.assertEqual(self.check(first, quote), 'allowed')
        self.assertEqual(self.check(second, quote), 'claim_quote_not_in_retrieved_evidence')

    def test_numeric_ranges_minus_signs_and_digit_spacing_are_not_equated(self):
        url = self.fetched('The “range” is 7–15; the offset is −2; tokens are 1 2.')
        for quote in ('The "range" is 7-15', 'the offset is -2', 'tokens are 12'):
            with self.subTest(quote=quote):
                self.assertEqual(self.check(url, quote), 'claim_quote_not_in_retrieved_evidence')

    def test_negation_and_word_order_must_survive(self):
        url = self.fetched('“Group A” did not follow group B.')
        for quote in ('"Group A" did follow group B.', 'group B did not follow "Group A".'):
            with self.subTest(quote=quote):
                self.assertEqual(self.check(url, quote), 'claim_quote_not_in_retrieved_evidence')

    def test_quotes_cannot_skip_a_heading_or_join_separate_sentences(self):
        url = self.fetched('“First” observation.\n\n## Different sample\n\nSecond observation.')
        for quote in ('"First" observation. Second observation.', '"First" observation. … Second observation.'):
            with self.subTest(quote=quote):
                self.assertEqual(self.check(url, quote), 'claim_quote_not_in_retrieved_evidence')

    def test_stored_source_is_never_rewritten_by_match_normalization(self):
        body = 'They’ll keep “the original” exactly as written.'
        url = self.fetched(body)
        original = self.db.execute('SELECT metadata FROM autonomy_calls WHERE kind=?', ('fetch',)).fetchone()[0]
        self.assertEqual(self.check(url, 'They\'ll keep "the original" exactly as written.'), 'allowed')
        after = self.db.execute('SELECT metadata FROM autonomy_calls WHERE kind=?', ('fetch',)).fetchone()[0]
        self.assertEqual(after, original)
        self.assertEqual(json.loads(after)['result_excerpt'].split('\nContent: ', 1)[1], body)


if __name__ == '__main__':
    unittest.main()
