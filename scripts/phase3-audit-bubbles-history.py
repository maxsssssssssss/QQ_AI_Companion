#!/usr/bin/env python3
"""Offline V3/V3.1 planning audit of fixed, previously paid synthetic replies.

Reads only an explicit hash-verified evidence allowlist, never private QQ
history, profiles or credentials. Makes no API request and sends no messages.
Reasoning content is neither used as a reply nor included in generated reports.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
BASELINE_SHA256 = 'aa639a1970640502086abcdba6ef3729a87d6f8904d2bd995a07563d6f536799'
PARSER_SHA256 = 'ae7c25bbe39198fb49029a9c967e98a37156622922349d826aeb815002453176'
HELPERS_SHA256 = '586ced4b28d6bae9c0ef0109e6ce71a954798ee19f2eb59aa8aa1ee912930a48'
TAG_REGISTRY_SHA256 = '9b001e3142c751badcc8a24f25debb909a770aeb5333e99b50f5d011e0d60104'
PRIVATE_COMMIT = 'da953559f34d28a43d8a2d44c1064b293229c1b8'
TRUSTED_FILES = {
    'multibubble-real-20261022-r2.json': '8f29e365303e8603bd970833df624cf86b0a0504aa1235b6e48ca3adb231804a',
    'multibubble-real-20261022-r2.raw.json': '0faa847e3fec977737f38d54fd409075da5fd4f2f6069a40f925e3c1de801d8b',
    'multibubble-real-20261022-r3.json': '677bb3626d2b2c261c1d67cd27d64542c9f8b655c9f0fad1b07a0e824af17226',
    'multibubble-real-20261022-r3.raw.json': '6047d86994b92ed82029cb093b2d13c159d5bf8e7f71f019a681436bb7f33532',
    'thinking-ab-20261024/raw.jsonl': '556fd16636b30724b26e78dc90217629c893abb47603f5b0988102593ecf657c',
    'thinking-off-low-20261024/raw.jsonl': '64d3b311cdbc293c2f90869e143e3cad8706a85e0f0bd16ab4c0675281f382a1',
}


def digest(raw): return hashlib.sha256(raw).hexdigest()


def no_links(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('symlink_rejected')


def verified_bytes(path, expected):
    no_links(path)
    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('bounded_evidence_file_required')
    raw = path.read_bytes()
    if digest(raw) != expected: raise ValueError('historical_evidence_hash_mismatch')
    return raw


def _multibubble_records(processed, raw, source, expected_count):
    if (processed.get('mode') != 'REAL_DEEPSEEK_SYNTHETIC'
            or processed.get('synthetic_only') is not True or processed.get('real_qq') is not False
            or processed.get('production_changed') is not False):
        raise ValueError('real_synthetic_source_required')
    rows, originals = processed.get('results', []), raw.get('results', [])
    if len(rows) != expected_count or len(originals) != expected_count:
        raise ValueError('historical_response_count_changed')
    result = []
    for index, (row, original) in enumerate(zip(rows, originals)):
        keys = ('case', 'variant', 'http', 'error_type', 'output', 'finish_reason', 'usage', 'model', 'elapsed_ms')
        if any(row.get(k) != original.get(k) for k in keys):
            raise ValueError('processed_raw_response_mismatch')
        if (row.get('http') != 200 or row.get('model') != 'deepseek-flash' or row.get('error_type') is not None
                or row.get('finish_reason') != 'stop' or not isinstance(row.get('output'), str)
                or not row['output'] or not isinstance(row.get('usage'), dict)):
            raise ValueError('successful_real_content_required')
        result.append({'id': source + ':' + str(index + 1), 'source': source, 'case': row['case'],
            'variant': row['variant'], 'user': None, 'output': row['output'], 'model': row['model'],
            'http': row['http'], 'finish_reason': row['finish_reason'], 'thinking': 'off',
            'input_scope': 'synthetic_only_persona_v01_plus_bubble_prompt_direct_api',
            'allow_fallback': row['case'] != 'search_final',
            'context_reason': 'search_final_excluded' if row['case'] == 'search_final' else 'ordinary_reply',
            'expected_body_sha256': digest(row['output'].encode())})
    return result


def _thinking_records(items, source, expected_count):
    metadata = [r for r in items if r.get('kind') == 'metadata']
    post = [r for r in items if r.get('kind') == 'postflight']
    starts = [r for r in items if r.get('kind') == 'request_start']
    rows = [r for r in items if r.get('kind') == 'result']
    if len(metadata) != 1 or len(post) != 1 or len(rows) != expected_count or len(starts) != expected_count:
        raise ValueError('complete_experiment_records_required')
    meta, after = metadata[0], post[0]
    if (meta.get('endpoint') != 'https://api.deepseek.com/chat/completions'
            or meta.get('model') != 'deepseek-flash' or meta.get('memory_injected') is not False
            or meta.get('initial_history') != [] or meta.get('stream') is not False
            or after.get('memory_database_opened') is not False or after.get('qq_messages') != 0
            or after.get('production_writes') is not False or after.get('service_restarts') is not False):
        raise ValueError('isolated_official_synthetic_provenance_required')
    start_map = {r.get('sequence'): r for r in starts}
    if len(start_map) != expected_count: raise ValueError('duplicate_request_sequence')
    result = []
    for row in rows:
        start = start_map.get(row.get('sequence'), {})
        body = row.get('response_body', {}); choices = body.get('choices', [])
        content = choices[0].get('message', {}).get('content') if len(choices) == 1 else None
        if (row.get('http') != 200 or row.get('status') != 'OK' or row.get('timeout') is not False
                or row.get('api_error') is not None or row.get('response_model') != 'deepseek-flash'
                or body.get('model') != 'deepseek-flash' or row.get('finish_reason') != 'stop'
                or not isinstance(content, str) or not content or row.get('output') != content
                or start.get('payload_sha256') != row.get('payload_sha256')
                or any(start.get(k) != row.get(k) for k in ('group', 'turn', 'mode'))):
            raise ValueError('successful_real_response_request_pair_required')
        result.append({'id': source + ':' + str(row['sequence']), 'source': source,
            'case': 'group_' + str(row['group']) + '_turn_' + str(row['turn']), 'variant': row['mode'],
            'user': row['user'], 'output': content, 'model': row['response_model'], 'http': 200,
            'finish_reason': row['finish_reason'], 'thinking': {'on': 'high'}.get(row['mode'], row['mode']),
            'input_scope': 'synthetic_persona_v02_only_no_guard_prompt_no_memory_no_tools',
            'allow_fallback': True, 'context_reason': 'ordinary_reply',
            'expected_body_sha256': digest(content.encode())})
    if len({r['id'] for r in result}) != expected_count: raise ValueError('duplicate_result_sequence')
    return result


def load_records(evidence_root):
    """Exact file allowlist, no glob over private historical diagnostics."""
    blobs = {name: verified_bytes(evidence_root / name, expected) for name, expected in TRUSTED_FILES.items()}
    rows = []
    for suffix, count in (('r2', 14), ('r3', 8)):
        name = 'multibubble-real-20261022-' + suffix + '.json'
        rows.extend(_multibubble_records(json.loads(blobs[name]),
            json.loads(blobs[name.replace('.json', '.raw.json')]), name, count))
    for folder, count in (('thinking-ab-20261024', 16), ('thinking-off-low-20261024', 4)):
        name = folder + '/raw.jsonl'
        rows.extend(_thinking_records([json.loads(line) for line in blobs[name].decode().splitlines()], name, count))
    if len(rows) != 42: raise ValueError('fixed_42_response_inventory_required')
    return rows, [{'path': name, 'sha256': digest(raw), 'bytes': len(raw)} for name, raw in blobs.items()]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
    return module


def native_parser():
    lock = json.loads((ROOT / 'config/plugins-lock.json').read_text())
    pinned = [p for p in lock['plugins'] if p['id'] == 'private_companion']
    if len(pinned) != 1 or pinned[0]['metadata_version'] != '6.7.0' or pinned[0]['commit'] != PRIVATE_COMMIT:
        raise ValueError('fixed_private_version_required')
    path = ROOT / '.runtime/upstream/private_companion/segmented_message.py'
    verified_bytes(path, PARSER_SHA256)
    namespace = types.ModuleType('history_audit_native_private')
    namespace.__path__ = [str(path.parent)]; sys.modules[namespace.__name__] = namespace
    return load_module(namespace.__name__ + '.segmented_message', path)


def native_terminal_cleanup():
    """Import the locked helper itself; never reproduce third-party code."""
    native_parser()  # Establish and verify the same pinned native namespace.
    directory = ROOT / '.runtime/upstream/private_companion'
    for name, expected in (('outbound_tag_registry', TAG_REGISTRY_SHA256), ('helpers', HELPERS_SHA256)):
        path = directory / (name + '.py')
        verified_bytes(path, expected)
        module = load_module('history_audit_native_private.' + name, path)
    return module._strip_terminal_sentence_period


def plan_record(module, parser, text, *, allow_fallback, final_cleanup=None):
    """Mechanical planning only. Without cleanup retain the frozen V3 path.

    The V3.1 passive adapter records candidates before the real pinned native
    terminal-period helper, then maps only into its final body. No body is ever
    restored from the original output. Other native hooks are outside this audit.
    """
    plan = module.semantic_plan(parser, text, enabled=True, fallback_enabled=True, allow_fallback=allow_fallback)
    raw_parts = list(plan.texts)
    if final_cleanup is not None:
        cleaned = final_cleanup(text)
        if not isinstance(cleaned, str): raise ValueError('native_cleanup_string_required')
        final_plan = module.semantic_plan(parser, cleaned, enabled=True, fallback_enabled=False,
                                          allow_fallback=allow_fallback)
        diagnostics = dict(final_plan.diagnostics())
        final_parts = list(final_plan.texts)
        mapping = 'NOT_ELIGIBLE'
        if (allow_fallback and plan.source == 'smalltalk_fallback'
                and final_plan.source == 'single' and final_plan.reason == 'fallback_disabled'):
            candidate = module.candidate_boundaries(text)
            mapped = module.map_candidate_boundaries(candidate, cleaned)
            if mapped and ''.join(mapped) == cleaned:
                final_parts = list(mapped)
                mapping = 'EXACT' if cleaned == text else 'TERMINAL_PERIOD_REMOVED'
                diagnostics.update(source='smalltalk_fallback', planned_count=len(final_parts),
                    reason='candidate_exact' if cleaned == text else 'candidate_terminal_period_removed')
            else:
                mapping = 'REJECTED' if candidate is not None else 'MISSING'
                diagnostics['reason'] = 'fallback_cleaned_mismatch' if candidate is not None else 'fallback_missing_candidate'
        elif final_plan.reason == 'fallback_disabled':
            # Passive mapping intentionally disables fresh heuristics. This is
            # not evidence that the independently configured fallback is off.
            diagnostics['reason'] = ('fallback_context_excluded' if not allow_fallback
                                     else 'fallback_candidate_unavailable')
        selected = diagnostics['source'] == 'smalltalk_fallback'
        # Match Guard V3.1: fallback final slices bypass all further formatting.
        parts = final_parts if selected else [module.plain_qq_text(p) for p in final_parts]
        exact = ''.join(parts) == cleaned if selected else None
        integrity = ('EXACT_FINAL_SLICES' if exact else 'FAIL_FALLBACK_CHANGED_CONTENT') if selected else (
            'NATIVE_CONTROL_CLEANUP' if final_plan.raw_marker_present else 'SINGLE_NATIVE_FORMATTING')
        return {'raw_plan': plan.diagnostics(), 'final_plan': diagnostics,
            'fallback_eligible': plan.source == 'smalltalk_fallback', 'fallback_selected': selected,
            'split': len(parts) > 1, 'raw': text, 'full_cleaned': cleaned,
            'raw_parts': raw_parts, 'final_parts': final_parts, 'formatted_parts': parts,
            'raw_join_equals_input': ''.join(raw_parts) == text,
            'final_join_equals_cleaned': ''.join(final_parts) == cleaned,
            'formatted_join_equals_cleaned': ''.join(parts) == cleaned,
            'fallback_exact_after_formatting': exact, 'candidate_mapping': mapping, 'integrity': integrity,
            'formatting_scope': 'pinned native terminal-period helper then passive candidate mapping; '
                'fallback exact final slices bypass plain_qq_text; other native hooks/transport/history not replayed'}
    final_plan = plan
    if plan.source == 'smalltalk_fallback':
        formatted = module.plain_qq_text(text)
        final_plan = module.semantic_plan(parser, formatted, enabled=True, fallback_enabled=True, allow_fallback=True)
        parts = list(final_plan.texts)
        exact = ''.join(raw_parts) == text and ''.join(parts) == formatted
        integrity = 'EXACT_FALLBACK_SLICES' if exact else 'FAIL_FALLBACK_CHANGED_CONTENT'
    else:
        parts = [module.plain_qq_text(p) for p in raw_parts]
        exact = None
        integrity = 'NATIVE_CONTROL_CLEANUP' if plan.raw_marker_present else 'SINGLE_NATIVE_FORMATTING'
    return {'raw_plan': plan.diagnostics(), 'final_plan': final_plan.diagnostics(),
        'fallback_eligible': plan.source == 'smalltalk_fallback', 'split': len(parts) > 1,
        'raw': text, 'full_cleaned': text, 'raw_parts': raw_parts, 'final_parts': list(final_plan.texts),
        'formatted_parts': parts, 'candidate_mapping': 'V3_BASELINE_NO_MAPPING',
        'raw_join_equals_input': ''.join(raw_parts) == text,
        'final_join_equals_cleaned': ''.join(final_plan.texts) == text,
        'formatted_join_equals_cleaned': ''.join(parts) == text,
        'fallback_exact_after_formatting': exact, 'integrity': integrity,
        'formatting_scope': 'existing plain_qq_text only; no native decoration hooks, transport or history projection'}


def compare_records(records, baseline, candidate, parser, *, final_cleanup):
    rows = []
    for original in records:
        row = dict(original)
        row['v3'] = plan_record(baseline, parser, row['output'], allow_fallback=row['allow_fallback'])
        row['v31'] = plan_record(candidate, parser, row['output'], allow_fallback=row['allow_fallback'],
                                  final_cleanup=final_cleanup)
        row['plan_changed'] = row['v3']['formatted_parts'] != row['v31']['formatted_parts']
        row['codex_rule_review'] = {'status': 'PENDING_PER_SAMPLE_REVIEW', 'verdict': None, 'reason': None}
        row['owner_naturalness'] = 'PENDING_OWNER_EVALUATION'
        rows.append(row)
    return rows


def fenced(text):
    width = max([2] + [len(match.group(0)) for match in re.finditer(r'`+', text)]) + 1
    fence = '`' * width
    return fence + '\n' + text + '\n' + fence


def markdown(report):
    lines = ['# V3 / V3.1 历史合成回复离线审查', '',
        '只重放既有真实 DeepSeek API 合成回复的解析与计划，不产生新模型请求、QQ发送或生产写入。',
        'Thinking 实验没有 Guard 分段提示词；它们可检验兜底对既有正文的行为，不能衡量线上分段提示效果。',
        'High/Low 回复只作已有正文样本；当前生产仍 Thinking Off。reasoning_content 不参与或展示。',
        'V3 保留旧 formatter 路径；V3.1 实际调用固定 Private 原生末尾句号清理 helper，再映射候选边界。'
        '兜底气泡必须逐字符拼回清理后的 final；其他原生清理 hooks、QQ 送达与自然度不由本审查证明。',
        '下列完整正文均为获准的合成语料。分段数不等于自然度；逐例误拆/漏拆须单独核对规则，用户自然度评价仍待完成。', '',
        '| 样本 | V3计划数 | V3.1计划数 | V3.1原因 | 原文切片完整性 |',
        '|---|---:|---:|---|---|']
    for row in report['samples']:
        lines.append('| ' + row['id'] + ' | ' + str(len(row['v3']['formatted_parts'])) + ' | '
            + str(len(row['v31']['formatted_parts'])) + ' | ' + row['v31']['final_plan']['reason']
            + ' | ' + row['v31']['integrity'] + ' |')
    for row in report['samples']:
        lines.extend(['', '## ' + row['id'], '', '来源：`' + row['source'] + '`；模式：' + row['thinking'] + '。'])
        if row['user'] is not None: lines.extend(['', '原合成用户输入：', '', fenced(row['user'])])
        else: lines.extend(['', '原实验场景：`' + row['case'] + '`；本证据没有用户原文副本，不补写。'])
        lines.extend(['', '模型最终正文（完整原文）：', '', fenced(row['output'])])
        for label, key in (('V3', 'v3'), ('V3.1', 'v31')):
            part = row[key]
            lines.extend(['', label + '：`' + part['final_plan']['source'] + '` / `' + part['final_plan']['reason'] + '`。'])
            lines.extend(['', '完整清理后正文：', '', fenced(part['full_cleaned']), '',
                '候选映射：`' + part['candidate_mapping'] + '`；最终计划拼接等于清理后正文：`'
                + str(part['final_join_equals_cleaned']) + '`；发送格式化后拼接等于清理后正文：`'
                + str(part['formatted_join_equals_cleaned']) + '`。'])
            for index, text in enumerate(part['formatted_parts'], 1):
                lines.extend(['', '计划气泡 ' + str(index) + '：', '', fenced(text)])
        lines.extend(['', '逐例规则审阅：待 Codex 人工检查误拆、漏拆或合理保留；不以 split 比例判定。',
                      '自然度：待用户评价；未生成 LLM 自评分。'])
    return '\n'.join(lines) + '\n'


def save_report(output, report):
    output = output.absolute(); no_links(output)
    if output.exists(): raise FileExistsError('new_audit_directory_required')
    if not output.parent.is_dir(): raise ValueError('existing_output_parent_required')
    if not output.resolve().is_relative_to(ROOT / 'evidence/phase3'):
        raise ValueError('ignored_local_evidence_output_required')
    check = subprocess.run(['git', '-C', str(ROOT), 'check-ignore', '-q', str(output)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
    if check.returncode: raise ValueError('output_must_be_git_ignored')
    output.mkdir(mode=0o700); output.chmod(0o700)
    for name, text in [('audit.json', json.dumps(report, ensure_ascii=False, indent=2) + '\n'),
                       ('samples.md', markdown(report))]:
        fd = os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(text); stream.flush(); os.fsync(stream.fileno())


def run(args):
    records, evidence = load_records(ROOT / 'evidence/phase3')
    if args.inventory:
        return {'status': 'VERIFIED_HISTORICAL_INVENTORY', 'responses': len(records), 'files': evidence,
                'sources': dict(Counter(r['source'] for r in records)), 'new_api_calls': 0, 'new_qq_sends': 0}
    if args.output_dir is None: raise ValueError('new_output_dir_required')
    baseline_path = ROOT / 'evidence/phase3/multibubble-v31-20261024/v3-baseline-bubbles.py'
    candidate_path = ROOT / 'integrations/phase1_guard/bubbles.py'
    verified_bytes(baseline_path, BASELINE_SHA256); no_links(candidate_path)
    baseline = load_module('history_audit_v3_baseline', baseline_path)
    candidate = load_module('history_audit_v31_candidate', candidate_path)
    cleanup = native_terminal_cleanup()
    rows = compare_records(records, baseline, candidate, native_parser(), final_cleanup=cleanup)
    failures = sum(r[key]['integrity'] == 'FAIL_FALLBACK_CHANGED_CONTENT' for r in rows for key in ('v3', 'v31'))
    report = {'status': 'OFFLINE_MECHANICS_COMPLETE_PENDING_REVIEW' if not failures else 'INTEGRITY_FAILURE',
        'observed_at': datetime.now(timezone.utc).isoformat(), 'historical_real_api_responses': len(rows),
        'new_api_calls': 0, 'new_qq_sends': 0, 'production_writes': False,
        'private_chat_or_profile_read': False, 'reasoning_content_exported': False,
        'source_files': evidence, 'algorithm_hashes': {'v3': BASELINE_SHA256, 'v31': digest(candidate_path.read_bytes()),
            'private_native_parser': PARSER_SHA256, 'private_native_helpers': HELPERS_SHA256,
            'private_native_tag_registry': TAG_REGISTRY_SHA256}, 'private_version': '6.7.0', 'private_commit': PRIVATE_COMMIT,
        'changed_plans': sum(r['plan_changed'] for r in rows), 'fallback_integrity_failures': failures,
        'counts': {key: {'eligible': sum(r[key]['fallback_eligible'] for r in rows),
                         'split': sum(r[key]['split'] for r in rows),
                         'reasons': dict(Counter(r[key]['final_plan']['reason'] for r in rows))} for key in ('v3', 'v31')},
        'codex_rule_review': 'PENDING_PER_SAMPLE_REVIEW', 'owner_naturalness': 'PENDING_OWNER_EVALUATION',
        'samples': rows}
    save_report(args.output_dir, report)
    return {k: report[k] for k in ('status', 'historical_real_api_responses', 'new_api_calls', 'new_qq_sends',
                                   'changed_plans', 'fallback_integrity_failures', 'counts')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', action='store_true')
    parser.add_argument('--output-dir', type=Path)
    try: result = run(parser.parse_args())
    except Exception as exc: result = {'status': 'FAILED', 'error_type': type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False))
    return int(result['status'] in {'FAILED', 'INTEGRITY_FAILURE'})


if __name__ == '__main__': raise SystemExit(main())
