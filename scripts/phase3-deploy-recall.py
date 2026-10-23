#!/usr/bin/env python3
"""Default-plan narrow runtime overlay, with fresh stopped backup and restore.

Does not import profiles, change native retrieval budgets, or alter databases.
The existing installer journals each file and preserves production ownership.
"""
import argparse,hashlib,importlib.util,json,os,shutil,sqlite3,sys
from pathlib import Path
from contextlib import closing

FILES=('main.py','recall.py','settings.py','_conf_schema.json')
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def module(name,p):
    spec=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(spec)
    sys.modules[name]=m;spec.loader.exec_module(m);return m


def validate(root,data):
    inventory=read(root/'source-manifest.json')
    expected={'integrations/phase3_runtime/'+f for f in FILES}|{'scripts/phase3-deploy-recall.py'}
    if set(inventory['files'])!=expected:raise ValueError('release_inventory_mismatch')
    if any(sha(root/'source'/name)!=value for name,value in inventory['files'].items()):
        raise ValueError('source_hash_mismatch')
    baseline=inventory['baseline']
    if set(baseline)!=set(FILES)-{'recall.py'}:raise ValueError('baseline_inventory_mismatch')
    if any(sha(data/'plugins/phase3_runtime'/name)!=value for name,value in baseline.items()):
        raise ValueError('runtime_changed_since_reviewed_baseline')
    if (data/'plugins/phase3_runtime/recall.py').exists():raise ValueError('recall_overlay_already_present')
    config=read(data/'config/phase3_runtime_config.json')
    if any(config.get(k) is not True for k in ('enabled','persona','natural_chat','memory')):
        raise ValueError('expected_active_phase3_memory')
    if config.get('memory_recall_guidance') is True:raise ValueError('recall_switch_already_enabled')
    return config


def run(args):
    os.umask(0o077)
    root=args.release.resolve();dep=args.deployment.resolve();data=dep/'state/data'
    validate(root,data)
    plan={'status':'PLAN','feature':'native_memory_recall_guidance_and_visible_topic_directory',
        'database_overwrite':False,'models_called':0,'proactive_permission_changed':False,
        'native_retrieval_and_summary_budgets_changed':False,'directory_timeout_seconds':1}
    if not args.apply:return plan
    common=args.common_scripts.resolve();sys.path.insert(0,str(common))
    backup=module('recall_backup',common/'phase2-backup.py')
    installer=module('recall_install',common/'phase3-install-overlay.py')
    services=backup.ComposeServices(dep);original=services.running()
    if original!={'astrbot','napcat'}:raise ValueError('both_original_services_required')
    with closing(sqlite3.connect((data/'plugin_data/phase1_guard/guard.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:
        if db.execute("SELECT count(*) FROM turns WHERE state IN ('collecting','ready','generating')").fetchone()[0]:
            raise ValueError('active_chat_wait_required')
    outcome={**plan,'status':'FAILED'}
    try:
        services.stop()
        proof=backup.exercise(dep,args.run_id,apply=True)
        (root/'backup-result.json').write_text(json.dumps(proof,indent=2))
        if proof.get('status')!='PASS':raise ValueError('fresh_backup_restore_failed')
        cfg=validate(root,data);cfg['memory_recall_guidance']=True
        candidate=root/'candidate';(candidate/'data/config').mkdir(mode=0o700,parents=True)
        (candidate/'data/config/phase3_runtime_config.json').write_text(json.dumps(cfg,ensure_ascii=False,indent=2))
        for name in FILES:
            target=candidate/'data/plugins/phase3_runtime'/name;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(root/'source/integrations/phase3_runtime'/name,target)
        shutil.copy2(data/'cmd_config.json',candidate/'data/cmd_config.json')
        (candidate/'review-manifest.json').write_text(json.dumps({'source_config_sha256':sha(data/'cmd_config.json')}))
        result=installer.install(candidate,dep,dep/'backups'/args.run_id,root/'installation-journal',apply=True)
        outcome={**plan,**result,'status':'INSTALLED_AWAITING_HEALTH','backup_status':proof['status'],
            'backup_run':args.run_id,'restore_status':proof['restore_integrity'],'sqlite_checked':proof['sqlite_integrity_checked_count']}
    except Exception as exc:
        outcome.update(status='FAILED',reason=str(exc) if isinstance(exc,ValueError) else type(exc).__name__)
    finally:
        services.start(original)
        outcome['original_services_resumed']=services.running()==original
        (root/'execution-result.json').write_text(json.dumps(outcome,indent=2))
    return outcome


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--release',type=Path,required=True)
    p.add_argument('--deployment',type=Path,default=Path('/opt/qq-ai-companion/deployment'))
    p.add_argument('--common-scripts',type=Path,default=Path('/opt/qq-ai-companion/releases/phase3-20261022-r1/source/scripts'))
    p.add_argument('--run-id',required=True);p.add_argument('--apply',action='store_true')
    try:r=run(p.parse_args())
    except Exception as exc:r={'status':'FAILED','reason':str(exc) if isinstance(exc,ValueError) else type(exc).__name__}
    print(json.dumps(r));raise SystemExit(r['status']=='FAILED')
