#!/usr/bin/env python3
"""Default dry-run profile release CLI for the pinned native Python runtime.

No SSH, stop/start, provider request or QQ operation is performed. Production
--apply refuses unless Docker reports stopped services and exact fresh backup.
"""
import argparse
import asyncio
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import stat
from types import ModuleType,SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'.runtime/upstream/AstrBot')]
from integrations.phase3_memory.bridge import native_config,MemoryDenied
from integrations.phase3_memory.profile_release import ProfileRelease,ProductionWindow,validate_approval
from integrations.phase3_memory.profile_release import _module


def private_json(path):
    path=Path(path)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'r',encoding='utf-8') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.geteuid() or info.st_mode&0o077:
            raise MemoryDenied('private_input_permissions_required')
        if info.st_size>1024*1024: raise MemoryDenied('private_input_too_large')
        return json.load(stream)


async def execute(args):
    manifest,approval=private_json(args.manifest),private_json(args.approval)
    plan=validate_approval(manifest,approval,args.environment)
    if not args.apply:
        return {**plan,'state':'DRY_RUN','native_writes':0,'model_calls':0}
    candidate=getattr(args,'candidate_root',None)
    if args.environment=='production' and not candidate:
        raise MemoryDenied('production_apply_requires_offline_controller')
    prepare=_module('profile_pinned_source',ROOT/'scripts/prepare_plugins.py')
    if prepare.source_tree_sha256(args.native_source)!='436147732fce123ddf1a61efb5add56e2f95867b19f319b0d23d82534db7aaa6':
        raise MemoryDenied('pinned_native_source_required')
    package=ModuleType('phase3_profile_native')
    package.__path__=[str(args.native_source)]
    sys.modules[package.__name__]=package
    Service=importlib.import_module(package.__name__+'.core.service').MemoryCompanionService
    Model=importlib.import_module(package.__name__+'.core.models').SessionContext
    def deny(*_args,**_kwargs): raise MemoryDenied('profile_import_never_calls_model')
    config=native_config({},enabled=False)
    config['private_companion_bridge']['accept_external_records']=True
    config['startup']={'background_grace_seconds':3600}
    service=Service(context=SimpleNamespace(get_provider_by_id=deny,get_using_provider=deny),config=config,
                    plugin_root=args.native_source,data_dir=args.native_data)
    try:
        ctx=Model(scope='private',**manifest['identity'])
        release=ProfileRelease(service,ctx,args.ledger)
        if candidate:
            return await release.prepare_candidate(manifest,approval,candidate_root=candidate,withdraw=args.withdraw)
        function=release.withdraw if args.withdraw else release.apply
        return await function(manifest,approval,environment=args.environment,apply=True)
    finally: await service.aclose()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--approval',type=Path,required=True)
    p.add_argument('--environment',choices=['isolated','production'],default='production')
    p.add_argument('--native-source',type=Path,default=ROOT/'.runtime/upstream/memory_companion')
    p.add_argument('--native-data',type=Path,required=True)
    p.add_argument('--ledger',type=Path,required=True)
    p.add_argument('--deployment',type=Path,default=Path('/opt/qq-ai-companion/deployment'))
    p.add_argument('--apply',action='store_true')
    p.add_argument('--withdraw',action='store_true')
    p.add_argument('--candidate-root',type=Path,help=argparse.SUPPRESS)
    args=p.parse_args()
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix='qq-profile-native-root-') as root:
        os.environ['ASTRBOT_ROOT']=root
        os.environ['ASTRBOT_DISABLE_METRICS']='1'
        try: result=asyncio.run(execute(args))
        except Exception as exc:
            result={'state':'BLOCKED','reason':str(exc) if isinstance(exc,MemoryDenied) else type(exc).__name__,'raw_content_logged':False}
    print(json.dumps(result,ensure_ascii=False))
    return 1 if result['state']=='BLOCKED' else 0


if __name__=='__main__': raise SystemExit(main())
