#!/usr/bin/env python3
"""Plan by default: replace only proactive.py after an AstrBot-only backup.

NapCat is never stopped, started, or snapshotted. Configuration and live data
are never restored or edited. Rollback restores source only, retaining new chat.
"""
import argparse
import ast
import fcntl
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import shutil
import stat
import sys

HELPER = 'scripts/phase3-deploy-runtime-code.py'
HELPER_SHA = '4484c510fcc4994cd39eba9138edbe66713f0098ca00c345789a2082e776ea82'
_helper_path = Path(__file__).parent / Path(HELPER).name
if hashlib.sha256(_helper_path.read_bytes()).hexdigest() != HELPER_SHA:
    raise RuntimeError('locked_runtime_release_helper_required')
_spec = importlib.util.spec_from_file_location('proactive_recovery_runtime_helper', _helper_path)
_helper = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _helper
_spec.loader.exec_module(_helper)
ReleaseError = _helper.ReleaseError
Services = _helper.Services
read, sha, safe, private_json, load = (_helper.read, _helper.sha, _helper.safe, _helper.private_json, _helper.load)
idle, snapshot, verify_native = _helper.idle, _helper.snapshot, _helper.verify_native
ASTRBOT_IMAGE, NAPCAT_IMAGE = _helper.ASTRBOT_IMAGE, _helper.NAPCAT_IMAGE

SCHEMA = 'qq-companion.proactive-recovery-release.v1'
BASE_RELEASE = 'phase3-20261024-fresh-start-r1'
CONTROLLER = 'scripts/phase3-deploy-proactive-recovery.py'
CANDIDATE = 'integrations/phase3_runtime/proactive.py'
TARGET = 'data/plugins/phase3_runtime/proactive.py'
PLUGINS = {'phase1_guard', 'phase2_policy', 'phase3_runtime', 'phase3_memory', 'phase3_search'}
SUFFIXES = {'.py', '.json', '.yaml', '.yml', '.md'}
PINNED = {**_helper.PINNED, HELPER: HELPER_SHA}


def source_inventory(state):
    observed = set()
    for name in PLUGINS:
        root = safe(state, 'data/plugins/' + name)
        if not root.is_dir():
            raise ReleaseError('complete_first_party_plugin_inventory_required')
        for path in root.rglob('*'):
            if '__pycache__' in path.parts or path.suffix == '.pyc':
                continue
            safe(state, path.relative_to(state))
            if path.is_file() and path.suffix in SUFFIXES:
                observed.add(path.relative_to(state).as_posix())
    return observed


def validate(source, deployment, *, installed=False):
    manifest = read(safe(source, 'source-manifest.json'))
    if manifest.get('schema') != SCHEMA or manifest.get('base_release') != BASE_RELEASE:
        raise ReleaseError('fresh_start_baseline_manifest_required')
    files = manifest.get('files', {})
    if set(files) != set(PINNED) | {CONTROLLER, CANDIDATE}:
        raise ReleaseError('exact_proactive_only_package_required')
    for name, digest in files.items():
        path = safe(source, name)
        if not path.is_file() or path.stat().st_size > 1024 * 1024 or sha(path) != digest:
            raise ReleaseError('reviewed_source_hash_mismatch')
    if any(files[name] != digest for name, digest in PINNED.items()):
        raise ReleaseError('locked_helper_or_dependency_changed')
    if files[CONTROLLER] != sha(Path(__file__)):
        raise ReleaseError('executing_controller_not_reviewed')
    ast.parse((source / CANDIDATE).read_text(encoding='utf-8'), filename='proactive.py')
    state = safe(deployment, 'state')
    baseline = manifest.get('baseline_source_files', {})
    if TARGET not in baseline or set(baseline) != source_inventory(state):
        raise ReleaseError('first_party_source_inventory_changed')
    for name, old_digest in baseline.items():
        parts = Path(name).parts
        if (len(parts) < 4 or parts[:2] != ('data', 'plugins')
                or parts[2] not in PLUGINS or Path(name).suffix not in SUFFIXES):
            raise ReleaseError('invalid_protected_source_path')
        digest = files[CANDIDATE] if installed and name == TARGET else old_digest
        if sha(safe(state, name)) != digest:
            raise ReleaseError('protected_source_changed')
    if baseline[TARGET] == files[CANDIDATE]:
        raise ReleaseError('candidate_must_change_proactive_source')
    configs = manifest.get('protected_config_files', {})
    config_root = safe(state, 'data/config')
    expected_configs = {'data/cmd_config.json'} | {
        p.relative_to(state).as_posix() for p in config_root.rglob('*') if p.is_file()}
    if set(configs) != expected_configs:
        raise ReleaseError('complete_readonly_config_inventory_required')
    for name, digest in configs.items():
        if sha(safe(state, name)) != digest:
            raise ReleaseError('production_configuration_changed')
    if (sha(safe(deployment, 'compose.yml')) != manifest.get('compose_sha256')
            or sha(safe(deployment, '.env')) != manifest.get('env_sha256')):
        raise ReleaseError('compose_or_image_environment_changed')
    verify_native(source, deployment)
    return manifest


