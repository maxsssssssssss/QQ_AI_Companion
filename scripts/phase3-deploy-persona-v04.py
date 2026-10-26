#!/usr/bin/env python3
"""Plan by default: publish the approved V0.4 persona after an AstrBot-only backup.

NapCat is never stopped, started, or snapshotted. Configuration and live data
are never directly restored or edited. Startup updates the existing native
persona through the normal policy. Rollback restores only source/template,
retaining new chat and memory.
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

HELPER = 'scripts/phase3-deploy-proactive-recovery.py'
HELPER_SHA = 'ea4c6f6cfa54cd9bd014567afddb6506a38ca87dfd2071c90f1bf610772d9eff'
_helper_path = Path(__file__).parent / Path(HELPER).name
if hashlib.sha256(_helper_path.read_bytes()).hexdigest() != HELPER_SHA:
    raise RuntimeError('locked_release_helper_required')
_spec = importlib.util.spec_from_file_location('persona_v04_release_helper', _helper_path)
h = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = h
_spec.loader.exec_module(h)
ReleaseError, Services = h.ReleaseError, h.Services
read, sha, safe, private_json, load = h.read, h.sha, h.safe, h.private_json, h.load
snapshot, verify_native, source_inventory = h.snapshot, h.verify_native, h.source_inventory
ASTRBOT_IMAGE, NAPCAT_IMAGE = h.ASTRBOT_IMAGE, h.NAPCAT_IMAGE

SCHEMA = 'qq-companion.persona-v04-release.v1'
BASE_RELEASE = 'phase3-20261026-persona-candidate-r1'
CONTROLLER = 'scripts/phase3-deploy-persona-v04.py'
TEMPLATE_TARGET = 'data/phase3/persona-v0.1.md'
TEMPLATE_SOURCE = 'persona/phase3-v0.4.md'
TARGETS = {'data/plugins/phase3_runtime/persona.py': 'integrations/phase3_runtime/persona.py',
           TEMPLATE_TARGET: TEMPLATE_SOURCE}
PLUGIN_TARGETS = set(TARGETS) - {TEMPLATE_TARGET}
APPROVED_TEMPLATE_SHA = 'a922fea5cb5c0fb3ee7fc28dfe9de6b219842cdbc85cb335a19b111bda2e49ca'
PINNED = {**h.PINNED, HELPER: HELPER_SHA}


def idle(deployment):
    h.idle(deployment)
    path = safe(deployment / 'state', 'data/plugin_data/phase3_runtime/proactive-controls.sqlite3')
    if not path.is_file():
        raise ReleaseError('proactive_ledger_required')
    from contextlib import closing
    import sqlite3
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        if (db.execute("SELECT count(*) FROM proactive_generations WHERE state='running'").fetchone()[0]
                or db.execute("SELECT count(*) FROM proactive_attempts WHERE state='pending'").fetchone()[0]):
            raise ReleaseError('active_proactive_retry_after_completion')


def baseline_sha(manifest, relative):
    return (manifest.get('baseline_template_sha256') if relative == TEMPLATE_TARGET
            else manifest['baseline_source_files'].get(relative))


def validate_template(path, version, count):
    text = path.read_text(encoding='utf-8')
    expected = {'0.3-candidate': '[QQ_COMPANION_PERSONA_V0_3_CANDIDATE]',
                '0.4': '# QQ_COMPANION_PERSONA_V0_4'}.get(version)
    markers = re.findall(r'\[QQ_COMPANION_PERSONA_[^\]\r\n]*\]|^# QQ_COMPANION_PERSONA_[^\r\n]*', text, re.MULTILINE)
    if (expected is None or markers != [expected] or not text.startswith(expected)
            or text.count('{{preferred_address}}') != count):
        raise ReleaseError('persona_template_version_or_address_count_mismatch')


def validate_renderer(path):
    """Inspect literals without executing candidate code or logging persona text."""
    tree = ast.parse(path.read_text(encoding='utf-8'), filename='persona.py')
    assignments = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            assignments[node.targets[0].id] = node.value
    current = assignments.get('CURRENT_TEMPLATE')
    versions = assignments.get('VERSIONS')
    if not isinstance(current, ast.Constant) or current.value != 'phase3-v0.4.md' or not isinstance(versions, ast.Dict):
        raise ReleaseError('v04_renderer_contract_required')
    registered = {}
    for key, value in zip(versions.keys, versions.values):
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            try: registered[key.value] = ast.literal_eval(value)
            except (ValueError, TypeError): raise ReleaseError('literal_persona_versions_required') from None
    if (registered.get('[QQ_COMPANION_PERSONA_V0_2]') != ('0.2', 3)
            or registered.get('[QQ_COMPANION_PERSONA_V0_3]') != ('0.3', 4)
            or registered.get('[QQ_COMPANION_PERSONA_V0_3_CANDIDATE]') != ('0.3-candidate', 6)
            or registered.get('# QQ_COMPANION_PERSONA_V0_4') != ('0.4', 8)):
        raise ReleaseError('v04_renderer_contract_required')


def validate(source, deployment, *, installed=False):
    manifest = read(safe(source, 'source-manifest.json'))
    if manifest.get('schema') != SCHEMA or manifest.get('base_release') != BASE_RELEASE:
        raise ReleaseError('latest_candidate_release_baseline_required')
    files = manifest.get('files', {})
    if set(files) != set(PINNED) | {CONTROLLER} | set(TARGETS.values()):
        raise ReleaseError('exact_persona_v04_package_required')
    for name, digest in files.items():
        path = safe(source, name)
        if not path.is_file() or path.stat().st_size > 1024 * 1024 or sha(path) != digest:
            raise ReleaseError('reviewed_source_hash_mismatch')
    if any(files[name] != digest for name, digest in PINNED.items()):
        raise ReleaseError('locked_helper_or_dependency_changed')
    if files[CONTROLLER] != sha(Path(__file__)):
        raise ReleaseError('executing_controller_not_reviewed')
    for relative in TARGETS.values():
        if relative.endswith('.py'):
            ast.parse((source / relative).read_text(encoding='utf-8'), filename=relative)
    if files[TEMPLATE_SOURCE] != APPROVED_TEMPLATE_SHA:
        raise ReleaseError('approved_v04_template_required')
    validate_template(source / TEMPLATE_SOURCE, '0.4', 8)
    validate_renderer(source / 'integrations/phase3_runtime/persona.py')
    state = safe(deployment, 'state')
    baseline = manifest.get('baseline_source_files', {})
    if not PLUGIN_TARGETS <= set(baseline) or set(baseline) != source_inventory(state):
        raise ReleaseError('first_party_source_inventory_changed')
    for name, old_digest in baseline.items():
        parts = Path(name).parts
        if (len(parts) < 4 or parts[:2] != ('data', 'plugins')
                or parts[2] not in h.PLUGINS or Path(name).suffix not in h.SUFFIXES):
            raise ReleaseError('invalid_protected_source_path')
        digest = files[TARGETS[name]] if installed and name in TARGETS else old_digest
        if sha(safe(state, name)) != digest:
            raise ReleaseError('protected_source_changed')
    template = safe(state, TEMPLATE_TARGET)
    old_template_sha = manifest.get('baseline_template_sha256')
    if not isinstance(old_template_sha, str) or not re.fullmatch(r'[0-9a-f]{64}', old_template_sha):
        raise ReleaseError('baseline_template_hash_required')
    if sha(template) != (files[TEMPLATE_SOURCE] if installed else old_template_sha):
        raise ReleaseError('protected_template_changed')
    validate_template(template, '0.4' if installed else '0.3-candidate', 8 if installed else 6)
    if any(baseline_sha(manifest, name) == files[rel] for name, rel in TARGETS.items()):
        raise ReleaseError('both_reviewed_targets_must_change')
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
    search = read(safe(state, 'data/config/phase3_search_config.json'))
    limits = search.get('limits', {})
    if (search.get('enabled') is not True or search.get('scope') != 'general_public'
            or type(search.get('daily_attempt_limit')) is not int or search['daily_attempt_limit'] != 60
            or not isinstance(limits, dict) or type(limits.get('max_calls', 3)) is not int
            or limits.get('max_calls', 3) != 3):
        raise ReleaseError('existing_general_search_with_3_60_required')
    verify_native(source, deployment)
    return manifest


def prepare_journal(deployment, journal, manifest):
    if journal.exists():
        raise ReleaseError('fresh_code_journal_required')
    journal.mkdir(mode=0o700)
    rows = []
    for relative, candidate in TARGETS.items():
        target = safe(deployment / 'state', relative)
        info = target.stat()
        row = {'path': relative, 'old_sha256': sha(target), 'new_sha256': manifest['files'][candidate],
               'uid': info.st_uid, 'gid': info.st_gid, 'mode': stat.S_IMODE(info.st_mode)}
        if row['old_sha256'] != baseline_sha(manifest, relative):
            raise ReleaseError('source_changed_before_journal')
        original = journal / 'original' / relative
        original.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copy2(target, original)
        original.chmod(0o600)
        rows.append(row)
    private_json(journal / 'journal.json', {'schema': SCHEMA, 'files': rows,
        'database_restore_permitted': False, 'config_change_permitted': False})
    return rows


def restore_sources(deployment, journal, installer, services, manifest):
    services.check(astr_running=False, require_nap=False)
    data = read(safe(journal, 'journal.json'))
    rows = data.get('files', [])
    if (data.get('schema') != SCHEMA or len(rows) != len(TARGETS)
            or {row.get('path') for row in rows} != set(TARGETS)
            or data.get('database_restore_permitted') is not False
            or data.get('config_change_permitted') is not False):
        raise ReleaseError('persona_v04_source_only_journal_required')
    # Validate all entries before the first rollback write. Never undo drift.
    for row in rows:
        relative = row['path']
        if (row['old_sha256'] != baseline_sha(manifest, relative)
                or row['new_sha256'] != manifest['files'][TARGETS[relative]]):
            raise ReleaseError('journal_does_not_match_reviewed_hashes')
        target = safe(deployment / 'state', relative)
        original = safe(journal, 'original/' + relative)
        if sha(target) not in {row['old_sha256'], row['new_sha256']}:
            raise ReleaseError('source_changed_since_install')
        if sha(original) != row['old_sha256']:
            raise ReleaseError('original_source_journal_corrupt')
    for row in reversed(rows):
        target = safe(deployment / 'state', row['path'])
        installer.publish(journal / 'original' / row['path'], target, row['uid'], row['gid'])
        target.chmod(row['mode'])
    for row in rows:
        if sha(safe(deployment / 'state', row['path'])) != row['old_sha256']:
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
        'target_files': list(TARGETS), 'napcat_restart': False, 'database_or_config_write': False,
        'whole_data_restore': False, 'models_called': 0, 'new_chats_preserved': True,
        'native_persona_update_on_start': True, 'persona_version': '0.3-candidate' if rolling_back else '0.4'}
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
        backup = load('persona_v04_backup', source / 'scripts/backup.py')
        installer = load('persona_v04_installer', source / 'scripts/phase3-install-overlay.py')
        validate(source, deployment, installed=rolling_back)
        idle(deployment); services.check(astr_running=initial_running)
        if initial_running:
            services.stop_astr()
        proof = snapshot(deployment, output, services, backup)
        validate(source, deployment, installed=rolling_back)
        idle(deployment)
        if rolling_back:
            touched = True
            restore_sources(deployment, journal, installer, services, manifest)
        else:
            rows = prepare_journal(deployment, journal, manifest)
            touched = True
            for row in rows:
                services.check(astr_running=False)
                target = safe(deployment / 'state', row['path'])
                installer.publish(source / TARGETS[row['path']], target, row['uid'], row['gid'])
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
