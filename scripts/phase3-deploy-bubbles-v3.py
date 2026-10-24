#!/usr/bin/env python3
"""PLAN by default: three Guard files and one independent fallback flag.

Reuses the pinned AstrBot-only controller's service and backup primitives.
Never restores live databases, changes pending releases, restarts NapCat or
enables proactive messages. Every replacement is journaled before writing.
"""
import argparse
import codecs
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys

RUNTIME_HELPER_SHA256 = '4484c510fcc4994cd39eba9138edbe66713f0098ca00c345789a2082e776ea82'
_helper_path = Path(__file__).with_name('phase3-deploy-runtime-code.py')
if hashlib.sha256(_helper_path.read_bytes()).hexdigest() != RUNTIME_HELPER_SHA256:
    raise ValueError('pinned_runtime_release_helper_changed')
_spec = importlib.util.spec_from_file_location('bubbles_v3_release_primitives', _helper_path)
core = importlib.util.module_from_spec(_spec); sys.modules[_spec.name] = core
_spec.loader.exec_module(core)
ReleaseError, Services = core.ReleaseError, core.Services
read, sha, safe, private_json = core.read, core.sha, core.safe, core.private_json
verify_native = core.verify_native
TARGETS = ('main.py', 'bubbles.py', '_conf_schema.json')
PREFIX = 'data/plugins/phase1_guard/'
GUARD_CONFIG = 'data/config/phase1_guard_config.json'
FLAG = 'phase3_casual_split_fallback_enabled'
PLUGINS = ('phase1_guard', 'phase2_policy', 'phase3_runtime', 'phase3_memory', 'phase3_search')
SCHEMA = 'qq-companion.bubbles-v3-release.v1'
JOURNAL_SCHEMA = 'qq-companion.bubbles-v3-journal.v1'
EXPECTED = {*core.PINNED, 'scripts/phase3-deploy-runtime-code.py',
            'scripts/phase3-deploy-bubbles-v3.py',
            *('integrations/phase1_guard/' + n for n in TARGETS)}
DESTINATIONS = {PREFIX + n for n in TARGETS} | {GUARD_CONFIG}


def inventory(deployment):
    """Complete current first-party source/config/persona inventory, no fixed count."""
    state = deployment / 'state'
    names = {'data/cmd_config.json'}
    for plugin in PLUGINS:
        directory = safe(state, 'data/plugins/' + plugin)
        if not directory.is_dir(): raise ReleaseError('first_party_directory_missing')
        for path in directory.rglob('*'):
            safe(state, path.relative_to(state))
            if '__pycache__' not in path.parts and path.is_file() and path.suffix in {'.py', '.json', '.yaml', '.yml', '.md'}:
                names.add(path.relative_to(state).as_posix())
    for path in safe(state, 'data/config').rglob('*'):
        safe(state, path.relative_to(state))
        if path.is_file(): names.add(path.relative_to(state).as_posix())
    personas = [p for p in safe(state, 'data/phase3').glob('persona*') if p.is_file()]
    if not personas: raise ReleaseError('persona_inventory_missing')
    names.update(p.relative_to(state).as_posix() for p in personas)
    for name in names:
        path = safe(state, name)
        if not path.is_file() or not stat.S_ISREG(path.stat().st_mode):
            raise ReleaseError('protected_regular_file_required')
    return names


def candidate_config(raw):
    value = json.loads(raw.decode('utf-8-sig'))
    if not isinstance(value, dict): raise ReleaseError('guard_object_required')
    value[FLAG] = True
    encoded = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    return (codecs.BOM_UTF8 if raw.startswith(codecs.BOM_UTF8) else b'') + encoded


