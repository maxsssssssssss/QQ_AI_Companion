"""T2 component checks of the fixed Private 6.7.0 proactive prompt builder.

Run with the pinned Phase 3 venv, PYTHONPATH=.runtime/upstream/AstrBot:.,
and an isolated ASTRBOT_ROOT. Conversation I/O is a synthetic in-memory
fixture; native parsing, clipping, formatting and prompt construction execute
unchanged. No QQ/API call or production/private data is used.
"""
from copy import deepcopy
from datetime import datetime, timedelta
import importlib
import inspect
import json
from pathlib import Path
import re
import sys
from types import ModuleType, SimpleNamespace
import unittest
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
try:
    package = ModuleType('phase3_native_context_fixture')
    package.__path__ = [str(ROOT / '.runtime/upstream/private_companion')]
    sys.modules[package.__name__] = package
    message = importlib.import_module(package.__name__ + '.proactive_message')
    daily = importlib.import_module(package.__name__ + '.daily_state')
    NATIVE = True
except ImportError:
    NATIVE = False


class HistoryHost:
    """Only unrelated persona/state I/O is replaced, never history processing."""
    def __init__(self, records):
        self.records = deepcopy(records)
        self.conversation = SimpleNamespace(history=json.dumps(records, ensure_ascii=False))
        self.data = {}
        self.user = {'user_id': '10001', 'umo': 'synthetic:FriendMessage:10001'}
        self.proactive_generation_history_limit = 80
        self.proactive_history_context_mode = 'compact'
        self.proactive_history_recent_raw_count = 8
        self.proactive_history_max_chars = 16000
        self.bot_name = '合成伙伴'
        self.reads = []
        self.model_calls = 0
        self.read_error = None

    def persona_setting(self, key, default=None):
        return getattr(self, key, default)

    async def _get_current_conversation_safely(self, umo, **kwargs):
        self.reads.append(umo)
        if self.read_error:
            raise self.read_error
        return self.conversation

    def _environment_fromtimestamp(self, value):
        return datetime.fromtimestamp(value, ZoneInfo('Asia/Shanghai'))

    def _environment_now(self):
        return datetime(2026, 10, 25, 15, tzinfo=ZoneInfo('Asia/Shanghai'))

    async def _llm_call(self, *args, **kwargs):
        self.model_calls += 1
        raise AssertionError('history formatting must not invoke a summarization model')

    async def _memory_companion_search_open_loops(self, **kwargs):
        return []

    async def _resolve_proactive_persona_prompt(self, user):
        return '合成测试人格，不含真实用户资料。'

    def _sanitize_owner_environment_context_for_private_user(self, value, user):
        return value

    def _sanitize_schedule_context_for_private_user(self, value, user):
        return value

    def _proactive_llm_segmenting_allowed(self, **kwargs):
        return False


# Binding the original descriptors preserves the upstream static methods and
# coroutine bodies. No copied/reimplemented excerpt or prompt-building logic.
if NATIVE:
    for owner, names in (
        (message.ProactiveMessageMixin, (
            '_proactive_history_limit', '_fit_proactive_history_lines',
            '_format_proactive_history_context', '_recent_private_conversation_for_proactive_review',
            '_build_framework_proactive_prompt', '_default_proactive_prompt_document',
            '_truncate_proactive_context')),
        (daily.DailyStateMixin, (
            '_load_conversation_history_items', '_parse_tail_json_items',
            '_format_history_item_for_summary', '_history_item_content_text',
            '_history_item_timestamp', '_daily_proactive_archive_context_text')),
    ):
        for name in names:
            setattr(HistoryHost, name, inspect.getattr_static(owner, name))
    for name in (
        '_format_action_prompt_context', '_format_proactive_relationship_fact',
        '_format_schedule_context_for_prompt', '_format_plan_item_for_prompt',
        '_format_state_for_framework_prompt', '_format_llm_timer_context',
        '_proactive_time_guard_hint', '_format_recent_proactive_topics_hint',
    ):
        setattr(HistoryHost, name, lambda *args, **kwargs: '')
    for name in (
        '_proactive_current_plan_item', '_proactive_troubleshooting_request_prompt_section',
        '_deferred_immediate_share_tense_prompt_section',
        '_format_proactive_future_schedule_hint_section',
        '_format_proactive_calendar_constraint_hint_section',
        '_proactive_expression_shape_prompt_section',
        '_format_proactive_relationship_initiative_prompt_section',
        '_proactive_natural_delivery_prompt_section',
        '_proactive_reaction_expression_prompt_section',
        '_proactive_visible_text_format_prompt_section',
        '_format_proactive_recipient_identity_guard_prompt_section',
    ):
        setattr(HistoryHost, name, lambda *args, **kwargs: None)


