"""Release strategy: direct draft or one local correction, no global rewrite."""
import json
import unittest

from test_phase3_autonomy_request import AutonomyRequestTests, response
from test_phase3_autonomy_content import ContentRevisionTests, answer
from integrations.phase3_runtime.autonomy_content import ContentRevision, DEFAULT_CONTENT_REVISION_POLICY
from integrations.phase3_runtime.autonomy_request import OpportunityStopped


class TargetedRevisionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = AutonomyRequestTests.asyncSetUp
    install = AutonomyRequestTests.install
    collect = AutonomyRequestTests.collect
    calls = AutonomyRequestTests.calls
    audits = AutonomyRequestTests.audits
    advancing_runner = ContentRevisionTests.advancing_runner
    source_receipt = ContentRevisionTests.source_receipt
    sourced_answer = ContentRevisionTests.sourced_answer
    with_calculations = ContentRevisionTests.with_calculations

    def revision(self):
        self.scope = {'evaluation_id': 'eval', 'opportunity_id': 'op',
                      'session': 'test:FriendMessage:10001',
                      'confirmed_active_history': ['我喜欢地图里的留白。']}
        return ContentRevision(self.state, self.scope)

    async def test_default_and_confirmed_history_do_not_force_rewrite(self):
        revision = self.revision()
        self.assertEqual(DEFAULT_CONTENT_REVISION_POLICY, 'targeted_once')
        raw = answer('留白之外，我还想让地图能留下玩家走错路的涂改痕迹。', checked=False)
        runner = self.advancing_runner([raw])
        self.install(runner, revision=revision.inspect)
        result = await self.collect(runner)
        self.assertEqual([item.completion_text for item in result], [raw])
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(revision.revisions, 0)
        self.assertEqual(self.audits('decision')[-1]['policy'], 'targeted_once')

    async def test_partial_recall_or_repeat_label_is_not_a_domain_ban(self):
        revision = self.revision()
        self.assertIsNone(revision.inspect(answer('我还是喜欢地图留白；这次想加可擦掉的路线。', novelty='repeat')))
        self.assertEqual(revision.revisions, 0)

    async def test_plain_draft_requires_one_control_decision_not_automatic_send(self):
        revision = self.revision()
        draft = '哈，这地图要是能写满吐槽就更好玩了。'
        final = answer(draft, checked=False)
        runner = self.advancing_runner([draft, final])
        self.install(runner, revision=revision.inspect)
        result = await self.collect(runner)
        self.assertEqual(result[0].completion_text, final)
        self.assertEqual(len(self.calls()), 2)
        self.assertIn('PHASE3_TARGETED_REVISION_V1', self.audits('request')[-1]['messages'][-1]['content'])
        self.assertEqual(revision.edit_ranges, [])

    async def test_metadata_repair_cannot_invent_probability_or_rewrite_good_draft(self):
        revision = self.revision()
        draft = '这次共有三个阵营。'
        revision.inspect(draft)
        with self.assertRaisesRegex(OpportunityStopped, 'revision_scope_violation'):
            revision.inspect(answer('这次共有三个阵营，所以每个人有2/3的概率白干。'))
        self.assertTrue(self.scope['content_review_rejected'])
        self.assertEqual(revision.revisions, 1)

    async def test_source_quote_repair_only_changes_metadata(self):
        revision = self.revision()
        url, quote = self.source_receipt()
        draft = self.sourced_answer('报警率为26%。我觉得值得继续看看。', url, '不存在的引文')
        self.assertIsNotNone(revision.inspect(draft))
        final = self.sourced_answer('报警率为26%。我觉得值得继续看看。', url, quote)
        self.assertIsNone(revision.inspect(final))
        self.assertEqual(revision.revisions, 1)

    async def test_valid_source_draft_does_not_force_second_generation(self):
        revision = self.revision()
        url, quote = self.source_receipt()
        self.assertIsNone(revision.inspect(self.sourced_answer('报警率为26%。', url, quote)))
        self.assertEqual(revision.revisions, 0)

    async def test_exact_calculation_fix_stays_in_original_numeric_span(self):
        revision = self.revision()
        draft = self.with_calculations(answer('这里只算一次：结果是5个。', checked=True),
            [{'expression': '2+2', 'result': '5', 'unit': '个'}])
        self.assertIsNotNone(revision.inspect(draft))
        fixed = self.with_calculations(answer('这里只算一次：结果是4个。'),
            [{'expression': '2+2', 'result': '4', 'unit': '个'}])
        self.assertIsNone(revision.inspect(fixed))
        self.assertTrue(self.scope['content_calculation_checks'][0]['equal'])

    async def test_episode_patch_cannot_change_other_sentence(self):
        revision = self.revision()
        draft = answer('我昨天玩了这个游戏。规则的留白挺有意思。')
        self.assertIsNotNone(revision.inspect(draft))
        self.assertIsNone(revision.inspect(answer('我觉得这个游戏值得研究。规则的留白挺有意思。')))
        revision = self.revision()
        revision.inspect(draft)
        with self.assertRaisesRegex(OpportunityStopped, 'revision_scope_violation'):
            revision.inspect(answer('我觉得这个游戏值得研究。规则有20种隐藏概率。'))

    async def test_one_attempt_only_and_never_reverts_to_unfixed_draft(self):
        revision = self.revision()
        draft = answer('我昨晚玩了这个游戏。')
        revision.inspect(draft)
        with self.assertRaisesRegex(OpportunityStopped, 'unsupported_concrete_self_episode'):
            revision.inspect(draft)
        self.assertEqual(revision.revisions, 1)

    async def test_plain_or_corrected_skip_does_not_force_send(self):
        revision = self.revision()
        self.assertIsNone(revision.inspect('[PHASE3_PROACTIVE:skip]'))
        self.assertEqual(revision.revisions, 0)
        revision = self.revision()
        revision.inspect('尚未按控制协议输出的普通草稿。')
        self.assertIsNone(revision.inspect('[PHASE3_PROACTIVE:skip]'))

    async def test_optional_creative_progress_reaches_existing_ttl_contract(self):
        revision = self.revision()
        lines = answer('新的创作思路。', novelty='continuation').splitlines()
        meta = json.loads(lines[1][len('[PHASE3_TOPIC:'):-1])
        meta.update(kind='creative_work', progress='加入一个可逆的叙事分支。')
        raw = '[PHASE3_PROACTIVE:defer:15]\n[PHASE3_TOPIC:' + json.dumps(meta, ensure_ascii=False) + ']'
        self.assertIsNone(revision.inspect(raw))
        self.assertEqual(self.scope['content_check']['novelty'], 'continuation')
        self.assertTrue(self.scope['content_check']['basis'])

    async def test_budget_and_tool_context_unchanged(self):
        revision = self.revision()
        self.state.model_limit = 1
        runner = self.advancing_runner(['缺控制行的正文。', answer('缺控制行的正文。')])
        self.install(runner, revision=revision.inspect)
        with self.assertRaisesRegex(OpportunityStopped, 'model_request_budget'):
            await self.collect(runner)
        self.assertEqual(len(self.calls()), 1)

    async def test_internal_json_not_sent_or_reconstructed(self):
        revision = self.revision()
        with self.assertRaisesRegex(OpportunityStopped, 'internal_audit_json_in_body'):
            revision.inspect(answer('正文。{"content_check":{"novelty":"new"}}'))
        self.assertEqual(revision.revisions, 0)

    async def test_metadata_patch_cannot_remove_source_obligation_or_downgrade_kind(self):
        for downgrade in (False, True):
            revision = self.revision()
            url, _ = self.source_receipt()
            draft = self.sourced_answer('报警率为26%。', url, '未取得引文')
            lines = draft.splitlines(); meta = json.loads(lines[1][len('[PHASE3_TOPIC:'):-1])
            meta['kind'] = 'external_fact'
            lines[1] = '[PHASE3_TOPIC:' + json.dumps(meta, ensure_ascii=False) + ']'
            revision.inspect('\n'.join(lines))
            meta.pop('content_check'); meta['source_refs'] = []
            if downgrade: meta['kind'] = 'opinion'
            lines[1] = '[PHASE3_TOPIC:' + json.dumps(meta, ensure_ascii=False) + ']'
            with self.assertRaisesRegex(OpportunityStopped, 'revision_(identity_changed|declared_evidence_removed)'):
                revision.inspect('\n'.join(lines))

    async def test_arithmetic_patch_cannot_change_premise_to_fit_old_wrong_answer(self):
        revision = self.revision()
        draft = self.with_calculations(answer('这个合成算式结果为5。'), [{'expression': '2+2', 'result': '5'}])
        revision.inspect(draft)
        final = self.with_calculations(answer('这个合成算式结果为5。'), [{'expression': '2+3', 'result': '5'}])
        with self.assertRaisesRegex(OpportunityStopped, 'revision_calculation_premise_changed'):
            revision.inspect(final)

    async def test_only_failed_calculation_result_is_editable(self):
        revision = self.revision()
        draft = self.with_calculations(answer('第一项为4，第二项为9。'),
            [{'expression': '2+2', 'result': '4'}, {'expression': '3+3', 'result': '9'}])
        revision.inspect(draft)
        self.assertEqual([revision.original_body[a:b] for a,b in revision.edit_ranges], ['9'])

    async def test_fixing_math_metadata_alone_does_not_leave_wrong_body(self):
        revision = self.revision()
        draft = self.with_calculations(answer('这里只算一次：结果是5个。'),
            [{'expression': '2+2', 'result': '5', 'unit': '个'}])
        revision.inspect(draft)
        final = self.with_calculations(answer('这里只算一次：结果是5个。'),
            [{'expression': '2+2', 'result': '4', 'unit': '个'}])
        with self.assertRaisesRegex(OpportunityStopped, 'revision_calculation_body_not_corrected'):
            revision.inspect(final)

    async def test_rounding_cannot_be_relaxed_to_accept_wrong_result(self):
        revision = self.revision()
        draft = self.with_calculations(answer('这个近似值为8。'),
            [{'expression': '365/48', 'result': '8', 'round_digits': 1}])
        revision.inspect(draft)
        final = self.with_calculations(answer('这个近似值为8。'),
            [{'expression': '365/48', 'result': '8', 'round_digits': 0}])
        with self.assertRaisesRegex(OpportunityStopped, 'revision_calculation_premise_changed'):
            revision.inspect(final)

    async def test_header_only_repair_keeps_original_bubble_boundaries_and_raw_audit(self):
        revision = self.revision()
        draft = '有个新点子。\n<<PRIVATE_COMPANION_SPLIT>>\n让地图可以自己写吐槽。\n\n再留点空白。'
        header = '\n'.join(answer(draft, checked=False).splitlines()[:2])
        runner = self.advancing_runner([draft, header])
        self.install(runner, revision=revision.inspect, finalize_revision=revision.finalize)
        rows = await self.collect(runner)
        from integrations.phase3_runtime.autonomy_contract import parse
        self.assertEqual(parse(rows[0].completion_text)['text'], draft)
        self.assertEqual(len(self.calls()), 2)
        audit = self.audits('decision')[-1]
        self.assertEqual(audit['visible_draft'], header)
        self.assertEqual(parse(audit['bound_draft'])['text'], draft)
        self.assertTrue(audit['bound_original_body'])

    async def test_header_only_still_requires_explicit_send_and_valid_evidence(self):
        revision = self.revision()
        revision.inspect('未发送的普通草稿。')
        skip = '[PHASE3_PROACTIVE:skip]'
        self.assertIsNone(revision.inspect(skip))
        self.assertEqual(revision.finalize(skip), skip)
        revision = self.revision()
        url, _ = self.source_receipt()
        draft = self.sourced_answer('报警率为26%。', url, '未取得原文')
        lines = draft.splitlines()
        meta = json.loads(lines[1][len('[PHASE3_TOPIC:'):-1])
        meta['kind'] = 'external_fact'
        header = lines[0] + '\n[PHASE3_TOPIC:' + json.dumps(meta, ensure_ascii=False) + ']'
        meta.pop('content_check')
        initial = lines[0] + '\n[PHASE3_TOPIC:' + json.dumps(meta, ensure_ascii=False) + ']\n' + lines[2]
        revision.inspect(initial)
        self.assertEqual(revision.edit_ranges, [])
        with self.assertRaisesRegex(OpportunityStopped, 'claim_quote_not_in_retrieved_evidence'):
            revision.inspect(header)
        self.assertEqual(revision.finalize(header), header)

    async def test_missing_claim_feedback_contains_actual_required_schema(self):
        revision = self.revision()
        raw = answer('已取得资料中的外部事实。', checked=False).replace('"opinion"', '"external_fact"')
        instruction = revision.inspect(raw)
        self.assertIn('content_check', instruction)
        self.assertIn('body_quote', instruction)
        self.assertIn('只输出两行', instruction)


if __name__ == '__main__':
    unittest.main()
