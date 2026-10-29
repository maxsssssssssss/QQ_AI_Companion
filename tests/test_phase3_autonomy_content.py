"""Same-Agent bounded correction, private audit and delivery-only preferences."""
import json
import hashlib
import unittest
from types import SimpleNamespace as NS

from test_phase3_autonomy_request import AutonomyRequestTests, Runner, response
from integrations.phase3_runtime.autonomy_content import ContentRevision, review_payload, review_reason, unsupported_episodes, internal_audit_json
from integrations.phase3_runtime.autonomy_request import OpportunityStopped
from integrations.phase3_memory.self_preferences import preference_only


def answer(body='我更喜欢解谜游戏的留白。', *, novelty='new', episode=False, checked=True):
    meta = {'topic': '解谜里的留白', 'kind': 'opinion', 'source_refs': []}
    if checked:
        meta['content_check'] = {'novelty': novelty, 'basis': '新的具体判断',
            'unsupported_self_episode': episode, 'claims': [],
            'contrast': {'history_index': -1, 'old_body_quote':'', 'old_core':'',
                         'new_core':'该合成回复的独立观点', 'new_body_quote':body}}
    return '[PHASE3_PROACTIVE:send]\n[PHASE3_TOPIC:'+json.dumps(meta, ensure_ascii=False)+']\n'+body


class ContentRevisionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = AutonomyRequestTests.asyncSetUp
    install = AutonomyRequestTests.install
    collect = AutonomyRequestTests.collect
    calls = AutonomyRequestTests.calls
    audits = AutonomyRequestTests.audits

    def revision(self):
        self.scope = {'evaluation_id': 'eval', 'opportunity_id': 'op', 'session': 'test:FriendMessage:10001'}
        return ContentRevision(self.state, self.scope, policy='legacy_two_pass')

    def source_receipt(self, url='https://example.org/2026/study', body='26% of birds gave an alarm call.'):
        charge=self.state.reserve_tool_attempt('eval','fetch')
        self.assertTrue(charge['allowed'])
        source={'attempt_id':charge['id'],'url':url,'content_sha256':hashlib.sha256(body.encode()).hexdigest(),
            'verification':'retrieved_not_fact_verified','observed_at':self.now}
        self.state.finish_call(charge['id'],'succeeded',metadata={'io_started':True,'sources':[source],
            'result_excerpt':'URL: '+url+'\nTitle: Synthetic study\nContent: '+body})
        return url,body

    def sourced_answer(self, body, url, quote, *, body_quote='报警率为26%'):
        lines=answer(body).splitlines()
        meta=json.loads(lines[1][len('[PHASE3_TOPIC:'):-1])
        meta['source_refs']=[url]
        meta['content_check']['claims']=[{'claim':'报警率','body_quote':body_quote,'url':url,'quote':quote}]
        lines[1]='[PHASE3_TOPIC:'+json.dumps(meta,ensure_ascii=False)+']'
        return '\n'.join(lines)

    def advancing_runner(self, texts):
        runner=Runner([response(text) for text in texts]);create=runner.create
        async def queued(**kw):
            value=await create(**kw)
            runner.responses=runner.responses[1:]
            return value
        runner.provider.client.chat.completions.create=queued
        return runner

    def with_calculations(self,text,calculations):
        lines=text.splitlines();meta=json.loads(lines[1][len('[PHASE3_TOPIC:'):-1])
        meta['content_check']['calculations']=calculations
        lines[1]='[PHASE3_TOPIC:'+json.dumps(meta,ensure_ascii=False)+']'
        return '\n'.join(lines)

    async def test_initial_draft_not_yielded_only_revised_final_with_same_context(self):
        draft, final = answer('旧观点换了一个比喻。', checked=False), answer()
        runner = Runner([response(draft), response(final)])
        create = runner.create
        async def queued(**kw):
            value = await create(**kw)
            runner.responses = runner.responses[1:]
            return value
        runner.provider.client.chat.completions.create = queued
        revision = self.revision()
        self.install(runner, revision=revision.inspect)
        result = await self.collect(runner)
        self.assertEqual([r.completion_text for r in result], [final])
        self.assertEqual(len(self.calls()), 2)
        actual = self.audits('request')[-1]['messages']
        self.assertEqual(actual[-2]['content'], draft)
        self.assertIn('PHASE3_CONTENT_REVISION_V1', actual[-1]['content'])
        self.assertEqual(runner.requests, 2)
        self.assertEqual(self.scope['content_review_reason'], 'allowed')

    async def test_skip_not_forced_into_send_or_extra_review(self):
        revision = self.revision()
        self.assertIsNone(revision.inspect('[PHASE3_PROACTIVE:skip]'))
        self.assertEqual(revision.revisions, 0)

    async def test_declared_same_thesis_and_unsupported_episode_cannot_be_sent(self):
        for raw, reason in [(answer(novelty='repeat'), 'repeated_core_claim'),
                            (answer(episode=True), 'unsupported_self_episode')]:
            revision = self.revision()
            revision.inspect(answer(checked=False))
            self.assertIn(reason, revision.inspect(raw))
            with self.assertRaisesRegex(OpportunityStopped, reason):
                revision.inspect(raw)
            self.assertEqual(revision.revisions, 2)

    async def test_missing_metadata_bounded_not_infinite_and_does_not_hide_original(self):
        revision = self.revision()
        raw = answer(checked=False)
        revision.inspect(raw); revision.inspect(raw)
        with self.assertRaises(OpportunityStopped): revision.inspect(raw)
        self.assertEqual(len(self.audits('decision')), 3)
        self.assertEqual(self.audits('decision')[0]['visible_draft'], raw)

    async def test_same_domain_new_content_and_continuation_allowed(self):
        for novelty in ('new', 'continuation'):
            self.assertEqual(review_reason(answer(novelty=novelty), self.state, 'eval',
                {'opportunity_id': 'op', 'session': 'test:FriendMessage:10001'}), 'allowed')

    async def test_confirmed_bubble_quote_display_binding_is_audited_not_rewritten(self):
        revision = self.revision(); revision.revisions = 2
        parts = ['地图标记太多会变成清单。', '探索像在交作业。']
        self.scope.update(require_content_bindings=True,
            confirmed_active_history=[''.join(parts)], confirmed_active_history_parts=[parts])
        raw = answer('道具也可以只用来说明世界曾经有人生活。')
        lines = raw.splitlines(); meta = json.loads(lines[1][len('[PHASE3_TOPIC:'):-1])
        meta['content_check']['contrast'].update(history_index=0,
            old_body_quote='\n'.join(parts), old_core='探索变成任务清单', new_core='无功能道具表现世界历史')
        lines[1] = '[PHASE3_TOPIC:'+json.dumps(meta, ensure_ascii=False)+']'
        raw = '\n'.join(lines)
        self.assertIsNone(revision.inspect(raw))
        saved = self.audits('decision')[-1]
        self.assertEqual(saved['visible_draft'], raw)
        self.assertEqual(saved['history_quote_binding']['match'], 'confirmed_bubble_boundary')
        self.assertEqual(self.scope['confirmed_active_history'], [''.join(parts)])

    async def test_invalid_history_quote_does_not_reuse_prior_binding(self):
        revision = self.revision(); revision.revisions = 2
        self.scope.update(require_content_bindings=True,
            confirmed_active_history=['原始观点。后续限定。'],
            confirmed_active_history_parts=[['原始观点。', '后续限定。']],
            content_history_quote_binding={'match':'stale'})
        lines = answer().splitlines(); meta = json.loads(lines[1][len('[PHASE3_TOPIC:'):-1])
        meta['content_check']['contrast'].update(history_index=0, old_body_quote='不存在的观点。', old_core='旧观点')
        lines[1] = '[PHASE3_TOPIC:'+json.dumps(meta, ensure_ascii=False)+']'
        with self.assertRaisesRegex(OpportunityStopped, 'contrast_not_confirmed_history'):
            revision.inspect('\n'.join(lines))
        self.assertIsNone(self.audits('decision')[-1]['history_quote_binding'])

    async def test_opinion_label_does_not_bypass_external_claim_receipt(self):
        text = answer()
        lines = text.splitlines()
        meta = json.loads(lines[1][len('[PHASE3_TOPIC:'):-1])
        meta['content_check']['claims'] = [{'claim': '一个外部事实', 'url': 'https://example.org/a', 'quote': '真实原文'}]
        meta['source_refs'] = ['https://example.org/a']
        lines[1] = '[PHASE3_TOPIC:'+json.dumps(meta, ensure_ascii=False)+']'
        self.assertEqual(review_reason('\n'.join(lines), self.state, 'eval',
            {'opportunity_id': 'op', 'session': 'test:FriendMessage:10001'}), 'claim_quote_not_in_retrieved_evidence')

    def test_preferences_not_episodic_truth_and_first_person_still_allowed(self):
        for text in ('我喜欢玩解谜游戏', '我喜欢去年发行的游戏', '我其实很喜欢听爵士'):
            self.assertTrue(preference_only(text))
        for text in ('我喜欢昨天玩过的那款解谜游戏', '我喜欢上周吃过的点心'):
            self.assertFalse(preference_only(text))
        for text in ('我喜欢解谜游戏。', '我刚看了这篇报道。', '如果我昨晚玩了游戏，我可能也不想睡。'):
            self.assertFalse(unsupported_episodes(text))
        self.assertTrue(unsupported_episodes('我最近列了二十部纪录片。'))
        self.assertTrue(unsupported_episodes('凌晨两点还没睡着，突然想起一件事。'))

    def test_invalid_or_oversized_check_not_accepted(self):
        self.assertIsNone(review_payload(answer(checked=False)))
        self.assertIsNone(review_payload(answer().replace('"new"', '"bad"')))

    def test_exact_trailing_pair_and_single_outer_brace_recover_without_prose_change(self):
        from integrations.phase3_runtime.autonomy_contract import parse
        original = answer('一句新观点。\n保留正文行。')
        lines = original.splitlines()
        relocated = '\n'.join(lines[2:]+lines[:2])
        fixed = parse(relocated)
        self.assertEqual(fixed['text'], '一句新观点。\n保留正文行。')
        self.assertEqual(fixed['metadata_repair'], 'trailing_control_pair')
        missing_outer = original.replace('}}]\n', '}]\n')
        self.assertEqual(parse(missing_outer)['text'], fixed['text'])
        self.assertEqual(review_payload(missing_outer)['novelty'], 'new')
        self.assertEqual(parse(relocated+'\n[PHASE3_PROACTIVE:skip]')['reason'], 'invalid_control')

    def test_review_compares_confirmed_history_not_draft_and_keeps_actual_history(self):
        revision = self.revision()
        revision.scope['confirmed_active_history'] = ['真实已确认的旧观点。']
        instruction = revision.inspect(answer('尚未发送的新草稿。', checked=False))
        self.assertIn('真实已确认的旧观点。', instruction)
        confirmed=instruction.split('confirmed_history（仅此为已确认历史，草稿不在其中）：\n',1)[1]
        confirmed=confirmed.split('\n待核对的实际草稿正文',1)[0]
        self.assertEqual(json.loads(confirmed),['真实已确认的旧观点。'])
        self.assertNotIn('尚未发送的新草稿。',confirmed)
        self.assertIn('尚未发送的新草稿。',instruction.split('\n待核对的实际草稿正文',1)[1])
        self.assertIn('草稿不在其中', instruction)

    async def test_old_quote_is_confirmed_and_new_paraphrase_is_only_audited(self):
        revision=self.revision();self.scope['confirmed_active_history']=['这是确实说过的旧观点。']
        original=answer();lines=original.splitlines();meta=json.loads(lines[1][len('[PHASE3_TOPIC:'):-1])
        meta['content_check']['contrast'].update(history_index=0,old_body_quote='未发送的草稿',old_core='旧判断')
        lines[1]='[PHASE3_TOPIC:'+json.dumps(meta,ensure_ascii=False)+']'
        self.assertEqual(review_reason('\n'.join(lines),self.state,'eval',self.scope),'contrast_not_confirmed_history')
        meta['content_check']['contrast'].update(old_body_quote=self.scope['confirmed_active_history'][0],new_body_quote='实际正文中没有此句')
        lines[1]='[PHASE3_TOPIC:'+json.dumps(meta,ensure_ascii=False)+']'
        changed='\n'.join(lines)
        self.assertEqual(review_reason(changed,self.state,'eval',self.scope),'allowed')
        from integrations.phase3_runtime.autonomy_contract import parse
        self.assertEqual(self.scope['content_body_binding']['sha256'],
            hashlib.sha256(parse(changed)['text'].encode()).hexdigest())
        self.assertFalse(self.scope['content_body_binding']['model_quote_exact'])
        self.assertEqual(self.scope['content_body_binding']['semantic_verification'],'same_agent_judgement_not_proof')
        revision.inspect(answer(checked=False))
        self.assertIn('PHASE3_FINAL_BODY_CHECK_V1',revision.inspect(changed))
        self.assertIsNone(revision.inspect(changed))
        audit=self.audits('decision')[-1]
        self.assertFalse(audit['body_binding']['model_quote_exact'])
        self.assertEqual(audit['check']['contrast']['new_body_quote'],'实际正文中没有此句')

    async def test_ordinary_counts_plans_and_reference_digits_are_not_external_facts(self):
        self.revision();url,quote=self.source_receipt()
        for suffix in ('我有两个想法。','我想过5分钟再整理。','假设有3种路线可以选择。','来源：'+url):
            with self.subTest(suffix=suffix):
                text=self.sourced_answer('报警率为26%。'+suffix,url,quote)
                self.assertEqual(review_reason(text,self.state,'eval',self.scope),'allowed')
                self.assertTrue(self.scope['content_claim_bindings'][0]['body_quote_exact'])

    async def test_full_quantity_subject_draft_reaches_same_agent_review_without_formal_truth_claim(self):
        revision=self.revision();url,quote=self.source_receipt()
        draft=answer('一共抓了15只鸟。报警率为26%。',checked=False)
        final=self.sourced_answer('资料只支持鸟的报警率为26%，不能据此推断总数。',url,quote)
        runner=self.advancing_runner([draft,final,final]);self.install(runner,revision=revision.inspect)
        delivered=await self.collect(runner)
        self.assertEqual([r.completion_text for r in delivered],[final])
        requests=self.audits('request')
        self.assertEqual(len(requests),3)
        self.assertIn('PHASE3_FINAL_BODY_CHECK_V1',requests[-1]['messages'][-1]['content'])
        self.assertEqual(self.audits('decision')[1]['reason'],'final_body_check_before_delivery')
        latest=json.dumps(requests[-1]['messages'],ensure_ascii=False)
        self.assertIn('一共抓了15只鸟。报警率为26%。',latest)
        self.assertIn('数值、主体、时间与限定应全部核对',latest)
        self.assertNotIn('15',delivered[0].completion_text)
        self.assertEqual(self.audits('decision')[-1]['semantic_fact_verification'],'model_judgement_not_proof')

    async def test_optional_claim_paraphrase_does_not_forge_literal_evidence(self):
        self.revision();url,quote=self.source_receipt()
        text=self.sourced_answer('资料中的报警率为26%。',url,quote,body_quote='模型对正文的概括')
        self.assertEqual(review_reason(text,self.state,'eval',self.scope),'allowed')
        self.assertFalse(self.scope['content_claim_bindings'][0]['body_quote_exact'])
        bad=self.sourced_answer('资料中的报警率为26%。',url,'从未取得的来源原文',body_quote='模型对正文的概括')
        self.assertEqual(review_reason(bad,self.state,'eval',self.scope),'claim_quote_not_in_retrieved_evidence')

    async def test_optional_nonstring_quote_is_inexact_audit_not_generation_failure(self):
        self.revision();url,quote=self.source_receipt()
        for value in (None,[],{'summary':'只是概括'},17,True):
            with self.subTest(value=value):
                lines=self.sourced_answer('报警率为26%。',url,quote).splitlines()
                meta=json.loads(lines[1][len('[PHASE3_TOPIC:'):-1])
                meta['content_check']['contrast']['new_body_quote']=value
                meta['content_check']['claims'][0]['body_quote']=value
                lines[1]='[PHASE3_TOPIC:'+json.dumps(meta,ensure_ascii=False)+']'
                self.assertEqual(review_reason('\n'.join(lines),self.state,'eval',self.scope),'allowed')
                self.assertFalse(self.scope['content_body_binding']['model_quote_exact'])
                self.assertFalse(self.scope['content_claim_bindings'][0]['body_quote_exact'])

    async def test_missing_episode_self_report_is_absent_not_fabricated_false(self):
        def without_self_report(body):
            lines=answer(body).splitlines()
            meta=json.loads(lines[1][len('[PHASE3_TOPIC:'):-1])
            del meta['content_check']['unsupported_self_episode']
            lines[1]='[PHASE3_TOPIC:'+json.dumps(meta,ensure_ascii=False)+']'
            return '\n'.join(lines)
        safe=without_self_report('我更喜欢地图里能自己发现的路线。')
        revision=self.revision();revision.inspect(answer(checked=False))
        self.assertIsNone(revision.inspect(safe))
        self.assertNotIn('unsupported_self_episode',review_payload(safe))
        self.assertNotIn('unsupported_self_episode',self.audits('decision')[-1]['check'])
        self.assertEqual(self.audits('decision')[-1]['semantic_fact_verification'],'model_judgement_not_proof')
        # Concrete-life checks still run even when a model omits its self-report.
        unsafe=without_self_report('我昨天玩了那款游戏。')
        revision=self.revision();revision.inspect(answer(checked=False))
        self.assertIn('unsupported_concrete_self_episode',revision.inspect(unsafe))
        with self.assertRaisesRegex(OpportunityStopped,'unsupported_concrete_self_episode'):
            revision.inspect(unsafe)
        self.assertNotIn('unsupported_self_episode',self.audits('decision')[-1]['check'])

    async def test_bare_audit_json_is_corrected_in_same_agent_without_yielding_it(self):
        revision=self.revision()
        leaked=answer('我喜欢这种留白。\n'+json.dumps({'content_check':{'novelty':'new'}},ensure_ascii=False))
        final=answer('留白让同一张地图有不同的走法。')
        runner=self.advancing_runner([answer(checked=False),leaked,final])
        self.install(runner,revision=revision.inspect)
        delivered=await self.collect(runner)
        self.assertEqual([r.completion_text for r in delivered],[final])
        self.assertEqual(len(self.audits('request')),3)
        self.assertEqual(self.audits('decision')[1]['reason'],'internal_audit_json_in_body')
        self.assertIn('internal_audit_json_in_body',json.dumps(self.audits('request')[-1],ensure_ascii=False))

    async def test_repeated_audit_json_is_bounded_and_never_yielded(self):
        revision=self.revision()
        leaked=answer('普通开头。\n'+json.dumps({'content_check':{'novelty':'new'}},ensure_ascii=False))
        runner=self.advancing_runner([leaked,leaked,leaked]);self.install(runner,revision=revision.inspect)
        delivered=[]
        with self.assertRaisesRegex(OpportunityStopped,'internal_audit_json_in_body'):
            async for item in runner._iter_llm_responses():delivered.append(item)
        self.assertFalse(delivered);self.assertEqual(len(self.audits('request')),3)
        self.assertTrue(self.scope['content_review_rejected']);self.assertEqual(revision.revisions,2)

    async def test_internal_json_recognition_is_structural_and_normal_json_is_allowed(self):
        self.revision()
        for obj in ({'content_check':{'novelty':'new'}},
                    {'novelty':'new','unsupported_self_episode':False,'claims':[]},
                    {'history_index':-1,'old_body_quote':'','old_core':'','new_core':'新观点'}):
            self.assertTrue(internal_audit_json('正文\n```json\n'+json.dumps(obj)+'\n```'))
        for body in ('配置可以写成 {"timeout":5,"mode":"demo"}。',
                     '字段名content_check并不是JSON对象。',
                     '{"content_check":"普通字符串字段示意"}',
                     '```json\n{"url":"https://example.org/2026","count":2}\n```'):
            with self.subTest(body=body):
                self.assertFalse(internal_audit_json(body))
                self.assertEqual(review_reason(answer(body),self.state,'eval',self.scope),'allowed')

    async def test_declared_exact_arithmetic_matches_without_fabricating_tool_calls(self):
        self.revision()
        items=[{'expression':'0.1+0.2','result':'0.3','unit':'比例'},
               {'expression':'1/3','result':'2/6','unit':'比例'},
               {'expression':'0.5*365','result':'182.5','unit':'小时'},
               {'expression':'182.5/24','result':'365/48','unit':'天'}]
        text=self.with_calculations(answer('0.3、2/6、182.5小时和365/48天是这些合成算式的结果。'),items)
        self.assertEqual(review_reason(text,self.state,'eval',self.scope),'allowed')
        rows=self.scope['content_calculation_checks']
        self.assertEqual(len(rows),4)
        self.assertTrue(all(row['equal'] and row['result_in_body'] and row['io'] is False for row in rows))
        self.assertTrue(all(row['scope']=='arithmetic_only_not_premises_or_units' for row in rows))
        self.assertEqual(self.gate_call_count(),0)
        self.assertEqual(self.state.summary()['calls'],{})

    def gate_call_count(self):
        return self.db.execute('SELECT count(*) FROM autonomy_calls').fetchone()[0]

    async def test_wrong_calculation_revises_same_agent_then_yields_only_corrected_final(self):
        revision=self.revision()
        wrong=self.with_calculations(answer('合成计算2+2等于5。'),[{'expression':'2+2','result':'5','unit':'个'}])
        correct=self.with_calculations(answer('合成计算2+2等于4。'),[{'expression':'2+2','result':'4','unit':'个'}])
        runner=self.advancing_runner([answer('先做一次合成核算。',checked=False),wrong,correct])
        self.install(runner,revision=revision.inspect)
        delivered=await self.collect(runner)
        self.assertEqual([item.completion_text for item in delivered],[correct])
        self.assertEqual(len(self.audits('request')),3)
        audit=self.audits('decision')
        self.assertEqual(audit[1]['reason'],'calculation_mismatch')
        self.assertFalse(audit[1]['calculation_checks'][0]['equal'])
        self.assertEqual(audit[-1]['calculation_checks'][0]['actual_result'],'4')
        self.assertTrue(audit[-1]['calculation_checks'][0]['equal'])
        self.assertFalse(self.db.execute("SELECT 1 FROM autonomy_calls WHERE kind!='model'").fetchone())

    async def test_approximation_requires_explicit_bounded_rounding(self):
        self.revision()
        for digits,result in ((0,'8'),(1,'7.6'),(6,'7.604167')):
            with self.subTest(digits=digits):
                item={'expression':'365/48','result':result,'unit':'天','round_digits':digits}
                text=self.with_calculations(answer('该合成算式近似为'+result+'天。'),[item])
                self.assertEqual(review_reason(text,self.state,'eval',self.scope),'allowed')
                self.assertEqual(self.scope['content_calculation_checks'][0]['round_digits'],digits)
                self.assertEqual(self.scope['content_calculation_checks'][0]['actual_result'],'365/48')
        exact=self.with_calculations(answer('约7.6天。'),[{'expression':'365/48','result':'7.6','unit':'天'}])
        self.assertEqual(review_reason(exact,self.state,'eval',self.scope),'calculation_mismatch')
        self.assertEqual(self.gate_call_count(),0)

    async def test_invalid_calculation_and_rounding_fail_without_io(self):
        self.revision()
        for item in ({'expression':'1/0','result':'0'}, {'expression':'__import__("os")','result':'1'},
                     {'expression':'2**8','result':'256'}, {'expression':'2+2','result':4},
                     {'expression':'2+2','result':'4','unit':'https://example.org'}):
            with self.subTest(item=item):
                text=self.with_calculations(answer('合成正文。'),[item])
                self.assertEqual(review_reason(text,self.state,'eval',self.scope),'invalid_calculation')
        for digits in (-1,7,True,'1',1.0):
            with self.subTest(digits=digits):
                item={'expression':'2+2','result':'4','round_digits':digits}
                self.assertEqual(review_reason(self.with_calculations(answer(),[item]),self.state,'eval',self.scope),
                    'invalid_calculation_rounding')
        self.assertEqual(self.gate_call_count(),0)

    async def test_calculation_metadata_is_bounded_to_four_objects(self):
        self.revision()
        for items in ([{'expression':'1+1','result':'2'}]*5,None,{},[1]):
            with self.subTest(items=items):
                text=self.with_calculations(answer(),items)
                self.assertIsNone(review_payload(text))
                self.assertEqual(review_reason(text,self.state,'eval',self.scope),'content_check_missing_or_invalid')
        self.assertEqual(self.gate_call_count(),0)

    async def test_undeclared_arithmetic_is_not_marked_verified_and_intermediate_result_need_not_be_sent(self):
        for text in (answer('这轮没有声明算式。'),self.with_calculations(answer('没有声明算式。'),[])):
            revision=self.revision();revision.inspect(answer(checked=False))
            self.assertIsNone(revision.inspect(text))
            self.assertEqual(self.audits('decision')[-1]['calculation_checks'],[])
        revision=self.revision();revision.inspect(answer(checked=False))
        text=self.with_calculations(answer('这里只说明一种计算思路，不列中间结果。'),
            [{'expression':'2+2','result':'4','unit':'未经验证的单位标签'}])
        self.assertIsNone(revision.inspect(text))
        row=self.audits('decision')[-1]['calculation_checks'][0]
        self.assertTrue(row['equal']);self.assertFalse(row['result_in_body'])
        self.assertEqual(row['scope'],'arithmetic_only_not_premises_or_units')
        self.assertEqual(self.gate_call_count(),0)

    async def test_local_arithmetic_cannot_fake_an_external_claim_source(self):
        self.revision()
        for url,expected in (('', 'content_check_missing_or_invalid'),
                             ('https://example.org/not-retrieved','claim_quote_not_in_retrieved_evidence')):
            lines=self.with_calculations(answer('外部样本共有4个。'),[{'expression':'2+2','result':'4'}]).splitlines()
            meta=json.loads(lines[1][len('[PHASE3_TOPIC:'):-1])
            meta['kind']='external_fact';meta['source_refs']=[url] if url else []
            meta['content_check']['claims']=[{'claim':'样本数量','url':url,'quote':'2+2=4','body_quote':'共有4个'}]
            lines[1]='[PHASE3_TOPIC:'+json.dumps(meta,ensure_ascii=False)+']'
            self.assertEqual(review_reason('\n'.join(lines),self.state,'eval',self.scope),expected)
        self.assertEqual(self.gate_call_count(),0)

    async def test_rejected_revision_audit_does_not_reuse_previous_body_or_calculation_binding(self):
        revision=self.revision();url,_=self.source_receipt()
        revision.inspect(answer(checked=False))
        wrong=self.with_calculations(self.sourced_answer('报警率为26%，另一个合成算式结果为2。',url,'不存在的原文'),
            [{'expression':'1+1','result':'2'}])
        revision.inspect(wrong)
        previous=self.audits('decision')[-1]
        self.assertIsNotNone(previous['body_binding']);self.assertTrue(previous['calculation_checks'])
        leaked=answer('另一个正文。\n{"content_check":{"novelty":"new"}}')
        with self.assertRaisesRegex(OpportunityStopped,'internal_audit_json_in_body'):
            revision.inspect(leaked)
        final=self.audits('decision')[-1]
        self.assertEqual(final['visible_draft'],leaked)
        for name in ('body_binding','claim_bindings','calculation_checks'):self.assertIsNone(final[name])
        self.assertNotIn('content_check_feedback',self.scope)


    async def test_final_body_check_keeps_same_runner_tools_and_confirmed_history(self):
        revision=self.revision()
        confirmed='已确认的旧观点：我喜欢地图留白。'
        self.scope['confirmed_active_history']=[confirmed]
        draft=answer('先写一条尚未发送的新草稿。',checked=False)
        reviewed=answer('我更喜欢让玩家自己决定路线的地图。')
        final=answer('我更喜欢让路线选择产生真实后果的地图。')
        runner=self.advancing_runner([draft,reviewed,final]);provider=runner.provider
        tools=object();runner.req.func_tool=tools
        tool_messages=[{'role':'assistant','content':'','tool_calls':[{
            'id':'known-tool-call','type':'function','function':{'name':'fetch','arguments':'{}'}}]},
            {'role':'tool','tool_call_id':'known-tool-call','content':'Synthetic tool evidence retained.'}]
        runner.run_context.messages.extend(tool_messages)
        self.install(runner,revision=revision.inspect)
        result=await self.collect(runner)
        self.assertEqual([r.completion_text for r in result],[final])
        self.assertIs(runner.provider,provider);self.assertIs(runner.req.func_tool,tools)
        self.assertEqual(runner.http_requests,3);self.assertEqual(len(self.calls()),3)
        self.assertEqual(self.state.summary()['usage']['total_tokens'],60)
        requests=self.audits('request');last=requests[-1]['messages']
        self.assertEqual(last[2:4],tool_messages)
        self.assertEqual(last[-4]['content'],draft);self.assertEqual(last[-2]['content'],reviewed)
        self.assertIn('PHASE3_FINAL_BODY_CHECK_V1',last[-1]['content'])
        self.assertIn(confirmed,last[-1]['content'])
        self.assertEqual(self.scope['confirmed_active_history'],[confirmed])
        audits=self.audits('decision')
        self.assertEqual([a['reason'] for a in audits],
            ['review_before_delivery','final_body_check_before_delivery','allowed'])
        self.assertEqual(audits[-1]['semantic_fact_verification'],'model_judgement_not_proof')

    async def test_final_body_check_may_choose_skip_without_forcing_send(self):
        revision=self.revision();self.scope['confirmed_active_history']=['旧的已确认观点。']
        runner=self.advancing_runner([answer(checked=False),answer(),'[PHASE3_PROACTIVE:skip]'])
        self.install(runner,revision=revision.inspect)
        result=await self.collect(runner)
        self.assertEqual([r.completion_text for r in result],['[PHASE3_PROACTIVE:skip]'])
        self.assertEqual(runner.http_requests,3);self.assertEqual(revision.revisions,2)
        self.assertEqual(self.scope['last_content_decision']['decision'],'skip')
        self.assertEqual(self.scope['content_review_reason'],'allowed')

    async def test_final_body_check_failure_never_requests_a_fourth_response(self):
        for kind in ('internal_json','source_quote'):
            with self.subTest(kind=kind):
                revision=self.revision();url,quote=self.source_receipt()
                reviewed=self.sourced_answer('报警率为26%。',url,quote)
                if kind=='internal_json':
                    final=self.sourced_answer('报警率为26%。\n{"content_check":{"novelty":"new"}}',url,quote)
                    reason='internal_audit_json_in_body'
                else:
                    final=self.sourced_answer('报警率为26%。',url,'没有取得的来源引文')
                    reason='claim_quote_not_in_retrieved_evidence'
                runner=self.advancing_runner([answer(checked=False),reviewed,final,reviewed])
                self.install(runner,revision=revision.inspect);delivered=[]
                before=len(self.audits('request'))
                with self.assertRaisesRegex(OpportunityStopped,reason):
                    async for item in runner._iter_llm_responses():delivered.append(item)
                self.assertFalse(delivered);self.assertEqual(runner.http_requests,3)
                self.assertEqual(len(self.audits('request'))-before,3)
                self.assertEqual(len(runner.responses),1)
                self.assertTrue(self.scope['content_review_rejected'])

    async def test_final_body_check_obeys_existing_model_budget(self):
        revision=self.revision();self.scope['confirmed_active_history']=['旧的已确认观点。']
        self.state.model_limit=2
        runner=self.advancing_runner([answer(checked=False),answer(),answer()])
        self.install(runner,revision=revision.inspect);delivered=[]
        with self.assertRaisesRegex(OpportunityStopped,'model_request_budget'):
            async for item in runner._iter_llm_responses():delivered.append(item)
        self.assertFalse(delivered);self.assertEqual(runner.http_requests,2)
        self.assertEqual(len(self.calls()),2)
        self.assertEqual(len(self.audits('request')),2)