def records(count=100, *, text_chars=30):
    start = datetime(2026, 10, 24, 8, tzinfo=ZoneInfo('Asia/Shanghai'))
    return [dict(role='user' if index % 2 == 0 else 'assistant',
                 content=f'SYN{index:03d} ' + '甲' * text_chars,
                 timestamp=(start + timedelta(minutes=index)).timestamp())
            for index in range(count)]


def block(text, title):
    match = re.search('【' + re.escape(title) + '】\\s*(.*?)(?=\\n【|\\Z)', text, re.S)
    return [line for line in match[1].splitlines() if line.strip()] if match else []


@unittest.skipUnless(NATIVE, 'requires pinned AstrBot/Private dependencies')
class NativeProactiveContextTests(unittest.IsolatedAsyncioTestCase):
    async def excerpt(self, host):
        return await host._recent_private_conversation_for_proactive_review(
            host.user, limit=host._proactive_history_limit('generation'))

    async def test_latest_eighty_messages_not_eighty_rounds(self):
        host = HistoryHost(records())
        before = host.conversation.history
        text = await self.excerpt(host)
        self.assertEqual(re.findall(r'SYN\d{3}', text), [f'SYN{i:03d}' for i in range(20, 100)])
        self.assertEqual(len(block(text, '较早对话（已压缩）')), 72)
        self.assertEqual(len(block(text, '最近对话（保留原文）')), 8)
        self.assertEqual(host.reads, [host.user['umo']])
        self.assertEqual(host.conversation.history, before)
        self.assertEqual(host.model_calls, 0)

    async def test_long_records_keep_roles_times_and_native_260_160_limits(self):
        host = HistoryHost(records(text_chars=500))
        text = await self.excerpt(host)
        older = block(text, '较早对话（已压缩）')
        recent = block(text, '最近对话（保留原文）')
        self.assertEqual((len(older), len(recent)), (72, 8))
        self.assertTrue(all(len(line) <= 160 for line in older))
        self.assertTrue(all(len(line.split(': ', 1)[1]) <= 260 for line in recent))
        self.assertTrue(all(re.match(r'10-24 \d{2}:\d{2} (用户|合成伙伴\(Bot回复\)): ', line)
                            for line in older + recent))
        self.assertLessEqual(len(text), 16000)
        self.assertEqual(host.model_calls, 0)

    async def test_larger_budget_restores_older_records_without_changing_recent(self):
        host = HistoryHost(records(text_chars=500))
        host.proactive_history_max_chars = 6000
        old = await self.excerpt(host)
        host.proactive_history_max_chars = 16000
        new = await self.excerpt(host)
        self.assertLess(len(block(old, '较早对话（已压缩）')), 72)
        self.assertEqual(len(block(new, '较早对话（已压缩）')), 72)
        self.assertEqual(block(old, '最近对话（保留原文）'), block(new, '最近对话（保留原文）'))
        self.assertLessEqual(len(old), 6000)
        self.assertLessEqual(len(new), 16000)

    async def test_tight_budget_preserves_newest_before_older(self):
        host = HistoryHost(records(text_chars=500))
        host.proactive_history_max_chars = 500
        text = await self.excerpt(host)
        self.assertIn('SYN099', text)
        self.assertNotIn('SYN020', text)
        recent = block(text, '最近对话（保留原文）')
        older = block(text, '较早对话（已压缩）')
        self.assertEqual(re.findall(r'SYN\d{3}', '\n'.join(recent)), ['SYN099'])
        # Native budgets the two blocks independently: another 260-char recent
        # line cannot fit, but its remaining budget can hold a 160-char older
        # line. Preserve that existing behavior rather than inventing a new
        # cross-block truncation algorithm.
        self.assertEqual(re.findall(r'SYN\d{3}', '\n'.join(older)), ['SYN091'])
        self.assertLessEqual(len(text), 500)

    async def test_short_history_does_not_invent_eighty_entries(self):
        host = HistoryHost(records(5))
        text = await self.excerpt(host)
        self.assertEqual(re.findall(r'SYN\d{3}', text), [f'SYN{i:03d}' for i in range(5)])
        self.assertEqual(block(text, '较早对话（已压缩）'), [])
        self.assertEqual(len(block(text, '最近对话（保留原文）')), 5)

    async def test_no_fixed_hour_cutoff_and_missing_time_is_not_invented(self):
        data = records(3)
        data[0]['timestamp'] -= 7 * 86400
        del data[2]['timestamp']
        host = HistoryHost(data)
        text = await self.excerpt(host)
        self.assertIn('10-17 08:00 用户: SYN000', text)
        self.assertIn('10-24 08:01 合成伙伴(Bot回复): SYN001', text)
        self.assertIn('\n用户: SYN002', text)

    async def test_media_text_and_native_single_line_normalization_are_unchanged(self):
        data = [dict(role='user', content=[{'type':'text','text':'图前\n\n说明'},
                    {'type':'image'}, {'type':'text','text':'图后说明'}])]
        host = HistoryHost(data)
        text = await self.excerpt(host)
        self.assertIn('用户: 图前 说明 [图片] 图后说明', text)
        self.assertEqual(host.conversation.history, json.dumps(data, ensure_ascii=False))
        self.assertEqual(host.model_calls, 0)

    async def test_internal_proactive_placeholders_are_not_user_dialogue(self):
        data = records(4)
        data[1]['content'] = '【主动消息】触发原因：合成；内部动机：合成'
        host = HistoryHost(data)
        text = await self.excerpt(host)
        self.assertNotIn('触发原因', text)
        self.assertEqual(re.findall(r'SYN\d{3}', text), ['SYN000','SYN002','SYN003'])

    async def test_missing_history_uses_bounded_native_last_exchange_fallback(self):
        host = HistoryHost([])
        host.read_error = RuntimeError('synthetic read unavailable')
        host.user.update(last_user_message='用户末条 ' + '甲' * 300,
                         last_companion_message='助手末条 ' + '乙' * 300)
        text = await self.excerpt(host)
        lines = block(text, '最近对话（保留原文）')
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(len(line.split(': ', 1)[1]) <= 180 for line in lines))
        self.assertEqual(host.model_calls, 0)

    async def test_actual_native_prompt_builder_receives_all_eighty_excerpts(self):
        host = HistoryHost(records(text_chars=500))
        before = host.conversation.history
        # news_share avoids unrelated schedule material I/O; it does not alter
        # the common native history assembly/append path under test.
        prompt = await host._build_framework_proactive_prompt(
            user=host.user, name='合成对象', reason='news_share', action='message',
            action_context='合成候选', motive='合成动机')
        excerpt = await self.excerpt(host)
        self.assertIn('【最近私聊实况】', prompt)
        self.assertIn(excerpt, prompt)
        self.assertEqual(re.findall(r'SYN\d{3}', prompt), [f'SYN{i:03d}' for i in range(20, 100)])
        self.assertIn('合成测试人格，不含真实用户资料。', prompt)
        self.assertIn('当前真实时间：2026-10-25 15:00', prompt)
        self.assertEqual(host.conversation.history, before)
        self.assertEqual(host.model_calls, 0)


if __name__ == '__main__':
    unittest.main()