def check_policy(deployment):
    state = deployment / 'state'
    cmd = read(safe(state, 'data/cmd_config.json'))
    if cmd.get('platform_settings', {}).get('segmented_reply', {}).get('enable') is not False:
        raise ReleaseError('global_segmented_reply_must_remain_off')
    try:
        model_id = cmd['agent_runner']['config']['model']['provider_id']
        models = [m for m in cmd['provider'] if m['id'] == model_id]
        if len(models) != 1: raise KeyError('model')
        model = models[0]
        sources = [s for s in cmd['provider_sources'] if s['id'] == model['provider_source_id']]
        if len(sources) != 1: raise KeyError('source')
        effective = {**sources[0], **model}
        valid = (model.get('enable') is True and sources[0].get('enable') is True
            and effective.get('model') == 'deepseek-flash'
            and effective.get('type') == 'openai_chat_completion'
            and effective.get('api_base') in {'https://api.deepseek.com', 'https://api.deepseek.com/v1'}
            and effective.get('custom_extra_body', {}).get('thinking') == {'type': 'disabled'})
    except (KeyError, TypeError): valid = False
    if not valid: raise ReleaseError('active_official_flash_thinking_off_required')
    runtime = read(safe(state, 'data/config/phase3_runtime_config.json'))
    guard = read(safe(state, GUARD_CONFIG))
    private = read(safe(state, 'data/config/astrbot_plugin_private_companion_config.json'))
    values = []
    def walk(value):
        if isinstance(value, dict):
            for k, v in value.items():
                if k == 'enable_llm_proactive_message': values.append(v)
                else: walk(v)
    walk(private)
    if (runtime.get('proactive') is not False or guard.get('phase3_proactive_enabled') is not False
            or not values or any(v is not False for v in values)):
        raise ReleaseError('proactive_must_remain_off')


def journal_rows(source, manifest):
    journal = safe(source, 'bubbles-v3-journal')
    data = read(safe(journal, 'journal.json'))
    rows = data.get('files', [])
    if (data.get('schema') != JOURNAL_SCHEMA or len(rows) != 4
            or {r.get('path') for r in rows} != DESTINATIONS
            or data.get('database_restore_permitted') is not False):
        raise ReleaseError('exact_four_file_journal_required')
    for row in rows:
        relative = row['path']
        expected_new = (manifest['guard_config_after_sha256'] if relative == GUARD_CONFIG
            else manifest['files']['integrations/phase1_guard/' + Path(relative).name])
        if (row.get('old_sha256') != manifest['protected_hashes'][relative]
                or row.get('new_sha256') != expected_new):
            raise ReleaseError('journal_hash_contract_mismatch')
        old = safe(journal, 'original/' + relative)
        if not old.is_file() or sha(old) != row['old_sha256']:
            raise ReleaseError('original_journal_corrupt')
    return rows


