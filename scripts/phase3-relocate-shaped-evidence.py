#!/usr/bin/env python3
"""Synthetic-only scope experiment; no native code, API, or fact changes.

The shaped fixture's memory part is JSON without the native context tags. Emit
a wrapper-only control as well as the relocated variant so model comparisons
can isolate scope from the added structural envelope.
"""
import copy
from hashlib import sha256
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'evidence/phase3/v2-shaped-request-20261023'
MARKER = '[PHASE3_PROFILE_EVIDENCE_V2]'
OPEN = '<memory_companion_context>'
CLOSE = '</memory_companion_context>'


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def main():
    metadata = json.loads((OUT / 'shape-summary.json').read_text())
    assert metadata['synthetic_only'] is True and metadata['production_payload_read'] is False
    original = json.loads((OUT / 'after-compact-refs.json').read_text())
    assert original['model'] == 'phase1-mock'
    parts = original['messages'][-1]['content']
    memory_index = next(i for i, p in enumerate(parts)
                        if '"fixture":"synthetic-current-native-memory-shape"' in p.get('text', ''))
    memory = parts[memory_index]['text']
    memory_data = json.loads(memory)
    assert len(memory_data['core_memory']) == 3 and len(memory_data['retrieved_memories']) == 4
    assert OPEN not in memory and CLOSE not in memory
    system = original['messages'][0]['content']
    begin = system.index(MARKER)
    data_begin = system.index('\n{', begin) + 1
    record_data, consumed = json.JSONDecoder().raw_decode(system[data_begin:])
    end = data_begin + consumed
    block = system[begin:end]
    assert system.count(MARKER) == 1

    control = copy.deepcopy(original)
    control['messages'][-1]['content'][memory_index]['text'] = OPEN + '\n' + memory + '\n' + CLOSE
    unified = copy.deepcopy(control)
    unified['messages'][0]['content'] = system[:begin] + system[end:]
    unified['messages'][-1]['content'][memory_index]['text'] = OPEN + '\n' + memory + '\n' + block + '\n' + CLOSE
    current = unified['messages'][-1]['content'][memory_index]['text']

    checks = {
        'all_non_message_request_fields_equal': {k:v for k,v in original.items() if k!='messages'} == {k:v for k,v in unified.items() if k!='messages'},
        'all_sixteen_history_messages_equal': original['messages'][1:-1] == unified['messages'][1:-1],
        'tools_equal': original['tools'] == unified['tools'],
        'all_non_memory_current_parts_equal': all(p == unified['messages'][-1]['content'][i] for i,p in enumerate(parts) if i!=memory_index),
        'system_outside_evidence_block_equal': unified['messages'][0]['content'] == system[:begin] + system[end:],
        'core_candidates_and_archive_bytes_equal': current[len(OPEN)+1:len(OPEN)+1+len(memory)] == memory,
        'original_evidence_block_bytes_preserved': block in current and current.count(MARKER)==1,
        'no_system_evidence_remains': MARKER not in unified['messages'][0]['content'],
        'inside_memory_closing_tag': current.index(MARKER) < current.index(CLOSE),
        'wrapper_control_system_unchanged': control['messages'][0] == original['messages'][0],
        'scope_only_delta_against_wrapper_control': current == control['messages'][-1]['content'][memory_index]['text'].replace('\n'+CLOSE, '\n'+block+'\n'+CLOSE),
    }
    assert all(checks.values())
    extracted = json.JSONDecoder().raw_decode(current[current.index('\n{', current.index(MARKER)) + 1:])[0]
    assert extracted == record_data
    checks['record_coverage_and_relations_equal'] = True
    for filename, value in [('wrapped-system-control.json', control), ('unified-native-context.json', unified)]:
        (OUT / filename).write_text(json.dumps(value, ensure_ascii=False, indent=2))
    report = {'status':'PASS','synthetic_only':True,'private_payload_read':False,
        'api_calls':0,'production_changed':False,'original': 'after-compact-refs.json',
        'control':'wrapped-system-control.json','variant':'unified-native-context.json',
        'level':'synthetic_offline_request_transform_not_native_T2_or_real_model',
        'important_boundary':'Original shaped memory is JSON without native tags. Both comparison files add the same structural envelope. Only the variant relocates the original unchanged evidence block.',
        'checks':checks,'original_request_sha256':digest(original),
        'control_request_sha256':digest(control),'variant_request_sha256':digest(unified),
        'evidence_block_sha256':sha256(block.encode()).hexdigest(),
        'evidence_json_sha256':digest(record_data),'memory_json_sha256':sha256(memory.encode()).hexdigest(),
        'memory_part_index':memory_index,'memory_json_chars':len(memory),
        'evidence_block_chars':len(block),'system_chars_before':len(system),
        'system_chars_unified':len(unified['messages'][0]['content']),
        'latest_memory_chars_control':len(control['messages'][-1]['content'][memory_index]['text']),
        'latest_memory_chars_unified':len(current)}
    (OUT/'unified-diffchecks.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({'status':'PASS','checks':len(checks),'evidence_block_chars':len(block),
        'original_data_unchanged':True,'api_calls':0}))


if __name__ == '__main__':
    main()
