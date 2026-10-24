"""Synthetic T1 semantic/parser and durable partial-send contracts; no networks."""
import asyncio
from dataclasses import FrozenInstanceError, asdict, replace
import hashlib
import importlib.util
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from integrations.phase1_guard.bubbles import (MARKER, PROMPT, Pacing, semantic_plan, semantic_text,
    send_sequence, plain_qq_text, candidate_boundaries, map_candidate_boundaries)
from integrations.phase1_guard.delivery import DeliveryService, DeliveryReceipt
from integrations.phase1_guard.store import GuardStore

ROOT=Path(__file__).resolve().parents[1]
package=types.ModuleType('bubble_pinned_private');package.__path__=[str(ROOT/'.runtime/upstream/private_companion')]
sys.modules[package.__name__]=package
spec=importlib.util.spec_from_file_location('bubble_pinned_private.segmented_message',ROOT/'.runtime/upstream/private_companion/segmented_message.py')
parser=importlib.util.module_from_spec(spec);sys.modules[spec.name]=parser;spec.loader.exec_module(parser)


class PlainQQTests(unittest.TestCase):
    def test_headings_bold_links_and_emoji_are_plain(self):
        self.assertEqual(plain_qq_text('## 价格\n**输入**：2元 🙂\n[官网](https://example.org/pricing/)'),
            '价格\n输入：2元 🙂\n官网（https://example.org/pricing/）')

    def test_code_body_math_and_literal_underscores_preserved(self):
        self.assertEqual(plain_qq_text('`__init__` 与 `**literal**`\n```python\nx = 2 ** 3\n# code comment\n```'),
            '__init__ 与 **literal**\nx = 2 ** 3\n# code comment')

    def test_table_cells_remain_in_one_answer(self):
        self.assertEqual(plain_qq_text('| 字段 | 数值 |\n| :--- | ---: |\n| **输入** | 2 |'),
            '| 字段 | 数值 |\n| 输入 | 2 |')

    def test_disable_and_oversize_fall_back_without_truncation(self):
        self.assertEqual(plain_qq_text('**原文**',enabled=False),'**原文**')
        text='**'+('大'*32768)+'**';self.assertIs(plain_qq_text(text),text)

    def test_empty_decoration_and_malformed_markup_do_not_discard(self):
        for text in ('```','\n```\n```\n','**','[broken](','*unterminated'):
            self.assertEqual(plain_qq_text(text),text)

    def test_no_catastrophic_scan_on_bounded_malformed_input(self):
        import time
        started=time.monotonic()
        for text in ('['*32768,'*'*32768,('`['*16384),'a'*32000+'**'):
            self.assertTrue(plain_qq_text(text))
        self.assertLess(time.monotonic()-started,2)


