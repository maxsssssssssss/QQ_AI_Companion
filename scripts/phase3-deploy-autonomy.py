#!/usr/bin/env python3
"""PLAN by default: scoped autonomy publication, AstrBot-only backup/restart.

Restore owned source/configuration fields only. New chat, research, private audit,
confirmed-send ledgers and native self-preferences are never rolled back as data.
"""
import argparse
import ast
from copy import deepcopy
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys

HELPER='scripts/phase3-deploy-proactive-frequency.py'
HELPER_SHA='d417928f3dfc7a5d8d241f0b2d4220407425382c41a624907c49c989b59981c1'
p=Path(__file__).with_name(Path(HELPER).name)
if hashlib.sha256(p.read_bytes()).hexdigest()!=HELPER_SHA:
    raise RuntimeError('locked_frequency_helper_required')
spec=importlib.util.spec_from_file_location('autonomy_frequency_helper',p)
f=importlib.util.module_from_spec(spec);sys.modules[spec.name]=f;spec.loader.exec_module(f)
ReleaseError,Services=f.ReleaseError,f.Services
safe,sha,read,private_json,load=f.safe,f.sha,f.read,f.private_json,f.load
verify_native=f.verify_native
SCHEMA='qq-companion.autonomy-release.v1'
BASE='phase3-20261027-proactive-frequency-r1'
CONTROLLER='scripts/phase3-deploy-autonomy.py'
PINNED={**f.PINNED,HELPER:HELPER_SHA}
TARGETS={
    **{'data/plugins/phase3_runtime/'+name:'integrations/phase3_runtime/'+name for name in (
        'main.py','proactive.py','settings.py','_conf_schema.json','autonomy.py',
        'autonomy_contract.py','autonomy_request.py','autonomy_state.py','autonomy_native.py')},
    'data/plugins/phase3_memory/self_preferences.py':'integrations/phase3_memory/self_preferences.py',
    'data/plugins/phase3_search/policy.py':'integrations/phase3_search/policy.py',
    'data/plugins/phase3_search/proactive.py':'integrations/phase3_search/proactive.py',
}
CONFIG='data/config/phase3_runtime_config.json'
TEMPLATE=f.TEMPLATE_TARGET
POLICY={'mode':'continuous','window_start':0,'window_end':0,'max_per_day':12,
        'max_generations_per_day':96,'cooldown':5400,'quiet_hours':[23,9]}


def config_patch(original,source):
    if not isinstance(original,dict) or any(original.get(k) is not True for k in
            ('enabled','persona','natural_chat','memory','search','proactive')):
        raise ReleaseError('existing_enabled_companion_required')
    window=original.get('proactive_window')
    if not isinstance(window,dict) or any(window.get(k)!=v for k,v in POLICY.items()):
        raise ReleaseError('live_90_96_12_policy_required_not_modified')
    old=original.get('proactive_autonomy',{})
    if not isinstance(old,dict) or old.get('enabled',False) is not False:
        raise ReleaseError('existing_autonomy_must_be_disabled')
    settings=load('autonomy_candidate_settings',source/'integrations/phase3_runtime/settings.py')
    candidate=settings.autonomy_candidate(deepcopy(original))
    if {k:v for k,v in candidate.items() if k!='proactive_autonomy'}!={k:v for k,v in original.items() if k!='proactive_autonomy'}:
        raise ReleaseError('candidate_changed_unowned_configuration')
    if candidate.get('proactive_autonomy',{}).get('enabled') is not True:
        raise ReleaseError('candidate_activation_required')
    return candidate


def idle(dep):
    f.idle(dep)
    path=safe(dep/'state','data/plugin_data/phase3_runtime/proactive-controls.sqlite3')
    import sqlite3
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        db.execute('PRAGMA query_only=ON');db.execute('PRAGMA trusted_schema=OFF')
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('autonomy_calls','autonomy_evaluations'):
            if table in tables and db.execute('SELECT count(*) FROM '+table+" WHERE state='running'").fetchone()[0]:
                raise ReleaseError('active_autonomy_retry_after_completion')


