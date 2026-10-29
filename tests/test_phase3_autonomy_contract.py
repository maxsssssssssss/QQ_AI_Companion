"""Control grammar and actual-content dedup, synthetic fixtures only."""
import json
import unittest

from integrations.phase3_runtime.autonomy_contract import parse, PROMPT, content_identity, repeated_content


def output(decision='send',body='Synthetic specific thought',**metadata):
    meta={'topic':'A concrete question','kind':'opinion','progress':'','pocket_id':'',**metadata}
    return '[PHASE3_PROACTIVE:'+decision+']\n[PHASE3_TOPIC:'+json.dumps(meta,ensure_ascii=False)+']\n'+body


class AutonomyContractTests(unittest.TestCase):
    def test_full_send_keeps_native_marker_and_all_visible_body(self):
        body='第一点想到的是这个。\n<<PRIVATE_COMPANION_SPLIT>>\n第二点是独立的具体想法。'
        result=parse(output(body=body,kind='hypothesis'))
        self.assertEqual(result['decision'],'send');self.assertEqual(result['text'],body)
        self.assertEqual(result['kind'],'hypothesis');self.assertEqual(result['topic'],'A concrete question')

    def test_positive_variable_defer_has_no_fixed_240_or_60_minimum(self):
        for minutes in (1,5,45,239,241,9999):
            with self.subTest(minutes=minutes):
                result=parse(output('defer:'+str(minutes),body=''))
                self.assertEqual(result['decision'],'defer');self.assertEqual(result['delay_minutes'],minutes)
                self.assertEqual(result['text'],'')
        self.assertNotIn('defer:240',PROMPT)

    def test_invalid_control_never_turns_into_visible_body(self):
        controls=['defer:0','defer','defer:-1','defer:1.5','defer:10000','send:5','skip:5','SEND','unknown']
        for control in controls:
            with self.subTest(control=control):
                result=parse(output(control))
                self.assertEqual(result['reason'],'invalid_control');self.assertEqual(result['text'],'')

    def test_missing_control_and_unsupported_metadata_cannot_leak_json(self):
        for text in ('ordinary unstructured reply','',output(kind='user_fact'),
                     output(topic=42),output(progress=['text']),output(pocket_id={'id':1}),
                     '[PHASE3_PROACTIVE:send]\n[PHASE3_TOPIC:{bad json}]\nVisible'):
            with self.subTest(text=text[:50]):
                result=parse(text);self.assertEqual(result['reason'],'invalid_control');self.assertEqual(result['text'],'')

    def test_embedded_control_or_interest_cannot_leak_to_guard(self):
        for body in ('A [PHASE3_PROACTIVE:send] B','A [PHASE3_TOPIC:{}] B','[PHASE3_INTEREST]\nHello'):
            with self.subTest(body=body):self.assertEqual(parse(output(body=body))['reason'],'invalid_control')

    def test_skip_and_defer_never_carry_chat_body_but_preserve_research(self):
        for decision in ('skip','defer:12'):
            result=parse(output(decision,body='Must not send this',progress='Unshared thought to verify'))
            self.assertEqual(result['text'],'');self.assertEqual(result['progress'],'Unshared thought to verify')
            self.assertEqual(result['raw_control'],'[PHASE3_PROACTIVE:'+decision+']')

    def test_no_external_fact_verification_is_fabricated_by_parser(self):
        result=parse(output(kind='external_fact'))
        self.assertNotIn('verified',result);self.assertNotIn('source_verified',result)
        self.assertEqual(result['kind'],'external_fact')

    def test_explicit_source_refs_remain_exact_and_are_not_truth_labels(self):
        refs=['https://science.nasa.gov/titan/', 'https://www.esa.int/Science_Exploration/Space_Science']
        result=parse(output(kind='external_fact',source_refs=refs))
        self.assertEqual(result['source_refs'],refs)
        self.assertNotIn('verified',result)
        self.assertEqual(parse(output(kind='opinion'))['source_refs'],[])

    def test_general_factual_and_text_only_contract_is_not_case_specific(self):
        for boundary in ('Fetch要等待Search返回后','包括查询参数','网页文字，不是图片或视频',
                         '不能靠标成opinion或observation免除事实依据','事实与比喻分开表达',
                         '第一个气泡就给出具体内容','不编造自己实际玩游戏'):
            self.assertIn(boundary,PROMPT)
        for case_specific in ('土卫六','金鱼','无人深空','不可压缩'):
            self.assertNotIn(case_specific,PROMPT)

    def test_only_missing_topic_wrapper_bracket_is_accepted(self):
        body='第一段原文。\n<<PRIVATE_COMPANION_SPLIT>>\n第二段原文。'
        original=output(body=body,source_refs=['https://example.com/a?keep=1'])
        lines=original.splitlines();lines[1]=lines[1][:-1]
        result=parse('\n'.join(lines))
        self.assertEqual(result['reason'],'model_decision')
        self.assertEqual(result['decision'],'send')
        self.assertEqual(result['text'],body)
        self.assertEqual(result['source_refs'],['https://example.com/a?keep=1'])
        self.assertEqual(result['metadata_repair'],'missing_topic_bracket')
        self.assertEqual(parse(original)['metadata_repair'],'')
        for control in ('skip','defer:7'):
            text=output(control,body='',progress='完整保留的未分享进度')
            result=parse(text[:-1] if text.endswith(']') else text.replace('}]\n','}\n'))
            self.assertEqual(result['decision'],control.split(':')[0])
            self.assertEqual(result['progress'],'完整保留的未分享进度')

    def test_wrapper_tolerance_never_repairs_invalid_json_primary_control_or_fields(self):
        meta='{"topic":"A","kind":"opinion","source_refs":[]}'
        for line in (meta+' trailing',meta+']]',meta[:-1],meta.replace('"opinion"','"user_fact"'),
                     meta.replace('"A"','42'),'{"topic":"A",}', '[{"topic":"A"}]'):
            raw='[PHASE3_PROACTIVE:send]\n[PHASE3_TOPIC:'+line+'\nSafe visible body'
            with self.subTest(line=line):
                result=parse(raw)
                self.assertEqual(result['reason'],'invalid_control')
                self.assertEqual(result['text'],'')
        self.assertEqual(parse('[PHASE3_PROACTIVE:send\n[PHASE3_TOPIC:'+meta+'\nVisible')['reason'],'invalid_control')

    def test_malformed_source_references_do_not_become_visible_output(self):
        for refs in ('https://example.com', [True], ['https://example.com']*2,
                     ['file:///etc/passwd'], ['https://name:secret@example.com'],
                     ['https://example.com/#private'], ['https://example.com/a b'],
                     [f'https://example.com/{i}' for i in range(13)]):
            with self.subTest(refs=refs):
                result=parse(output(kind='external_fact',source_refs=refs))
                self.assertEqual(result['reason'],'invalid_control')
                self.assertEqual(result['text'],'')

    def test_empty_send_is_invalid_even_with_valid_metadata(self):
        self.assertEqual(parse(output(body=' \n'))['reason'],'invalid_control')

    def test_same_content_identity_ignores_cosmetic_whitespace_and_punctuation(self):
        self.assertEqual(content_identity('同一个具体想法。'),content_identity('同一个 具体想法！'))
        self.assertEqual(content_identity('A Thought!'),content_identity('a thought'))

    def test_same_domain_new_content_is_allowed_but_actual_repetition_is_blocked(self):
        previous=['这个游戏里最有趣的是把建筑的负空间也当成资源来设计。']
        self.assertTrue(repeated_content(previous[0],previous))
        self.assertFalse(repeated_content('这个游戏的语言系统让我想到不同文明怎么给颜色命名。',previous))
        self.assertFalse(repeated_content('突然想到，假如引力每天换一个方向，城市会长成什么样？',previous))

    def test_near_literal_replay_and_empty_output_are_blocked_without_domain_bans(self):
        old='这部游戏让我想到了一个很具体的空间设计问题，为什么走廊转角总比直道更容易让人迷路呢'
        self.assertTrue(repeated_content(old+'？',[old+'。']))
        self.assertTrue(repeated_content('...',['Something']))
        self.assertFalse(repeated_content('哈？',['真的？']))


if __name__=='__main__':unittest.main()