class SemanticTests(unittest.TestCase):
    def test_daily_and_model_chooses_one(self):
        for text in ['嗯，慢慢来。','修好就好。\n今天可以歇一歇了。','技术解释。'*600]:
            self.assertEqual(semantic_text(parser,text),[text])

    def test_two_three_four_without_mechanical_sentence_split(self):
        for count in [2,3,4]:
            parts=['好耶。可算结束了！','这下轻松一点啦。','要歇会儿吗？','那我陪你。'][:count]
            self.assertEqual(semantic_text(parser,('\n'+MARKER+'\n').join(parts)),parts)

    def test_fence_table_are_one_and_reserved_tokens_do_not_leak(self):
        for text in [f'解释\n```text\n{MARKER}\n```\n继续',f'| A | B |\n| - | - |\n| {MARKER} | x |',f'~~~\n{MARKER}\n~~~']:
            out=semantic_text(parser,text)
            self.assertEqual(len(out),1)
            self.assertNotIn(MARKER,out[0])

    def test_emoji_bilingual_stay_in_their_semantic_bubble(self):
        text='修好了 🎉 nice！\n'+MARKER+'\n今晚可以 chill 一下。'
        self.assertEqual(semantic_text(parser,text),['修好了 🎉 nice！','今晚可以 chill 一下。'])

    def test_disable_retains_one_clean_reply(self):
        result=semantic_text(parser,'甲\n'+MARKER+'\n乙',enabled=False)
        self.assertEqual(len(result),1);self.assertNotIn(MARKER,result[0])

    def test_excess_parts_fail_safe_to_one(self):
        out=semantic_text(parser,('\n'+MARKER+'\n').join(['内容']*5))
        self.assertEqual(len(out),1);self.assertNotIn(MARKER,out[0])

    def test_parser_exception_keeps_useful_long_answer_in_one_clean_bubble(self):
        class Broken:
            def parse_llm_segment_control(self,text):raise RuntimeError('synthetic_parser_failure')
        body='技术说明。'*800
        result=semantic_text(Broken(),'先说明\n'+MARKER+'\n'+body)
        self.assertEqual(result,['先说明\n\n'+body])
        self.assertEqual(result[0].count('技术说明。'),800)

    def test_missing_parser_removes_reserved_escaped_variants_but_keeps_content(self):
        for token in (MARKER,MARKER.lower(),'&lt;&lt;PRIVATE_COMPANION_SPLIT&gt;&gt;',
                      '&#60;&#60;PRIVATE_COMPANION_SPLIT&#62;&#62;',
                      r'\<\<PRIVATE_COMPANION_SPLIT\>\>','{{split_marker}}'):
            with self.subTest(token=token):
                self.assertEqual(semantic_text(None,'甲\n'+token+'\n乙'),['甲\n\n乙'])

    def test_parser_empty_or_invalid_output_does_not_swallow_original(self):
        for value in ('',None):
            fake=types.SimpleNamespace(parse_llm_segment_control=lambda text:types.SimpleNamespace(
                controlled=False,sanitized_text=value))
            self.assertEqual(semantic_text(fake,'有效正文'),['有效正文'])

    def test_format_fallback_keeps_code_emoji_and_urls(self):
        text='正文 🙂\n```python\nx = 2 ** 3\n```\nhttps://example.invalid/a?x=1&y=2'
        self.assertEqual(semantic_text(None,text),[text])

    def test_pacing_validated_and_bounded(self):
        p=Pacing()
        self.assertEqual((p.minimum,p.maximum),(1.0,2.0))
        self.assertGreaterEqual(p.delay('短'),1.0);self.assertEqual(p.delay('长'*5000),2.0)
        for args in [(-1,1),(1,.5),(0,4),(float('nan'),2),(0,float('inf'))]:
            with self.assertRaises(ValueError):Pacing(*args)

    def test_prompt_preserves_persona_one_generation_no_forced_question(self):
        for text in ['遵循当前已加载的人格设定','简单回应可以只有一条','不意味着每轮都需要追问',
                     '不要机械拆句、重复内容、凑数量','最多 4 个','工具参数不得','所有气泡仍由同一次模型生成']:
            self.assertIn(text,PROMPT)


