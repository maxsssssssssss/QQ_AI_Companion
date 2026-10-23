#!/usr/bin/env python3
"""Install a staged first-party overlay while AstrBot is stopped.

Default is a read-only plan. Never replaces the data directory, starts QQ,
copies databases, removes old chat records, or changes Compose/network policy.
Every replaced file is journaled privately before any mutation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile

PLUGINS = {'phase1_guard','phase2_policy','phase3_runtime','phase3_memory','phase3_search'}
CONFIGS = {'phase1_guard','phase2_policy','phase3_runtime','phase3_search',
           'astrbot_plugin_private_companion','astrbot_plugin_memory_companion'}


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()


def no_links(path):
    if any(p.is_symlink() for p in (path,*path.parents)):
        raise ValueError('symlink_path_rejected')


def inventory(candidate):
    no_links(candidate)
    selected = []
    for path in sorted((candidate/'data').rglob('*')):
        no_links(path)
        if path.is_dir(): continue
        rel = path.relative_to(candidate)
        parts = rel.parts
        allowed = (rel.as_posix() == 'data/cmd_config.json' or
            (len(parts)==3 and parts[:2]==('data','config') and path.name in {x+'_config.json' for x in CONFIGS}) or
            (len(parts)>=4 and parts[:2]==('data','plugins') and parts[2] in PLUGINS and path.suffix in {'.py','.json','.yaml','.yml','.md'}) or
            rel.as_posix() in {'data/phase3/persona-v0.1.md','data/phase3/memory-backlog-approval.json'})
        if not allowed or not stat.S_ISREG(path.stat().st_mode):
            raise ValueError('unexpected_candidate_file')
        if path.stat().st_size>1024*1024: raise ValueError('oversized_candidate_file')
        selected.append(rel)
    if Path('data/cmd_config.json') not in selected: raise ValueError('missing_candidate_config')
    return selected


def verify_stopped(deployment):
    prefix=['docker','compose','--project-directory',str(deployment),'-f',str(deployment/'compose.yml')]
    result=subprocess.run(prefix+['ps','--all','--quiet','astrbot'],capture_output=True,text=True,check=True,timeout=15)
    ids=result.stdout.split()
    if len(ids)!=1: raise ValueError('one_existing_astrbot_required')
    template='{"running":{{json .State.Running}},"status":{{json .State.Status}},"project":{{json (index .Config.Labels "com.docker.compose.project")}},"mounts":{{json .Mounts}}}'
    data=json.loads(subprocess.check_output(['docker','inspect','--format',template,ids[0]],text=True,timeout=15))
    if data['running'] or data['status']!='exited' or data['project']!='qq-ai-companion':
        raise ValueError('astrbot_must_be_stopped')
    if not any(m.get('Source')==str(deployment/'state/data') and m.get('Destination')=='/AstrBot/data' for m in data['mounts']):
        raise ValueError('production_mount_mismatch')


def verify_backup(backup):
    no_links(backup)
    result=json.loads((backup/'result.json').read_text())
    if result.get('status')!='PASS' or result.get('backup_integrity')!='PASS' or result.get('restore_integrity')!='PASS' or result.get('restored_copy_started') is not False:
        raise ValueError('verified_isolated_restore_required')
    for file,key in [('astrbot-state.zip','astrbot_archive_sha256'),('napcat-opaque.zip','opaque_archive_sha256')]:
        if sha(backup/file)!=result.get(key): raise ValueError('backup_digest_mismatch')


def publish(source, destination, uid, gid):
    no_links(destination)
    missing=[];parent=destination.parent
    while not parent.exists(): missing.append(parent);parent=parent.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        os.chown(directory,uid,gid)
    fd,name=tempfile.mkstemp(prefix='.phase3-overlay-',dir=destination.parent)
    try:
        with os.fdopen(fd,'wb') as output, source.open('rb') as incoming:
            shutil.copyfileobj(incoming,output);output.flush();os.fsync(output.fileno())
            os.fchmod(output.fileno(),0o600);os.fchown(output.fileno(),uid,gid)
        os.replace(name,destination)
    finally:
        if os.path.exists(name):os.unlink(name)


def install(candidate, deployment, backup, journal, *, apply=False, check_stopped=verify_stopped, owner=(10001,10001)):
    for p in (candidate,deployment,backup,journal):no_links(p)
    files=inventory(candidate)
    state=deployment/'state'
    planned={'status':'PLAN','file_count':len(files),'databases_touched':False,'services_started':False}
    if not apply:return planned
    if journal.exists(): raise ValueError('new_journal_required')
    verify_backup(backup);check_stopped(deployment)
    manifest=json.loads((candidate/'review-manifest.json').read_text())
    if sha(state/'data/cmd_config.json')!=manifest['source_config_sha256']:
        raise ValueError('config_changed_since_staging')
    journal.mkdir(mode=0o700,parents=True)
    records=[]
    for rel in files:
        target=state/rel;no_links(target)
        record={'path':rel.as_posix(),'new_sha256':sha(candidate/rel),'existed':target.exists()}
        if target.exists():
            if not target.is_file():raise ValueError('nonregular_destination')
            old=journal/'original'/rel;old.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            shutil.copy2(target,old);old.chmod(0o600)
            record.update(old_sha256=sha(old),uid=target.stat().st_uid,gid=target.stat().st_gid,mode=stat.S_IMODE(target.stat().st_mode))
        records.append(record)
    (journal/'manifest.json').write_text(json.dumps(records,indent=2))
    completed=[]
    try:
        for record in records:
            rel=Path(record['path']);publish(candidate/rel,state/rel,*owner);completed.append(record)
        for record in records:
            if sha(state/record['path'])!=record['new_sha256']:raise ValueError('installed_digest_mismatch')
    except BaseException:
        for record in reversed(completed):
            target=state/record['path']
            if record['existed']:
                publish(journal/'original'/record['path'],target,record['uid'],record['gid']);target.chmod(record['mode'])
            else:target.unlink()
        raise
    result={**planned,'status':'INSTALLED_SERVICES_STOPPED','files':[{k:r[k] for k in ('path','new_sha256')} for r in records]}
    (journal/'result.json').write_text(json.dumps(result,indent=2))
    return {k:v for k,v in result.items() if k!='files'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('candidate','deployment','backup','journal'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--apply',action='store_true');args=parser.parse_args();os.umask(0o077)
    try:print(json.dumps(install(**vars(args))))
    except Exception as exc:
        print(json.dumps({'status':'FAILED','error_type':type(exc).__name__,'reason':str(exc) if isinstance(exc,ValueError) else 'operation_failed'}));raise SystemExit(1)
