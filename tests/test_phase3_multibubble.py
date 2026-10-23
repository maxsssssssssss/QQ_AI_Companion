"""Synthetic T1 semantic/parser and durable partial-send contracts; no networks."""
import asyncio
import importlib.util
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest

from integrations.phase1_guard.bubbles import MARKER, PROMPT, Pacing, semantic_text, send_sequence, plain_qq_text
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
        self.assertGreaterEqual(p.delay('短'),.6);self.assertEqual(p.delay('长'*5000),1.8)
        for args in [(-1,1),(1,.5),(0,4),(float('nan'),2),(0,float('inf'))]:
            with self.assertRaises(ValueError):Pacing(*args)

    def test_prompt_preserves_persona_one_generation_no_forced_question(self):
        for text in ['V0.1','只发一条','不必结尾追问','不凑数量','最多四个','工具参数不得','所有气泡一次生成']:
            self.assertIn(text,PROMPT)


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
        for a,b in zip(self.calls,self.calls[1:]):self.assertTrue(.6<=b[1]-a[1]<=1.8)
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