class SmalltalkFallbackTests(unittest.TestCase):
    def test_eligible_pair_keeps_original_exactly_and_reports_native_single(self):
        text='今天可算忙完了！今晚可以安心歇一会儿了。'
        plan=semantic_plan(parser,text,fallback_enabled=True)
        self.assertEqual(plan.texts,('今天可算忙完了！','今晚可以安心歇一会儿了。'))
        self.assertEqual(''.join(plan.texts),text)
        self.assertEqual((plan.source,plan.native_count,plan.native_controlled),
                         ('smalltalk_fallback',1,False))
        self.assertFalse(plan.raw_marker_present)
        self.assertEqual(plan.raw_exact_marker_count,0)
        self.assertEqual(plan.diagnostics(),{
            'source':'smalltalk_fallback','reason':'bounded_complete_sentences',
            'raw_marker_present':False,'raw_exact_marker_count':0,
            'native_count':1,'native_controlled':False,'planned_count':2})
        self.assertNotIn('texts',plan.diagnostics())

    def test_model_markers_have_priority_even_if_heuristic_context_excluded(self):
        text='甲\n'+MARKER+'\n乙\n'+MARKER+'\n丙'
        plan=semantic_plan(parser,text,fallback_enabled=True,allow_fallback=False)
        self.assertEqual(plan.texts,('甲','乙','丙'))
        self.assertEqual((plan.source,plan.native_count,plan.raw_exact_marker_count),
                         ('model_markers',3,2))
        self.assertTrue(plan.native_controlled)

    def test_fallback_can_be_disabled_without_changing_native_protocol(self):
        text='哈哈，终于收工了！今晚可以好好休息啦。'
        for kwargs in ({},{'fallback_enabled':False},
                       {'fallback_enabled':True,'enabled':False},
                       {'fallback_enabled':True,'allow_fallback':False}):
            with self.subTest(kwargs=kwargs):self.assertEqual(semantic_text(parser,text,**kwargs),[text])

    def test_short_and_continuous_long_sentence_stay_single(self):
        for text in ('好呀。','嗯，收到啦 🙂','哈哈，今晚吃点好吃的，吃完就放松一下，想躺就躺着。',
                     '哈哈，'+('我慢慢听你说，'*35)+'说完就歇一会儿吧。'):
            with self.subTest(text=text[:12]):
                self.assertEqual(semantic_text(parser,text,fallback_enabled=True),[text])

    def test_no_split_at_comma_newline_or_fixed_length(self):
        for text in ('哈哈，终于好了，今晚可以歇歇啦','今天终于忙完啦\n今晚可以安心歇一会儿了。',
                     '终于'+('很开心'*90)+'！今天可以轻松一点了。'):
            with self.subTest(text=text[:12]):
                self.assertEqual(semantic_text(parser,text,fallback_enabled=True),[text])

    def test_technical_code_table_link_quote_and_list_are_single(self):
        texts=[
            '今天可算忙完啦！数据库事务必须保持原子性。',
            '今天可算忙完啦！首先介绍原理，再解释步骤。',
            '今天可算忙完啦！`print("hello")` 会输出文本。',
            '今天可算忙完啦！\n```sql\nSELECT 1;\n```',
            '今天可算忙完啦！| 字段 | 值 |',
            '今天可算忙完啦！官网在 https://example.org/a。',
            '今天可算忙完啦！官网 example.org 可以打开。',
            '今天可算忙完啦！他说“这句话不要拆开”。',
            "今天可算忙完啦！他说'这句话不要拆开'。",
            '今天可算忙完啦！> 这是引用正文。',
            '今天可算忙完啦！1. 确认输入 2. 保存结果。',
            '今天可算忙完啦！- 第一件事 - 第二件事。',
            '今天可算忙完啦！① 确认输入 ② 保存结果。',
            '今天真的**特别开心**！今晚可以安心歇一会儿了。',
            '今天真的__特别开心__！今晚可以安心歇一会儿了。',
        ]
        for text in texts:
            with self.subTest(text=text):
                self.assertEqual(semantic_text(parser,text,fallback_enabled=True),[text])

    def test_whitespace_emoji_and_bilingual_content_not_deleted_or_duplicated(self):
        text='  哈哈，nice！🎉  今晚可以 chill 一下。  '
        plan=semantic_plan(parser,text,fallback_enabled=True)
        self.assertEqual(plan.texts,('  哈哈，nice！🎉  ','今晚可以 chill 一下。  '))
        self.assertEqual(''.join(plan.texts),text)

    def test_marker_mentions_and_damaged_markers_never_enter_heuristic(self):
        for token in (MARKER,'<<PRIVATE_COMPANION_SPLIT>',
                      '&lt;&lt;PRIVATE_COMPANION_SPLIT&gt;&gt;','{{split_marker}}'):
            text='哈哈，忙完啦！'+token+'今晚可以歇一下。'
            with self.subTest(token=token):
                plan=semantic_plan(parser,text,fallback_enabled=True)
                self.assertEqual(len(plan.texts),1)
                self.assertTrue(plan.raw_marker_present)
                self.assertNotIn('PRIVATE_COMPANION_SPLIT',plan.texts[0])
                self.assertNotEqual(plan.source,'smalltalk_fallback')
                self.assertIn('哈哈，忙完啦！',plan.texts[0])
                self.assertIn('今晚可以歇一下。',plan.texts[0])

    def test_parser_or_heuristic_exception_preserves_one_readable_original(self):
        text='  终于忙完啦！今晚可以安心歇一会儿了。  '
        self.assertEqual(semantic_text(None,text,fallback_enabled=True),[text])
        with patch('integrations.phase1_guard.bubbles._smalltalk_parts',side_effect=ValueError('synthetic')):
            plan=semantic_plan(parser,text,fallback_enabled=True)
        self.assertEqual(plan.texts,(text,))
        self.assertEqual(plan.reason,'parser_or_fallback_error')

    def test_two_three_four_complete_sentences_without_opening_cue(self):
        sentences=['今天终于忙完啦！','今晚可以歇一会儿啦。','明天再慢慢继续就好。',
                   '现在想聊点开心的呀？','也能一起看看窗外呀。']
        for count in (2,3,4):
            with self.subTest(count=count):
                text=''.join(sentences[:count])
                self.assertEqual(semantic_text(parser,text,fallback_enabled=True),sentences[:count])
        text=''.join(sentences)
        self.assertEqual(semantic_text(parser,text,fallback_enabled=True),[text])

    def test_search_context_remains_excluded(self):
        progress='好呀！我去看看官方说明。'
        self.assertEqual(semantic_text(parser,progress,fallback_enabled=True,allow_fallback=False),[progress])
        final='好呀！这是我查到的资料。'
        self.assertEqual(semantic_text(parser,final,fallback_enabled=True,allow_fallback=False),[final])

    def test_limit_is_100_unicode_codepoints_including_spaces_and_emoji(self):
        first='甲'*46+'🙂  。';second='乙'*49+'。'
        text=first+second
        self.assertEqual(len(text),100)
        self.assertEqual(semantic_text(parser,text,fallback_enabled=True),[first,second])
        text=first+second[:-1]+'🙂。'
        self.assertEqual(len(text),101)
        self.assertEqual(semantic_text(parser,text,fallback_enabled=True),[text])

    def test_short_sentence_merges_forward_and_last_short_merges_backward(self):
        first='今天终于忙完啦！';last='今晚可以歇一会儿啦。'
        cases=[(['好耶！',first,last],['好耶！'+first,last]),
               ([first,'嗯。',last],[first,'嗯。'+last]),
               ([first,last,'好呀。'],[first,last+'好呀。']),
               (['好耶！','太开心了！',last],['好耶！太开心了！',last]),
               (['今天好了！','明天继续！','今晚歇歇！','慢慢来呀！'],
                ['今天好了！明天继续！','今晚歇歇！慢慢来呀！'])]
        for original,expected in cases:
            text=''.join(original)
            with self.subTest(text=text):
                actual=semantic_text(parser,text,fallback_enabled=True)
                self.assertEqual(actual,expected)
                self.assertEqual(''.join(actual),text)

    def test_short_merges_with_only_one_result_keep_original_single(self):
        for text in ('终于忙完啦！今晚可以安心歇一会儿了。','好呀！行呀！嗯嗯！晚安！',
                     '好耶！开心！今晚可以歇一会儿了。'):
            with self.subTest(text=text):
                self.assertEqual(semantic_text(parser,text,fallback_enabled=True),[text])

    def test_effective_length_counts_letters_numbers_not_punctuation_or_emoji(self):
        last='今晚可以歇一会儿啦。'
        for first in ('今天可以歇啦！','I feel good！','今天 123 呀！'):
            with self.subTest(first=first):
                self.assertEqual(semantic_text(parser,first+last,fallback_enabled=True),[first,last])
        for short in ('终于忙完啦！！！🥳','五个字数啊1️⃣！','五个字数啊👩🏽‍💻！'):
            with self.subTest(short=short):
                self.assertEqual(semantic_text(parser,short+last,fallback_enabled=True),[short+last])

    def test_unfinished_tail_or_ascii_period_not_a_complete_sentence(self):
        for text in ('今天可算忙完了！今晚可以歇一会儿啦',
                     '今天可算忙完了！今晚可以歇一会儿啦🙂  ',
                     'Today was lovely. Tomorrow will be lovely too.'):
            with self.subTest(text=text):
                self.assertEqual(semantic_text(parser,text,fallback_enabled=True),[text])

    def test_punctuation_runs_and_emoji_clusters_preserve_every_character(self):
        first='  今天👩🏽‍💻忙完好开心！？!!🎉🇨🇳  \r\n'
        second='今晚可以 chill 一下。❤️1️⃣  '
        text=first+second
        plan=semantic_plan(parser,text,fallback_enabled=True)
        self.assertEqual(plan.texts,(first,second))
        self.assertEqual(''.join(plan.texts),text)
        self.assertLessEqual(len(text),100)

    def test_single_fallback_and_disabled_preserve_crlf_and_outer_spaces(self):
        text='  今天终于忙完啦\r\n可以轻松一点啦。  '
        for kwargs in ({'fallback_enabled':True},{'fallback_enabled':False},
                       {'fallback_enabled':True,'allow_fallback':False},
                       {'fallback_enabled':True,'enabled':False}):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(semantic_text(parser,text,**kwargs),[text])

    def test_proactive_text_uses_same_plan_without_a_second_protocol(self):
        text='嗨，今天忙完啦？想听听你今天过得怎么样。'
        self.assertEqual(semantic_text(parser,text,fallback_enabled=True),
                         ['嗨，今天忙完啦？','想听听你今天过得怎么样。'])


