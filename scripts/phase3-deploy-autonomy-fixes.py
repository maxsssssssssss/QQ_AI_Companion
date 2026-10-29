#!/usr/bin/env python3
"""PLAN by default: thirteen autonomy repair sources, zero configuration writes.

Reuse the locked AstrBot-only backup/isolated-restore transaction. Rollback
touches owned Python files only; new chat, memory, counters and additive SQLite
columns remain in place. The configuration journal contains an empty delta and
byte-identical proof, never a candidate configuration change.
"""
import argparse
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

HELPER='scripts/phase3-deploy-autonomy.py'
HELPER_SHA='44186b21400e90c1451dd79642c12cff2f7d9b3e459d812d581766fda4d13d2c'
path=Path(__file__).with_name(Path(HELPER).name)
if hashlib.sha256(path.read_bytes()).hexdigest()!=HELPER_SHA:
    raise RuntimeError('locked_autonomy_release_helper_required')
spec=importlib.util.spec_from_file_location('autonomy_fixes_release_engine',path)
engine=importlib.util.module_from_spec(spec);sys.modules[spec.name]=engine;spec.loader.exec_module(engine)

SCHEMA='qq-companion.autonomy-fixes-release.v1'
BASE='phase3-20261028-bubble-pacing-r1'
CONTROLLER='scripts/phase3-deploy-autonomy-fixes.py'
PINNED={**engine.PINNED,HELPER:HELPER_SHA}
TARGETS={
    **{'data/plugins/phase3_runtime/'+name:'integrations/phase3_runtime/'+name for name in (
        'autonomy.py','autonomy_contract.py','autonomy_request.py','autonomy_state.py',
        'autonomy_native.py','autonomy_supplement.py','autonomy_content.py','autonomy_arithmetic.py','proactive.py')},
    **{'data/plugins/phase3_memory/'+name:'integrations/phase3_memory/'+name for name in (
        'self_preferences.py','summary_provider.py')},
    **{'data/plugins/phase3_search/'+name:'integrations/phase3_search/'+name for name in (
        'proactive.py','policy.py')},
}
NEW_TARGETS={'data/plugins/phase3_runtime/'+name for name in (
    'autonomy_supplement.py','autonomy_content.py','autonomy_arithmetic.py')}
CONFIG='data/config/phase3_runtime_config.json'
GUARD_CONFIG='data/config/phase1_guard_config.json'
POLICY={'mode':'continuous','window_start':0,'window_end':0,'max_per_day':12,
        'max_generations_per_day':96,'cooldown':5400,'quiet_hours':[23,9]}
AUTONOMY_LIMITS={'model_requests_per_day':144,'tool_attempts_per_day':288,'tools_per_opportunity':3}
ReleaseError,Services=engine.ReleaseError,engine.Services
safe,sha,read=engine.safe,engine.sha,engine.read


def config_patch(original,source):
    if not isinstance(original,dict) or any(original.get(k) is not True for k in
            ('enabled','persona','natural_chat','memory','search','proactive')):
        raise ReleaseError('existing_enabled_companion_required')
    window=original.get('proactive_window')
    if not isinstance(window,dict) or any(window.get(k)!=v or
            (isinstance(v,(int,float)) and type(window.get(k)) not in (int,float)) for k,v in POLICY.items()):
        raise ReleaseError('existing_90_96_12_policy_required')
    autonomy=original.get('proactive_autonomy')
    if not isinstance(autonomy,dict) or autonomy.get('enabled') is not True or any(
            type(autonomy.get(k)) is not int or autonomy[k]!=v for k,v in AUTONOMY_LIMITS.items()):
        raise ReleaseError('existing_enabled_144_288_3_autonomy_required')
    return deepcopy(original)


def unchanged_config_bytes(value,original=b''):
    if not original or json.loads(original.decode('utf-8-sig'))!=value:
        raise ReleaseError('zero_configuration_change_required')
    return original


