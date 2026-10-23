#!/usr/bin/env python3
"""Enable only the already-approved public Search/Fetch trial; dry-run default.

Reuse the server-local key without printing or exporting it. Fresh stopped
backup and isolated restore precede a narrow first-party/configuration overlay.
No personal-profile import, proactive enablement, image/port/SSH change.
"""
import argparse
from datetime import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

FILES=('runtime.py','budget.py')

def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec)
    sys.modules[name]=m;spec.loader.exec_module(m);return m

def run(args):
    os.umask(0o077)
    source=args.source.resolve();dep=args.deployment.resolve();data=dep/'state/data'
    prior=Path('/opt/qq-ai-companion/releases/phase3-20261022-r1/source')
    sys.path[:0]=[str(prior),str(prior/'scripts')]
    manifest=read(source/'source-manifest.json')
    names={'integrations/phase3_search/'+f for f in FILES}|{'scripts/phase3-deploy-search.py'}
    if set(manifest['files'])!=names:raise ValueError('unexpected_release_inventory')
    for name,digest in manifest['files'].items():
        if sha(source/name)!=digest:raise ValueError('release_hash_mismatch')
    runtime=read(data/'config/phase3_runtime_config.json')
    if not all(runtime.get(k) is True for k in ('enabled','persona','natural_chat','memory')):
        raise ValueError('passive_release_required')
    if any(runtime.get(k) for k in ('search','proactive','background_web')):
        raise ValueError('expected_passive_only')
    original=read(prior.parent/'installation-journal/manifest.json')
    hashes={r['path']:r['new_sha256'] for r in original}
    for f in FILES:
        rel='data/plugins/phase3_search/'+f
        if sha(dep/'state'/rel)!=hashes.get(rel):raise ValueError('unexpected_search_source_change')
    plan={'status':'PLAN','search':True,'proactive':False,'background_web':False,
          'public_topics':['DeepSeek','AstrBot','炉石传说'],'per_turn_attempts':3,'daily_attempts':60,
          'separate_search_config':True,'chat_provider_changed':False}
    if not args.apply:return plan
    backup=module('search_release_backup',prior/'scripts/phase2-backup.py')
    result=backup.exercise(dep,args.run_id,apply=True)
    (source/'backup-result.json').write_text(json.dumps(result,indent=2))
    if result.get('status')!='PASS':raise ValueError('fresh_backup_restore_failed')
    prefix=['docker','compose','--project-directory',str(dep),'-f',str(dep/'compose.yml')]
    def command(parts):subprocess.run(prefix+parts,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60)
    command(['stop','--timeout','30','astrbot'])
    try:
        base=read(data/'cmd_config.json');identity=read(data/'config/phase2_policy_config.json')
        settings=read(data/'config/phase3_runtime_config.json');settings['search']=True
        staging=module('search_release_staging',prior/'scripts/phase3-stage.py')
        key=staging.helpers.read_private(dep/'state/secrets/deepseek-key').strip()
        config=staging.candidate(base,settings=settings,admin_id=identity['admin_id'],platform_id=identity['platform_id'],search_key=key)
        candidate=source/'candidate';(candidate/'data/config').mkdir(mode=0o700,parents=True)
        for name,value in [('data/cmd_config.json',config),('data/config/phase3_runtime_config.json',settings),
            ('data/config/phase3_search_config.json',{'enabled':True,'admin_id':identity['admin_id'],
                'platform_id':identity['platform_id'],'daily_attempt_limit':60,
                'public_terms':plan['public_topics'],'fetch_hosts':['api-docs.deepseek.com','docs.astrbot.app','hearthstone.blizzard.com'],
                'limits':{'max_calls':3,'progress_timeout':4,'tool_timeout':20,'turn_timeout':60,'max_result_chars':10000}})]:
            (candidate/name).write_text(json.dumps(value,ensure_ascii=False,indent=2));(candidate/name).chmod(0o600)
        for f in FILES:
            dest=candidate/'data/plugins/phase3_search'/f;dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source/'integrations/phase3_search'/f,dest)
        (candidate/'review-manifest.json').write_text(json.dumps({'source_config_sha256':sha(data/'cmd_config.json')}))
        # Count the four earlier authorized public probes exactly once even
        # though they ran outside the production Agent. Never reset counters.
        budget_module=module('search_release_budget',source/'integrations/phase3_search/budget.py')
        budget_dir=data/'plugin_data/phase3_search';budget_dir.mkdir(mode=0o700,exist_ok=True)
        budget_path=budget_dir/'budget.sqlite3';budget=budget_module.DailyBudget(budget_path,limit=60)
        try:adopted=budget.adopt('approved-protocol-probe-20261022','2026-10-22',4)
        finally:budget.close()
        os.chown(budget_dir,10001,10001);os.chown(budget_path,10001,10001)
        install=module('search_release_installer',prior/'scripts/phase3-install-overlay.py')
        installed=install.install(candidate,dep,dep/'backups'/args.run_id,source/'installation-journal',apply=True)
        outcome={**plan,**installed,'status':'INSTALLED_AWAITING_HEALTH_AND_QQ','prior_probe_attempts':4,
                 'prior_probe_count_newly_adopted':adopted,'backup_status':result['status'],'restore_status':result['restore_integrity']}
        (source/'installed.json').write_text(json.dumps(outcome,indent=2))
    finally:command(['start','astrbot'])
    return outcome

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',required=True,type=Path)
    p.add_argument('--deployment',type=Path,default=Path('/opt/qq-ai-companion/deployment'))
    p.add_argument('--run-id',required=True);p.add_argument('--apply',action='store_true')
    try:result=run(p.parse_args())
    except Exception as exc:result={'status':'FAILED','reason':str(exc) if isinstance(exc,ValueError) else type(exc).__name__}
    print(json.dumps(result));raise SystemExit(result['status']=='FAILED')
