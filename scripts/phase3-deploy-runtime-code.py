#!/usr/bin/env python3
"""PLAN by default: three runtime source files, AstrBot-only stopped snapshot.

Never stops/starts NapCat, restores live databases, edits configuration, pulls
images, or creates containers. Backup/restore uses the fixed Phase 1 helper.
"""
import argparse
from contextlib import closing
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile

BASE_RELEASE = 'phase3-20261023-experience-v2-r2'
BASE_BACKUP = 'phase3-experience-v2-20261023-r2'
ASTRBOT_IMAGE = 'sha256:a0bf74942fc2ba504932897f7f6ba8d8bd01c1084ea6448c399b8df579bc77af'
NAPCAT_IMAGE = 'sha256:2f1dd81bdd62f27b3431ac9254000602ed7ed6dfbf0a38bb6e735fd16bc4ee87'
TARGETS = ('main.py', 'profile_recall.py', 'profile_publication.py')
PREFIX = 'data/plugins/phase3_runtime/'
LEGACY_PROTECTED_SOURCE = {
    'data/plugins/phase2_policy/plugin_permissions.py':
        '9c76c3f8429ecccaa8267364cb23b47c86a0e833b121e9ee0cbe2e946b80a17e',
}
PINNED = {
    'scripts/backup.py': '7595ee47b32260282fd861ade2317cb163e44df6ace2146778fee17b49bf005f',
    'scripts/phase3-install-overlay.py': '9558c9e2443654cb9e83bee72fe95109ffe94437ef14fe4a7bf1dbaa16bd0ec4',
    'scripts/prepare_plugins.py': '4c17c041d7935fec5a5e0a0a59b885e7adf27ce99e7e89d48be13c46d1198771',
    'config/plugins-lock.json': '808c9d5ff05e21ba5fe9a0367c323b137bbec230569d45001e8967a232ac7896',
    'config/upstream-lock.json': 'c647979e3b71a7f24646f69e1389533e5ba5c526db43319d03a92917c423dfe2',
}


class ReleaseError(ValueError):
    pass


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe(root, relative='.'):
    rel = Path(relative)
    if rel.is_absolute() or '..' in rel.parts:
        raise ReleaseError('unsafe_relative_path')
    path = root / rel
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ReleaseError('symlink_rejected')
    return path


def private_json(path, value):
    safe(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.runtime-release-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def command(argv):
    try:
        result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, timeout=50)
    except (OSError, subprocess.TimeoutExpired):
        raise ReleaseError('docker_command_unavailable') from None
    if result.returncode or len(result.stdout) > 131072:
        raise ReleaseError('docker_command_failed')
    return result.stdout


class Services:
    def __init__(self, deployment, runner=command):
        self.deployment, self.runner = deployment, runner
        self.prefix = ['docker', 'compose', '--project-directory', str(deployment),
                       '-f', str(deployment / 'compose.yml')]
        self.original = None

    def snapshot(self, *, include_nap=True):
        found = {}
        targets = [('astrbot', ASTRBOT_IMAGE)] + ([('napcat', NAPCAT_IMAGE)] if include_nap else [])
        for service, image in targets:
            ids = self.runner(self.prefix + ['ps', '--all', '--quiet', service]).split()
            if len(ids) != 1 or not re.fullmatch('[0-9a-f]{12,64}', ids[0]):
                raise ReleaseError('one_existing_service_required')
            template = ('{"id":{{json .Id}},"image":{{json .Image}},'
                '"running":{{json .State.Running}},"status":{{json .State.Status}},'
                '"started_at":{{json .State.StartedAt}},"restart_count":{{json .RestartCount}},'
                '"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
                '"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
                '"mounts":{{json .Mounts}}}')
            row = json.loads(self.runner(['docker', 'inspect', '--format', template, ids[0]]))
            if (row.get('image') != image or row.get('project') != 'qq-ai-companion'
                or row.get('service') != service or row.get('status') not in {'running', 'exited'}
                or row.get('running') is not (row.get('status') == 'running')):
                raise ReleaseError('locked_stable_service_required')
            found[service] = row
        state = (self.deployment / 'state').resolve()
        if not any(m.get('Source') == str(state / 'data') and m.get('Destination') == '/AstrBot/data'
                   and m.get('RW') is True for m in found['astrbot']['mounts']):
            raise ReleaseError('astrbot_state_mount_mismatch')
        # A running NapCat must not write anywhere included by the Astr snapshot.
        for mount in found.get('napcat', {}).get('mounts', []):
            source = Path(mount.get('Source', '/nonexistent')).resolve()
            if mount.get('RW') and (source.is_relative_to(state) or state.is_relative_to(source)):
                raise ReleaseError('napcat_writes_astrbot_snapshot_scope')
        return found

    @staticmethod
    def nap_identity(row):
        return {key: row[key] for key in ('id', 'image', 'started_at', 'restart_count', 'running')}

    def check(self, *, astr_running=None, require_nap=True):
        state = self.snapshot(include_nap=require_nap)
        if self.original and state['astrbot']['id'] != self.original['astrbot']['id']:
            raise ReleaseError('astrbot_container_replaced')
        if require_nap and self.original and self.nap_identity(state['napcat']) != self.nap_identity(self.original['napcat']):
            raise ReleaseError('napcat_restarted_or_replaced')
        if astr_running is not None and state['astrbot']['running'] is not astr_running:
            raise ReleaseError('astrbot_running_state_mismatch')
        return state

    def stop_astr(self, *, require_nap=True):
        self.check(require_nap=require_nap)
        self.runner(['docker', 'stop', '--time', '30', self.original['astrbot']['id']])
        self.check(astr_running=False, require_nap=require_nap)

    def start_astr(self, *, require_nap=True):
        self.check(astr_running=False, require_nap=require_nap)
        self.runner(['docker', 'start', self.original['astrbot']['id']])
        self.check(astr_running=True, require_nap=require_nap)