_validate=engine.validate


def validate(source,deployment,installed=False,*,config_mode='baseline'):
    manifest=read(safe(source,'source-manifest.json'))
    expected=set(PINNED)|{CONTROLLER}|set(TARGETS.values())
    observed={p.relative_to(source).as_posix() for directory in ('scripts','config','integrations')
              for p in safe(source,directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    if observed!=expected:raise ReleaseError('exact_autonomy_fixes_package_required')
    baseline=manifest.get('baseline_source_files',{})
    if not (set(TARGETS)-NEW_TARGETS).issubset(baseline):
        raise ReleaseError('existing_autonomy_baseline_sources_required')
    result=_validate(source,deployment,installed,config_mode=config_mode)
    guard=read(safe(deployment/'state',GUARD_CONFIG))
    if any(type(guard.get(k)) not in (int,float) or guard[k]!=v for k,v in
            {'phase3_bubble_min_interval':2.0,'phase3_bubble_max_interval':4.0}.items()):
        raise ReleaseError('existing_guard_2_4_pacing_required')
    template=safe(deployment/'state',engine.TEMPLATE)
    if 'QQ_COMPANION_PERSONA_V0_4' not in template.read_text(encoding='utf-8-sig'):
        raise ReleaseError('existing_v04_persona_required')
    return result


def install(source,deployment,manifest,installer,services):
    _,journal=engine.validate_candidates(source,manifest)
    if journal['configs'][0]['changes']:
        raise ReleaseError('configuration_change_not_owned')
    for row in journal['sources']:
        services.check(astr_running=False)
        engine.f.publish_file(installer,source/TARGETS[row['path']],
                              safe(deployment/'state',row['path']),row)


def restore(source,deployment,manifest,installer,services):
    services.check(astr_running=False,require_nap=False)
    journal_path,journal=engine.load_journal(source,manifest)
    if journal['configs'][0]['changes']:
        raise ReleaseError('configuration_change_not_owned')
    state=deployment/'state'
    for row in journal['sources']:
        target=safe(state,row['path']);digest=sha(target) if target.exists() else None
        if digest not in (row['old_sha256'],row['new_sha256']):
            raise ReleaseError('source_changed_since_release')
    for row in reversed(journal['sources']):
        target=safe(state,row['path'])
        if row['old_sha256'] is None:
            if target.exists():target.unlink()
        else:
            engine.f.publish_file(installer,journal_path/'original'/row['path'],target,row)


def result_metadata(value):
    if isinstance(value,dict) and value.get('operation') in ('install_autonomy','rollback_owned_code_and_fields'):
        value=dict(value)
        value['operation']='install_autonomy_fixes' if value['operation']=='install_autonomy' else 'rollback_autonomy_fixes'
        value.update(config_files=[],configuration_modified=False,configuration_bytes_preserved=True,
                     database_columns_preserved=True,sqlite_write=False)
    return value


_private_json=engine.private_json


def private_json(path,value):return _private_json(path,result_metadata(value))


# Specialize an isolated module instance; all helper files remain immutable.
engine.__file__=__file__
engine.f.encode=unchanged_config_bytes
for name in ('SCHEMA','BASE','CONTROLLER','PINNED','TARGETS','CONFIG',
             'config_patch','validate','install','restore','private_json'):
    setattr(engine,name,globals()[name])


def execute(args,services=None):return result_metadata(engine.execute(args,services))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--deployment',type=Path,default=Path('/opt/qq-ai-companion/deployment'))
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--apply',action='store_true');parser.add_argument('--rollback',action='store_true')
    try:result=execute(parser.parse_args())
    except Exception as exc:result={'status':'FAILED','reason':str(exc) if isinstance(exc,ReleaseError) else type(exc).__name__}
    print(json.dumps(result));raise SystemExit(result['status'].startswith(('FAILED','BLOCKED')))
