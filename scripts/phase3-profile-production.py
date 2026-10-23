#!/usr/bin/env python3
"""Production profile controller: dry-run by default; no service stop/start.

Runs the pinned AstrBot image as a networkless API worker with only an offline
candidate mounted writable. Installs only Memory's DB and import-reference ledger.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
from integrations.phase3_memory.profile_release import ProductionWindow,MemoryDenied,validate_approval,_module

IMAGE='sha256:a0bf74942fc2ba504932897f7f6ba8d8bd01c1084ea6448c399b8df579bc77af'
NATIVE_HASH='436147732fce123ddf1a61efb5add56e2f95867b19f319b0d23d82534db7aaa6'
WORKER=_module('profile_worker_cli',ROOT/'scripts/phase3-profile-import.py')


def write_private(path,value):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())


def sql_integrity(path):
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise MemoryDenied('candidate_sqlite_integrity_failed')


def normalized_database(path):
    """Checkpoint the candidate only, keeping every original row and source."""
    dest=path.with_name(path.name+'.prepared')
    with closing(sqlite3.connect(path)) as source,closing(sqlite3.connect(dest)) as target:
        source.backup(target)
        target.execute('PRAGMA journal_mode=DELETE')
    dest.chmod(0o600);sql_integrity(dest);os.replace(dest,path)
    for base in (path,dest):
        for suffix in ('-wal','-shm','-journal'):Path(str(base)+suffix).unlink(missing_ok=True)


def worker_command(candidate,native_source):
    return ['docker','run','--rm','--pull','never','--network','none','--read-only','--cap-drop','ALL',
        '--security-opt','no-new-privileges','--user',f'{os.geteuid()}:{os.getegid()}',
        '--pids-limit','64','--memory','768m','--cpus','1',
        '--tmpfs','/tmp:rw,nosuid,nodev,size=128m',
        '--mount',f'type=bind,src={ROOT/"scripts"},dst=/repo/scripts,readonly',
        '--mount',f'type=bind,src={ROOT/"integrations"/"phase3_memory"},dst=/repo/integrations/phase3_memory,readonly',
        '--mount',f'type=bind,src={native_source},dst=/native,readonly',
        '--mount',f'type=bind,src={candidate},dst=/candidate',
        '--env','PYTHONPATH=/AstrBot:/repo:/repo/scripts','--env','ASTRBOT_ROOT=/tmp/astrbot',
        '--env','ASTRBOT_DISABLE_METRICS=1','--env','PYTHONDONTWRITEBYTECODE=1',
        '--entrypoint','python',IMAGE,'/repo/scripts/phase3-profile-import.py',
        '--environment','production','--manifest','/candidate/manifest.json',
        '--approval','/candidate/approval.json','--native-source','/native',
        '--native-data','/candidate/native','--ledger','/candidate/ledger.sqlite3',
        '--candidate-root','/candidate','--apply']


def install_files(window,native_db,ledger,candidate,journal,*,replace_file=os.replace):
    """Journal each exact destination and restore it on any installation failure."""
    window.verify(native_db)
    changes={native_db:candidate/'native'/'memory_companion.db',ledger:candidate/'ledger.sqlite3'}
    for base in (native_db,ledger):
        for suffix in ('-wal','-shm','-journal'):changes[Path(str(base)+suffix)]=None
    journal.mkdir(mode=0o700,exist_ok=False)
    records=[]
    native_owner=native_db.stat()
    for index,(target,source) in enumerate(changes.items()):
        if target.is_symlink() or target.parent.resolve()!=target.parent.absolute():raise MemoryDenied('unsafe_install_destination')
        old=journal/f'{index}.before'
        if target.exists():
            if not target.is_file() or target.stat().st_nlink!=1:raise MemoryDenied('unsafe_install_destination')
            shutil.copy2(target,old);old.chmod(0o600)
        owner=target.stat() if target.exists() else native_owner
        records.append({'target':str(target.relative_to(window.deployment/'state')),
            'uid':owner.st_uid,'gid':owner.st_gid,
            'old':old.name if old.exists() else None,'source':str(source) if source else None,
            'old_sha256':hashlib.sha256(old.read_bytes()).hexdigest() if old.exists() else None,
            'new_sha256':hashlib.sha256(source.read_bytes()).hexdigest() if source else None})
    receipt={'state':'PREPARED','destinations':records,'all_other_state_untouched':True}
    write_private(journal/'journal.json',receipt)
    try:
        for row in records:
            window.assert_stopped()
            target=window.deployment/'state'/row['target']
            new_parent=not target.parent.exists()
            target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            if new_parent:os.chown(target.parent,row['uid'],row['gid'])
            if row['source']:
                tmp=target.with_name(target.name+'.profile-pending')
                if tmp.exists():raise MemoryDenied('unfinished_profile_install_requires_review')
                shutil.copy2(row['source'],tmp);tmp.chmod(0o600)
                os.chown(tmp,row['uid'],row['gid'])
                replace_file(tmp,target)
            else:target.unlink(missing_ok=True)
            row['installed']=True;write_private(journal/'journal.json',receipt)
        sql_integrity(native_db);sql_integrity(ledger);window.assert_stopped()
        receipt['state']='APPLIED';write_private(journal/'journal.json',receipt)
    except BaseException:
        # Never clear other state or touch SSH/QQ/secret/config files.
        for row in reversed(records):
            target=window.deployment/'state'/row['target']
            if row['old']:
                tmp=target.with_name(target.name+'.profile-restore')
                shutil.copy2(journal/row['old'],tmp);os.chown(tmp,row['uid'],row['gid']);os.replace(tmp,target)
            else:target.unlink(missing_ok=True)
            target.with_name(target.name+'.profile-pending').unlink(missing_ok=True)
        receipt['state']='ROLLED_BACK';write_private(journal/'journal.json',receipt)
        raise
    return {'state':'APPLIED','installed_scope':'native_memory_database_and_profile_reference_ledger',
            'journal':str(journal/'journal.json'),'other_state_changed':False}


def execute(args,*,run_worker=None):
    manifest,approval=WORKER.private_json(args.manifest),WORKER.private_json(args.approval)
    plan=validate_approval(manifest,approval,'production')
    if args.withdraw and approval.get('withdraw_batch') is not True:raise MemoryDenied('explicit_batch_withdrawal_required')
    if not args.apply:return {**plan,'state':'DRY_RUN','production_writes':0,'model_calls':0}
    if approval.get('runtime_image_id')!=IMAGE:raise MemoryDenied('exact_runtime_image_approval_required')
    deployment=args.deployment.resolve();state=deployment/'state'
    native=args.native_data.resolve();ledger=args.ledger.resolve();source=args.native_source.resolve()
    if not native.is_relative_to(state/'data'/'plugin_data') or not ledger.is_relative_to(state/'data'/'plugin_data'/'phase3_profile_release'):
        raise MemoryDenied('narrow_production_paths_required')
    proof=approval.get('backup',{})
    window=ProductionWindow(deployment,proof.get('run_id',''),proof.get('astrbot_archive_sha256',''))
    window.verify(native/'memory_companion.db')
    ids=window.runner(['docker','compose','--project-directory',str(deployment),'-f',str(deployment/'compose.yml'),'ps','--all','--quiet','astrbot']).split()
    if len(ids)!=1:raise MemoryDenied('existing_astrbot_image_required')
    running=json.loads(window.runner(['docker','inspect',ids[0]]))[0]
    if running.get('Image')!=IMAGE:raise MemoryDenied('installed_runtime_image_mismatch')
    prepare=_module('profile_prepare_sources',ROOT/'scripts/prepare_plugins.py')
    if prepare.source_tree_sha256(source)!=NATIVE_HASH:raise MemoryDenied('pinned_native_source_required')
    run=deployment/'backups'/window.run_id
    candidate=Path(tempfile.mkdtemp(prefix='.profile-candidate-',dir=run))
    backup=_module('profile_candidate_backup',ROOT/'scripts/backup.py')
    restored=candidate/'restored-state';backup.restore_backup(run/'astrbot-state.zip',restored)
    shutil.copytree(restored/native.relative_to(state),candidate/'native')
    old_ledger=restored/ledger.relative_to(state)
    if old_ledger.exists():shutil.copy2(old_ledger,candidate/'ledger.sqlite3')
    # The worker receives ONLY the native subset and requested profile. Do not
    # mount the full restored state, its credentials or other users' archives.
    shutil.rmtree(restored)
    write_private(candidate/'manifest.json',manifest);write_private(candidate/'approval.json',approval)
    write_private(candidate/'candidate.json',{'schema':'qq-companion.offline-profile-candidate.v1',
        'manifest_hash':plan['manifest_hash'],'backup':approval.get('backup'),'operation':'withdraw' if args.withdraw else 'apply'})
    before,_=backup._tree(candidate/'native')
    usage_path=candidate/'native'/'memory_companion_token_usage.json'
    usage_before=usage_path.read_bytes() if usage_path.exists() else None
    usage_mode=usage_path.stat().st_mode&0o777 if usage_path.exists() else None
    cmd=worker_command(candidate,source)
    if args.withdraw:cmd.append('--withdraw')
    if run_worker:result=run_worker(cmd,candidate)
    else:
        proc=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=180)
        # Native import should not log content. Drop all worker logs regardless;
        # only parse the bounded final receipt into the user-visible result.
        try:result=json.loads(proc.stdout.strip().splitlines()[-1])
        except (ValueError,IndexError):raise MemoryDenied('offline_worker_receipt_unavailable')
        if proc.returncode:raise MemoryDenied('offline_worker_failed')
    if result.get('state')!='CANDIDATE_PREPARED' or result.get('manifest_hash')!=plan['manifest_hash']:
        raise MemoryDenied('exact_candidate_receipt_required')
    normalized_database(candidate/'native'/'memory_companion.db');normalized_database(candidate/'ledger.sqlite3')
    # Pinned aclose() adds only its usage schema version even with zero model
    # calls. Retain the exact original accounting file instead of installing a
    # fabricated reset or broadening this release to unrelated native files.
    if usage_path.exists():
        previous=json.loads(usage_before) if usage_before is not None else {}
        current=json.loads(usage_path.read_bytes())
        if current=={**previous,'token_usage_schema_version':2}:
            if usage_before is None:usage_path.unlink()
            else:
                usage_path.write_bytes(usage_before)
                # The offline worker uses umask 077 while the pinned native
                # writer may have originally used 0644 inside its private dir.
                # This unrelated accounting file is never installed or reset.
                usage_path.chmod(usage_mode)
    after,_=backup._tree(candidate/'native')
    permitted={'memory_companion.db','memory_companion.db-wal','memory_companion.db-shm','memory_companion.db-journal'}
    if {k:v for k,v in before.items() if k not in permitted}!={k:v for k,v in after.items() if k not in permitted}:
        # Keep candidate-only diagnostics private; the CLI still emits a fixed
        # reason code and never publishes filenames or contents.
        write_private(candidate/'side-effect-audit.json',{'changed':[
            {'file':k,'before':before.get(k),'after':after.get(k)} for k in sorted(set(before)|set(after))
            if k not in permitted and before.get(k)!=after.get(k)]})
        raise MemoryDenied('unexpected_native_candidate_side_effect')
    output=install_files(window,native/'memory_companion.db',ledger,candidate,candidate/'install-journal')
    return {**plan,**output,'operation':'withdraw' if args.withdraw else 'apply','model_calls':0,
        'services_restarted':False,'fresh_chat_context_required_before_resume':bool(args.withdraw)}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('manifest','approval','native-data','native-source','ledger'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--deployment',type=Path,default=Path('/opt/qq-ai-companion/deployment'))
    p.add_argument('--apply',action='store_true');p.add_argument('--withdraw',action='store_true');args=p.parse_args()
    os.umask(0o077)
    try:result=execute(args)
    except Exception as e:result={'state':'BLOCKED','reason':str(e) if isinstance(e,MemoryDenied) else type(e).__name__,'raw_content_logged':False}
    print(json.dumps(result,ensure_ascii=False));return int(result['state']=='BLOCKED')


if __name__=='__main__':raise SystemExit(main())