def load_journal(source,m):
    journal=safe(source,'release-journal');j=read(safe(journal,'journal.json'))
    if (j.get('schema')!=SCHEMA or j.get('database_restore_permitted') is not False
            or j.get('native_store_write_permitted') is not False
            or len(j.get('sources',[]))!=len(TARGETS)
            or {row.get('path') for row in j['sources']}!=set(TARGETS)
            or len(j.get('configs',[]))!=1 or j['configs'][0].get('path')!=CONFIG):
        raise ReleaseError('narrow_autonomy_journal_required')
    for row in j['sources']:
        rel=row['path']
        if (row.get('old_sha256')!=m['baseline_source_files'].get(rel)
                or row.get('new_sha256')!=m['files'][TARGETS[rel]]):
            raise ReleaseError('source_journal_hash_changed')
        if row['old_sha256'] is not None and sha(safe(journal,'original/'+rel))!=row['old_sha256']:
            raise ReleaseError('source_journal_corrupt')
    row=j['configs'][0];original=safe(journal,'original/'+CONFIG)
    if sha(original)!=m['protected_config_files'][CONFIG] or row.get('old_sha256')!=sha(original):
        raise ReleaseError('configuration_journal_corrupt')
    before=read(original);after=config_patch(before,source)
    if (row.get('changes')!=f.delta(before,after)
            or row.get('new_sha256')!=hashlib.sha256(f.encode(after,original.read_bytes())).hexdigest()):
        raise ReleaseError('configuration_delta_changed')
    return journal,j


def validate(source,dep,installed=False,*,config_mode='baseline'):
    m=read(safe(source,'source-manifest.json'));files=m.get('files',{})
    if m.get('schema')!=SCHEMA or m.get('base_release')!=BASE:raise ReleaseError('wrong_baseline')
    if set(files)!=set(PINNED)|{CONTROLLER}|set(TARGETS.values()):raise ReleaseError('exact_autonomy_package_required')
    for rel,digest in files.items():
        path=safe(source,rel)
        if not path.is_file() or path.stat().st_size>1048576 or sha(path)!=digest:
            raise ReleaseError('package_hash_changed')
    if any(files[k]!=v for k,v in PINNED.items()) or files[CONTROLLER]!=sha(Path(__file__)):
        raise ReleaseError('locked_controller_changed')
    for rel in TARGETS.values():
        if rel.endswith('.py'):ast.parse((source/rel).read_text())
        else:read(source/rel)
    state=safe(dep,'state');baseline=m.get('baseline_source_files',{})
    if not isinstance(baseline,dict) or not baseline:raise ReleaseError('source_baseline_required')
    expected=set(baseline)|(set(TARGETS) if installed else set())
    if f.h.source_inventory(state)!=expected:raise ReleaseError('source_inventory_changed')
    for rel in expected:
        digest=files[TARGETS[rel]] if installed and rel in TARGETS else baseline[rel]
        if sha(safe(state,rel))!=digest:raise ReleaseError('source_baseline_changed')
    configs=m.get('protected_config_files',{})
    observed={'data/cmd_config.json'}|{p.relative_to(state).as_posix() for p in safe(state,'data/config').rglob('*') if p.is_file()}
    if set(configs)!=observed or CONFIG not in configs:raise ReleaseError('configuration_inventory_changed')
    journal_config=None
    if config_mode!='baseline':journal_config=load_journal(source,m)[1]['configs'][0]
    for rel,digest in configs.items():
        path=safe(state,rel)
        if rel==CONFIG and journal_config:
            if config_mode=='installed':
                if sha(path)!=journal_config['new_sha256']:raise ReleaseError('installed_configuration_changed')
            elif config_mode in ('current_candidate','restored'):
                side='new' if config_mode=='current_candidate' else 'old';document=read(path)
                if any(f.value_at(document,c['path'])!=(c[side+'_present'],c[side]) for c in journal_config['changes']):
                    raise ReleaseError('owned_configuration_changed_since_release')
            else:raise ReleaseError('invalid_configuration_mode')
        elif sha(path)!=digest:raise ReleaseError('protected_configuration_changed')
    if sha(safe(dep,'compose.yml'))!=m.get('compose_sha256') or sha(safe(dep,'.env'))!=m.get('env_sha256'):
        raise ReleaseError('compose_or_environment_changed')
    f.native_target(dep,expected=(12,))
    digest=m.get('baseline_template_sha256')
    if not isinstance(digest,str) or not re.fullmatch(r'[0-9a-f]{64}',digest) or sha(safe(state,TEMPLATE))!=digest:
        raise ReleaseError('persona_template_changed')
    if config_mode=='baseline':config_patch(read(safe(state,CONFIG)),source)
    verify_native(source,dep)
    return m


