#!/usr/bin/env python3
"""Narrow, default-plan formatting overlay following the multibubble release.

Preserves existing search/persona/memory settings and every database. Fresh
stopped backup + isolated restore precede three Guard files and one option.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

FILES=('main.py','bubbles.py','_conf_schema.json')
def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec)
    sys.modules[name]=value;spec.loader.exec_module(value);return value

def run(args):
    os.umask(0o077)
    source=args.source.resolve();dep=args.deployment.resolve();data=dep/'state/data'
    baseline=args.prior_source.resolve()
    common=Path('/opt/qq-ai-companion/releases/phase3-20261022-r1/source/scripts')
    sys.path.insert(0,str(common))
    inventory=read(source/'source-manifest.json')['files']
    names={'integrations/phase1_guard/'+f for f in FILES}|{'scripts/phase3-deploy-plain-text.py'}
    if set(inventory)!=names:raise ValueError('release_inventory_mismatch')
    if any(sha(source/name)!=digest for name,digest in inventory.items()):raise ValueError('source_hash_mismatch')
    installed=read(baseline/'installation-journal/manifest.json')
    old={row['path']:row['new_sha256'] for row in installed}
    for name in FILES:
        if sha(data/'plugins/phase1_guard'/name)!=old.get('data/plugins/phase1_guard/'+name):
            raise ValueError('guard_changed_since_reviewed_baseline')
    guard=read(data/'config/phase1_guard_config.json')
    if guard.get('phase3_enabled') is not True or guard.get('phase3_plain_text_enabled') is True:
        raise ValueError('expected_existing_unformatted_phase3')
    plan={'status':'PLAN','feature':'bounded_plain_qq_text','source_files':3,
          'database_writes':False,'other_runtime_settings_changed':False,
          'models_called':0,'proactive_permission_changed':False}
    if not args.apply:return plan
    with sqlite3.connect((data/'plugin_data/phase1_guard/guard.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        if db.execute("SELECT count(*) FROM turns WHERE state IN ('collecting','ready','generating')").fetchone()[0]:
            raise ValueError('active_chat_wait_required')
    backup=module('plain_backup',common/'phase2-backup.py')
    proof=backup.exercise(dep,args.run_id,apply=True)
    (source/'backup-result.json').write_text(json.dumps(proof,indent=2))
    if proof.get('status')!='PASS':raise ValueError('fresh_backup_restore_failed')
    prefix=['docker','compose','--project-directory',str(dep),'-f',str(dep/'compose.yml')]
    def command(parts):subprocess.run(prefix+parts,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60)
    command(['stop','--timeout','30','astrbot'])
    try:
        candidate=source/'candidate';(candidate/'data/config').mkdir(mode=0o700,parents=True)
        shutil.copy2(data/'cmd_config.json',candidate/'data/cmd_config.json')
        guard=read(data/'config/phase1_guard_config.json');guard['phase3_plain_text_enabled']=True
        (candidate/'data/config/phase1_guard_config.json').write_text(json.dumps(guard,indent=2))
        for name in FILES:
            target=candidate/'data/plugins/phase1_guard'/name;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source/'integrations/phase1_guard'/name,target)
        (candidate/'review-manifest.json').write_text(json.dumps({'source_config_sha256':sha(data/'cmd_config.json')}))
        installer=module('plain_installer',common/'phase3-install-overlay.py')
        result=installer.install(candidate,dep,dep/'backups'/args.run_id,source/'installation-journal',apply=True)
        outcome={**plan,**result,'status':'INSTALLED_AWAITING_HEALTH','backup_status':proof['status'],
            'backup_run':args.run_id,'restore_status':proof['restore_integrity']}
        (source/'installed.json').write_text(json.dumps(outcome,indent=2))
    finally:command(['start','astrbot'])
    return outcome

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True);p.add_argument('--prior-source',type=Path,required=True)
    p.add_argument('--deployment',type=Path,default=Path('/opt/qq-ai-companion/deployment'))
    p.add_argument('--run-id',required=True);p.add_argument('--apply',action='store_true')
    try:result=run(p.parse_args())
    except Exception as exc:result={'status':'FAILED','reason':str(exc) if isinstance(exc,ValueError) else type(exc).__name__}
    print(json.dumps(result));raise SystemExit(result['status']=='FAILED')
