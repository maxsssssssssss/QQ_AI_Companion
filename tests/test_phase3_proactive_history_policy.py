"""Synthetic clock/data checks using pinned native topic methods, no I/O."""
import ast
from copy import deepcopy
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Any
import unittest

from integrations.phase3_runtime.proactive_history import NativeProactiveHistory
from integrations.phase3_runtime.proactive import NativeProactiveBridge


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / '.runtime/upstream/private_companion/daily_state.py'
NATIVE_METHODS = {'_cleanup_recent_proactive_topics', '_proactive_dedup_window_minutes',
    '_format_recent_proactive_topics_hint', '_recent_proactive_topic_repeated',
    '_proactive_dedup_age_allowed', '_topic_signature_similar', '_remember_proactive_topic'}


def native_host(clock):
    # Execute unchanged pinned methods locally; do not vendor third-party code
    # or import the large plugin's network/scheduler dependencies.
    tree = ast.parse(SOURCE.read_text())
    original = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                    and node.name == 'DailyStateMixin')
    host = ast.ClassDef(name='PinnedTopicHost', bases=[], keywords=[],
        body=[node for node in original.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
              and node.name in NATIVE_METHODS], decorator_list=[])
    env = {'Any': Any, 're': re, '_now_ts': lambda: clock[0],
        '_safe_float': lambda value, default: float(value or default),
        '_single_line': lambda value, limit=80: re.sub(r'\s+', ' ', str(value or '')).strip()[:limit],
        'runtime_persona_setting': lambda owner, key, default: getattr(owner, key, default),
        'logger': SimpleNamespace(warning=lambda *args: None)}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[host], type_ignores=[])), str(SOURCE), 'exec'), env)
    obj = env['PinnedTopicHost']()
    obj._proactive_topic_signature = lambda text, *args: (
        'ordinary_weather_topic' if str(text).startswith('weather') else str(text))
    obj._format_timestamp_elapsed = lambda timestamp: str(int((clock[0] - timestamp) / 60)) + '分钟前'
    obj._framework_agent_meta_summary_leak = lambda text: 'SYNTHETIC_INTERNAL_LEAK' in text
    return obj


