#!/usr/bin/env python3
"""PLAN by default: publish the reviewed proactive and bubble candidates.

Stop only AstrBot; verify a consistent backup and isolated restore before any
replacement. Merge approved configuration fields from current files. Rollback
restores code and owned fields, never live databases or whole native history.
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
import sqlite3
import stat
import sys

HELPER='scripts/phase3-deploy-proactive-recovery.py'
HELPER_SHA='ea4c6f6cfa54cd9bd014567afddb6506a38ca87dfd2071c90f1bf610772d9eff'
p=Path(__file__).with_name(Path(HELPER).name)
if hashlib.sha256(p.read_bytes()).hexdigest()!=HELPER_SHA:
    raise RuntimeError('locked_helper_required')
spec=importlib.util.spec_from_file_location('tuning_bubbles_helper',p)
h=importlib.util.module_from_spec(spec);sys.modules[spec.name]=h;spec.loader.exec_module(h)
ReleaseError,Services=h.ReleaseError,h.Services
safe,sha,read,private_json,load=h.safe,h.sha,h.read,h.private_json,h.load
verify_native=h.verify_native
CONTROLLER='scripts/phase3-deploy-tuning-and-bubbles.py'
SCHEMA='qq-companion.tuning-bubbles-release.v1'
BASE='phase3-20261025-bubble-boundary-r1'
TARGETS={**{'data/plugins/phase1_guard/'+n:'integrations/phase1_guard/'+n
            for n in ('bubbles.py','main.py')},
         **{'data/plugins/phase3_runtime/'+n:'integrations/phase3_runtime/'+n
            for n in ('main.py','settings.py','proactive.py','proactive_history.py','_conf_schema.json')}}
NEW_TARGET='data/plugins/phase3_runtime/proactive_history.py'
PINNED={**h.PINNED,HELPER:HELPER_SHA}
CONFIG_TARGETS=('data/config/phase3_runtime_config.json',
                'data/config/astrbot_plugin_private_companion_config.json')
NATIVE_STORE='data/plugin_data/astrbot_plugin_private_companion/companions.json'
POLICY={'mode':'continuous','window_start':0,'window_end':0,'max_per_day':8,
        'max_generations_per_day':48,'cooldown':7200,'quiet_hours':[23,9]}
HISTORY={'summary_limit':8,'ordinary_retention_hours':24}
NATIVE_FIELDS={'basic_config':{'quiet_hours':'23:00-09:00'},
 'private_followup_config':{'min_interval_minutes':120},
 'proactive_generation_config':{'proactive_generation_history_limit':80,
  'proactive_history_context_mode':'compact','proactive_history_recent_raw_count':8,
  'proactive_history_max_chars':16000}}


def encode(value,original=b''):
    return (b'\xef\xbb\xbf' if original.startswith(b'\xef\xbb\xbf') else b'')+(json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode()


def config_patch(original,kind):
    result=deepcopy(original)
    if not isinstance(result,dict):raise ReleaseError('configuration_object_required')
    if kind=='runtime':
        if not all(result.get(k) is True for k in ('enabled','persona','natural_chat','proactive')):
            raise ReleaseError('existing_active_runtime_required')
        if not isinstance(result.get('proactive_window'),dict) or result['proactive_window'].get('mode')!='continuous':
            raise ReleaseError('existing_continuous_policy_required')
        result['proactive_window']=deepcopy(POLICY)
        result['proactive_history']=deepcopy(HISTORY)
    elif kind=='private':
        for group,values in NATIVE_FIELDS.items():
            if not isinstance(result.get(group,{}),dict):raise ReleaseError('configuration_group_required')
            result.setdefault(group,{}).update(values)
            for key,value in values.items():
                if key in result:result[key]=deepcopy(value)
        def find(value,key):
            found=[]
            if isinstance(value,dict):
                for k,v in value.items():
                    if k==key:found.append(v)
                    if isinstance(v,dict):found.extend(find(v,key))
            return found
        generation=find(result,'enable_llm_proactive_message');limits=find(result,'max_daily_messages')
        if (not generation or not limits or any(v is not False for v in generation)
                or any(type(v) is not int or v!=0 for v in limits)):
            raise ReleaseError('native_disk_generation_must_remain_disabled')
        if any(v!='json' for v in find(result,'storage_backend')):
            raise ReleaseError('native_json_backend_required')
    else:raise ReleaseError('unknown_configuration_kind')
    return result


def delta(before,after,prefix=()):
    """Describe only changed JSON leaves, including absent values."""
    result=[]
    for key in sorted(set(before)|set(after)):
        old_present=key in before;new_present=key in after
        old=before.get(key);new=after.get(key);path=prefix+(key,)
        if old_present and new_present and old==new:continue
        if isinstance(old,dict) and isinstance(new,dict):result+=delta(old,new,path)
        else:result.append({'path':list(path),'old_present':old_present,'old':old,
                            'new_present':new_present,'new':new})
    return result


def value_at(document,path):
    current=document
    for key in path[:-1]:
        if not isinstance(current,dict) or key not in current:return False,None
        current=current[key]
    return (path[-1] in current,current.get(path[-1])) if isinstance(current,dict) else (False,None)


def restore_fields(document,changes):
    result=deepcopy(document)
    for change in changes:
        present,current=value_at(result,change['path'])
        old=(change['old_present'],change['old']);new=(change['new_present'],change['new'])
        if (present,current) not in (old,new):raise ReleaseError('owned_config_field_changed_since_release')
        parent=result
        for key in change['path'][:-1]:
            if key not in parent:parent[key]={}
            if not isinstance(parent[key],dict):raise ReleaseError('owned_config_parent_changed')
            parent=parent[key]
        if change['old_present']:parent[change['path'][-1]]=deepcopy(change['old'])
        else:parent.pop(change['path'][-1],None)
    return result


def native_target(dep,*,expected=(5,8)):
    state=dep/'state'
    # Phase2Policy passes this identity to Runtime.prepare at startup. Runtime's
    # persisted configuration intentionally has no administrator/adapter fields.
    policy=read(safe(state,'data/config/phase2_policy_config.json'))
    guard=read(safe(state,'data/config/phase1_guard_config.json'))
    admin,platform=policy.get('admin_id'),policy.get('platform_id')
    if (not isinstance(admin,str) or not re.fullmatch(r'[0-9]{5,15}',admin)
            or not isinstance(platform,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',platform)
            or guard.get('phase3_admin_id')!=admin or guard.get('phase3_platform_id')!=platform):
        raise ReleaseError('verified_policy_guard_identity_required')
    command_config=read(safe(state,'data/cmd_config.json'))
    platform_settings=command_config.get('platform_settings',{})
    if (command_config.get('admins_id')!=[admin] or not isinstance(platform_settings,dict)
            or platform_settings.get('enable_id_white_list') is not True
            or platform_settings.get('id_whitelist')!=[platform+':FriendMessage:'+admin]
            or platform_settings.get('unique_session') is not True):
        raise ReleaseError('existing_single_private_acl_required')
    path=safe(state,NATIVE_STORE)
    if not path.is_file() or path.stat().st_size>64*1024*1024:raise ReleaseError('native_store_required')
    raw=path.read_bytes();data=json.loads(raw.decode('utf-8-sig'));users=data.get('users')
    if not isinstance(users,dict):raise ReleaseError('native_users_required')
    matches=[];active=[]
    for key,user in users.items():
        if not isinstance(user,dict):continue
        caps=user.get('unified_profile_capabilities',{})
        if not isinstance(caps,dict):raise ReleaseError('native_capabilities_required')
        if user.get('proactive_private_enabled') is True or caps.get('proactive_private_enabled') is True:active.append(key)
        if (user.get('umo')==platform+':FriendMessage:'+admin and user.get('identity_subject_id')==admin
                and user.get('identity_adapter_instance_id')==platform and user.get('identity_bot_id')
                and user.get('identity_platform_kind') not in (None,'','generic')):matches.append(key)
    if len(matches)!=1 or active!=matches:raise ReleaseError('unique_previously_authorized_target_required')
    key=matches[0];user=users[key];storage=str(user.get('user_id') or key)
    if ':' not in storage and storage!=admin:raise ReleaseError('native_storage_identity_mismatch')
    if (user.get('enabled') is False or user.get('proactive_private_enabled') is not True
            or user['unified_profile_capabilities'].get('proactive_private_enabled') is not True
            or type(user.get('proactive_daily_limit')) is not int or user['proactive_daily_limit'] not in expected
            or type(user.get('proactive_min_interval_minutes')) is not int or user['proactive_min_interval_minutes']!=-1):
        raise ReleaseError('existing_native_limit_or_inheritance_changed')
    return path,raw,data,key


def idle(dep):
    h.idle(dep)
    path=safe(dep/'state','data/plugin_data/phase3_runtime/proactive-controls.sqlite3')
    if not path.is_file():raise ReleaseError('proactive_ledger_required')
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        if (db.execute("SELECT count(*) FROM proactive_generations WHERE state='running'").fetchone()[0]
                or db.execute("SELECT count(*) FROM proactive_attempts WHERE state='pending'").fetchone()[0]):
            raise ReleaseError('active_proactive_retry_after_completion')


def load_journal(source,m):
    journal=safe(source,'release-journal');j=read(safe(journal,'journal.json'))
    if (j.get('schema')!=SCHEMA or j.get('database_restore_permitted') is not False
            or len(j.get('sources',[]))!=len(TARGETS) or {r['path'] for r in j['sources']}!=set(TARGETS)
            or len(j.get('configs',[]))!=2 or {r['path'] for r in j['configs']}!=set(CONFIG_TARGETS)):
        raise ReleaseError('narrow_release_journal_required')
    for row in j['sources']:
        rel=row['path']
        if row['old_sha256']!=m['baseline_source_files'].get(rel) or row['new_sha256']!=m['files'][TARGETS[rel]]:
            raise ReleaseError('source_journal_hash_changed')
        if row['old_sha256'] is not None and sha(safe(journal,'original/'+rel))!=row['old_sha256']:
            raise ReleaseError('source_journal_corrupt')
    for row in j['configs']:
        rel=row['path'];old=safe(journal,'original/'+rel)
        if sha(old)!=m['protected_config_files'][rel] or row['old_sha256']!=sha(old):raise ReleaseError('configuration_journal_corrupt')
        original=read(old);candidate=config_patch(original,'runtime' if rel==CONFIG_TARGETS[0] else 'private')
        expected=encode(candidate,old.read_bytes())
        if row['changes']!=delta(original,candidate) or row['new_sha256']!=hashlib.sha256(expected).hexdigest():
            raise ReleaseError('configuration_delta_changed')
    if j.get('native',{}).get('old_daily_limit')!=5 or j['native'].get('new_daily_limit')!=8:
        raise ReleaseError('native_journal_limit_changed')
    return journal,j


def validate(source,dep,installed=False,*,config_mode='baseline'):
    m=read(safe(source,'source-manifest.json'));files=m.get('files',{})
    if m.get('schema')!=SCHEMA or m.get('base_release')!=BASE:raise ReleaseError('wrong_baseline')
    if set(files)!=set(PINNED)|{CONTROLLER}|set(TARGETS.values()):raise ReleaseError('exact_seven_source_package_required')
    for rel,digest in files.items():
        path=safe(source,rel)
        if not path.is_file() or path.stat().st_size>1048576 or sha(path)!=digest:raise ReleaseError('package_hash_changed')
    if any(files[k]!=v for k,v in PINNED.items()) or files[CONTROLLER]!=sha(Path(__file__)):
        raise ReleaseError('locked_controller_changed')
    for rel in TARGETS.values():
        if rel.endswith('.py'):ast.parse((source/rel).read_text())
        else:read(source/rel)
    baseline=m.get('baseline_source_files',{});state=safe(dep,'state')
    if NEW_TARGET in baseline or not set(TARGETS)-{NEW_TARGET}<=set(baseline):raise ReleaseError('new_history_source_must_be_absent')
    expected=set(baseline)|({NEW_TARGET} if installed else set())
    if h.source_inventory(state)!=expected:raise ReleaseError('source_inventory_changed')
    for rel in expected:
        expected_hash=files[TARGETS[rel]] if installed and rel in TARGETS else baseline[rel]
        if sha(safe(state,rel))!=expected_hash:raise ReleaseError('source_baseline_changed')
    cfg=m.get('protected_config_files',{})
    observed={'data/cmd_config.json'}|{p.relative_to(state).as_posix() for p in safe(state,'data/config').rglob('*') if p.is_file()}
    if set(cfg)!=observed or not set(CONFIG_TARGETS)<=set(cfg):raise ReleaseError('configuration_inventory_changed')
    configs={}
    if config_mode!='baseline':
        _,j=load_journal(source,m);configs={r['path']:r for r in j['configs']}
    for rel,digest in cfg.items():
        path=safe(state,rel)
        if rel in configs:
            row=configs[rel]
            if config_mode=='installed':
                if sha(path)!=row['new_sha256']:raise ReleaseError('installed_configuration_changed')
            elif config_mode in ('current_candidate','restored'):
                side='new' if config_mode=='current_candidate' else 'old'
                document=read(path)
                if any(value_at(document,c['path'])!=(c[side+'_present'],c[side]) for c in row['changes']):
                    raise ReleaseError('owned_config_field_changed_since_release')
            else:raise ReleaseError('invalid_configuration_validation_mode')
        elif sha(path)!=digest:raise ReleaseError('protected_configuration_changed')
    if sha(safe(dep,'compose.yml'))!=m.get('compose_sha256') or sha(safe(dep,'.env'))!=m.get('env_sha256'):
        raise ReleaseError('compose_or_environment_changed')
    native_target(dep,expected=(8,) if installed else (5,))
    verify_native(source,dep)
    return m


def metadata(path):
    info=path.stat() if path.exists() else path.parent.stat()
    return {'uid':info.st_uid,'gid':info.st_gid,'mode':stat.S_IMODE(info.st_mode) if path.exists() else 0o600}


def prepare_journal(source,dep,m):
    journal=safe(source,'release-journal')
    if journal.exists():raise ReleaseError('new_journal_required')
    journal.mkdir(mode=0o700);rows=[];configs=[];state=dep/'state'
    def copy_original(rel):
        path=safe(state,rel);out=safe(journal,'original/'+rel)
        out.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        shutil.copy2(path,out);out.chmod(0o600)
    for rel,candidate in TARGETS.items():
        target=safe(state,rel);old=sha(target) if target.exists() else None
        if old!=m['baseline_source_files'].get(rel):raise ReleaseError('source_changed_before_journal')
        rows.append({'path':rel,'old_sha256':old,'new_sha256':m['files'][candidate],**metadata(target)})
        if old is not None:copy_original(rel)
    for rel in CONFIG_TARGETS:
        target=safe(state,rel);raw=target.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=m['protected_config_files'][rel]:raise ReleaseError('configuration_changed_before_journal')
        original=json.loads(raw.decode('utf-8-sig'));candidate=config_patch(original,'runtime' if rel==CONFIG_TARGETS[0] else 'private')
        encoded=encode(candidate,raw);copy_original(rel)
        output=safe(journal,'candidate/'+rel);output.parent.mkdir(parents=True,exist_ok=True,mode=0o700);output.write_bytes(encoded);output.chmod(0o600)
        configs.append({'path':rel,'old_sha256':sha(target),'new_sha256':sha(output),
                        'changes':delta(original,candidate),**metadata(target)})
    native_path,raw,document,key=native_target(dep,expected=(5,))
    changed=deepcopy(document);changed['users'][key]['proactive_daily_limit']=8
    native_output=safe(journal,'candidate/'+NATIVE_STORE);native_output.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    native_output.write_bytes(encode(changed,raw));native_output.chmod(0o600)
    native={'path':NATIVE_STORE,'key':key,'old_daily_limit':5,'new_daily_limit':8,
            'old_sha256':hashlib.sha256(raw).hexdigest(),'new_sha256':sha(native_output),**metadata(native_path)}
    private_json(journal/'journal.json',{'schema':SCHEMA,'sources':rows,'configs':configs,'native':native,
                                       'database_restore_permitted':False})
    load_journal(source,m)
    return journal


def publish_file(installer,source,target,row):
    installer.publish(source,target,row['uid'],row['gid']);target.chmod(row['mode'])


def validate_candidates(source,m):
    journal,j=load_journal(source,m)
    # Check both generated configuration files before publishing any source.
    # A corrupt candidate must never enter the live identity/configuration path.
    for row in j['configs']:
        if sha(safe(journal,'candidate/'+row['path']))!=row['new_sha256']:
            raise ReleaseError('configuration_candidate_corrupt')
    return journal,j


def install(source,dep,m,installer,services):
    journal,j=validate_candidates(source,m)
    for row in j['sources']:
        services.check(astr_running=False)
        publish_file(installer,source/TARGETS[row['path']],safe(dep/'state',row['path']),row)
    for row in j['configs']:
        services.check(astr_running=False)
        publish_file(installer,journal/'candidate'/row['path'],safe(dep/'state',row['path']),row)
    row=j['native'];path,raw,document,key=native_target(dep,expected=(5,))
    if key!=row['key'] or hashlib.sha256(raw).hexdigest()!=row['old_sha256']:
        raise ReleaseError('native_store_changed_before_publication')
    candidate=read(journal/'candidate'/NATIVE_STORE);expected=deepcopy(document);expected['users'][key]['proactive_daily_limit']=8
    if candidate!=expected or sha(journal/'candidate'/NATIVE_STORE)!=row['new_sha256']:
        raise ReleaseError('native_candidate_must_change_only_daily_limit')
    services.check(astr_running=False)
    publish_file(installer,journal/'candidate'/NATIVE_STORE,path,row)


def restore(source,dep,m,installer,services):
    services.check(astr_running=False,require_nap=False);journal,j=load_journal(source,m);state=dep/'state'
    # Validate all prospective writes before changing any; reject new drift.
    for row in j['sources']:
        target=safe(state,row['path']);digest=sha(target) if target.exists() else None
        if digest not in (row['old_sha256'],row['new_sha256']):raise ReleaseError('source_changed_since_release')
    configs=[]
    for row in j['configs']:
        path=safe(state,row['path']);raw=path.read_bytes();restored=restore_fields(json.loads(raw.decode('utf-8-sig')),row['changes'])
        original=journal/'original'/row['path']
        encoded=original.read_bytes() if restored==read(original) else encode(restored,raw)
        output=journal/'rollback-candidate'/row['path'];output.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        output.write_bytes(encoded);output.chmod(0o600);configs.append((output,path,row))
    path,raw,document,key=native_target(dep,expected=(5,8));row=j['native']
    if key!=row['key']:raise ReleaseError('native_target_changed_since_release')
    restored=deepcopy(document);restored['users'][key]['proactive_daily_limit']=5
    output=journal/'rollback-candidate'/NATIVE_STORE;output.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    output.write_bytes(encode(restored,raw));output.chmod(0o600)
    configs.append((output,path,metadata(path)))
    for output,path,info in configs:publish_file(installer,output,path,info)
    for row in reversed(j['sources']):
        target=safe(state,row['path'])
        if row['old_sha256'] is None:
            if target.exists():target.unlink()
        else:publish_file(installer,journal/'original'/row['path'],target,row)


def execute(args,services=None):
    safe(args.source);safe(args.deployment);source=args.source.resolve();dep=args.deployment.resolve();back=bool(args.rollback)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',args.run_id):raise ReleaseError('invalid_run_id')
    mode='current_candidate' if back else 'baseline'
    m=validate(source,dep,back,config_mode=mode);services=services or Services(dep);services.original=services.snapshot()
    was_running=services.original['astrbot']['running']
    if not services.original['napcat']['running'] or (not back and not was_running):raise ReleaseError('running_services_required')
    idle(dep)
    result={'status':'PLAN','operation':'rollback_owned_code_and_fields' if back else 'install_tuning_and_bubbles',
        'target_files':list(TARGETS),'config_files':list(CONFIG_TARGETS),'native_daily_limit':5 if back else 8,
        'napcat_restart':False,'whole_data_restore':False,'sqlite_write':False,'models_called':0,
        'new_chats_preserved':True,'counters_reset':False}
    if not args.apply:return result
    operation=safe(source,'operations/'+args.run_id+('-rollback.json' if back else '-install.json'))
    out=safe(dep,'backups/'+args.run_id)
    if operation.exists() or out.exists():raise ReleaseError('new_operation_required')
    os.umask(0o077);lock=os.open(dep/'.astrbot-runtime-release.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:os.close(lock);raise ReleaseError('another_release_running') from None
    result['status']='FAILED';touched=False
    try:
        backup=load('joint_release_backup',source/'scripts/backup.py')
        installer=load('joint_release_installer',source/'scripts/phase3-install-overlay.py')
        validate(source,dep,back,config_mode=mode);idle(dep);services.check(astr_running=was_running)
        if was_running:services.stop_astr()
        idle(dep);proof=h.snapshot(dep,out,services,backup)
        validate(source,dep,back,config_mode=mode)
        if back:
            touched=True;restore(source,dep,m,installer,services)
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
            validate(source,dep,back and not touched,config_mode=('restored' if touched else mode))
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