def verify_native(source, deployment):
    helper = load('runtime_release_native_hash', source / 'scripts/prepare_plugins.py')
    for plugin in read(source / 'config/plugins-lock.json')['plugins']:
        path = safe(deployment / 'state', 'data/plugins/' + plugin['directory'])
        if helper.source_tree_sha256(path) != plugin['source_tree_sha256']:
            raise ReleaseError('locked_upstream_source_changed')


def validate(source, deployment, base_receipt, *, installed=False):
    manifest = read(source / 'source-manifest.json')
    if manifest.get('schema') != 'qq-companion.astrbot-code-release.v1' or manifest.get('base_release') != BASE_RELEASE:
        raise ReleaseError('fixed_r2_baseline_required')
    safe(base_receipt.parent)
    if BASE_RELEASE not in base_receipt.parts or sha(base_receipt) != manifest.get('base_receipt_sha256'):
        raise ReleaseError('reviewed_r2_receipt_required')
    receipt = read(base_receipt)
    if (set(receipt) != {'originally_running_services', 'backup_run_id', 'restart_owned_by',
                        'production_resumed', 'candidate_applied'}
        or sorted(receipt.get('originally_running_services', [])) != ['astrbot', 'napcat']
        or receipt.get('backup_run_id') != BASE_BACKUP
        or receipt.get('restart_owned_by') != 'experience_controller'
        or receipt.get('production_resumed') is not True
        or receipt.get('candidate_applied') is not True):
        raise ReleaseError('completed_r2_operation_receipt_required')
    expected = {*PINNED, 'scripts/phase3-deploy-runtime-code.py',
                *('integrations/phase3_runtime/' + name for name in TARGETS)}
    files = manifest.get('files', {})
    if set(files) != expected:
        raise ReleaseError('exact_narrow_package_required')
    for name, digest in files.items():
        path = safe(source, name)
        if not path.is_file() or path.stat().st_size > 1024*1024 or sha(path) != digest:
            raise ReleaseError('reviewed_source_hash_mismatch')
    if any(files[name] != digest for name, digest in PINNED.items()):
        raise ReleaseError('locked_helper_or_dependency_changed')
    if files['scripts/phase3-deploy-runtime-code.py'] != sha(Path(__file__)):
        raise ReleaseError('executing_controller_not_reviewed')
    baseline = manifest.get('baseline_source_files', {})
    if len(baseline) != 47 or not {PREFIX+'main.py', PREFIX+'profile_recall.py'} <= baseline.keys():
        raise ReleaseError('all_47_r2_source_hashes_required')
    if PREFIX+'profile_publication.py' in baseline:
        raise ReleaseError('new_publication_module_must_be_absent_in_r2')
    for name in baseline:
        parts = Path(name).parts
        if (len(parts) < 4 or parts[:2] != ('data', 'plugins') or not parts[2].startswith('phase')
            or Path(name).suffix not in {'.py','.json','.yaml','.yml','.md'}):
            raise ReleaseError('invalid_r2_source_inventory')
        safe(deployment / 'state', name)
    protected_sources = {**LEGACY_PROTECTED_SOURCE, **baseline}
    if set(LEGACY_PROTECTED_SOURCE) & set(baseline):
        raise ReleaseError('legacy_source_must_remain_outside_r2_overlay')
    plugin_dirs = {Path(name).parts[2] for name in protected_sources}
    observed = {p.relative_to(deployment / 'state').as_posix()
        for directory in plugin_dirs for p in (deployment / 'state/data/plugins' / directory).rglob('*')
        if p.is_file() and '__pycache__' not in p.parts and p.suffix in {'.py','.json','.yaml','.yml','.md'}}
    if observed != set(protected_sources) | ({PREFIX+'profile_publication.py'} if installed else set()):
        raise ReleaseError('r2_source_inventory_changed')
    for name, digest in LEGACY_PROTECTED_SOURCE.items():
        if sha(safe(deployment / 'state', name)) != digest:
            raise ReleaseError('legacy_protected_source_changed')
    for name, old in baseline.items():
        expected_hash = files['integrations/phase3_runtime/'+Path(name).name] if installed and name in {PREFIX+n for n in TARGETS} else old
        target = safe(deployment / 'state', name)
        if not target.is_file() or sha(target) != expected_hash:
            raise ReleaseError('r2_source_baseline_changed')
    new = safe(deployment / 'state', PREFIX+'profile_publication.py')
    if (not installed and new.exists()) or (installed and (not new.is_file() or sha(new) != files['integrations/phase3_runtime/profile_publication.py'])):
        raise ReleaseError('new_publication_module_state_mismatch')
    configs = manifest.get('protected_config_files', {})
    expected_configs = {'data/cmd_config.json'} | {p.relative_to(deployment / 'state').as_posix()
        for p in (deployment / 'state/data/config').rglob('*') if p.is_file()}
    if set(configs) != expected_configs:
        raise ReleaseError('complete_readonly_config_inventory_required')
    for name, value in configs.items():
        if sha(safe(deployment / 'state', name)) != value:
            raise ReleaseError('production_configuration_changed')
    if sha(safe(deployment, 'compose.yml')) != manifest.get('compose_sha256') or sha(safe(deployment, '.env')) != manifest.get('env_sha256'):
        raise ReleaseError('compose_or_image_environment_changed')
    verify_native(source, deployment)
    return manifest


