#!/usr/bin/env python3
"""One stopped, reversible Phase3 V2 release; read-only PLAN by default.

Requires a source-manifest.json with exact `files` hashes, `baseline` target
hashes (null only for new files), `compose_sha256`, `approved_profile_hash`, and
`approved_profile_identity_hash`. No source download, API call or QQ test occurs.
"""
import argparse
from contextlib import closing
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT),str(ROOT/'scripts')]
from integrations.phase3_memory.profile_release import ProductionWindow, _module, validate_approval, digest
from integrations.phase3_memory.bridge import MemoryDenied

SCRIPTS = frozenset({'phase3-deploy-experience.py','phase3-core-profile-worker.py',
    'phase3-profile-production.py','phase3-profile-import.py','phase3-install-overlay.py',
    'phase2-backup.py','backup.py','prepare_plugins.py','plugin_permissions.py'})
PLUGINS = frozenset({'phase1_guard','phase2_policy','phase3_runtime','phase3_memory','phase3_search'})
CONFIGS = ('data/cmd_config.json','data/config/astrbot_plugin_memory_companion_config.json',
           'data/config/astrbot_plugin_private_companion_config.json')
BASELINE_EXTRA = ('data/config/phase3_runtime_config.json','data/config/phase1_guard_config.json',
                  'data/config/phase2_policy_config.json','data/config/phase3_search_config.json')
NAPCAT_IMAGE = 'sha256:2f1dd81bdd62f27b3431ac9254000602ed7ed6dfbf0a38bb6e735fd16bc4ee87'


def read(path): return json.loads(path.read_text(encoding='utf-8-sig'))
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_path(root,name):
    rel = Path(name)
    if rel.is_absolute() or '..' in rel.parts or not rel.parts:
        raise MemoryDenied('unsafe_release_path')
    target = root/rel
    if any(p.is_symlink() for p in (target,*target.parents)):
        raise MemoryDenied('symlink_release_path')
    return target


def package_files(root,manifest):
    names = manifest.get('files',{})
    if not isinstance(names,dict): raise MemoryDenied('source_inventory_required')
    required = {'scripts/'+s for s in SCRIPTS}
    required |= {'integrations/'+p+'/__init__.py' for p in PLUGINS}
    required |= {'integrations/phase3_runtime/main.py','integrations/phase3_memory/core_profile.py',
                 'integrations/phase1_guard/delivery.py','integrations/phase3_runtime/profile_recall.py'}
    if not required.issubset(names): raise MemoryDenied('incomplete_release_package')
    targets = {}
    for name,expected in names.items():
        parts = Path(name).parts
        allowed = (name in {'scripts/'+s for s in SCRIPTS} or
            (len(parts)>=3 and parts[0]=='integrations' and parts[1] in PLUGINS
             and Path(name).suffix in {'.py','.json','.yaml','.yml','.md'} and '__pycache__' not in parts))
        if not allowed: raise MemoryDenied('unexpected_release_source')
        path = safe_path(root,name)
        if not path.is_file() or path.stat().st_size>1024*1024 or sha(path)!=expected:
            raise MemoryDenied('source_hash_mismatch')
        if name.startswith('integrations/'):
            targets['data/plugins/'+name[len('integrations/'):]] = name
    targets['data/plugins/phase2_policy/plugin_permissions.py'] = 'scripts/plugin_permissions.py'
    return targets


def verify_baseline(dep,manifest,targets):
    baseline = manifest.get('baseline',{})
    expected = set(targets)|set(CONFIGS)|set(BASELINE_EXTRA)
    if set(baseline)!=expected: raise MemoryDenied('complete_exact_baseline_required')
    for name,value in baseline.items():
        path = safe_path(dep/'state',name)
        actual = sha(path) if path.is_file() else None
        if path.exists() and not path.is_file(): raise MemoryDenied('nonregular_baseline_target')
        if actual!=value: raise MemoryDenied('production_changed_since_release_review')
    if sha(dep/'compose.yml')!=manifest.get('compose_sha256'):
        raise MemoryDenied('compose_changed_since_release_review')


