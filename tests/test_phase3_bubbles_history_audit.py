"""Synthetic audit-tool fixtures only; no real API call or private history."""
from copy import deepcopy
import hashlib
import importlib.util
import json
import re
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bubbles_history_audit_test', ROOT / 'scripts/phase3-audit-bubbles-history.py')
audit = importlib.util.module_from_spec(spec); spec.loader.exec_module(audit)


class SyntheticAlgorithm:
    def __init__(self, parts=None, *, source='smalltalk_fallback'):
        self.parts = parts; self.source = source

    def plain_qq_text(self, text): return text

    def trim_boundary_newlines(self, text):
        if not text.strip(): return text
        start, end = len(text) - len(text.lstrip()), len(text.rstrip())
        return text[:start].replace('\r', '').replace('\n', '') + text[start:end] + text[end:].replace('\r', '').replace('\n', '')

    def semantic_plan(self, parser, text, *, enabled, fallback_enabled, allow_fallback):
        split = self.parts is not None and (self.source == 'model_markers' or (allow_fallback and fallback_enabled))
        parts = self.parts if split else [text]
        source = self.source if split else 'single'
        reason = 'fixture_boundary' if split else ('fallback_context_excluded' if not allow_fallback else 'fallback_disabled')
        return SimpleNamespace(texts=parts, source=source, reason=reason,
            raw_marker_present='PRIVATE_COMPANION_SPLIT' in text,
            diagnostics=lambda: {'source': source, 'reason': reason, 'planned_count': len(parts)})

    def candidate_boundaries(self, text): return tuple(self.parts) if self.parts is not None else None

    def map_candidate_boundaries(self, candidate, final_text):
        return candidate if candidate and ''.join(candidate) == final_text else None

    def candidate_mapping_reason(self, candidate, final_text):
        return 'candidate_exact' if self.map_candidate_boundaries(candidate, final_text) else None