def prepare_journal(deployment, journal, manifest):
    if journal.exists():
        raise ReleaseError('fresh_code_journal_required')
    journal.mkdir(mode=0o700)
    target = safe(deployment / 'state', TARGET)
    info = target.stat()
    row = {'path': TARGET, 'old_sha256': sha(target), 'new_sha256': manifest['files'][CANDIDATE],
           'uid': info.st_uid, 'gid': info.st_gid, 'mode': stat.S_IMODE(info.st_mode)}
    if row['old_sha256'] != manifest['baseline_source_files'][TARGET]:
        raise ReleaseError('source_changed_before_journal')
    original = journal / 'original' / 'proactive.py'
    original.parent.mkdir(mode=0o700)
    shutil.copy2(target, original)
    original.chmod(0o600)
    private_json(journal / 'journal.json', {'schema': 'qq-companion.proactive-source-journal.v1',
        'files': [row], 'database_restore_permitted': False, 'config_change_permitted': False})
    return row


def restore_sources(deployment, journal, installer, services, manifest):
    services.check(astr_running=False, require_nap=False)
    data = read(safe(journal, 'journal.json'))
    rows = data.get('files', [])
    if (data.get('schema') != 'qq-companion.proactive-source-journal.v1'
            or len(rows) != 1 or rows[0].get('path') != TARGET
            or data.get('database_restore_permitted') is not False
            or data.get('config_change_permitted') is not False):
        raise ReleaseError('proactive_source_only_journal_required')
    row = rows[0]
    if (row['old_sha256'] != manifest['baseline_source_files'][TARGET]
            or row['new_sha256'] != manifest['files'][CANDIDATE]):
        raise ReleaseError('journal_does_not_match_reviewed_hashes')
    target = safe(deployment / 'state', TARGET)
    original = safe(journal, 'original/proactive.py')
    if sha(target) not in {row['old_sha256'], row['new_sha256']}:
        raise ReleaseError('source_changed_since_install')
    if sha(original) != row['old_sha256']:
        raise ReleaseError('original_source_journal_corrupt')
    installer.publish(original, target, row['uid'], row['gid'])
    target.chmod(row['mode'])
    if sha(target) != row['old_sha256']:
        raise ReleaseError('source_rollback_verification_failed')


