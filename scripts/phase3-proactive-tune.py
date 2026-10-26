#!/usr/bin/env python3
"""Prepare a narrow proactive tuning candidate; never edit live files or restart.

Keeps an already enabled/disabled Runtime in that state. The candidate is saved
outside live data and Git, and must be published with matching Runtime sources.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from integrations.phase3_runtime.settings import tuned_proactive_candidate

spec = importlib.util.spec_from_file_location('proactive_tuning_private_files',
    Path(__file__).with_name('phase3-context-config.py'))
files = importlib.util.module_from_spec(spec)
spec.loader.exec_module(files)

NATIVE_FIELDS = {
    'basic_config': {'quiet_hours': '23:00-09:00'},
    'private_followup_config': {'min_interval_minutes': 120},
    'proactive_generation_config': {
        'proactive_generation_history_limit': 80,
        'proactive_history_context_mode': 'compact',
        'proactive_history_recent_raw_count': 8,
        'proactive_history_max_chars': 16000,
    },
}


def native_context_candidate(original):
    """Only the six reviewed fields; retain dedup, generation and other switches."""
    result = deepcopy(original)
    for group, changes in NATIVE_FIELDS.items():
        if not isinstance(result.get(group, {}), dict):
            raise ValueError('native_config_group_must_be_object')
        result.setdefault(group, {}).update(changes)
        for key, value in changes.items():
            if key in result:
                result[key] = deepcopy(value)
    return result


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save_candidate(runtime_source, private_source, output):
    sources = [Path(runtime_source), Path(private_source)]
    output = Path(output)
    destination = output.resolve()
    if output.exists() or output.is_symlink():
        raise FileExistsError('new_output_directory_required')
    for source in sources:
        boundary = source.parent.parent if source.parent.name == 'config' else source.parent
        if destination == boundary.resolve() or boundary.resolve() in destination.parents:
            raise ValueError('candidate_must_be_outside_live_data')
    if any((p / '.git').is_file() or (p / '.git/HEAD').is_file()
           for p in (destination, *destination.parents)):
        raise ValueError('private_candidate_must_be_outside_git_worktree')
    before = [files._read_private(p) for p in sources]
    runtime, native = [json.loads(raw.decode('utf-8-sig')) for raw in before]
    proposed = [tuned_proactive_candidate(runtime), native_context_candidate(native)]
    encoded = [files._encode_config(value, raw) for value, raw in zip(proposed, before)]
    if any(files._read_private(p) != raw for p, raw in zip(sources, before)):
        raise ValueError('source_changed_during_preparation')
    output.mkdir(mode=0o700)
    for name, raw, candidate in zip(('phase3_runtime', 'private_companion'), before, encoded):
        files._write_private(output / ('baseline-' + name + '_config.json'), raw)
        files._write_private(output / ('candidate-' + name + '_config.json'), candidate)
    report = {
        'status': 'SAVED_NOT_APPLIED', 'created_at': datetime.now(timezone.utc).isoformat(),
        'baseline_sha256': dict(zip(('runtime', 'private'), map(sha, before))),
        'candidate_sha256': dict(zip(('runtime', 'private'), map(sha, encoded))),
        'policy': proposed[0]['proactive_window'],
        'history': proposed[0]['proactive_history'],
        'native_config_changes': NATIVE_FIELDS,
        'runtime_proactive_switch_preserved': proposed[0].get('proactive', False) == runtime.get('proactive', False),
        'native_target_migration_required': {'proactive_daily_limit': 8,
            'proactive_min_interval_minutes': 'preserve inheritance, or narrow only an existing target override to 120'},
        'source_unchanged': all(files._read_private(p) == raw for p, raw in zip(sources, before)),
        'production_applied': False, 'service_restarted': False, 'model_calls': 0, 'qq_messages': 0,
        'ledger_reset': False, 'allowed_hours': '09:00:00 <= Asia/Shanghai time < 23:00:00',
        'activation': 'Publish matching first-party sources and only reviewed fields after backup/restore checks. '
            'Preserve current counters, candidates, pause/reply state and all chat/memory. '
            'Do not replace whole live configurations with stale candidate files.',
    }
    if not report['source_unchanged']:
        report['status'] = 'STALE_DO_NOT_APPLY'
    files._write_private(output / 'manifest.json', (json.dumps(report, ensure_ascii=False, indent=2) + '\n').encode())
    if not report['source_unchanged']:
        raise ValueError('source_changed_during_preparation')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-config', type=Path, required=True)
    parser.add_argument('--private-config', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(save_candidate(args.runtime_config, args.private_config, args.output_dir), ensure_ascii=False))