@unittest.skipUnless(SOURCE.exists(), 'Pinned Private source required')
class ProactiveHistoryPolicyTests(unittest.TestCase):
    def setUp(self):
        self.clock = [200000.0]
        self.private = native_host(self.clock)
        self.user = {'umo': 'offline:FriendMessage:10001', 'ignored_streak': 2,
            'awaiting_reply_since': 123.0, 'recent_proactive_topics': []}
        self.adapter = NativeProactiveHistory(self.private, self.user['umo'],
            {'summary_limit': 8, 'ordinary_retention_hours': 24})
        self.bindings = {}
        self.adapter.attach(self.bindings)

    def rows(self, ages, *, prefix='topic'):
        return [{'ts': self.clock[0] - age * 3600, 'signature': prefix + str(index),
                 'text': prefix + str(index)} for index, age in enumerate(ages)]

    def summaries(self, text):
        return [line for line in text.splitlines() if '说过：' in line]

    def test_eight_summaries_native_order_and_80_character_limit(self):
        rows = self.rows(range(11, -1, -1))
        for row in rows:
            row['text'] += ' 中😀文\n' * 40
        self.user['recent_proactive_topics'] = rows
        before = deepcopy(self.user)
        output = self.summaries(self.private._format_recent_proactive_topics_hint(self.user))
        self.assertEqual(len(output), 8)
        self.assertTrue(output[0].startswith('- 420分钟前说过：topic4'))
        self.assertTrue(output[-1].startswith('- 0分钟前说过：topic11'))
        self.assertTrue(all(len(row.split('说过：', 1)[1]) == 80 for row in output))
        self.assertEqual(self.user, before)

    def test_retention_24_hours_boundary_without_resetting_timestamps(self):
        self.user['recent_proactive_topics'] = self.rows([24 + 1/3600, 24, 23, 7, 0])
        expected = deepcopy(self.user['recent_proactive_topics'][1:])
        self.assertEqual(self.private._cleanup_recent_proactive_topics(self.user), expected)

    def test_retained_older_topic_does_not_extend_duplicate_cooldown(self):
        self.user['recent_proactive_topics'] = self.rows([5])
        self.assertEqual(len(self.private._cleanup_recent_proactive_topics(self.user)), 1)
        self.assertEqual(self.private._proactive_dedup_window_minutes('sent', 240), 240)
        self.assertFalse(self.private._recent_proactive_topic_repeated(self.user, 'topic0'))
        self.user['recent_proactive_topics'][0]['ts'] = self.clock[0] - 3 * 3600
        self.assertTrue(self.private._recent_proactive_topic_repeated(self.user, 'topic0'))

    def test_weather_retention_stays_18_hours_and_guidance_once(self):
        self.user['recent_proactive_topics'] = self.rows([19, 17], prefix='weather') + self.rows(range(7, -1, -1))
        output = self.private._format_recent_proactive_topics_hint(self.user)
        self.assertEqual(len(self.summaries(output)), 8)
        self.assertEqual(output.count('最近已经用天气开过话题'), 1)
        self.assertEqual(len(self.user['recent_proactive_topics']), 9)
        self.assertEqual(self.private._proactive_dedup_window_minutes('weather', 1080), 1080)

    def test_native_store_cap_and_nontext_leak_filter_preserved(self):
        self.user['recent_proactive_topics'] = self.rows(range(15, -1, -1))
        self.user['recent_proactive_topics'][-1]['text'] = 'SYNTHETIC_INTERNAL_LEAK'
        output = self.private._format_recent_proactive_topics_hint(self.user)
        self.assertEqual(len(self.user['recent_proactive_topics']), 12)
        self.assertNotIn('SYNTHETIC_INTERNAL_LEAK', output)
        self.assertEqual(len(self.summaries(output)), 8)

    def test_new_native_remember_retains_old_entries_and_reply_state(self):
        self.user['recent_proactive_topics'] = self.rows([20, 7, 2])
        self.private._remember_proactive_topic(self.user, text='new confirmed message')
        self.assertEqual(len(self.user['recent_proactive_topics']), 4)
        self.assertEqual(self.user['ignored_streak'], 2)
        self.assertEqual(self.user['awaiting_reply_since'], 123)

    def test_empty_or_fewer_than_eight_do_not_create_placeholder_summaries(self):
        self.assertEqual(self.private._format_recent_proactive_topics_hint(self.user), '')
        self.user['recent_proactive_topics'] = self.rows([20, 2])
        self.assertEqual(len(self.summaries(self.private._format_recent_proactive_topics_hint(self.user))), 2)

    def test_other_session_keeps_native_four_and_six_hours(self):
        other = {**self.user, 'umo': 'offline:FriendMessage:20002',
                 'recent_proactive_topics': self.rows([20, 7, 5, 4, 3, 2, 1])}
        output = self.private._format_recent_proactive_topics_hint(other)
        self.assertEqual(len(other['recent_proactive_topics']), 5)
        self.assertEqual(len(self.summaries(output)), 4)

    def test_detach_restores_original_behavior_and_no_policy_does_not_bind(self):
        for name, original in self.bindings.items():
            setattr(self.private, name, original)
        self.user['recent_proactive_topics'] = self.rows([20, 7, 5, 4, 3, 2, 1])
        output = self.private._format_recent_proactive_topics_hint(self.user)
        self.assertEqual(len(self.summaries(output)), 4)
        adapter = NativeProactiveHistory(self.private, self.user['umo'])
        bindings = {}
        adapter.attach(bindings)
        self.assertEqual(bindings, {})

    def test_cleanup_exception_restores_original_duplicate_window(self):
        self.adapter.originals['_cleanup_recent_proactive_topics'] = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('fixture'))
        with self.assertRaises(RuntimeError):
            self.private._cleanup_recent_proactive_topics(self.user)
        self.assertEqual(self.private._proactive_dedup_window_minutes('sent', 240), 240)

    def test_schema_default_policy_preserves_custom_native_retention(self):
        for window_minutes, expected in ((0, 2), (720, 1)):
            for policy in (None, {}, {'summary_limit': 4, 'ordinary_retention_hours': 6}):
                with self.subTest(window_minutes=window_minutes, policy=policy):
                    private = native_host(self.clock)
                    private.proactive_dedup_sent_window_minutes = window_minutes
                    adapter = NativeProactiveHistory(private, self.user['umo'], policy)
                    bindings = {}
                    adapter.attach(bindings)
                    self.assertEqual(bindings, {})
                    user = {**self.user, 'recent_proactive_topics': self.rows([20, 8])}
                    self.assertEqual(len(private._cleanup_recent_proactive_topics(user)), expected)
                    self.assertEqual(private._proactive_dedup_window_minutes('sent', 240), window_minutes)

    def test_native_api_or_policy_mismatch_is_rejected(self):
        for policy in ({'summary_limit': 9}, {'summary_limit': True},
                       {'ordinary_retention_hours': 25}, {'unexpected': 1}):
            with self.assertRaises(ValueError):
                NativeProactiveHistory(self.private, self.user['umo'], policy)
        with self.assertRaises(ValueError):
            NativeProactiveHistory(object(), self.user['umo'], {'summary_limit': 8}).check_api()


class NativeReasonWindowPolicyTests(unittest.TestCase):
    def test_continuous_candidate_window_follows_current_quiet_hours(self):
        bridge = object.__new__(NativeProactiveBridge)
        bridge.gate = SimpleNamespace(mode='continuous', enabled=True,
            session='offline:FriendMessage:10001', quiet=(23, 9))
        bridge._native_settings_active = True
        bridge._additional_bindings = {'_reason_windows': lambda reason, user: [(12*60, 13*60)]}
        target = {'umo': bridge.gate.session}
        self.assertEqual(bridge.reason_windows('quiet_care', target), [(540, 1380)])
        bridge.gate.quiet = (0, 9)
        self.assertEqual(bridge.reason_windows('quiet_care', target), [(540, 1440)])
        self.assertEqual(bridge.reason_windows('meal_care', target), [(720, 780)])
        self.assertEqual(bridge.reason_windows('quiet_care', {'umo': 'another'}), [(720, 780)])


if __name__ == '__main__':
    unittest.main()
