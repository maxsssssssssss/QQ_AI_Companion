#!/usr/bin/env python3
"""Build entirely synthetic A/B HTTP bodies; not a native reassembly claim."""
import argparse
import copy
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
import phase3_request_shape_fixture as fixture
from integrations.phase3_runtime.profile_recall import MARKER, project_record_for_model


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--synthetic-state', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    marker = json.loads((args.synthetic_state / 'probe-snapshot.json').read_text())
    assert marker.get('synthetic_only') is True and marker.get('disposable') is True
    report = json.loads((args.synthetic_state / 'private/request-summary-r2.json').read_text())
    assert report['synthetic_fixture_imported'] is True and report['status'] == 'CAPTURED'
    baseline_path = args.synthetic_state / 'private/request-payload-r2.json'
    baseline = json.loads(baseline_path.read_text())
    assert len(baseline['messages']) == 18
    assert baseline['model'] == 'phase1-mock'
    # Only the previously captured SYNTHETIC input is accepted by this builder.
    assert 'synthetic-old-profile-tool-' in json.dumps(baseline)
    before = copy.deepcopy(baseline)
    before['messages'][1:-1] = fixture.failed_tool_history()
    system = before['messages'][0]['content']
    marker_index = system.index(MARKER)
    data_start = system.index('\n{', marker_index) + 1
    old_evidence, used = json.JSONDecoder().raw_decode(system[data_start:])
    record = {'id': 'synthetic-shape-game-evidence',
        'version': sha256(fixture.GAME_BODY.encode()).hexdigest(),
        'source_refs': [fixture.synthetic_ref(i) for i in range(33)],
        'text': fixture.GAME_BODY, 'confidence': 'user_confirmed', 'layer': 'L2'}
    old_evidence.update(status='found', coverage='complete_approved_topics', relation='list', temporal='unspecified', evidence=[record])
    prefix, suffix = system[:data_start], system[data_start + used:]
    before['messages'][0]['content'] = prefix + json.dumps(old_evidence, ensure_ascii=False) + suffix

    # Latest user structure stays multipart. Preserve the native reminder and
    # Private component; replace only the old synthetic memory block with
    # explicitly labelled synthetic core/candidate records, no gaming answer.
    latest = before['messages'][-1]
    assert latest['role'] == 'user' and isinstance(latest['content'], list)
    parts = latest['content']
    memory_index = next(i for i, p in enumerate(parts) if p.get('type') == 'text' and '用户自述（测试核心）' in p.get('text', ''))
    cores = [fixture.core_body('交流方式', '喜欢清楚、温和且不重复的表达。'),
             fixture.core_body('做事习惯', '习惯把复杂任务拆成若干可检查的步骤。'),
             '用户自述（测试核心）：希望清楚区分已有资料和未知信息。']
    candidates = [{'id': f'synthetic-native-candidate-{i}', 'kind': 'conversation_summary',
        'content': (f'合成日常资料：平时在{topic}方面，会先整理现有记录再安排下一步。'
            '资料中保留了日期、来源编号和修改缘由；已确认的日常细节可以引用，未记录的部分留待以后确认。')}
        for i, topic in enumerate(('书桌整理', '航海阅读', '植物观察', '纸模型制作'))]
    memory = {'fixture': 'synthetic-current-native-memory-shape',
        'core_memory': [{'id': f'synthetic-core-{i}', 'content': text} for i, text in enumerate(cores)],
        'retrieved_memories': candidates, 'coverage': 'partial', 'archive_notes': ''}
    target = 2921
    basic = json.dumps(memory, ensure_ascii=False, separators=(',', ':'))
    padding = '合成背景说明：记录按来源和修订日期保存，普通候选来自日常整理、阅读与观察。'
    memory['archive_notes'] = (padding * (target // len(padding) + 1))[:max(0, target - len(basic))]
    parts[memory_index]['text'] = json.dumps(memory, ensure_ascii=False, separators=(',', ':'))
    # No game title occurs in the current ordinary candidates or core blocks.
    assert not any(name in parts[memory_index]['text'] for name in fixture.COMMON_GAMES + fixture.EXPERIENCED_GAMES)
    assert len(parts[memory_index]['text']) == target

    after = copy.deepcopy(before)
    compact = copy.deepcopy(old_evidence)
    compact['evidence'] = [project_record_for_model(record)]
    after['messages'][0]['content'] = prefix + json.dumps(compact, ensure_ascii=False) + suffix
    assert before['messages'][1:] == after['messages'][1:]
    assert {k:v for k,v in before.items() if k != 'messages'} == {k:v for k,v in after.items() if k != 'messages'}
    args.output.mkdir(parents=True, exist_ok=True)
    for name, value in [('before-verbose-refs.json', before), ('after-compact-refs.json', after), ('expected.json', fixture.expected())]:
        (args.output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2))
    summary = {'schema': 'qq-companion.synthetic-shaped-request.v1', 'synthetic_only': True,
        'production_payload_read': False, 'production_changed': False, 'paid_api_calls': 0,
        'level': 'synthetic_model_fixture_derived_from_native_capture_not_new_T2_capture',
        'source_capture_sha256': sha256(baseline_path.read_bytes()).hexdigest(),
        'difference': 'Only V2 evidence projection: verbose source_refs before body versus body-first native id/version plus source_count/digest.',
        'message_roles': [m['role'] for m in before['messages']],
        'tool_pairs': sum(len(m.get('tool_calls', [])) for m in before['messages']),
        'tool_body_chars': [len(m['content']) for m in before['messages'] if m['role'] == 'tool'],
        'game_body_chars': len(fixture.GAME_BODY), 'refs': 33, 'ref_chars': 94,
        'native_shaped_core_count': len(cores), 'ordinary_candidate_count': len(candidates),
        'latest_user_text_part_chars': [len(p.get('text', '')) for p in parts],
        'before_system_chars': len(before['messages'][0]['content']),
        'after_system_chars': len(after['messages'][0]['content']),
        'before_profile_body_offset': before['messages'][0]['content'].index(fixture.GAME_BODY),
        'after_profile_body_offset': after['messages'][0]['content'].index(fixture.GAME_BODY),
        'tools': [t['function']['name'] for t in before['tools']]}
    (args.output / 'shape-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