def validate(source, deployment, *, installed=False, partial=False):
    manifest = read(safe(source, 'source-manifest.json'))
    change = manifest.get('config_change', {})
    if (manifest.get('schema') != SCHEMA or not isinstance(change, dict)
            or set(change) != {FLAG} or change[FLAG] is not True):
        raise ReleaseError('reviewed_bubbles_v3_manifest_required')
    files = manifest.get('files', {})
    if set(files) != EXPECTED: raise ReleaseError('exact_narrow_source_package_required')
    observed = {p.relative_to(source).as_posix() for directory in ('scripts', 'config', 'integrations')
        for p in safe(source, directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    if observed != EXPECTED: raise ReleaseError('source_package_inventory_changed')
    for name, digest in files.items():
        path = safe(source, name)
        if not re.fullmatch('[0-9a-f]{64}', str(digest)) or not path.is_file() or path.stat().st_size > 1024 * 1024 or sha(path) != digest:
            raise ReleaseError('reviewed_source_hash_mismatch')
    if any(files[name] != digest for name, digest in core.PINNED.items()):
        raise ReleaseError('locked_helper_or_dependency_changed')
    if (files['scripts/phase3-deploy-runtime-code.py'] != RUNTIME_HELPER_SHA256
            or files['scripts/phase3-deploy-bubbles-v3.py'] != sha(Path(__file__))):
        raise ReleaseError('executing_controller_not_reviewed')
    protected = manifest.get('protected_hashes', {})
    if set(protected) != inventory(deployment) or not DESTINATIONS <= protected.keys():
        raise ReleaseError('complete_current_protected_inventory_required')
    for name, old in protected.items():
        if not re.fullmatch('[0-9a-f]{64}', str(old)): raise ReleaseError('invalid_baseline_hash')
        new = (manifest.get('guard_config_after_sha256') if name == GUARD_CONFIG else
            files['integrations/phase1_guard/' + Path(name).name] if name in DESTINATIONS else old)
        expected = {old, new} if partial else {new if installed else old}
        if sha(safe(deployment / 'state', name)) not in expected:
            raise ReleaseError('protected_file_drift')
    if installed or partial:
        journal_rows(source, manifest)
        raw = safe(source, 'bubbles-v3-journal/original/' + GUARD_CONFIG).read_bytes()
    else:
        raw = safe(deployment / 'state', GUARD_CONFIG).read_bytes()
    if hashlib.sha256(candidate_config(raw)).hexdigest() != manifest.get('guard_config_after_sha256'):
        raise ReleaseError('single_config_field_candidate_mismatch')
    if sha(safe(deployment, 'compose.yml')) != manifest.get('compose_sha256') or sha(safe(deployment, '.env')) != manifest.get('env_sha256'):
        raise ReleaseError('compose_or_image_environment_changed')
    check_policy(deployment)
    verify_native(source, deployment)
    return manifest


def prepare_journal(source, deployment, manifest):
    journal = safe(source, 'bubbles-v3-journal')
    if journal.exists(): raise ReleaseError('fresh_bubbles_journal_required')
    journal.mkdir(mode=0o700)
    rows = []
    for relative in [*(PREFIX + n for n in TARGETS), GUARD_CONFIG]:
        target = safe(deployment / 'state', relative); info = target.stat()
        old = safe(journal, 'original/' + relative)
        old.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(target, old); old.chmod(0o600)
        rows.append({'path': relative, 'old_sha256': sha(old),
            'new_sha256': manifest['guard_config_after_sha256'] if relative == GUARD_CONFIG else
                manifest['files']['integrations/phase1_guard/' + target.name],
            'uid': info.st_uid, 'gid': info.st_gid, 'mode': stat.S_IMODE(info.st_mode)})
    candidate = journal / 'candidate-guard-config.json'
    candidate.write_bytes(candidate_config((journal / 'original' / GUARD_CONFIG).read_bytes())); candidate.chmod(0o600)
    private_json(journal / 'journal.json', {'schema': JOURNAL_SCHEMA, 'files': rows,
        'database_restore_permitted': False, 'only_config_field': FLAG})
    journal_rows(source, manifest)
    return rows


def restore_files(source, deployment, manifest, installer, services):
    services.check(astr_running=False, require_nap=False)
    validate(source, deployment, partial=True)
    for row in reversed(journal_rows(source, manifest)):
        target = safe(deployment / 'state', row['path'])
        if sha(target) not in {row['old_sha256'], row['new_sha256']}:
            raise ReleaseError('file_changed_during_rollback')
        installer.publish(safe(source, 'bubbles-v3-journal/original/' + row['path']), target, row['uid'], row['gid'])
        target.chmod(row['mode'])
    validate(source, deployment)


def execute(args, *, services=None):
    source, deployment = args.source.absolute(), args.deployment.absolute()
    for path in (source, deployment): safe(path)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', args.run_id): raise ReleaseError('invalid_run_id')
    rolling_back = bool(args.rollback)
    manifest = validate(source, deployment, installed=rolling_back)
    services = services or Services(deployment)
    services.original = services.snapshot()
    initially_running = services.original['astrbot']['running']
    if not services.original['napcat']['running'] or (not rolling_back and not initially_running):
        raise ReleaseError('napcat_must_run_install_requires_running_astrbot')
    core.idle(deployment)
    plan = {'status': 'PLAN', 'operation': 'rollback_four_files' if rolling_back else 'install_four_files',
        'target_files': sorted(DESTINATIONS), 'config_change': {FLAG: True}, 'napcat_restart': False,
        'database_write': False, 'whole_data_restore': False, 'models_called': 0,
        'new_chats_preserved': True, 'pending_releases_modified': False, 'proactive_enabled': False}
    if not args.apply: return plan
    operation = safe(source, 'operations/' + args.run_id + ('-rollback.json' if rolling_back else '-install.json'))
    if operation.exists(): raise ReleaseError('new_operation_run_id_required')
    os.umask(0o077)
    lock = os.open(deployment / '.astrbot-runtime-release.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(lock); raise ReleaseError('another_release_running') from None
    touched = False; result = {**plan, 'status': 'FAILED'}
    backup = core.load('bubbles_v3_backup', source / 'scripts/backup.py')
    installer = core.load('bubbles_v3_installer', source / 'scripts/phase3-install-overlay.py')
    try:
        validate(source, deployment, installed=rolling_back)
        core.idle(deployment); services.check(astr_running=initially_running)
        if initially_running: services.stop_astr()
        proof = core.snapshot(deployment, deployment / 'backups' / args.run_id, services, backup)
        validate(source, deployment, installed=rolling_back)
        if rolling_back:
            touched = True
            restore_files(source, deployment, manifest, installer, services)
        else:
            rows = prepare_journal(source, deployment, manifest)
            touched = True
            for row in rows:
                services.check(astr_running=False)
                incoming = (source / 'bubbles-v3-journal/candidate-guard-config.json' if row['path'] == GUARD_CONFIG
                    else source / 'integrations/phase1_guard' / Path(row['path']).name)
                if sha(incoming) != row['new_sha256']: raise ReleaseError('candidate_changed_before_publish')
                target = safe(deployment / 'state', row['path'])
                if sha(target) != row['old_sha256']: raise ReleaseError('baseline_changed_before_publish')
                installer.publish(incoming, target, row['uid'], row['gid']); target.chmod(row['mode'])
            validate(source, deployment, installed=True)
        services.start_astr()
        result.update(status='ROLLED_BACK_AWAITING_HEALTH' if rolling_back else 'INSTALLED_AWAITING_HEALTH_AND_QQ',
            backup_run_id=args.run_id, backup_integrity=proof['backup_integrity'], restore_integrity=proof['restore_integrity'],
            sqlite_checked_count=proof['sqlite_checked_count'], source_journal=str(source / 'bubbles-v3-journal'))
    except BaseException as exc:
        result['reason'] = str(exc) if isinstance(exc, ReleaseError) else type(exc).__name__
        try:
            state = services.check(require_nap=False)
            if touched:
                if state['astrbot']['running']: services.stop_astr(require_nap=False)
                restore_files(source, deployment, manifest, installer, services)
            validate(source, deployment, installed=rolling_back and not touched)
            resume = touched or initially_running
            running = services.check(require_nap=False)['astrbot']['running']
            if resume and not running: services.start_astr(require_nap=False)
            elif not resume and running: services.stop_astr(require_nap=False)
            result.update(known_files_recovered=touched, original_astrbot_resumed=resume,
                          recovered_version='candidate' if rolling_back and not touched else 'baseline')
        except BaseException as recovery:
            result.update(status='BLOCKED_FILE_RECOVERY_REQUIRED', recovery_error_type=type(recovery).__name__)
    finally:
        try:
            state = services.snapshot()
            result['napcat_identity_unchanged'] = services.nap_identity(state['napcat']) == services.nap_identity(services.original['napcat'])
            result['astrbot_running'] = state['astrbot']['running']
            if not result['napcat_identity_unchanged'] and result['status'].startswith(('INSTALLED', 'ROLLED_BACK')):
                result.update(status='BLOCKED_NAPCAT_STATE_CHANGED', file_operation_completed=True)
        except Exception:
            result['napcat_identity_unchanged'] = None
            if result['status'].startswith(('INSTALLED', 'ROLLED_BACK')):
                result.update(status='BLOCKED_SERVICE_STATE_UNAVAILABLE', file_operation_completed=True)
        private_json(operation, result)
        private_json(source / ('rollback-result.json' if rolling_back else 'install-result.json'), result)
        os.close(lock)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--deployment', type=Path, default=Path('/opt/qq-ai-companion/deployment'))
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--rollback', action='store_true')
    parser.add_argument('--apply', action='store_true')
    try: result = execute(parser.parse_args())
    except Exception as exc:
        result = {'status': 'FAILED', 'reason': str(exc) if isinstance(exc, ReleaseError) else type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False))
    return int(result['status'].startswith(('FAILED', 'BLOCKED')))


if __name__ == '__main__':
    raise SystemExit(main())
