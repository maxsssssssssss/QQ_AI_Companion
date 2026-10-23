#!/usr/bin/env python3
"""Authorize the existing unique native Private target while AstrBot is stopped.

Default prepares a private backup and isolated candidate only. --apply requires
--approved-target-migration. No service is stopped/started, no QQ process or model
is launched, and no native global configuration is enabled. The existing image
runs one networkless Python worker against a copy, using pinned native APIs.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import types

ROOT = Path(__file__).resolve().parent.parent
PINNED_TREE = 'e69f2fb6b9272ad74215550d8ea9c43d17fdc9ac231c5a2d084cdf2dfde31300'
PINNED_VERSION = '6.7.0'
ALLOWED_FIELDS = {'unified_profile_capabilities', 'private_companion_enabled',
    'proactive_private_enabled', 'enabled', 'unified_profile_capability_audit', 'proactive_daily_limit'}
MAX_FILE = 64 * 1024 * 1024


class MigrationError(Exception):
    """Only fixed error codes, never private data."""


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def protected_read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
            raise MigrationError('unsafe_file_permissions')
        if os.geteuid() != 0 and info.st_uid != os.geteuid():
            raise MigrationError('unexpected_owner')
        if info.st_size > MAX_FILE:
            raise MigrationError('oversized_file')
        raw = source.read(MAX_FILE + 1)
        if len(raw) > MAX_FILE:
            raise MigrationError('oversized_file')
        return raw, info


def write_new(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as target:
        target.write(raw)
        target.flush()
        os.fsync(target.fileno())


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()


def parse_identity(raw):
    value = json.loads(raw)
    admin, platform = value.get('admin_id'), value.get('platform_id')
    if not isinstance(admin, str) or not re.fullmatch(r'[0-9]{5,15}', admin):
        raise MigrationError('invalid_authorized_identity')
    if not isinstance(platform, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', platform):
        raise MigrationError('invalid_authorized_identity')
    return admin, platform


def config_values(config, name):
    values = []
    for key, value in config.items():
        if key == name: values.append(value)
        if isinstance(value, dict): values.extend(config_values(value, name))
    return values


def check_cold_json_backend(config, marker):
    if any(value != 'json' for value in config_values(config, 'storage_backend')):
        raise MigrationError('unsupported_native_backend')
    if marker is not None and marker.get('backend') != 'json':
        raise MigrationError('unsupported_native_backend')
    # Never authorize a durable target while an unguarded native worker could
    # activate from disk at the next start.
    if any(type(value) is not int or value != 0 for value in config_values(config, 'max_daily_messages')):
        raise MigrationError('native_disk_generation_not_disabled')
    if any(value is not False for value in config_values(config, 'enable_llm_proactive_message')):
        raise MigrationError('native_disk_generation_not_disabled')
    if not config_values(config, 'max_daily_messages') or not config_values(config, 'enable_llm_proactive_message'):
        raise MigrationError('native_disk_generation_not_disabled')


def tree_hash(source):
    result = hashlib.sha256()
    for path in sorted(source.rglob('*')):
        relative = path.relative_to(source)
        if '.git' in relative.parts or '__pycache__' in relative.parts or path.suffix == '.pyc': continue
        if path.is_symlink():
            kind, payload = b'link', str(path.readlink()).encode()
        elif path.is_file(): kind, payload = b'file', path.read_bytes()
        else: continue
        result.update(relative.as_posix().encode() + b'\0' + kind + b'\0')
        result.update(hashlib.sha256(payload).digest())
    return result.hexdigest()


def check_source(source):
    if source.is_symlink() or tree_hash(source) != PINNED_TREE:
        raise MigrationError('native_source_integrity_mismatch')


def native_modules(source):
    check_source(source)
    # Do not execute the plugin's main module or initialize any plugin/context.
    name = '_phase3_offline_private'
    package = types.ModuleType(name)
    package.__path__ = [str(source)]
    sys.modules[name] = package
    return tuple(importlib.import_module(name + '.' + part) for part in
        ('proactive', 'core_store', 'unified_profile_service', 'storage.json_backend'))


def validate_delta(before, after, target_key):
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise MigrationError('invalid_native_store')
    if {k:v for k,v in before.items() if k != 'users'} != {k:v for k,v in after.items() if k != 'users'}:
        raise MigrationError('unexpected_native_changes')
    old_users, new_users = before.get('users'), after.get('users')
    if not isinstance(old_users, dict) or not isinstance(new_users, dict) or set(old_users) != set(new_users):
        raise MigrationError('unexpected_native_changes')
    for key, old in old_users.items():
        new = new_users[key]
        if key != target_key:
            if old != new: raise MigrationError('unexpected_native_changes')
        elif not isinstance(new,dict) or {k:v for k,v in old.items() if k not in ALLOWED_FIELDS} != {
            k:v for k,v in new.items() if k not in ALLOWED_FIELDS}:
            raise MigrationError('unexpected_native_changes')
    target = new_users[target_key]
    if target.get('proactive_private_enabled') is not True or target.get('proactive_daily_limit') != 2:
        raise MigrationError('native_update_not_applied')
    if target.get('unified_profile_capabilities', {}).get('proactive_private_enabled') is not True:
        raise MigrationError('native_update_not_applied')


def native_candidate(source, store_path, identity_path, output_path):
    scheduler, core, capability, backend_module = native_modules(source)
    admin, platform = parse_identity(protected_read(identity_path)[0])
    before = json.loads(protected_read(store_path)[0])
    if not isinstance(before,dict) or not isinstance(before.get('users'),dict):
        raise MigrationError('invalid_native_store')
    class OfflineHost(scheduler.ProactiveMixin, core.CoreStoreMixin):
        pass
    host = OfflineHost()
    host.data = deepcopy(before)
    host.private_user_aliases = {}
    session = platform + ':FriendMessage:' + admin
    matches = [(key, user) for key, user in host.data['users'].items()
        if isinstance(user,dict) and user.get('umo') == session
        and user.get('identity_subject_id') == admin
        and user.get('identity_adapter_instance_id') == platform
        and user.get('identity_bot_id')
        and host._proactive_identity_binding_is_verified(str(key), user)]
    if len(matches) != 1:
        raise MigrationError('native_identity_missing_or_ambiguous')
    key, user = matches[0]
    if not host._proactive_identity_binding_is_unique(str(key), user):
        raise MigrationError('native_identity_missing_or_ambiguous')
    for other_key, other in host.data['users'].items():
        if other_key != key and isinstance(other,dict) and capability.proactive_private_gate(other).get('allowed'):
            raise MigrationError('another_native_target_already_authorized')
    was_enabled = capability.proactive_private_gate(user).get('allowed') is True
    old_portrait = capability.capability_summary(user).get('portrait_mode')
    outcome = capability.update_capabilities(user, {'proactive_private_enabled': True},
        actor_authorized=True, grant_source='administrator', actor_id='phase3_approved_migration',
        target_identity=str(key), reason_code='phase3_approved_window')
    if outcome.get('ok') is not True or capability.capability_summary(user).get('portrait_mode') != old_portrait:
        raise MigrationError('native_capability_update_rejected')
    user['proactive_daily_limit'] = 2
    validate_delta(before, host.data, key)
    if output_path.exists() or output_path.is_symlink():
        raise MigrationError('candidate_must_not_exist')
    old_mask = os.umask(0o077)
    try:
        backend = backend_module.JsonStoreBackend(output_path, lambda data:data, lambda:{})
        backend.save_store(host.data)
    finally: os.umask(old_mask)
    if backend.last_write_status.get('accepted') is not True:
        raise MigrationError('native_persistence_rejected')
    saved = json.loads(protected_read(output_path)[0])
    if saved != host.data: raise MigrationError('native_persistence_verification_failed')
    return {'status':'CANDIDATE_READY', 'native_version':PINNED_VERSION,
        'unique_existing_target':True, 'previously_authorized':was_enabled,
        'daily_limit':2, 'native_api_called':True, 'native_atomic_save_used':True,
        'global_generation_enabled':False, 'real_qq':False, 'model_calls':0}


def command(args, *, timeout=15):
    try: result = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired): raise MigrationError('command_unavailable_or_timeout') from None
    if result.returncode: raise MigrationError('command_failed')
    if len(result.stdout) > 1024 * 1024: raise MigrationError('oversized_command_output')
    return result.stdout


def stopped_image(deployment):
    ids = command(['docker','compose','--project-directory',str(deployment),'-f',str(deployment/'compose.yml'),
        'ps','--all','--quiet','astrbot']).decode().split()
    if len(ids) != 1 or not re.fullmatch(r'[0-9a-f]{12,64}',ids[0]):
        raise MigrationError('existing_astrbot_container_required')
    value = json.loads(command(['docker','inspect','--format',
        '{"status":{{json .State.Status}},"running":{{json .State.Running}},"image":{{json .Image}},'
        '"service":{{json (index .Config.Labels "com.docker.compose.service")}}}',ids[0]]))
    if value.get('running') is not False or value.get('status') not in ('exited','created') or value.get('service') != 'astrbot':
        raise MigrationError('astrbot_must_be_stopped')
    image = value.get('image','')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}',image): raise MigrationError('invalid_existing_image')
    return ids[0], image


def run_worker(image, source, work):
    for path in (source, work, Path(__file__).resolve()):
        if ',' in str(path) or '\n' in str(path): raise MigrationError('unsupported_mount_path')
    # Only the isolated copy is writable. Live data is never mounted here.
    command(['docker','run','--rm','--pull','never','--network','none','--read-only',
        '--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','64','--memory','768m',
        '--cpus','1','--user','0:0','--tmpfs','/tmp:rw,nosuid,nodev,size=128m,mode=1777',
        '--mount',f'type=bind,src={source},dst=/native,readonly',
        '--mount',f'type=bind,src={work},dst=/work',
        '--mount',f'type=bind,src={Path(__file__).resolve()},dst=/migration.py,readonly',
        '-e','ASTRBOT_ROOT=/tmp/offline-native','-e','ASTRBOT_DISABLE_METRICS=1',
        # The production sitecustomize requires the live secret configuration.
        # This inspection worker has no live mount or service entrypoint; use
        # the image's documented inspection Python path, as the profile worker.
        '-e','PYTHONDONTWRITEBYTECODE=1','-e','PYTHONPATH=/AstrBot',
        '--workdir','/tmp','--entrypoint','python',image,'/migration.py','--worker'], timeout=90)
    return json.loads(protected_read(work/'worker-result.json')[0])


def replace_preserving_owner(store, raw, info):
    fd,name=tempfile.mkstemp(prefix='.phase3-native-',suffix='.tmp',dir=store.parent)
    try:
        with os.fdopen(fd,'wb') as target:
            target.write(raw);target.flush();os.fsync(target.fileno())
            os.fchmod(target.fileno(),0o600)
            if os.geteuid()==0: os.fchown(target.fileno(),info.st_uid,info.st_gid)
        os.replace(name,store)
        directory=os.open(store.parent,os.O_RDONLY|os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(name): os.unlink(name)
    if protected_read(store)[0]!=raw: raise MigrationError('applied_file_verification_failed')


def _migrate_locked(deployment, backup_dir, *, apply=False, approved=False):
    if approved is not True: raise MigrationError('explicit_target_migration_approval_required')
    deployment = deployment.resolve()
    container, image = stopped_image(deployment)
    data = deployment/'state/data'
    store = data/'plugin_data/astrbot_plugin_private_companion/companions.json'
    identity = data/'config/phase2_policy_config.json'
    config = data/'config/astrbot_plugin_private_companion_config.json'
    source = data/'plugins/astrbot_plugin_private_companion'
    for path in (store, identity, config, source):
        if path.is_symlink() or data not in path.resolve().parents: raise MigrationError('unsafe_state_path')
    check_source(source)
    raw, info = protected_read(store)
    identity_raw = protected_read(identity)[0]
    parse_identity(identity_raw)
    config_raw = protected_read(config)[0]
    marker_path = store.parent/'.storage-backend-state.json'
    marker_raw = protected_read(marker_path)[0] if marker_path.exists() else None
    check_cold_json_backend(json.loads(config_raw), json.loads(marker_raw) if marker_raw else None)
    # A fresh exact private backup is created by this operation in addition to
    # the operator's whole-deployment backup. Never overwrite an old backup.
    if backup_dir.exists() or backup_dir.is_symlink(): raise MigrationError('new_backup_directory_required')
    if backup_dir.parent.is_symlink() or not backup_dir.parent.is_dir(): raise MigrationError('unsafe_backup_parent')
    backup_dir.mkdir(mode=0o700)
    backup_dir=backup_dir.resolve()
    write_new(backup_dir/'companions.before.json',raw)
    write_new(backup_dir/'identity.json',identity_raw)
    write_new(backup_dir/'native-config.before.json',config_raw)
    if marker_raw: write_new(backup_dir/'backend-marker.before.json',marker_raw)
    before_hash = digest(raw)
    write_new(backup_dir/'backup-manifest.json', json_bytes({'created_at':datetime.now(timezone.utc).isoformat(),
        'sha256':before_hash,'uid':info.st_uid,'gid':info.st_gid,'mode':'0600','native_version':PINNED_VERSION}))
    # The outer original-file lock serializes cooperating migrations. This
    # private operation marker remains with the backup for operator review.
    with (backup_dir/'migration.lock').open('x') as lock:
        os.chmod(lock.name,0o600)
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        result = run_worker(image,source,backup_dir)
        candidate = protected_read(backup_dir/'companions.after.json')[0]
        before,after=json.loads(raw),json.loads(candidate)
        admin,platform=parse_identity(identity_raw)
        keys=[key for key,user in before.get('users',{}).items() if isinstance(user,dict)
              and user.get('umo')==platform+':FriendMessage:'+admin]
        if len(keys)!=1: raise MigrationError('native_identity_missing_or_ambiguous')
        validate_delta(before,after,keys[0])
        if result.get('status')!='CANDIDATE_READY' or result.get('native_api_called') is not True:
            raise MigrationError('invalid_worker_result')
        if stopped_image(deployment)!=(container,image): raise MigrationError('container_changed')
        if protected_read(store)[0]!=raw or protected_read(identity)[0]!=identity_raw or protected_read(config)[0]!=config_raw:
            raise MigrationError('source_changed_during_migration')
        if (protected_read(marker_path)[0] if marker_path.exists() else None)!=marker_raw:
            raise MigrationError('source_changed_during_migration')
        check_source(source)
        if apply:
            replace_preserving_owner(store,candidate,info)
        report={'status':'APPLIED' if apply else 'PREPARED_NOT_APPLIED',
            'native_version':PINNED_VERSION,'backup_verified':True,'before_sha256':before_hash,
            'candidate_sha256':digest(candidate),'unique_existing_target':True,'daily_limit':2,
            'native_api_called':True,'global_generation_enabled':False,'service_started':False,
            'real_qq':False,'model_calls':0}
        try: write_new(backup_dir/'migration-result.json',json_bytes(report))
        except OSError:
            raise MigrationError('applied_but_receipt_write_failed' if apply else 'candidate_receipt_write_failed') from None
        return report


def migrate(deployment, backup_dir, *, apply=False, approved=False):
    if approved is not True: raise MigrationError('explicit_target_migration_approval_required')
    stopped_image(deployment)
    store=deployment/'state/data/plugin_data/astrbot_plugin_private_companion/companions.json'
    fd=os.open(store,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise MigrationError('another_migration_is_running') from None
        return _migrate_locked(deployment,backup_dir,apply=apply,approved=approved)


def rollback(deployment, backup_dir, *, approved=False):
    if approved is not True: raise MigrationError('explicit_target_migration_approval_required')
    original_container=stopped_image(deployment)
    store=deployment/'state/data/plugin_data/astrbot_plugin_private_companion/companions.json'
    data=(deployment/'state/data').resolve()
    if store.is_symlink() or data not in store.resolve().parents: raise MigrationError('unsafe_state_path')
    before=protected_read(backup_dir/'companions.before.json')[0]
    manifest=json.loads(protected_read(backup_dir/'backup-manifest.json')[0])
    result=json.loads(protected_read(backup_dir/'migration-result.json')[0])
    if digest(before)!=manifest.get('sha256') or digest(before)!=result.get('before_sha256') or result.get('status')!='APPLIED':
        raise MigrationError('rollback_backup_mismatch')
    fd=os.open(store,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise MigrationError('another_migration_is_running') from None
        current,info=protected_read(store)
        if digest(current)!=result.get('candidate_sha256'):
            raise MigrationError('rollback_would_overwrite_newer_data')
        if stopped_image(deployment)!=original_container: raise MigrationError('container_changed')
        replace_preserving_owner(store,before,info)
    return {'status':'ROLLED_BACK','before_sha256':digest(before),'service_started':False,
            'global_generation_enabled':False,'real_qq':False,'model_calls':0}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment',type=Path,default=Path('/opt/qq-ai-companion/deployment'))
    parser.add_argument('--backup-dir',type=Path)
    parser.add_argument('--approved-target-migration',action='store_true')
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--rollback',action='store_true',help='Restore an APPLIED backup only if the current file still matches its exact candidate hash.')
    parser.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
    try:
        if args.worker:
            result=native_candidate(Path('/native'),Path('/work/companions.before.json'),
                Path('/work/identity.json'),Path('/work/companions.after.json'))
            write_new(Path('/work/worker-result.json'),json_bytes(result))
        else:
            if args.backup_dir is None: raise MigrationError('new_backup_directory_required')
            if args.apply and args.rollback: raise MigrationError('conflicting_operation')
            if args.rollback:
                result=rollback(args.deployment,args.backup_dir,approved=args.approved_target_migration)
            else:
                result=migrate(args.deployment,args.backup_dir,apply=args.apply,approved=args.approved_target_migration)
        print(json.dumps(result,ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({'status':'BLOCKED','reason':str(exc) if isinstance(exc,MigrationError) else 'migration_failed',
                          'service_started':False,'global_generation_enabled':False}))
        return 1


if __name__=='__main__': raise SystemExit(main())