def idle(deployment):
    path = safe(deployment / 'state', 'data/plugin_data/phase1_guard/guard.sqlite3')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
        if db.execute("SELECT count(*) FROM turns WHERE state IN ('collecting','ready','generating')").fetchone()[0]:
            raise ReleaseError('active_turn_retry_after_completion')
        for table in ('deliveries', 'outbound'):
            if db.execute(f"SELECT count(*) FROM {table} WHERE state='pending'").fetchone()[0]:
                raise ReleaseError('pending_delivery_retry_after_completion')


def snapshot(deployment, output, services, backup):
    services.check(astr_running=False); idle(deployment)
    if output.exists(): raise ReleaseError('new_astrbot_backup_directory_required')
    output.mkdir(parents=True, mode=0o700)
    archive = output / 'astrbot-state.zip'
    original = backup.create_backup(deployment / 'state', archive)
    services.check(astr_running=False)
    restored = backup.restore_backup(archive, output / 'isolated-astrbot-restore')
    if original != restored or backup._tree(output / 'isolated-astrbot-restore') != (original['files'], original['directories']):
        raise ReleaseError('isolated_restore_inventory_mismatch')
    services.check(astr_running=False)
    result = {'schema':'qq-companion.astrbot-only-backup.v1','status':'PASS',
        'scope':'AstrBot stopped state only; NapCat remained running and was not snapshotted',
        'backup_integrity':'PASS','restore_integrity':'PASS','restored_copy_started':False,
        'astrbot_archive_sha256':sha(archive),'sqlite_checked_count':len(restored['sqlite_integrity_checked']),
        'napcat_snapshot':'NOT_PERFORMED','phase2_full_backup':'NOT_REPLACED',
        'napcat_identity':services.nap_identity(services.original['napcat'])}
    private_json(output / 'astrbot-only-result.json', result)
    return result


