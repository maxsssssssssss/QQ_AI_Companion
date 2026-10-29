"""Confirmed bubble boundaries are display separators, not altered claims."""
import unittest

from integrations.phase3_runtime.autonomy_content import confirmed_history_quote


class ConfirmedHistoryQuotes(unittest.TestCase):
    def test_exact_quote_does_not_need_boundary_metadata(self):
        self.assertEqual(confirmed_history_quote('第一句。', '第一句。第二句。'), 'exact')

    def test_space_newline_and_crlf_at_confirmed_boundary(self):
        parts = ['先说一个想法。', '再补一个观点。', '还有一个条件。']
        for separator in (' ', '\n', '\r\n', ' \n\t'):
            with self.subTest(separator=repr(separator)):
                self.assertEqual(confirmed_history_quote(separator.join(parts), ''.join(parts), parts),
                                 'confirmed_bubble_boundary')

    def test_partial_quote_crossing_a_real_boundary(self):
        parts = ['完整开头，先说一个想法。', '再补一个观点。完整尾部']
        self.assertEqual(confirmed_history_quote('一个想法。\n再补一个观点。', ''.join(parts), parts),
                         'confirmed_bubble_boundary')

    def test_missing_or_inconsistent_parts_fail_closed(self):
        body = '第一句。第二句。'
        for parts in (None, [], ['不符。', '第二句。'], ['第一句。第二句。']):
            self.assertIsNone(confirmed_history_quote('第一句。 第二句。', body, parts))

    def test_internal_spaces_are_not_removed_or_inserted(self):
        parts = ['Value 1 2 is not twelve.', '再说一句。']
        body = ''.join(parts)
        for quote in ('Value 12 is not twelve. 再说一句。', 'Value 1 2 is nottwelve. 再说一句。',
                      'Value 1 2 is not twelve. 再 说一句。'):
            self.assertIsNone(confirmed_history_quote(quote, body, parts))

    def test_numeric_and_identifier_boundary_not_reinterpreted(self):
        for parts in (['value=1', '2;'], ['note', 'book。'], ['code_', 'word。']):
            self.assertIsNone(confirmed_history_quote(' '.join(parts), ''.join(parts), parts))

    def test_different_word_number_negation_or_order_never_matches(self):
        parts = ['只观察到12只鸟。', '没有证明因果。']
        for quote in ('只观察到13只鸟。 没有证明因果。', '只观察到12只鸟。 已经证明因果。',
                      '没有证明因果。 只观察到12只鸟。', '只观察到12只鸟。\n没有...因果。'):
            self.assertIsNone(confirmed_history_quote(quote, ''.join(parts), parts))

    def test_cannot_omit_middle_bubble(self):
        parts = ['先说结论。', '必要限定。', '再说另一点。']
        self.assertIsNone(confirmed_history_quote('先说结论。 再说另一点。', ''.join(parts), parts))

    def test_quote_spacing_is_bounded(self):
        parts = ['甲。', '乙。']
        self.assertIsNone(confirmed_history_quote(parts[0] + ' ' * 65 + parts[1], ''.join(parts), parts))
        self.assertIsNone(confirmed_history_quote('x' * 8001, ''.join(parts), parts))


if __name__ == '__main__':
    unittest.main()
