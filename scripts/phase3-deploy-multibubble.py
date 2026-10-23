#!/usr/bin/env python3
"""Narrow approved R1 -> multibubble overlay; default dry run.

Run on the VPS from an explicit first-party source bundle. Preserves fresh
production state with the existing stopped backup/isolated restore procedure.
Never writes conversation databases, copies a whole data directory, enables
Search/proactive or changes images, ports, SSH or QQ identities.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

FILES=('phase1_guard/main.py','phase1_guard/store.py','phase1_guard/delivery.py',
       'phase1_guard/bubbles.py','phase1_guard/_conf_schema.json','phase3_runtime/proactive.py')
SCRIPTS=('phase3-install-overlay.py','phase3-deploy-multibubble.py','phase3-qq-audit.py','phase3-status.py','phase2-status.py')

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def read_json(p):return json.loads(p.read_text(encoding='utf-8-sig'))

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value);return value

def run(args):
    root=args.source.resolve();deployment=args.deployment.resolve()
    prior=Path('/opt/qq-ai-companion/releases/phase3-20261022-r1')
    for p in [root,deployment,prior]:
        if p.is_symlink() or not p.is_dir():raise ValueError('expected_existing_directories')
    proof=read_json(root/'source-manifest.json')
    expected={'integrations/'+f for f in FILES}|{'scripts/'+f for f in SCRIPTS}
    if set(proof['files'])!=expected:raise ValueError('release_inventory_mismatch')
    for name,digest in proof['files'].items():
        if sha(root/name)!=digest:raise ValueError('source_hash_mismatch')
    if any('integrations/'+f not in proof['files'] for f in FILES):raise ValueError('missing_release_file')
    current=deployment/'state/data'
    settings=read_json(current/'config/phase3_runtime_config.json')
    if settings.get('search') or settings.get('proactive') or settings.get('background_web'):
        raise ValueError('passive_r1_scope_required')
    old=read_json(prior/'installation-journal/manifest.json')
    old_hashes={r['path']:r['new_sha256'] for r in old}
    for name in FILES:
        dest=current/'plugins'/name
        key='data/plugins/'+name
        if dest.exists() and (key not in old_hashes or sha(dest)!=old_hashes[key]):
            raise ValueError('production_source_changed_since_r1')
    plan={'status':'PLAN','source_files':len(FILES),'features_added':['multibubble'],
          'search_enabled':False,'proactive_enabled':False,'images_changed':False,'whole_data_overwrite':False}
    if not args.apply:return plan
    os.umask(0o077)
    sys.path.insert(0,str(prior/'source/scripts'))
    backup=module('approved_backup',prior/'source/scripts/phase2-backup.py')
    install=module('approved_installer',root/'scripts/phase3-install-overlay.py')
    backup_run=args.verified_backup_run or args.run_id
    if args.verified_backup_run:
        if not __import__('re').fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',backup_run):raise ValueError('invalid_backup_run')
        install.verify_backup(deployment/'backups'/backup_run)
        result=read_json(deployment/'backups'/backup_run/'result.json')
    else:
        result=backup.exercise(deployment,args.run_id,apply=True)
    (root/'backup-result.json').write_text(json.dumps(result,indent=2))
    if result.get('status')!='PASS':raise ValueError('fresh_backup_restore_failed')
    prefix=['docker','compose','--project-directory',str(deployment),'-f',str(deployment/'compose.yml')]
    def command(parts):subprocess.run(prefix+parts,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60)
    command(['stop','--timeout','30','astrbot'])
    try:
        candidate=root/'candidate';candidate.mkdir(mode=0o700)
        (candidate/'data/config').mkdir(parents=True)
        shutil.copy2(current/'cmd_config.json',candidate/'data/cmd_config.json')
        guard=read_json(current/'config/phase1_guard_config.json')
        guard.update(phase3_multibubble_enabled=True,phase3_bubble_min_interval=.6,phase3_bubble_max_interval=1.8)
        (candidate/'data/config/phase1_guard_config.json').write_text(json.dumps(guard,indent=2))
        for name in FILES:
            dest=candidate/'data/plugins'/name;dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(root/'integrations'/name,dest)
        (candidate/'review-manifest.json').write_text(json.dumps({'source_config_sha256':sha(current/'cmd_config.json')}))
        installed=install.install(candidate,deployment,deployment/'backups'/backup_run,root/'installation-journal',apply=True)
        outcome={**plan,**installed,'backup_status':result['status'],'restore_status':result['restore_integrity'],
            'backup_run':backup_run,'release_source_sha256':sha(root/'source-manifest.json')}
        (root/'installed.json').write_text(json.dumps(outcome,indent=2))
    finally:command(['start','astrbot'])
    return {**outcome,'status':'INSTALLED_AWAITING_HEALTH_AND_QQ'}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True)
    p.add_argument('--deployment',type=Path,default=Path('/opt/qq-ai-companion/deployment'))
    p.add_argument('--run-id',required=True);p.add_argument('--apply',action='store_true')
    p.add_argument('--verified-backup-run',help='Reuse an explicitly named, hash-verified stopped backup from this release attempt.')
    try:result=run(p.parse_args())
    except Exception as exc:result={'status':'FAILED','reason':str(exc) if isinstance(exc,ValueError) else type(exc).__name__}
    print(json.dumps(result));raise SystemExit(result['status']=='FAILED')