def prepare_journal(source, deployment, journal, manifest):
    if journal.exists(): raise ReleaseError('fresh_code_journal_required')
    journal.mkdir(mode=0o700)
    rows = []
    for name in TARGETS:
        relative = PREFIX + name
        target = safe(deployment / 'state', relative)
        info = target.stat() if target.exists() else target.parent.stat()
        row = {'path':relative,'old_sha256':sha(target) if target.exists() else None,
            'new_sha256':manifest['files']['integrations/phase3_runtime/'+name],
            'uid':info.st_uid,'gid':info.st_gid,'mode':stat.S_IMODE(info.st_mode) if target.exists() else 0o600}
        if target.exists():
            old = journal / 'original' / name
            old.parent.mkdir(mode=0o700, exist_ok=True)
            shutil.copy2(target, old); old.chmod(0o600)
        rows.append(row)
    private_json(journal / 'journal.json', {'schema':'qq-companion.source-only-journal.v1', 'files':rows,
        'database_restore_permitted':False, 'config_change_permitted':False})
    return rows


def restore_sources(deployment, journal, installer, services, manifest):
    services.check(astr_running=False, require_nap=False)
    data = read(journal / 'journal.json')
    rows = data['files']
    if data.get('schema') != 'qq-companion.source-only-journal.v1' or len(rows) != 3 or {r['path'] for r in rows} != {PREFIX+n for n in TARGETS}:
        raise ReleaseError('narrow_source_journal_required')
    # Validate the entire rollback before changing any file; never undo drift.
    for row in rows:
        expected_old = manifest['baseline_source_files'].get(row['path'])
        expected_new = manifest['files']['integrations/phase3_runtime/'+Path(row['path']).name]
        if row['old_sha256'] != expected_old or row['new_sha256'] != expected_new:
            raise ReleaseError('journal_does_not_match_reviewed_source_hashes')
        target = safe(deployment / 'state', row['path'])
        current = sha(target) if target.is_file() else None
        if current not in {row['old_sha256'], row['new_sha256']}:
            raise ReleaseError('source_changed_since_code_install')
        if row['old_sha256'] is not None and sha(safe(journal, 'original/'+Path(row['path']).name)) != row['old_sha256']:
            raise ReleaseError('original_source_journal_corrupt')
    for row in reversed(rows):
        target = safe(deployment / 'state', row['path'])
        if row['old_sha256'] is None:
            if target.exists(): target.unlink()
        else:
            installer.publish(journal / 'original' / target.name, target, row['uid'], row['gid'])
            target.chmod(row['mode'])
    for row in rows:
        target = safe(deployment / 'state', row['path'])
        if (sha(target) if target.exists() else None) != row['old_sha256']:
            raise ReleaseError('source_rollback_verification_failed')


