#!/usr/bin/env python3
"""Narrow, stopped-service installer for the reviewed unified r4 release.

PLAN is read-only. Only thirteen first-party files and five reviewed JSON
configuration deltas are installed; databases, persona, login state, upstream
sources, budgets and Compose are never written. Runtime/Guard proactive arming
requires the existing native migration receipt. Native global generation stays
cold on disk. This does not invoke or alter the older V3 release controller.

Every before/after file is journaled privately before replacement. Rollback is
code/config-only, never resurrects old conversation data, and refuses newer
configuration values. Neither operation stops, starts or restarts a service.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

R4_MANIFEST_SHA256 = '3f9b5277804f5ee5e5c2a13c5f8b9a21f7593231e1e9f94858fab41150b7f0ae'
PINNED_ASTRBOT_IMAGE = 'sha256:a0bf74942fc2ba504932897f7f6ba8d8bd01c1084ea6448c399b8df579bc77af'
OVERLAY = tuple(['phase1_guard/' + n for n in ('main.py', 'bubbles.py', '_conf_schema.json')]
    + ['phase3_runtime/' + n for n in ('main.py', 'settings.py', 'proactive.py', 'context_compression.py', '_conf_schema.json')]
    + ['phase3_search/' + n for n in ('policy.py', 'main.py', 'runtime.py', 'contract.py', '_conf_schema.json')])
CONFIGS = {
    'cmd': ('cmd_config.json', 'state/data/cmd_config.json'),
    'runtime': ('phase3_runtime_config.json', 'state/data/config/phase3_runtime_config.json'),
    'private': ('private_companion_config.json', 'state/data/config/astrbot_plugin_private_companion_config.json'),
    'guard': ('phase1_guard_config.json', 'state/data/config/phase1_guard_config.json'),
    'search': ('phase3_search_config.json', 'state/data/config/phase3_search_config.json'),
}
DESTINATIONS = {'state/data/plugins/' + p for p in OVERLAY} | {p for _, p in CONFIGS.values()}
OPTIONAL_NEW = {'state/data/plugins/phase3_runtime/context_compression.py'}
SCHEMA = 'qq-companion.unified-r4-journal.v1'
MAX_FILE = 4 * 1024 * 1024


class ReleaseError(Exception):
    """Fixed error codes only; never include private file content."""


def require(condition, code):
    if not condition:
        raise ReleaseError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def safe(path):
    path = Path(path).absolute()
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'symlink_rejected')
    return path


def relative(root, name):
    rel = Path(name)
    require(isinstance(name, str) and not rel.is_absolute() and '..' not in rel.parts
            and name == rel.as_posix() and name not in {'', '.'}, 'unsafe_relative_path')
    return safe(root / rel)


def read(path):
    path = safe(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_size <= MAX_FILE, 'regular_bounded_file_required')
        value = source.read(MAX_FILE + 1)
    require(len(value) <= MAX_FILE, 'oversized_file')
    return value


def decode(raw):
    return json.loads(raw.decode('utf-8-sig'))


def encode(value, original=b''):
    return (b'\xef\xbb\xbf' if original.startswith(b'\xef\xbb\xbf') else b'') + (
        json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()


def create(path, raw):
    safe(path)
    missing, parent = [], path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())


def replace(path, raw, uid, gid):
    safe(path)
    fd, name = tempfile.mkstemp(prefix='.unified-r4-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
            os.fchmod(output.fileno(), 0o600)
            if os.geteuid() == 0:
                os.fchown(output.fileno(), uid, gid)
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def json_equal(a, b):
    # Unlike Python equality, true and 1 are different configuration values.
    return json.dumps(a, sort_keys=True, ensure_ascii=False, separators=(',', ':')) == json.dumps(
        b, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def current_bytes(path):
    path = safe(path)
    return read(path) if path.exists() else None


MISSING = object()


def diff(before, after, path=()):
    if isinstance(before, dict) and isinstance(after, dict):
        result = []
        for key in sorted(set(before) | set(after)):
            if key not in before or key not in after:
                result.append(path + (key,))
            else:
                result.extend(diff(before[key], after[key], path + (key,)))
        return result
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        return [p for i, (a, b) in enumerate(zip(before, after)) for p in diff(a, b, path + (i,))]
    return [] if json_equal(before, after) else [path]


def label(path):
    result = ''
    for part in path:
        result += f'[{part}]' if isinstance(part, int) else ('.' if result else '') + part
    return result


def lookup(value, path):
    for part in path:
        if isinstance(part, int):
            if not isinstance(value, list) or not 0 <= part < len(value):
                return MISSING
        elif not isinstance(value, dict) or part not in value:
            return MISSING
        value = value[part]
    return value


def allowed(kind, path):
    text = label(path)
    if kind == 'cmd':
        return (text in {'agent_runner.config.compression.' + k for k in (
            'instruction', 'max_turns', 'overflow_strategy', 'keep_recent_ratio', 'provider_id', 'fallback_max_tokens')}
        or bool(re.fullmatch(r'provider\[\d+\]\.max_context_tokens', text)))
    if kind == 'runtime':
        return text == 'context_compression_fallback_turns' or text in {
            'proactive_window.' + k for k in ('mode', 'window_start', 'window_end', 'max_per_day',
                'max_generations_per_day', 'cooldown', 'quiet_hours', 'quiet_hours[0]', 'quiet_hours[1]')}
    if kind == 'private':
        return text in {'basic_config.quiet_hours', 'private_followup_config.min_interval_minutes',
            'quiet_hours', 'min_interval_minutes', 'enable_auto_user_profile_creation', 'enable_meal_care_proactive',
            'enabled_proactive_actions', 'legacy_compat_config.enabled_proactive_actions'}
    return text in ({'phase3_bubble_min_interval', 'phase3_bubble_max_interval'} if kind == 'guard'
                    else {'scope', 'public_terms', 'fetch_hosts'})


def merge(kind, baseline, candidate, current, declared):
    changes = diff(baseline, candidate)
    require(sorted(map(label, changes)) == sorted(declared), 'manifest_config_diff_mismatch')
    require(all(allowed(kind, path) for path in changes), 'unreviewed_config_field')
    result = deepcopy(current)
    for path in changes:
        if kind == 'cmd' and len(path) == 3 and path[0] == 'provider' and isinstance(path[1], int):
            identities = [lookup(obj, path[:2] + ('id',)) for obj in (baseline, candidate, current)]
            require(identities == ['deepseek/flash'] * 3, 'provider_order_or_identity_changed')
        old, new, now = lookup(baseline, path), lookup(candidate, path), lookup(current, path)
        require(new is not MISSING, 'candidate_field_deletion_rejected')
        same_old = old is MISSING and now is MISSING or old is not MISSING and now is not MISSING and json_equal(old, now)
        same_new = now is not MISSING and json_equal(now, new)
        require(same_old or same_new, 'reviewed_field_conflict')
        target = lookup(result, path[:-1])
        require(target is not MISSING, 'config_parent_missing')
        target[path[-1]] = deepcopy(new)
    return result


def nested_values(value, key):
    if not isinstance(value, dict):
        return []
    return ([value[key]] if key in value else []) + [item for child in value.values()
        if isinstance(child, dict) for item in nested_values(child, key)]


def validate_configs(values, identity, proactive):
    cmd, runtime, private, guard, search = [values[k] for k in CONFIGS]
    admin, platform = identity.get('admin_id'), identity.get('platform_id')
    require(isinstance(admin, str) and bool(re.fullmatch(r'\d{5,15}', admin))
            and isinstance(platform, str) and bool(platform), 'invalid_identity')
    require((guard.get('phase3_admin_id'), guard.get('phase3_platform_id')) == (admin, platform)
            and (search.get('admin_id'), search.get('platform_id')) == (admin, platform), 'identity_mismatch')
    require(cmd.get('platform_settings', {}).get('segmented_reply', {}).get('enable') is False,
            'global_segmentation_must_remain_off')
    runner = cmd.get('agent_runner', {})
    require(runner.get('runner_type') == 'local' and runner.get('config', {}).get('model', {}).get('provider_id')
            == 'deepseek/flash', 'fixed_runner_required')
    models = [m for m in cmd.get('provider', []) if m.get('id') == 'deepseek/flash']
    require(len(models) == 1 and models[0].get('model') == 'deepseek-flash' and models[0].get('enable') is True,
            'fixed_model_required')
    sources = [s for s in cmd.get('provider_sources', []) if s.get('id') == models[0].get('provider_source_id')]
    require(len(sources) == 1 and sources[0].get('enable') is True, 'fixed_source_required')
    effective = {**sources[0], **models[0]}
    require(effective.get('type') == 'openai_chat_completion' and effective.get('api_base') in {
        'https://api.deepseek.com', 'https://api.deepseek.com/v1'} and effective.get('custom_extra_body', {}).get(
        'thinking') == {'type': 'disabled'}, 'official_flash_thinking_off_required')
    compression = runner['config'].get('compression', {})
    require(compression.get('max_turns') == -1 and compression.get('overflow_strategy') == 'llm_compress'
            and models[0].get('max_context_tokens') == 128000 and runtime.get('context_compression_fallback_turns') == 80,
            'compression_candidate_mismatch')
    require(all(runtime.get(k) is True for k in ('enabled', 'persona', 'natural_chat', 'memory', 'search'))
            and runtime.get('background_web') is False, 'runtime_feature_mismatch')
    require(runtime.get('proactive') is proactive and guard.get('phase3_proactive_enabled') is proactive,
            'proactive_switch_mismatch')
    required = dict(mode='continuous', window_start=0, window_end=0, max_per_day=5,
                    max_generations_per_day=5, cooldown=10800, quiet_hours=[0, 9])
    require(json_equal(runtime.get('proactive_window'), required), 'continuous_policy_mismatch')
    require(search.get('scope') == 'general_public' and search.get('public_terms') == [] and search.get('fetch_hosts') == []
            and search.get('daily_attempt_limit', 60) == 60 and search.get('limits', {}).get('max_calls', 3) == 3,
            'search_limits_mismatch')
    require(guard.get('phase3_casual_split_fallback_enabled') is True
            and guard.get('phase3_bubble_min_interval') == 1.0 and guard.get('phase3_bubble_max_interval') == 2.0,
            'v31_config_mismatch')
    flags, limits = nested_values(private, 'enable_llm_proactive_message'), nested_values(private, 'max_daily_messages')
    require(bool(flags) and bool(limits) and all(v is False for v in flags)
            and all(type(v) is int and v == 0 for v in limits), 'native_disk_generation_must_remain_cold')
    require(private.get('enable_auto_user_profile_creation') is False and private.get('enable_meal_care_proactive') is False
            and private.get('enabled_proactive_actions') == 'message'
            and private.get('legacy_compat_config', {}).get('enabled_proactive_actions') == 'message',
            'native_text_only_policy_required')


def verify_native_receipt(deployment, path):
    require(path is not None, 'native_migration_receipt_required')
    receipt = decode(read(path))
    require(receipt.get('status') == 'APPLIED' and receipt.get('native_api_called') is True
            and receipt.get('unique_existing_target') is True and receipt.get('daily_limit') == 5
            and receipt.get('global_generation_enabled') is False, 'native_migration_receipt_invalid')
    store = deployment / 'state/data/plugin_data/astrbot_plugin_private_companion/companions.json'
    require(sha(read(store)) == receipt.get('candidate_sha256'), 'native_store_changed_since_migration')


def verify_stopped(deployment, expected_image):
    command = ['docker', 'compose', '--project-directory', str(deployment), '-f', str(deployment / 'compose.yml')]
    result = subprocess.run(command + ['ps', '--all', '--quiet', 'astrbot'], capture_output=True, timeout=20, check=True)
    ids = result.stdout.decode().split()
    require(len(ids) == 1, 'one_astrbot_required')
    template = ('{"running":{{json .State.Running}},"status":{{json .State.Status}},"image":{{json .Image}},'
        '"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
        '"project":{{json (index .Config.Labels "com.docker.compose.project")}},"mounts":{{json .Mounts}}}')
    result = subprocess.run(['docker', 'inspect', '--format', template, ids[0]], capture_output=True, timeout=20, check=True)
    state = json.loads(result.stdout)
    require(state.get('running') is False and state.get('status') in {'exited', 'created'}
            and state.get('service') == 'astrbot' and state.get('project') == 'qq-ai-companion'
            and state.get('image') == expected_image, 'pinned_astrbot_must_be_stopped')
    require(any(m.get('Source') == str(deployment / 'state/data') and m.get('Destination') == '/AstrBot/data'
                for m in state.get('mounts', [])), 'production_mount_mismatch')


def plan(candidate, deployment, *, expected_manifest_sha256=R4_MANIFEST_SHA256,
         enable_proactive=False, native_migration_result=None, clean_baseline_receipt=None):
    candidate, deployment = safe(candidate), safe(deployment)
    manifest_raw, receipt_raw = read(candidate / 'manifest.json'), read(candidate / 'sync-receipt.json')
    manifest, receipt = decode(manifest_raw), decode(receipt_raw)
    require(sha(manifest_raw) == expected_manifest_sha256 == receipt.get('manifest_sha256'), 'manifest_digest_mismatch')
    require(manifest.get('status') == receipt.get('status') == 'SYNCED_NOT_APPLIED'
            and receipt.get('production_applied') is False, 'verified_pending_release_required')
    require(set(manifest.get('runtime_overlay', [])) == set(OVERLAY)
            and len(manifest['runtime_overlay']) == len(OVERLAY), 'overlay_inventory_mismatch')
    for folder, hashes in [('source', manifest['source_files']), ('config-candidate', manifest['candidate_config_hashes'])]:
        found = {p.relative_to(candidate / folder).as_posix() for p in (candidate / folder).rglob('*') if p.is_file()}
        require(found == set(hashes), 'candidate_inventory_mismatch')
        for name, expected in hashes.items():
            require(sha(read(relative(candidate / folder, name))) == expected, 'candidate_file_digest_mismatch')
    require(receipt.get('source_hashes') == manifest['source_files'], 'receipt_source_mismatch')
    require(receipt.get('services', {}).get('astrbot', {}).get('image') == PINNED_ASTRBOT_IMAGE,
            'release_image_mismatch')
    expected_configs = {prefix + name for name, _ in CONFIGS.values() for prefix in ('baseline-', 'candidate-')}
    require(set(manifest['candidate_config_hashes']) == expected_configs, 'configuration_inventory_mismatch')
    # Configuration merges compare actual parsed values; unrelated edits are
    # retained. Every other reviewed live code/persona/policy/Compose file must
    # still match the original baseline byte-for-byte.
    config_paths = {p for _, p in CONFIGS.values()}
    require(all('state/data/plugins/' + p in manifest['live_baseline'] for p in OVERLAY
                if 'state/data/plugins/' + p not in OPTIONAL_NEW), 'live_code_baseline_missing')
    for name, expected in manifest['live_baseline'].items():
        if name not in config_paths:
            require(sha(read(relative(deployment, name))) == expected, 'live_baseline_drift')
    values, records = {}, []
    for kind, (name, destination) in CONFIGS.items():
        base = decode(read(candidate / 'config-candidate' / ('baseline-' + name)))
        new = decode(read(candidate / 'config-candidate' / ('candidate-' + name)))
        old_raw = read(relative(deployment, destination))
        values[kind] = merge(kind, base, new, decode(old_raw), manifest['configuration_changes'][kind])
        require(values[kind] is not None, 'invalid_merged_config')
        records.append({'path': destination, 'old': old_raw, 'kind': 'config', 'config_name': kind})
    require(values['runtime'].get('proactive') is False and values['guard'].get('phase3_proactive_enabled') is False,
            'source_proactive_must_be_cold')
    if enable_proactive:
        verify_native_receipt(deployment, native_migration_result)
        values['runtime']['proactive'] = True
        values['guard']['phase3_proactive_enabled'] = True
    identity = decode(read(deployment / 'state/data/config/phase2_policy_config.json'))
    validate_configs(values, identity, enable_proactive)
    for record in records:
        record['new'] = encode(values[record['config_name']], record['old'])
    for name in OVERLAY:
        destination = 'state/data/plugins/' + name
        target = relative(deployment, destination)
        if destination not in manifest['live_baseline']:
            require(destination in OPTIONAL_NEW and current_bytes(target) is None, 'unexpected_new_live_file')
        records.append({'path': destination, 'old': current_bytes(target),
                        'new': read(candidate / 'source/integrations' / name), 'kind': 'code'})
    proof = None
    if clean_baseline_receipt is not None:
        proof_raw = read(clean_baseline_receipt)
        proof = decode(proof_raw)
        require(proof.get('status') == 'PASS' and proof.get('old_content_removed') is True
                and proof.get('persona_profile_preserved') is True, 'clean_baseline_receipt_invalid')
        proof = sha(proof_raw)
    return {'records': records, 'image': receipt['services']['astrbot']['image'],
        'manifest_sha256': sha(manifest_raw), 'native_receipt_sha256': sha(read(native_migration_result)) if enable_proactive else None,
        'clean_baseline_receipt_sha256': proof, 'proactive_enabled': enable_proactive}


def summary(value, status):
    return {'status': status, 'file_count': len(value['records']), 'code_files': 13, 'config_files': 5,
        'proactive_enabled': value['proactive_enabled'], 'databases_touched': False,
        'service_started': False, 'model_calls': 0, 'qq_messages': 0, 'manifest_sha256': value['manifest_sha256']}


def install(candidate, deployment, journal, *, apply=False, check_stopped=verify_stopped, **kwargs):
    deployment, journal = safe(deployment), safe(journal)
    require(journal != deployment and deployment not in journal.parents, 'journal_must_be_outside_deployment')
    require(not any((p / '.git').is_file() or (p / '.git/HEAD').is_file() for p in (journal, *journal.parents)),
            'private_journal_must_be_outside_git_worktree')
    require(not journal.exists(), 'new_journal_required')
    value = plan(candidate, deployment, **kwargs)
    if not apply:
        return summary(value, 'PLAN')
    check_stopped(deployment, value['image'])
    journal.mkdir(mode=0o700)
    create(journal / 'operation.lock', b'')
    with (journal / 'operation.lock').open('rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        records = []
        for entry in value['records']:
            destination = relative(deployment, entry['path'])
            require(current_bytes(destination) == entry['old'], 'changed_during_preparation')
            info = destination.stat() if destination.exists() else destination.parent.stat()
            for prefix, key in [('before', 'old'), ('after', 'new')]:
                if entry[key] is not None:
                    create(relative(journal / prefix, entry['path']), entry[key])
            records.append({'path': entry['path'], 'kind': entry['kind'], 'existed': entry['old'] is not None,
                'old_sha256': sha(entry['old']) if entry['old'] is not None else None,
                'new_sha256': sha(entry['new']), 'uid': info.st_uid, 'gid': info.st_gid})
        metadata = {k: v for k, v in value.items() if k != 'records'}
        metadata.update(schema=SCHEMA, deployment=str(deployment), records=records,
                        created_at=datetime.now(timezone.utc).isoformat())
        create(journal / 'manifest.json', encode(metadata))
        create(journal / 'status.json', encode({'status': 'PREPARED'}))
        check_stopped(deployment, value['image'])
        # Final complete read barrier, before any live file is changed.
        require(all(current_bytes(relative(deployment, e['path'])) == e['old'] for e in value['records']), 'changed_before_install')
        replace(journal / 'status.json', encode({'status': 'APPLYING'}), os.getuid(), os.getgid())
        completed = []
        try:
            for entry, record in zip(value['records'], records):
                require(current_bytes(relative(deployment, entry['path'])) == entry['old'], 'changed_during_install')
                completed.append(record)
                replace(relative(deployment, entry['path']), entry['new'], record['uid'], record['gid'])
            require(all(sha(read(relative(deployment, r['path']))) == r['new_sha256'] for r in records), 'installed_digest_mismatch')
        except BaseException:
            for record in reversed(completed):
                target = relative(deployment, record['path'])
                current = current_bytes(target)
                if (sha(current) if current is not None else None) == record['old_sha256']:
                    continue
                require(current is not None and sha(current) == record['new_sha256'], 'concurrent_write_requires_manual_recovery')
                if record['existed']:
                    replace(target, read(relative(journal / 'before', record['path'])), record['uid'], record['gid'])
                else:
                    target.unlink()
            replace(journal / 'status.json', encode({'status': 'FAILED_RESTORED'}), os.getuid(), os.getgid())
            raise
        result = summary(value, 'INSTALLED_SERVICES_STOPPED')
        replace(journal / 'status.json', encode(result), os.getuid(), os.getgid())
        return result


def rollback(deployment, journal, *, apply=False, check_stopped=verify_stopped):
    deployment, journal = safe(deployment), safe(journal)
    metadata = decode(read(journal / 'manifest.json'))
    require(metadata.get('schema') == SCHEMA and metadata.get('deployment') == str(deployment), 'journal_identity_mismatch')
    records = metadata.get('records', [])
    require(len(records) == len(DESTINATIONS) and {r['path'] for r in records} == DESTINATIONS, 'journal_inventory_mismatch')
    require(decode(read(journal / 'status.json')).get('status') in {'INSTALLED_SERVICES_STOPPED', 'APPLYING', 'FAILED_RESTORED'},
            'journal_status_invalid')
    restore = []
    for record in records:
        before = read(relative(journal / 'before', record['path'])) if record.get('existed') else None
        after = read(relative(journal / 'after', record['path']))
        require(before is not None or record['path'] in OPTIONAL_NEW, 'journal_missing_original')
        require((sha(before) if before is not None else None) == record['old_sha256']
                and sha(after) == record['new_sha256'], 'journal_digest_mismatch')
        current = current_bytes(relative(deployment, record['path']))
        same_before, same_after = current == before, current == after
        if record['kind'] == 'config' and current is not None:
            same_before, same_after = json_equal(decode(current), decode(before)), json_equal(decode(current), decode(after))
        require(same_before or same_after, 'rollback_would_overwrite_newer_data')
        restore.append((record, before, current))
    result = {'status': 'ROLLBACK_PLAN', 'file_count': len(records), 'databases_touched': False, 'service_started': False}
    if not apply:
        return result
    check_stopped(deployment, metadata['image'])
    with (journal / 'operation.lock').open('rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(all(current_bytes(relative(deployment, r['path'])) == current for r, _, current in restore), 'changed_before_rollback')
        for record, before, current in reversed(restore):
            target = relative(deployment, record['path'])
            require(current_bytes(target) == current, 'changed_during_rollback')
            if before is not None:
                replace(target, before, record['uid'], record['gid'])
            elif target.exists():
                target.unlink()
        require(all((sha(raw) if (raw := current_bytes(relative(deployment, r['path']))) is not None else None)
                    == r['old_sha256'] for r in records), 'rollback_digest_mismatch')
        result['status'] = 'ROLLED_BACK_SERVICES_STOPPED'
        replace(journal / 'status.json', encode(result), os.getuid(), os.getgid())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--deployment', type=Path, required=True)
    parser.add_argument('--journal', type=Path, required=True)
    parser.add_argument('--expected-manifest-sha256', default=R4_MANIFEST_SHA256)
    parser.add_argument('--enable-proactive', action='store_true')
    parser.add_argument('--native-migration-result', type=Path)
    parser.add_argument('--clean-baseline-receipt', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.rollback:
            result = rollback(args.deployment, args.journal, apply=args.apply)
        else:
            require(args.candidate is not None, 'candidate_required')
            options = vars(args).copy()
            options.pop('rollback')
            result = install(**options)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'BLOCKED', 'reason': str(exc) if isinstance(exc, ReleaseError) else 'operation_failed',
                          'services_started': False}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