def prepare_journal(source,dep,m):
    journal=safe(source,'release-journal')
    if journal.exists():raise ReleaseError('new_journal_required')
    journal.mkdir(mode=0o700);rows=[];state=dep/'state'
    def copy_original(rel):
        target=safe(state,rel);saved=safe(journal,'original/'+rel)
        saved.parent.mkdir(parents=True,exist_ok=True,mode=0o700);shutil.copy2(target,saved);saved.chmod(0o600)
    for rel,candidate in TARGETS.items():
        target=safe(state,rel);old=sha(target) if target.exists() else None
        if old!=m['baseline_source_files'].get(rel):raise ReleaseError('source_changed_before_journal')
        rows.append({'path':rel,'old_sha256':old,'new_sha256':m['files'][candidate],**f.metadata(target)})
        if old is not None:copy_original(rel)
    target=safe(state,CONFIG);raw=target.read_bytes()
    if sha(target)!=m['protected_config_files'][CONFIG]:raise ReleaseError('configuration_changed_before_journal')
    original=read(target);candidate=config_patch(original,source);copy_original(CONFIG)
    output=safe(journal,'candidate/'+CONFIG);output.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    output.write_bytes(f.encode(candidate,raw));output.chmod(0o600)
    config={'path':CONFIG,'old_sha256':sha(target),'new_sha256':sha(output),
            'changes':f.delta(original,candidate),**f.metadata(target)}
    private_json(journal/'journal.json',{'schema':SCHEMA,'sources':rows,'configs':[config],
        'database_restore_permitted':False,'native_store_write_permitted':False})
    load_journal(source,m);return journal


def validate_candidates(source,m):
    journal,j=load_journal(source,m)
    row=j['configs'][0]
    if sha(safe(journal,'candidate/'+CONFIG))!=row['new_sha256']:
        raise ReleaseError('configuration_candidate_corrupt')
    return journal,j


def install(source,dep,m,installer,services):
    journal,j=validate_candidates(source,m)
    for row in j['sources']:
        services.check(astr_running=False)
        f.publish_file(installer,source/TARGETS[row['path']],safe(dep/'state',row['path']),row)
    services.check(astr_running=False)
    f.publish_file(installer,journal/'candidate'/CONFIG,safe(dep/'state',CONFIG),j['configs'][0])


def restore(source,dep,m,installer,services):
    services.check(astr_running=False,require_nap=False);journal,j=load_journal(source,m);state=dep/'state'
    for row in j['sources']:
        target=safe(state,row['path']);digest=sha(target) if target.exists() else None
        if digest not in (row['old_sha256'],row['new_sha256']):raise ReleaseError('source_changed_since_release')
    row=j['configs'][0];target=safe(state,CONFIG);raw=target.read_bytes()
    document=f.restore_fields(read(target),row['changes']);original=journal/'original'/CONFIG
    encoded=original.read_bytes() if document==read(original) else f.encode(document,raw)
    output=safe(journal,'rollback-candidate/'+CONFIG);output.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    output.write_bytes(encoded);output.chmod(0o600)
    f.publish_file(installer,output,target,row)
    for row in reversed(j['sources']):
        target=safe(state,row['path'])
        if row['old_sha256'] is None:
            if target.exists():target.unlink()
        else:f.publish_file(installer,journal/'original'/row['path'],target,row)