def private_config(current):
    memory = deepcopy(read(current/'config/astrbot_plugin_memory_companion_config.json'))
    memory.setdefault('core_memory',{}).update(enabled=True,max_chars=1600)
    memory['core_memory']['max_blocks'] = max(8,memory['core_memory'].get('max_blocks',8))
    memory.setdefault('memory_injection',{})['max_chars'] = max(4000,memory.get('memory_injection',{}).get('max_chars',4000))
    private = deepcopy(read(current/'config/astrbot_plugin_private_companion_config.json'))
    def update(value):
        for key,item in value.items():
            if key=='proactive_unanswered_pause_after': value[key]=0
            elif isinstance(item,dict): update(item)
    update(private)
    private.setdefault('private_followup_config',{})['proactive_unanswered_pause_after']=0
    return memory,private


def validate_passive(dep):
    data = dep/'state/data'
    settings = read(data/'config/phase3_runtime_config.json')
    guard = read(data/'config/phase1_guard_config.json')
    if (not all(settings.get(k) is True for k in ('enabled','persona','natural_chat','memory'))
        or settings.get('proactive') or settings.get('background_web') or guard.get('phase3_proactive_enabled')):
        raise MemoryDenied('approved_passive_candidate_with_proactive_off_required')


def require_idle(dep):
    guard=dep/'state/data/plugin_data/phase1_guard/guard.sqlite3'
    with closing(sqlite3.connect(guard.as_uri()+'?mode=ro',uri=True)) as db:
        if db.execute("SELECT count(*) FROM turns WHERE state IN ('collecting','ready','generating')").fetchone()[0]:
            raise MemoryDenied('active_private_turn_retry_after_completion')


def require_locked_images(dep,controller,runner=ProductionWindow._run):
    prefix=['docker','compose','--project-directory',str(dep),'-f',str(dep/'compose.yml')]
    for service,image in [('astrbot',controller.IMAGE),('napcat',NAPCAT_IMAGE)]:
        ids=runner(prefix+['ps','--all','--quiet',service]).split()
        if len(ids)!=1: raise MemoryDenied('one_existing_service_required')
        row=json.loads(runner(['docker','inspect',ids[0]]))[0]
        if row.get('Image')!=image: raise MemoryDenied('pinned_production_image_required')


def prepare_overlay(root,dep,targets,memory,private,controller):
    candidate=root/'candidate'
    candidate.mkdir(mode=0o700)
    for dest,source in targets.items():
        target=candidate/dest;target.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
        shutil.copy2(root/source,target);target.chmod(0o600)
    (candidate/'data/config').mkdir(parents=True,mode=0o700,exist_ok=True)
    shutil.copy2(dep/'state/data/cmd_config.json',candidate/'data/cmd_config.json')
    (candidate/'data/cmd_config.json').chmod(0o600)
    for name,value in [(CONFIGS[1],memory),(CONFIGS[2],private)]:
        controller.write_private(candidate/name,value)
    controller.write_private(candidate/'review-manifest.json',{'source_config_sha256':sha(dep/'state/data/cmd_config.json')})
    return candidate


def rollback_database(window,journal,controller):
    """Only before resume, and only if each destination is still our version."""
    window.assert_stopped()
    receipt=read(journal/'journal.json')
    if receipt.get('state')=='ROLLED_BACK': return
    if receipt.get('state')!='APPLIED': raise MemoryDenied('database_journal_not_applied')
    for row in receipt['destinations']:
        path=safe_path(window.deployment/'state',row['target'])
        if (sha(path) if path.is_file() else None)!=row['new_sha256']:
            raise MemoryDenied('database_changed_after_install_no_automatic_restore')
    for row in reversed(receipt['destinations']):
        path=window.deployment/'state'/row['target']
        if row['old']:
            prior=journal/row['old']
            if sha(prior)!=row['old_sha256']: raise MemoryDenied('database_journal_digest_mismatch')
            temporary=path.with_name(path.name+'.experience-rollback')
            shutil.copy2(prior,temporary);temporary.chmod(0o600)
            os.chown(temporary,row['uid'],row['gid']);os.replace(temporary,path)
        else: path.unlink(missing_ok=True)
    receipt['state']='ROLLED_BACK';controller.write_private(journal/'journal.json',receipt)