class CandidateBoundaryTests(unittest.TestCase):
    sentences=('今天可算忙完了！','今晚可以安心歇一会儿了。',
               '明天再慢慢继续就好。','现在想聊点开心的呀。')

    def test_exact_and_terminal_period_mapping_for_two_three_four_bubbles(self):
        for count in (2,3,4):
            parts=self.sentences[:count];raw=''.join(parts)
            with self.subTest(count=count):
                candidate=candidate_boundaries(raw)
                self.assertIsNotNone(candidate)
                self.assertEqual(map_candidate_boundaries(candidate,raw),parts)
                final=raw[:-1]
                mapped=map_candidate_boundaries(candidate,final)
                self.assertEqual(mapped,parts[:-1]+(parts[-1][:-1],))
                self.assertEqual(''.join(mapped),final)
                self.assertEqual(candidate.raw_end_offsets[-1],len(raw))
                self.assertEqual(candidate.terminal_period_removed_end_offsets[-1],len(final))
                self.assertEqual(candidate.raw_end_offsets[:-1],candidate.terminal_period_removed_end_offsets[:-1])

    def test_terminal_period_run_removal_keeps_spaces_crlf_and_emoji_unchanged(self):
        first='  今天可算忙完了！🎉  \r\n'
        last='今晚可以安心歇一会儿了。。。 \t\r\n'
        raw=first+last;candidate=candidate_boundaries(raw)
        final=first+'今晚可以安心歇一会儿了 \t\r\n'
        self.assertEqual(map_candidate_boundaries(candidate,raw),(first,last))
        parts=map_candidate_boundaries(candidate,final)
        self.assertEqual(parts,(first,'今晚可以安心歇一会儿了 \t\r\n'))
        self.assertEqual(''.join(parts),final)
        # Partial removal is not the known native transformation.
        self.assertIsNone(map_candidate_boundaries(candidate,raw.replace('。。。','。。')))

    def test_optional_variant_is_only_known_terminal_chinese_period_subset(self):
        for suffix in ('！','？','。❤️','。🎉  '):
            raw=self.sentences[0]+self.sentences[1][:-1]+suffix
            with self.subTest(suffix=suffix):
                candidate=candidate_boundaries(raw)
                self.assertIsNotNone(candidate)
                self.assertIsNone(candidate.terminal_period_removed_sha256)
                self.assertIsNone(candidate.terminal_period_removed_length)
                self.assertIsNone(candidate.terminal_period_removed_end_offsets)
                self.assertEqual(''.join(map_candidate_boundaries(candidate,raw)),raw)
                self.assertIsNone(map_candidate_boundaries(candidate,raw[:-1]))

    def test_any_other_body_change_or_empty_final_fails_closed(self):
        raw='  '+self.sentences[0]+'\r\n'+self.sentences[1]+'  '
        candidate=candidate_boundaries(raw)
        variants=(raw.strip(),raw.replace('\r\n','\n'),raw.replace('！','？',1),
                  raw.replace('可以','能够',1),raw+'！','',None)
        for final in variants:
            with self.subTest(final_type=type(final).__name__,final=final):
                self.assertIsNone(map_candidate_boundaries(candidate,final))
        # Removing an internal full stop cannot be confused with terminal cleanup.
        raw=''.join(self.sentences[:3]);candidate=candidate_boundaries(raw)
        self.assertIsNone(map_candidate_boundaries(candidate,raw.replace('。','',1)))

    def test_candidate_contains_only_immutable_hash_length_and_offset_metadata(self):
        raw=''.join(self.sentences[:2]);candidate=candidate_boundaries(raw)
        values=asdict(candidate)
        self.assertEqual(set(values),{'raw_sha256','raw_length','raw_end_offsets',
            'terminal_period_removed_sha256','terminal_period_removed_length',
            'terminal_period_removed_end_offsets'})
        self.assertEqual(candidate.raw_sha256,hashlib.sha256(raw.encode()).hexdigest())
        for value in values.values():
            if isinstance(value,str):self.assertRegex(value,r'^[0-9a-f]{64}$')
            elif isinstance(value,tuple):self.assertTrue(all(type(offset) is int for offset in value))
            else:self.assertTrue(type(value) is int or value is None)
        self.assertNotIn(raw,repr(candidate))
        self.assertFalse(hasattr(candidate,'__dict__'))
        with self.assertRaises(FrozenInstanceError):candidate.raw_length=0

    def test_mapping_does_not_rerun_eligibility_on_final_body(self):
        raw=''.join(self.sentences[:2]);candidate=candidate_boundaries(raw)
        with patch('integrations.phase1_guard.bubbles._smalltalk_parts',side_effect=AssertionError('must not rerun')):
            self.assertEqual(map_candidate_boundaries(candidate,raw),self.sentences[:2])
            final=raw[:-1]
            self.assertEqual(''.join(map_candidate_boundaries(candidate,final)),final)

    def test_marker_ineligible_or_formatting_cleaned_body_cannot_gain_a_candidate(self):
        marked='\n'+MARKER+'\n'
        for raw in (marked.join(self.sentences[:2]),self.sentences[0],
                    ''.join(self.sentences[:2])[:-1],'甲'*101,
                    '今天真的**特别开心**！今晚可以安心歇一会儿了。',
                    '今天真的__特别开心__！今晚可以安心歇一会儿了。'):
            with self.subTest(raw=raw):
                candidate=candidate_boundaries(raw)
                self.assertIsNone(candidate)
                self.assertIsNone(map_candidate_boundaries(candidate,plain_qq_text(raw)))

    def test_corrupt_metadata_and_helper_exceptions_return_no_mapping(self):
        raw=''.join(self.sentences[:2]);candidate=candidate_boundaries(raw)
        corrupt=(None,{},types.SimpleNamespace(**asdict(candidate)),
                 replace(candidate,raw_sha256='0'*64),
                 replace(candidate,raw_length=len(raw)+1),
                 replace(candidate,raw_end_offsets=(0,len(raw))),
                 replace(candidate,raw_end_offsets=(len(raw),)),
                 replace(candidate,raw_end_offsets=(len(raw),len(raw))),
                 replace(candidate,raw_end_offsets=(True,len(raw))),
                 replace(candidate,raw_end_offsets=[len(self.sentences[0]),len(raw)]),
                 replace(candidate,terminal_period_removed_length=None),
                 replace(candidate,terminal_period_removed_sha256='not-a-digest'),
                 replace(candidate,terminal_period_removed_end_offsets=(1,len(raw)-1)))
        for value in corrupt:
            with self.subTest(value=value):
                self.assertIsNone(map_candidate_boundaries(value,raw))
        with patch('integrations.phase1_guard.bubbles._smalltalk_parts',side_effect=ValueError('synthetic')):
            self.assertIsNone(candidate_boundaries(raw))
        self.assertIsNone(candidate_boundaries(None))


class BubbleLedgerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'guard.sqlite3'
        self.store=GuardStore(self.path)
        self.store.claim('test','test:FriendMessage:10001','m1','10001','合成输入',[])
        self.before=self.store.snapshot()
        self.store.enable_phase3();self.store.enable_bubbles()
        self.store.create_turn('t1','test','test:FriendMessage:10001',['m1'])
        self.service=DeliveryService(self.store,timeout=.03)
        self.current=True;self.clock=0;self.calls=[];self.mode='ack';self.stop_on_sleep=False
        self.texts=['已确认第一条','后续第二条','后续第三条']

    def tearDown(self):
        self.store.close();self.temp.cleanup()

    async def sleep(self,delay):
        self.clock+=delay
        if self.stop_on_sleep:self.current=False

    async def part(self,index,delivery_id):
        async def sender():
            self.calls.append((index,self.clock))
            if index==1:
                if self.mode=='reject':return DeliveryReceipt(False)
                if self.mode=='missing':return None
                if self.mode=='timeout':await asyncio.sleep(.2)
                if self.mode=='cancel':raise asyncio.CancelledError()
            return DeliveryReceipt(True,source='synthetic_ack')
        return await self.service.send(turn_id='t1',text=self.texts[index],chain=[{'type':'text'}],
            sender=sender,part_index=index,delivery_id=delivery_id,kind='normal',is_current=lambda:self.current)

    async def send(self):
        return await send_sequence(self.store,turn_id='t1',kind='normal',texts=self.texts,
            send_part=self.part,is_current=lambda:self.current,sleep=self.sleep)

    async def test_first_immediate_later_bounded_and_independent_ledger(self):
        out=await self.send()
        self.assertEqual(out['state'],'sent');self.assertEqual(out['sent_count'],3)
        self.assertEqual(self.calls[0],(0,0))
        for a,b in zip(self.calls,self.calls[1:]):self.assertTrue(1.0<=b[1]-a[1]<=2.0)
        self.assertEqual([r['transport_attempts'] for r in out['parts']],[1,1,1])
        self.assertTrue(all(r['finished_at'] for r in out['parts']))
        self.assertEqual(len({r['delivery_id'] for r in out['parts']}),3)
        self.assertEqual(self.store.source_ids('t1'),['m1'])
        self.assertEqual(self.store.snapshot(),self.before)

    async def test_second_failed_does_not_mark_third_sent(self):
        self.mode='reject';out=await self.send()
        self.assertEqual([r['state'] for r in out['parts']],['sent','failed','cancelled'])
        self.assertEqual(out['delivered_text'],self.texts[0]);self.assertEqual(len(self.calls),2)
        self.assertEqual(out['parts'][2]['transport_attempts'],0)

    async def test_unknown_never_retries_entire_group(self):
        self.mode='missing';out=await self.send();again=await self.send()
        self.assertEqual(out,again);self.assertEqual(out['state'],'unknown')
        self.assertEqual([r['state'] for r in out['parts']],['sent','unknown','cancelled'])
        self.assertEqual(len(self.calls),2)

    async def test_unknown_old_plan_does_not_block_a_new_turn(self):
        self.mode='missing';out=await self.send()
        self.assertEqual(out['state'],'unknown')
        self.store.claim('test','test:FriendMessage:10001','m2','10001','合法新输入',[])
        self.store.create_turn('t2','test','test:FriendMessage:10001',['m2'])
        async def send_new(index,delivery_id):
            async def ack():return DeliveryReceipt(True,source='synthetic_ack')
            return await self.service.send(turn_id='t2',text='新轮答复',chain=[{'type':'text'}],
                sender=ack,delivery_id=delivery_id,part_index=index)
        new=await send_sequence(self.store,turn_id='t2',kind='normal',texts=['新轮答复'],
            send_part=send_new,is_current=lambda:True,sleep=self.sleep)
        self.assertEqual(new['state'],'sent')
        self.assertEqual(self.store.bubble_result('t1','normal'),out)

    async def test_failure_after_ack_reconciles_only_confirmed_prefix(self):
        original=self.store.finish_bubble
        def fail_after_second_ack(turn_id,kind,index,state):
            if index==1:raise RuntimeError('synthetic_bookkeeping_failure')
            return original(turn_id,kind,index,state)
        self.store.finish_bubble=fail_after_second_ack
        with self.assertRaisesRegex(RuntimeError,'bookkeeping'):await self.send()
        result=self.store.bubble_result('t1','normal')
        self.assertEqual([part['state'] for part in result['parts']],['sent','sent','cancelled'])
        self.assertEqual(result['delivered_text'],'\n'.join(self.texts[:2]))
        self.assertEqual(len(self.calls),2)

    async def test_timeout_keeps_confirmed_text_only(self):
        self.mode='timeout';out=await self.send()
        self.assertEqual(out['state'],'unknown');self.assertEqual(out['delivered_text'],self.texts[0])

    async def test_interjection_during_interval_cancels_unsent(self):
        self.stop_on_sleep=True;out=await self.send()
        self.assertEqual([r['state'] for r in out['parts']],['sent','cancelled','cancelled'])
        self.assertEqual(len(self.calls),1);self.assertLessEqual(self.clock,.1)

    async def test_cancel_before_first_never_attempts(self):
        self.current=False;out=await self.send()
        self.assertEqual([r['state'] for r in out['parts']],['cancelled']*3)
        self.assertEqual(self.calls,[])

    async def test_task_cancellation_preserves_unknown_no_replay(self):
        self.mode='cancel'
        with self.assertRaises(asyncio.CancelledError):await self.send()
        out=await self.send()
        self.assertEqual([r['state'] for r in out['parts']],['sent','unknown','cancelled'])
        self.assertEqual(len(self.calls),2)

    async def test_restart_recovers_attempted_unknown_unattempted_cancelled(self):
        self.store.plan_bubbles('t1','normal',self.texts,['a','b','c'],'hash')
        self.store.begin_delivery('a','t1','normal',0,self.texts[0],[{'type':'text'}])
        self.store.mark_bubble_transport('a');self.store.close()
        self.store=GuardStore(self.path);self.store.enable_phase3();self.store.recover_phase3_interrupted()
        self.store.enable_bubbles();self.store.recover_bubbles()
        out=self.store.bubble_result('t1','normal')
        self.assertEqual([r['state'] for r in out['parts']],['unknown','cancelled','cancelled'])
        self.assertEqual(out['delivered_text'],'')
        with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')

    async def test_same_turn_duplicate_and_changed_content_cannot_resend(self):
        await self.send();await self.send();self.assertEqual(len(self.calls),3)
        self.texts[0]='内容被修改'
        with self.assertRaisesRegex(ValueError,'Collision'):await self.send()
        self.assertEqual(len(self.calls),3)

    async def test_progress_separate_from_final_group(self):
        self.assertTrue(self.store.claim_progress('t1'));self.assertFalse(self.store.claim_progress('t1'))
        async def ack():return DeliveryReceipt(True)
        await self.service.send(turn_id='t1',text='我看看',chain=[{'type':'text'}],sender=ack,kind='progress')
        await self.send()
        rows=self.store.phase3_snapshot()['deliveries']
        self.assertEqual(len(rows),4);self.assertEqual([r['kind'] for r in rows],['progress']+['normal']*3)

    def test_migration_retains_snapshot_no_original_archive_deletion(self):
        self.assertEqual(self.store.snapshot(),self.before)
        backup=self.path.with_name(self.path.name+'.pre-multibubble.sqlite3')
        self.assertTrue(backup.exists())
        with sqlite3.connect(backup) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(db.execute('SELECT count(*) FROM inbound').fetchone()[0],1)