class HistoryAuditTests(unittest.TestCase):
    def frozen_baseline(self, name):
        path = ROOT / audit.V31_BASELINE_RELATIVE
        if not path.is_file(): self.skipTest('local historical V3.1 evidence not installed')
        audit.verified_bytes(path, audit.V31_BASELINE_SHA256)
        return audit.load_module(name, path)

    def multibubble(self):
        row = {'case': 'daily', 'variant': 'candidate', 'http': 200, 'error_type': None,
            'output': '合成输出。', 'finish_reason': 'stop', 'usage': {'prompt_tokens': 1},
            'model': 'deepseek-flash', 'elapsed_ms': 1}
        data = {'mode': 'REAL_DEEPSEEK_SYNTHETIC', 'synthetic_only': True, 'real_qq': False,
            'production_changed': False, 'results': [row]}
        return data, {'results': [deepcopy(row)]}

    def thinking(self):
        return [
            {'kind': 'metadata', 'endpoint': 'https://api.deepseek.com/chat/completions',
             'model': 'deepseek-flash', 'memory_injected': False, 'initial_history': [], 'stream': False},
            {'kind': 'request_start', 'sequence': 1, 'group': 1, 'turn': 1, 'mode': 'low', 'payload_sha256': 'synthetic-hash'},
            {'kind': 'result', 'sequence': 1, 'group': 1, 'turn': 1, 'mode': 'low', 'payload_sha256': 'synthetic-hash',
             'http': 200, 'status': 'OK', 'timeout': False, 'api_error': None, 'response_model': 'deepseek-flash',
             'finish_reason': 'stop', 'output': '合成正文。', 'user': '合成提问。',
             'response_body': {'model': 'deepseek-flash', 'choices': [{'message': {'content': '合成正文。',
                 'reasoning_content': 'SYNTHETIC_REASONING_MUST_NOT_EXPORT'}}]}},
            {'kind': 'postflight', 'memory_database_opened': False, 'qq_messages': 0,
             'production_writes': False, 'service_restarts': False}]

    def test_real_source_metadata_required_and_processed_raw_must_match(self):
        good, raw = self.multibubble()
        row = audit._multibubble_records(good, raw, 'synthetic-validator-fixture', 1)[0]
        self.assertEqual(row['output'], good['results'][0]['output'])
        for field, value in [('mode', 'MOCK'), ('synthetic_only', False), ('real_qq', True)]:
            changed = deepcopy(good); changed[field] = value
            with self.assertRaises(ValueError): audit._multibubble_records(changed, raw, 'fixture', 1)
        changed = deepcopy(good); changed['results'][0]['output'] = 'edited'
        with self.assertRaisesRegex(ValueError, 'processed_raw'): audit._multibubble_records(changed, raw, 'fixture', 1)
        good['results'][0]['model'] = raw['results'][0]['model'] = 'phase1-mock'
        with self.assertRaisesRegex(ValueError, 'successful_real'): audit._multibubble_records(good, raw, 'fixture', 1)

    def test_native_response_content_kept_but_reasoning_and_full_payload_excluded(self):
        rows = audit._thinking_records(self.thinking(), 'synthetic-validator-fixture', 1)
        self.assertEqual(rows[0]['output'], '合成正文。')
        self.assertEqual(rows[0]['thinking'], 'low')
        self.assertNotIn('reasoning_content', json.dumps(rows)); self.assertNotIn('MUST_NOT_EXPORT', json.dumps(rows))
        self.assertNotIn('response_body', rows[0]); self.assertNotIn('request_messages', rows[0])

    def test_thinking_provenance_pair_model_memory_failure_or_missing_postflight_rejected(self):
        mutations = [lambda d: d[0].update(endpoint='https://mock.invalid'),
            lambda d: d[0].update(memory_injected=True), lambda d: d[3].update(qq_messages=1),
            lambda d: d[2].update(response_model='phase1-mock'), lambda d: d[2].update(timeout=True),
            lambda d: d[2].update(payload_sha256='wrong'), lambda d: d[2].update(output='changed'),
            lambda d: d.pop()]
        for mutate in mutations:
            data = self.thinking(); mutate(data)
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                audit._thinking_records(data, 'fixture', 1)

    def test_verified_hash_is_required_before_evidence_can_be_read_as_real(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'mock.json'; path.write_text('{"model":"deepseek-flash","http":200}')
            with self.assertRaisesRegex(ValueError, 'hash_mismatch'): audit.verified_bytes(path, '0' * 64)
            expected = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(audit.verified_bytes(path, expected), path.read_bytes())
            alias = path.with_name('alias.json'); alias.symlink_to(path)
            with self.assertRaises(ValueError): audit.verified_bytes(alias, expected)

    def test_fallback_whitespace_emoji_slices_have_exact_integrity(self):
        parts = ['好耶！  ', '终于可以歇会儿了🙂。']; text = ''.join(parts)
        record = audit.plan_record(SyntheticAlgorithm(parts), None, text, allow_fallback=True)
        self.assertTrue(record['raw_join_equals_input']); self.assertTrue(record['fallback_exact_after_formatting'])
        self.assertEqual(record['integrity'], 'EXACT_FALLBACK_SLICES')
        self.assertEqual(record['formatted_parts'], parts)
        broken = audit.plan_record(SyntheticAlgorithm([parts[0].strip(), parts[1]]), None, text, allow_fallback=True)
        self.assertEqual(broken['integrity'], 'FAIL_FALLBACK_CHANGED_CONTENT')

    def test_search_final_exclusion_and_native_marker_cleanup_are_not_claimed_as_loss(self):
        algo = SyntheticAlgorithm(['好耶！', '公开资料已核对。'])
        record = audit.plan_record(algo, None, '好耶！公开资料已核对。', allow_fallback=False)
        self.assertFalse(record['split']); self.assertFalse(record['fallback_eligible'])
        self.assertEqual(record['final_plan']['reason'], 'fallback_context_excluded')
        marked = '甲\n<<PRIVATE_COMPANION_SPLIT>>\n乙'
        record = audit.plan_record(SyntheticAlgorithm(['甲', '乙'], source='model_markers'), None, marked, allow_fallback=True)
        self.assertEqual(record['integrity'], 'NATIVE_CONTROL_CLEANUP')
        self.assertIsNone(record['fallback_exact_after_formatting']); self.assertFalse(record['raw_join_equals_input'])
        candidate = audit.plan_record(SyntheticAlgorithm(), None, '普通单条回复', allow_fallback=True,
                                      final_cleanup=lambda text: text)
        self.assertEqual(candidate['final_plan']['reason'], 'fallback_candidate_unavailable')
        self.assertEqual(candidate['formatted_parts'], ['普通单条回复'])
        excluded = audit.plan_record(SyntheticAlgorithm(), None, '搜索回复', allow_fallback=False,
                                     final_cleanup=lambda text: text)
        self.assertEqual(excluded['final_plan']['reason'], 'fallback_context_excluded')
        self.assertEqual(excluded['formatted_parts'], ['搜索回复'])

    def test_comparison_and_markdown_preserve_full_samples_without_quality_claim(self):
        rows = audit._thinking_records(self.thinking(), 'synthetic-validator-fixture', 1)
        rows[0]['output'] = '好耶！  开心🙂。\n```literal fence```'
        before = deepcopy(rows)
        records = audit.compare_records(rows, SyntheticAlgorithm(), SyntheticAlgorithm(), None,
                                        final_cleanup=lambda text: text)
        self.assertEqual(rows, before)
        self.assertEqual(records[0]['codex_rule_review']['status'], 'PENDING_PER_SAMPLE_REVIEW')
        self.assertEqual(records[0]['owner_naturalness'], 'PENDING_OWNER_EVALUATION')
        text = audit.markdown({'samples': records})
        self.assertIn(rows[0]['output'], text); self.assertIn('````', text)
        self.assertIn('未生成 LLM 自评分', text); self.assertNotIn('SYNTHETIC_REASONING', text)

    def test_candidate_fallback_skips_formatter_and_joins_the_exact_final(self):
        parts = ['今天终于准时下班了！  ', '我们可以慢慢享受这一晚🙂！  ']
        algorithm = SyntheticAlgorithm(parts)
        with patch.object(algorithm, 'plain_qq_text', side_effect=AssertionError('must_not_format_fallback')):
            record = audit.plan_record(algorithm, None, ''.join(parts), allow_fallback=True,
                                       final_cleanup=lambda text: text)
        self.assertEqual(record['candidate_mapping'], 'EXACT')
        self.assertEqual(record['formatted_parts'], parts)
        self.assertEqual(record['integrity'], 'EXACT_FINAL_SLICES')
        self.assertTrue(record['final_join_equals_cleaned'])
        self.assertTrue(record['formatted_join_equals_cleaned'])

    def test_candidate_unknown_cleanup_does_not_restore_the_raw_reply(self):
        raw_parts = ['今天终于准时下班了！  ', '我们可以慢慢享受这一晚。  ']
        algorithm = SyntheticAlgorithm(raw_parts)
        raw = ''.join(raw_parts); final = '本轮后置安全清理后的有效正文'
        record = audit.plan_record(algorithm, None, raw, allow_fallback=True,
                                   final_cleanup=lambda text: final)
        self.assertEqual(record['candidate_mapping'], 'REJECTED')
        self.assertFalse(record['fallback_selected'])
        self.assertEqual(record['formatted_parts'], [final])
        self.assertEqual(record['raw'], raw); self.assertEqual(record['full_cleaned'], final)
        self.assertTrue(record['final_join_equals_cleaned'])
        # A corrupt mapper also cannot replace the actual cleaned body.
        with patch.object(algorithm, 'map_candidate_boundaries', return_value=tuple(raw_parts)):
            record = audit.plan_record(algorithm, None, raw, allow_fallback=True,
                                       final_cleanup=lambda text: final)
        self.assertEqual(record['formatted_parts'], [final])

    def test_pinned_native_helper_and_real_candidate_map_only_eof_chinese_period(self):
        # Local pure functions, synthetic text. No model, event or production data.
        candidate = self.frozen_baseline('audit_current_candidate_test')
        cleanup = audit.native_terminal_cleanup(); parser = audit.native_parser()
        raw = '  今天终于准时下班了！  我们可以慢慢享受这一晚。  \r\n'
        final = cleanup(raw)
        self.assertEqual(final, '  今天终于准时下班了！  我们可以慢慢享受这一晚  \r\n')
        with patch.object(candidate, 'plain_qq_text', side_effect=AssertionError('must_not_format_fallback')):
            record = audit.plan_record(candidate, parser, raw, allow_fallback=True, final_cleanup=cleanup)
        self.assertEqual(record['candidate_mapping'], 'TERMINAL_PERIOD_REMOVED')
        self.assertEqual(record['raw_plan']['source'], 'smalltalk_fallback')
        self.assertEqual(record['final_plan']['reason'], 'candidate_terminal_period_removed')
        self.assertEqual(''.join(record['formatted_parts']), final)
        self.assertTrue(record['fallback_exact_after_formatting'])
        self.assertTrue(record['formatted_join_equals_cleaned'])
        self.assertNotEqual(''.join(record['formatted_parts']), raw)
        changed = audit.plan_record(candidate, parser, raw, allow_fallback=True,
                                    final_cleanup=lambda text: cleanup(text).strip())
        self.assertEqual(changed['candidate_mapping'], 'REJECTED')
        self.assertFalse(changed['fallback_selected']); self.assertEqual(changed['formatted_parts'], [final.strip()])
        self.assertEqual(cleanup('合成句子。”'), '合成句子”')
        self.assertEqual(cleanup('合成句子.'), '合成句子')

    def test_mapping_diagnostic_uses_actual_variant_not_assumed_terminal_period(self):
        candidate = self.frozen_baseline('audit_whitespace_candidate_test')
        parser = audit.native_parser()
        raw = '今天终于准时下班了！ \n我们可以慢慢享受这一晚。'
        # Synthetic transform fixture only. The audit CLI still replays its
        # documented terminal helper, not the full hidden-intent native hook.
        line_cleanup = lambda text: re.sub(r'[ \t]+(?=\r?$)', '', text, flags=re.M)
        for cleanup, reason, mapping in (
            (lambda text: text, 'candidate_exact', 'EXACT'),
            (line_cleanup, 'candidate_line_end_whitespace_removed', 'LINE_END_WHITESPACE_REMOVED'),
            (lambda text: line_cleanup(text)[:-1],
             'candidate_line_end_whitespace_and_terminal_period_removed',
             'LINE_END_WHITESPACE_AND_TERMINAL_PERIOD_REMOVED')):
            with self.subTest(reason=reason):
                record = audit.plan_record(candidate, parser, raw, allow_fallback=True, final_cleanup=cleanup)
                self.assertEqual(record['final_plan']['reason'], reason)
                self.assertEqual(record['candidate_mapping'], mapping)
                self.assertEqual(''.join(record['formatted_parts']), cleanup(raw))
                self.assertTrue(record['fallback_selected'])
                self.assertTrue(record['formatted_join_equals_cleaned'])
                self.assertEqual(record['raw'], raw)

    def test_v4_classifies_final_allowed_text_without_raw_mapper_or_formatter(self):
        final_parts = ['哈？\n', '原生允许的新正文（真的）']
        final = ''.join(final_parts)
        algorithm = SyntheticAlgorithm(final_parts)
        with patch.object(algorithm, 'candidate_boundaries', side_effect=AssertionError('no_raw_candidate')), \
             patch.object(algorithm, 'map_candidate_boundaries', side_effect=AssertionError('no_raw_mapping')), \
             patch.object(algorithm, 'plain_qq_text', side_effect=AssertionError('no_second_formatter')):
            record = audit.final_text_plan_record(algorithm, None, '插件处理前内容不能恢复',
                allow_fallback=True, final_cleanup=lambda text: final)
        self.assertEqual(record['full_cleaned'], final)
        self.assertEqual(record['formatted_parts'], final_parts)
        self.assertTrue(record['fallback_exact_after_formatting'])
        self.assertEqual(record['candidate_mapping'], 'NOT_USED_FINAL_ALLOWED_TEXT')
        self.assertNotIn('插件处理前', ''.join(record['formatted_parts']))

    def test_v4_separates_exact_slicing_and_boundary_display_cleanup(self):
        parts = [' 哈？ \r\n', '\n 真的呀（先缓缓） \n']
        algorithm = SyntheticAlgorithm(parts)
        record = audit.final_text_plan_record(algorithm, None, ''.join(parts),
            allow_fallback=True, final_cleanup=lambda text: text)
        shown = audit.with_display_edges(record, algorithm)
        self.assertTrue(shown['final_join_equals_cleaned'])
        self.assertFalse(shown['display_join_equals_cleaned'])
        self.assertEqual(shown['display_parts'], [' 哈？ ', ' 真的呀（先缓缓） '])
        self.assertEqual(shown['boundary_newlines_removed'], 4)
        self.assertTrue(shown['display_boundary_only'])
        with patch.object(algorithm, 'trim_boundary_newlines', side_effect=lambda text: text.strip()):
            altered = audit.with_display_edges(record, algorithm)
        self.assertFalse(altered['display_boundary_only'])

    def test_v4_bad_slicer_and_nonstring_cleanup_are_rejected(self):
        algorithm = SyntheticAlgorithm(['哈？', '擅自改写'])
        record = audit.final_text_plan_record(algorithm, None, '哈？本轮允许的原文',
            allow_fallback=True, final_cleanup=lambda text: text)
        self.assertEqual(record['integrity'], 'FAIL_FALLBACK_CHANGED_CONTENT')
        with self.assertRaisesRegex(ValueError, 'native_cleanup_string_required'):
            audit.final_text_plan_record(algorithm, None, '合成正文', allow_fallback=True,
                final_cleanup=lambda text: None)

    def test_v4_search_excluded_and_empty_cleanup_not_restored(self):
        algorithm = SyntheticAlgorithm()
        for final in ('搜索最终回复', ''):
            with self.subTest(final=final):
                record = audit.final_text_plan_record(algorithm, None, '清理前正文',
                    allow_fallback=False, final_cleanup=lambda text: final)
                self.assertFalse(record['fallback_selected'])
                self.assertEqual(record['formatted_parts'], [final])
                self.assertTrue(record['formatted_join_equals_cleaned'])

    def test_v4_comparison_and_report_keep_complete_model_body_and_scope(self):
        rows = audit._thinking_records(self.thinking(), 'synthetic-validator-fixture', 1)
        rows[0]['output'] = '哈？\n真的呀（先缓缓）'
        before = deepcopy(rows)
        candidate = SyntheticAlgorithm(['哈？\n', '真的呀（先缓缓）'])
        compared = audit.compare_v31_v4_records(rows, SyntheticAlgorithm(), candidate, None,
            final_cleanup=lambda text: text)
        self.assertEqual(rows, before)
        self.assertTrue(compared[0]['plan_changed'])
        self.assertTrue(compared[0]['short_unmarked_nonsearch'])
        report = audit.markdown({'comparison': 'v31-v4', 'samples': compared})
        self.assertIn(rows[0]['output'], report)
        self.assertIn('V3.1', report); self.assertIn('V4', report)
        self.assertIn('未生成 LLM 自评分', report)
        self.assertIn('待用户评价', report)
        self.assertNotIn('SYNTHETIC_REASONING', report)

    def test_frozen_v31_hash_is_verified_independent_of_current_source(self):
        self.frozen_baseline('audit_v31_identity_test')
        raw = audit.verified_bytes(ROOT / audit.V31_BASELINE_RELATIVE, audit.V31_BASELINE_SHA256)
        self.assertIn(b'candidate_boundaries', raw)

    def test_missing_mapper_reason_cannot_be_reported_as_confirmed_period_cleanup(self):
        parts = ['今天终于准时下班了！', '我们可以慢慢享受这一晚。']
        algorithm = SyntheticAlgorithm(parts)
        for reason in (None, '', 'terminal_period_removed'):
            with self.subTest(reason=reason), patch.object(algorithm, 'candidate_mapping_reason', return_value=reason):
                with self.assertRaisesRegex(ValueError, 'candidate_mapping_reason_required'):
                    audit.plan_record(algorithm, None, ''.join(parts), allow_fallback=True,
                                      final_cleanup=lambda text: text)

    def test_native_cleanup_dependency_hash_is_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); upstream = root / '.runtime/upstream/private_companion'
            upstream.mkdir(parents=True)
            (upstream / 'outbound_tag_registry.py').write_text('untrusted helper dependency')
            with patch.object(audit, 'ROOT', root), patch.object(audit, 'native_parser'):
                with self.assertRaisesRegex(ValueError, 'hash_mismatch'):
                    audit.native_terminal_cleanup()

    def test_report_only_new_ignored_local_path_and_permissions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); evidence = root / 'evidence/phase3'; evidence.mkdir(parents=True)
            out = evidence / 'new'; report = {'samples': []}
            with patch.object(audit, 'ROOT', root), patch.object(audit.subprocess, 'run', return_value=SimpleNamespace(returncode=0)):
                audit.save_report(out, report)
                with self.assertRaises(FileExistsError): audit.save_report(out, report)
                with self.assertRaises(ValueError): audit.save_report(root / 'public', report)
            self.assertEqual(out.stat().st_mode & 0o777, 0o700)
            for path in out.iterdir(): self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with patch.object(audit, 'ROOT', root), patch.object(audit.subprocess, 'run', return_value=SimpleNamespace(returncode=1)):
                with self.assertRaises(ValueError): audit.save_report(evidence / 'not-ignored', report)
            self.assertFalse((evidence / 'not-ignored').exists())


if __name__ == '__main__': unittest.main()
