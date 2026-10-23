import json
import unittest

import phase3_request_shape_fixture as fixture


class ShapeFixtureTests(unittest.TestCase):
    def test_history_pairs_are_complete_and_match_target_roles(self):
        rows = fixture.failed_tool_history()
        self.assertEqual([r['role'] for r in rows], [
            'user','assistant','tool','assistant','tool','assistant','user',
            'assistant','tool','assistant','user','assistant','user','assistant','user','assistant'])
        pending = set()
        for row in rows:
            if row['role'] == 'user':
                self.assertFalse(pending)
            for call in row.get('tool_calls', []):
                self.assertNotIn(call['id'], pending)
                json.loads(call['function']['arguments'])
                pending.add(call['id'])
            if row['role'] == 'tool':
                self.assertIn(row['tool_call_id'], pending)
                pending.remove(row['tool_call_id'])
                json.loads(row['content'])
        self.assertFalse(pending)
        self.assertEqual([len(r['content']) for r in rows if r['role']=='tool'], [4619,4710,1883])

    def test_sources_are_explicitly_synthetic_unique_and_bounded(self):
        refs = [fixture.synthetic_ref(i) for i in range(33)]
        self.assertEqual(len(set(refs)), 33)
        self.assertTrue(all(r.startswith('synthetic-shape:') and len(r)==94 for r in refs))
        self.assertEqual(len(fixture.GAME_BODY), 450)
        self.assertTrue(set(fixture.COMMON_GAMES).isdisjoint(fixture.EXPERIENCED_GAMES))
        self.assertEqual(fixture.expected()['longest'], 'unknown')
        self.assertEqual(fixture.expected()['yesterday'], 'unknown')

    def test_old_unrelated_tool_material_does_not_contain_expected_answers(self):
        text = '\n'.join(r['content'] for r in fixture.failed_tool_history() if r['role']=='tool')
        for game in fixture.COMMON_GAMES + fixture.EXPERIENCED_GAMES:
            self.assertNotIn(game, text)


if __name__ == '__main__':
    unittest.main()