def execute(args, *, services=None):
    safe(args.source); safe(args.deployment)
    source, deployment = args.source.resolve(), args.deployment.resolve()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', args.run_id):
        raise ReleaseError('invalid_run_id')
    rolling_back = bool(args.rollback)
    manifest = validate(source, deployment, installed=rolling_back)
    services = services or Services(deployment)
    services.original = services.snapshot()
    initial_running = services.original['astrbot']['running']
    if not services.original['napcat']['running'] or (not rolling_back and not initial_running):
        raise ReleaseError('napcat_must_run_and_install_requires_running_astrbot')
    idle(deployment)
    plan = {'status': 'PLAN', 'operation': 'rollback_source_only' if rolling_back else 'install_source_only',
        'target_files': [TARGET], 'napcat_restart': False, 'database_or_config_write': False,
        'whole_data_restore': False, 'models_called': 0, 'new_chats_preserved': True}
    if not args.apply:
        return plan
    operation = safe(source, 'operations/' + args.run_id + ('-rollback.json' if rolling_back else '-install.json'))
    journal = safe(source, 'astrbot-code-journal')
    output = safe(deployment, 'backups/' + args.run_id)
    if operation.exists() or output.exists():
        raise ReleaseError('new_operation_run_id_required')
    os.umask(0o077)
    lock = os.open(deployment / '.astrbot-runtime-release.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(lock)
        raise ReleaseError('another_release_running') from None
    result = {**plan, 'status': 'FAILED'}
    touched = False
    try:
        backup = load('proactive_recovery_backup', source / 'scripts/backup.py')
        installer = load('proactive_recovery_installer', source / 'scripts/phase3-install-overlay.py')
        validate(source, deployment, installed=rolling_back)
        idle(deployment); services.check(astr_running=initial_running)
        if initial_running:
            services.stop_astr()
        proof = snapshot(deployment, output, services, backup)
        validate(source, deployment, installed=rolling_back)
        if rolling_back:
            touched = True
            restore_sources(deployment, journal, installer, services, manifest)
        else:
            row = prepare_journal(deployment, journal, manifest)
            services.check(astr_running=False)
            touched = True
            target = safe(deployment / 'state', TARGET)
            installer.publish(source / CANDIDATE, target, row['uid'], row['gid'])
            target.chmod(row['mode'])
        validate(source, deployment, installed=not rolling_back)
        services.start_astr()
        result.update(status='SOURCE_ROLLED_BACK_AWAITING_HEALTH' if rolling_back else 'INSTALLED_AWAITING_HEALTH',
            backup_run_id=args.run_id, backup_integrity=proof['backup_integrity'],
            restore_integrity=proof['restore_integrity'], sqlite_checked_count=proof['sqlite_checked_count'],
            source_journal=str(journal), astrbot_container_id=services.original['astrbot']['id'])
    except BaseException as exc:
        result['reason'] = str(exc) if isinstance(exc, ReleaseError) else type(exc).__name__
        try:
            current = services.check(require_nap=False)
            if touched:
                if current['astrbot']['running']:
                    services.stop_astr(require_nap=False)
                restore_sources(deployment, journal, installer, services, manifest)
            recovered_installed = rolling_back and not touched
            validate(source, deployment, installed=recovered_installed)
            resume = touched or initial_running
            if resume and not services.check(require_nap=False)['astrbot']['running']:
                services.start_astr(require_nap=False)
            result.update(known_source_recovered=touched, original_astrbot_resumed=resume,
                recovered_source_version='candidate' if recovered_installed else 'baseline')
        except BaseException as recovery:
            result.update(status='BLOCKED_SOURCE_RECOVERY_REQUIRED',
                recovery_error_type=type(recovery).__name__, services_left_as_observed=True)
    finally:
        try:
            final = services.snapshot()
            unchanged = services.nap_identity(final['napcat']) == services.nap_identity(services.original['napcat'])
            result.update(napcat_identity_unchanged=unchanged, astrbot_running=final['astrbot']['running'])
            if not unchanged and result['status'].startswith(('INSTALLED', 'SOURCE_ROLLED_BACK')):
                result.update(status='BLOCKED_NAPCAT_STATE_CHANGED', source_operation_completed=True)
        except Exception:
            result.update(final_service_state_unavailable=True, napcat_identity_unchanged=None)
            if result['status'].startswith(('INSTALLED', 'SOURCE_ROLLED_BACK')):
                result.update(status='BLOCKED_FINAL_SERVICE_STATE_UNAVAILABLE', source_operation_completed=True)
        try:
            private_json(operation, result)
            private_json(source / ('source-rollback-result.json' if rolling_back else 'source-install-result.json'), result)
        finally:
            os.close(lock)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--deployment', type=Path, default=Path('/opt/qq-ai-companion/deployment'))
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--rollback', action='store_true')
    parser.add_argument('--apply', action='store_true')
    try:
        result = execute(parser.parse_args())
    except Exception as exc:
        result = {'status': 'FAILED', 'reason': str(exc) if isinstance(exc, ReleaseError) else type(exc).__name__}
    import json
    print(json.dumps(result, ensure_ascii=False))
    return int(result['status'].startswith(('FAILED', 'BLOCKED')))


if __name__ == '__main__':
    raise SystemExit(main())