def execute(args, *, services=None):
    source, deployment = args.source.resolve(), args.deployment.resolve()
    for path in (source, deployment): safe(path)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', args.run_id): raise ReleaseError('invalid_run_id')
    rolling_back = bool(args.rollback)
    manifest = validate(source, deployment, args.base_receipt.resolve(), installed=rolling_back)
    services = services or Services(deployment)
    services.original = services.snapshot()
    initial_astr_running = services.original['astrbot']['running']
    if not services.original['napcat']['running'] or (not rolling_back and not initial_astr_running):
        raise ReleaseError('napcat_must_run_and_install_requires_running_astrbot')
    idle(deployment)
    plan = {'status':'PLAN','operation':'rollback_source_only' if rolling_back else 'install_source_only',
        'target_files':[PREFIX+n for n in TARGETS],'napcat_restart':False,'database_or_config_write':False,
        'backup_scope':'AstrBot only; existing Phase2 full backup retained','whole_data_restore':False,
        'models_called':0,'new_chats_preserved':True}
    if not args.apply: return plan
    operation_path = source / 'operations' / (args.run_id + ('-rollback.json' if rolling_back else '-install.json'))
    if operation_path.exists(): raise ReleaseError('new_operation_run_id_required')
    os.umask(0o077)
    lock = os.open(deployment / '.astrbot-runtime-release.lock', os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW, 0o600)
    try: fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        os.close(lock); raise ReleaseError('another_release_running') from None
    journal = source / 'astrbot-code-journal'
    result = {**plan,'status':'FAILED'}
    touched = False
    backup = load('runtime_release_backup', source / 'scripts/backup.py')
    installer = load('runtime_release_installer', source / 'scripts/phase3-install-overlay.py')
    try:
        validate(source, deployment, args.base_receipt.resolve(), installed=rolling_back)
        idle(deployment); services.check(astr_running=initial_astr_running)
        if initial_astr_running: services.stop_astr()
        proof = snapshot(deployment, deployment / 'backups' / args.run_id, services, backup)
        validate(source, deployment, args.base_receipt.resolve(), installed=rolling_back)
        if rolling_back:
            touched = True
            restore_sources(deployment, journal, installer, services, manifest)
            validate(source, deployment, args.base_receipt.resolve(), installed=False)
        else:
            rows = prepare_journal(source, deployment, journal, manifest)
            touched = True
            for row in rows:
                services.check(astr_running=False)
                target = safe(deployment / 'state', row['path'])
                installer.publish(source / 'integrations/phase3_runtime' / target.name, target, row['uid'], row['gid'])
                target.chmod(row['mode'])
            validate(source, deployment, args.base_receipt.resolve(), installed=True)
        services.start_astr()
        result.update(status='SOURCE_ROLLED_BACK_AWAITING_HEALTH' if rolling_back else 'INSTALLED_AWAITING_HEALTH_AND_QQ',
            backup_run_id=args.run_id, backup_integrity=proof['backup_integrity'], restore_integrity=proof['restore_integrity'],
            sqlite_checked_count=proof['sqlite_checked_count'], napcat_identity_unchanged=True,
            source_journal=str(journal), astrbot_container_id=services.original['astrbot']['id'])
    except BaseException as exc:
        result['reason'] = str(exc) if isinstance(exc, ReleaseError) else type(exc).__name__
        try:
            state = services.check(require_nap=False)
            if touched:
                if state['astrbot']['running']: services.stop_astr(require_nap=False)
                # For failed explicit rollback keep the known original source;
                # the same idempotent operation never restores a database.
                restore_sources(deployment, journal, installer, services, manifest)
            # Before source mutation, restore exactly the entering version and
            # running state. In particular, a failed rollback preflight must
            # neither strand a healthy candidate nor start an exited candidate.
            recovered_installed = rolling_back and not touched
            validate(source, deployment, args.base_receipt.resolve(), installed=recovered_installed)
            resume = touched or initial_astr_running
            running = services.check(require_nap=False)['astrbot']['running']
            if resume and not running: services.start_astr(require_nap=False)
            elif not resume and running: services.stop_astr(require_nap=False)
            result['known_source_recovered'] = touched
            result['recovered_source_version'] = 'candidate' if recovered_installed else 'r2'
            result['original_astrbot_resumed'] = resume
        except BaseException as recovery:
            result.update(status='BLOCKED_SOURCE_RECOVERY_REQUIRED',
                recovery_error_type=type(recovery).__name__, services_left_as_observed=True)
    finally:
        try:
            state = services.snapshot()
            result['napcat_identity_unchanged'] = services.nap_identity(state['napcat']) == services.nap_identity(services.original['napcat'])
            result['astrbot_running'] = state['astrbot']['running']
            if not result['napcat_identity_unchanged'] and result['status'].startswith(('INSTALLED', 'SOURCE_ROLLED_BACK')):
                result.update(status='BLOCKED_NAPCAT_STATE_CHANGED', source_operation_completed=True)
        except Exception:
            result['final_service_state_unavailable'] = True
            result['napcat_identity_unchanged'] = None
            if result['status'].startswith(('INSTALLED', 'SOURCE_ROLLED_BACK')):
                result.update(status='BLOCKED_FINAL_SERVICE_STATE_UNAVAILABLE', source_operation_completed=True)
            try: result['astrbot_running'] = services.check(require_nap=False)['astrbot']['running']
            except Exception: pass
        private_json(operation_path, result)
        private_json(source / ('source-rollback-result.json' if rolling_back else 'source-install-result.json'), result)
        os.close(lock)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--deployment', type=Path, default=Path('/opt/qq-ai-companion/deployment'))
    parser.add_argument('--base-receipt', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--rollback', action='store_true')
    parser.add_argument('--apply', action='store_true')
    try: result = execute(parser.parse_args())
    except Exception as exc:
        result = {'status':'FAILED','reason':str(exc) if isinstance(exc, ReleaseError) else type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result['status'].startswith(('FAILED','BLOCKED')) else 0


if __name__ == '__main__':
    raise SystemExit(main())