def rollback_overlay(dep,journal,installer):
    """Restore exact overlaid files; no conversation/database directory restore."""
    if not (journal/'manifest.json').exists(): return
    installer.verify_stopped(dep)
    rows=read(journal/'manifest.json')
    for row in reversed(rows):
        target=dep/'state'/row['path']
        actual=sha(target) if target.is_file() else None
        if actual==row.get('old_sha256'): continue
        if actual!=row['new_sha256']: raise MemoryDenied('overlay_changed_after_install_no_automatic_restore')
        if row['existed']:
            prior=journal/'original'/row['path']
            if sha(prior)!=row['old_sha256']: raise MemoryDenied('overlay_journal_digest_mismatch')
            installer.publish(prior,target,row['uid'],row['gid']);target.chmod(row['mode'])
        else: target.unlink()


def run(args,*,run_worker=None):
    os.umask(0o077)
    root=args.source.absolute();dep=args.deployment.absolute()
    for path in (root,dep):
        if path.resolve()!=path or not path.is_dir(): raise MemoryDenied('real_existing_directories_required')
    if root!=ROOT: raise MemoryDenied('run_controller_from_reviewed_source_bundle')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',args.run_id): raise MemoryDenied('invalid_run_id')
    package=read(root/'source-manifest.json')
    targets=package_files(root,package);verify_baseline(dep,package,targets);validate_passive(dep)
    controller=_module('experience_profile_controller',root/'scripts/phase3-profile-production.py')
    installer=_module('experience_overlay',root/'scripts/phase3-install-overlay.py')
    backup=_module('experience_backup',root/'scripts/phase2-backup.py')
    manifest=controller.WORKER.private_json(args.manifest)
    approval=controller.WORKER.private_json(args.approval)
    plan=validate_approval(manifest,approval,'production')
    if (plan['layers']!={'L1':3,'L2':12} or plan['manifest_hash']!=package.get('approved_profile_hash')
        or digest(manifest['identity'])!=package.get('approved_profile_identity_hash')):
        raise MemoryDenied('exact_existing_approved_profile_required')
    if approval.get('runtime_image_id')!=controller.IMAGE: raise MemoryDenied('approved_locked_runtime_required')
    native=dep/'state/data/plugin_data/astrbot_plugin_memory_companion'
    ledger=dep/'state/data/plugin_data/phase3_profile_release/ledger.sqlite3'
    source=dep/'state/data/plugins/astrbot_plugin_memory_companion'
    if not ledger.is_file(): raise MemoryDenied('existing_profile_ledger_required')
    native_prepare=_module('experience_native_source',root/'scripts/prepare_plugins.py')
    if native_prepare.source_tree_sha256(source)!=controller.NATIVE_HASH: raise MemoryDenied('pinned_native_source_required')
    require_locked_images(dep,controller)
    memory,private=private_config(dep/'state/data')
    result={'status':'PLAN','source_files':len(targets),'l1_promote_existing':3,'l2_preserved':12,
        'profile_reimported':False,'whole_data_overwrite':False,'proactive_enabled':False,
        'core_max_chars':1600,'model_calls':0,'images_changed':False,'ports_changed':False}
    if not args.apply: return result
    if (root/'candidate').exists() or (root/'installation-journal').exists():
        raise MemoryDenied('new_release_directory_required')
    services=backup.ComposeServices(dep)
    original=services.running()
    if original!={'astrbot','napcat'}: raise MemoryDenied('both_original_services_running_required')
    require_idle(dep)
    db_journal=None; resume=True; applied=False
    try:
        services.stop()
        # The outer controller owns restart. exercise() sees an empty running
        # set, so its finally keeps both stopped through worker and overlay.
        proof=backup.exercise(dep,args.run_id,apply=True)
        if proof.get('status')!='PASS': raise MemoryDenied('fresh_stopped_backup_restore_failed')
        package_files(root,package)
        verify_baseline(dep,package,targets);validate_passive(dep)
        window=ProductionWindow(dep,args.run_id,proof['astrbot_archive_sha256'])
        window.verify(native/'memory_companion.db')
        controller.write_private(root/'operation.json',{'originally_running_services':sorted(original),
            'backup_run_id':args.run_id,'restart_owned_by':'experience_controller','production_resumed':False})
        candidate=prepare_overlay(root,dep,targets,memory,private,controller)
        reviewed=root/'reviewed';reviewed.mkdir(mode=0o700)
        fresh_approval=deepcopy(approval)
        fresh_approval['backup']={'run_id':args.run_id,'astrbot_archive_sha256':proof['astrbot_archive_sha256']}
        controller.write_private(reviewed/'manifest.json',manifest)
        controller.write_private(reviewed/'approval.json',fresh_approval)
        worker_receipt={}
        def core_worker(_unused_command,offline):
            nonlocal db_journal
            # Remember the location before the host installs, so an interruption
            # immediately after install_files can still discover its journal.
            db_journal=offline/'install-journal'
            controller.write_private(offline/'core-config.json',memory)
            command=controller.worker_command(offline,source)
            index=command.index('/repo/scripts/phase3-profile-import.py')
            command=command[:index]+['/repo/scripts/phase3-core-profile-worker.py',
                '--manifest','/candidate/manifest.json','--approval','/candidate/approval.json',
                '--native-config','/candidate/core-config.json','--native-source','/native',
                '--native-data','/candidate/native','--ledger','/candidate/ledger.sqlite3',
                '--candidate-root','/candidate','--apply']
            if run_worker: value=run_worker(command,offline)
            else:
                process=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=180)
                try: value=json.loads(process.stdout.strip().splitlines()[-1])
                except (ValueError,IndexError): raise MemoryDenied('core_worker_receipt_unavailable')
                if process.returncode: raise MemoryDenied('core_worker_failed')
            if value.get('native_core_injection_full_body_count')!=3 or value.get('model_calls')!=0:
                raise MemoryDenied('full_native_core_injection_proof_required')
            worker_receipt.update(value)
            return value
        options=SimpleNamespace(manifest=reviewed/'manifest.json',approval=reviewed/'approval.json',
            apply=True,withdraw=False,deployment=dep,native_data=native,ledger=ledger,native_source=source)
        migrated=controller.execute(options,run_worker=core_worker)
        db_journal=Path(migrated['journal']).parent
        # Do not call window.verify here: the just-installed native DB is an
        # intentional difference from the old snapshot. assert_stopped suffices.
        window.assert_stopped()
        installer.install(candidate,dep,dep/'backups'/args.run_id,root/'installation-journal',apply=True)
        applied=True
        result.update(status='INSTALLED_AWAITING_HEALTH_AND_QQ',backup_run_id=args.run_id,
            backup_integrity=proof['backup_integrity'],restore_integrity=proof['restore_integrity'],
            native_core_injection_full_body_count=worker_receipt['native_core_injection_full_body_count'],
            native_db_journal=str(db_journal/'journal.json'),overlay_journal=str(root/'installation-journal'),
            rollback_after_resume='disable_new_features_first; preserve_post_release_chat_before_any_data_restore')
    except BaseException:
        try:
            # Installer self-rolls back copy failures; this also handles an
            # interrupted outer operation after a completed installation.
            rollback_overlay(dep,root/'installation-journal',installer)
            if db_journal and (db_journal/'journal.json').exists():
                rollback_database(window,db_journal,controller)
        except BaseException:
            resume=False
            controller.write_private(root/'release-failure.json',{'status':'BLOCKED_REQUIRES_RECOVERY',
                'services_left_stopped':True,'reason':'narrow_rollback_not_verified','raw_content_logged':False})
            raise MemoryDenied('narrow_rollback_not_verified_services_left_stopped') from None
        raise
    finally:
        if resume:
            services.start(original)
            if services.running()!=original: raise MemoryDenied('original_services_not_restored')
            if (root/'operation.json').exists():
                controller.write_private(root/'operation.json',{'originally_running_services':sorted(original),
                    'backup_run_id':args.run_id,'restart_owned_by':'experience_controller','production_resumed':True,
                    'candidate_applied':applied})
    controller.write_private(root/'installed.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--deployment',type=Path,default=Path('/opt/qq-ai-companion/deployment'))
    parser.add_argument('--manifest',type=Path,required=True);parser.add_argument('--approval',type=Path,required=True)
    parser.add_argument('--run-id',required=True);parser.add_argument('--apply',action='store_true')
    try: result=run(parser.parse_args())
    except Exception as exc:
        result={'status':'FAILED','reason':str(exc) if isinstance(exc,MemoryDenied) else type(exc).__name__,
                'raw_content_logged':False}
    print(json.dumps(result,ensure_ascii=False));return int(result['status']=='FAILED')


if __name__=='__main__': raise SystemExit(main())