def execute(args,services=None):
    safe(args.source);safe(args.deployment);source=args.source.resolve();dep=args.deployment.resolve();back=bool(args.rollback)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',args.run_id):raise ReleaseError('invalid_run_id')
    mode='current_candidate' if back else 'baseline'
    m=validate(source,dep,back,config_mode=mode);services=services or Services(dep);services.original=services.snapshot()
    was_running=services.original['astrbot']['running']
    if not services.original['napcat']['running'] or (not back and not was_running):raise ReleaseError('running_services_required')
    idle(dep)
    result={'status':'PLAN','operation':'rollback_owned_code_and_fields' if back else 'install_autonomy',
        'target_files':list(TARGETS),'config_files':[CONFIG],'native_store_modified':False,
        'napcat_restart':False,'whole_data_restore':False,'sqlite_write':False,'models_called':0,
        'new_chats_preserved':True,'counters_reset':False,'frequency_policy_modified':False}
    if not args.apply:return result
    operation=safe(source,'operations/'+args.run_id+('-rollback.json' if back else '-install.json'))
    out=safe(dep,'backups/'+args.run_id)
    if operation.exists() or out.exists():raise ReleaseError('new_operation_required')
    os.umask(0o077);lock=os.open(dep/'.astrbot-runtime-release.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:os.close(lock);raise ReleaseError('another_release_running') from None
    result['status']='FAILED';touched=False
    try:
        backup=load('autonomy_release_backup',source/'scripts/backup.py')
        installer=load('autonomy_release_installer',source/'scripts/phase3-install-overlay.py')
        validate(source,dep,back,config_mode=mode);idle(dep);services.check(astr_running=was_running)
        if was_running:services.stop_astr()
        idle(dep);proof=f.h.snapshot(dep,out,services,backup)
        validate(source,dep,back,config_mode=mode)
        if back:touched=True;restore(source,dep,m,installer,services)
        else:
            prepare_journal(source,dep,m);validate_candidates(source,m)
            touched=True;install(source,dep,m,installer,services)
        validate(source,dep,not back,config_mode='restored' if back else 'installed')
        services.start_astr()
        result.update(status='ROLLED_BACK_AWAITING_HEALTH' if back else 'INSTALLED_AWAITING_HEALTH',
            backup_integrity=proof['backup_integrity'],restore_integrity=proof['restore_integrity'],
            sqlite_checked_count=proof['sqlite_checked_count'],backup_run_id=args.run_id,
            source_journal=str(source/'release-journal'))
    except BaseException as exc:
        result['reason']=str(exc) if isinstance(exc,ReleaseError) else type(exc).__name__
        try:
            observed=services.check(require_nap=False)
            if touched:
                if observed['astrbot']['running']:services.stop_astr(require_nap=False)
                restore(source,dep,m,installer,services)
            validate(source,dep,back and not touched,config_mode='restored' if touched else mode)
            if (touched or was_running) and not services.check(require_nap=False)['astrbot']['running']:
                services.start_astr(require_nap=False)
            result.update(known_baseline_recovered=touched,original_astrbot_resumed=touched or was_running)
        except BaseException as recovery:
            result.update(status='BLOCKED_RELEASE_RECOVERY_REQUIRED',recovery_error_type=type(recovery).__name__)
    finally:
        try:
            observed=services.snapshot();same=services.nap_identity(observed['napcat'])==services.nap_identity(services.original['napcat'])
            result.update(napcat_identity_unchanged=same,astrbot_running=observed['astrbot']['running'])
            if not same and result['status'].startswith(('INSTALLED','ROLLED_BACK')):result['status']='BLOCKED_NAPCAT_STATE_CHANGED'
        except Exception:result.update(status='BLOCKED_FINAL_SERVICE_STATE_UNAVAILABLE')
        try:private_json(operation,result);private_json(source/('rollback-result.json' if back else 'install-result.json'),result)
        finally:os.close(lock)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--deployment',type=Path,default=Path('/opt/qq-ai-companion/deployment'))
    parser.add_argument('--run-id',required=True);parser.add_argument('--apply',action='store_true');parser.add_argument('--rollback',action='store_true')
    try:result=execute(parser.parse_args())
    except Exception as exc:result={'status':'FAILED','reason':str(exc) if isinstance(exc,ReleaseError) else type(exc).__name__}
    print(json.dumps(result));raise SystemExit(result['status'].startswith(('FAILED','BLOCKED')))
