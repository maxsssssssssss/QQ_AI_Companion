#!/usr/bin/env python3
"""Networkless native L1 promotion worker; never re-imports approved profiles.

The host mounts only an offline native DB copy, the existing ledger, the exact
reviewed manifest and a maintenance configuration. No production state mount.
"""
import argparse
import asyncio
from contextlib import closing
from dataclasses import replace
import hashlib
import importlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'scripts')]
from integrations.phase3_memory.bridge import MemoryDenied, native_config
from integrations.phase3_memory.profile_release import ProfileRelease, _module, digest
from integrations.phase3_memory.profile_release import validate_approval
from integrations.phase3_memory.core_profile import promote_l1, validate_candidate

PINNED_NATIVE = '436147732fce123ddf1a61efb5add56e2f95867b19f319b0d23d82534db7aaa6'


def preserved_rows(path, excluded):
    """Private aggregate proof: original timeline and every non-L1 row intact."""
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        memories = [dict(r) for r in db.execute('SELECT * FROM memories ORDER BY id') if r['id'] not in excluded]
        timeline = [tuple(r) for r in db.execute('SELECT * FROM timeline ORDER BY id')]
    return {'other_memory_count':len(memories), 'other_memory_hash':digest(memories),
            'timeline_count':len(timeline), 'timeline_hash':digest(timeline)}


async def execute(args):
    cli = _module('experience_import_private_json', ROOT/'scripts/phase3-profile-import.py')
    manifest, approval = cli.private_json(args.manifest), cli.private_json(args.approval)
    plan = validate_approval(manifest,approval,'production')
    if not getattr(args,'apply',False):
        return {**plan,'state':'PLAN','native_writes':0,'model_calls':0,'production_writes':0}
    config = cli.private_json(args.native_config)
    prepare = _module('experience_prepare_pinned', ROOT/'scripts/prepare_plugins.py')
    if prepare.source_tree_sha256(args.native_source) != PINNED_NATIVE:
        raise MemoryDenied('pinned_native_source_required')
    # Copy configured injection budgets while disabling every model/capture path.
    if config.get('core_memory',{}).get('max_chars') != 1600:
        raise MemoryDenied('reviewed_core_budget_1600_required')
    config = native_config(config, enabled=False)
    config.setdefault('startup',{})['background_grace_seconds'] = 3600
    config.setdefault('retrieval',{}).update(embedding_enabled=False, embedding_backfill_enabled=False,
        mode='basic', rerank_provider_id='', embedding_provider_id='')
    config.setdefault('maintenance',{})['auto_schedule_enabled'] = False
    package = ModuleType('experience_native_core_worker')
    package.__path__ = [str(args.native_source)]
    sys.modules[package.__name__] = package
    Service = importlib.import_module(package.__name__+'.core.service').MemoryCompanionService
    Context = importlib.import_module(package.__name__+'.core.models').SessionContext
    def deny(*_a,**_k): raise MemoryDenied('core_migration_never_calls_model')
    service = Service(context=SimpleNamespace(get_provider_by_id=deny,get_using_provider=deny),
        config=config, plugin_root=args.native_source, data_dir=args.native_data)
    try:
        ctx = Context(scope='private',**manifest['identity'])
        release = ProfileRelease(service,ctx,args.ledger)
        plan = validate_candidate(release,manifest,approval,args.candidate_root)
        l1 = [e for e in manifest['entries'] if e['layer']=='L1']
        ids = [service.stable_id('phase3_profile_release',digest(manifest['identity']),
                                manifest['batch_id'],e['id']) for e in l1]
        # Validate all 15 approved bodies/sources before any mutation. A changed
        # L2 must not be silently included under the old import approval.
        for entry in manifest['entries']:
            key = service.stable_id('phase3_profile_release',digest(manifest['identity']),
                                   manifest['batch_id'],entry['id'])
            record = await service.store.get_memory(key)
            if (not record or record.content != entry['text'] or record.source_plugin!='phase3_profile_release'
                or record.metadata.get('source_refs') != entry['source_refs']
                or record.metadata.get('manifest_hash') != plan['manifest_hash']
                or record.metadata.get('profile_layer') != entry['layer']
                or record.validity_status != 'active'):
                raise MemoryDenied('approved_profile_record_changed_or_unavailable')
        before = preserved_rows(Path(service.store.db_path),set(ids))
        ledger_hash = hashlib.sha256(args.ledger.read_bytes()).hexdigest()
        result = await promote_l1(release,manifest,approval,environment='production',
                                 apply=True,candidate_root=args.candidate_root)
        visible = await service.core_memories_for_context(ctx)
        if not set(ids).issubset({r.id for r in visible}):
            raise MemoryDenied('all_approved_l1_must_be_visible_native_core')
        # Use the actual native service orchestration and native renderer. This
        # catches whole-pack and per-block truncation; a DB count is not proof.
        injection = await service._compose_memory_injection(replace(ctx,message_text='你好'),
            max_chars=config.get('memory_injection',{}).get('max_chars',4000),write_log=False)
        escaped = service.injection._safe_text
        if any(escaped(entry['text'],4000) not in injection for entry in l1):
            raise MemoryDenied('native_core_injection_truncated_approved_l1')
        after = preserved_rows(Path(service.store.db_path),set(ids))
        if before != after or hashlib.sha256(args.ledger.read_bytes()).hexdigest()!=ledger_hash:
            raise MemoryDenied('non_l1_or_existing_ledger_changed')
        return {**plan,**result,'state':'CANDIDATE_PREPARED','model_calls':0,
            'production_writes':0,'native_core_injection_full_body_count':len(l1),
            'core_max_chars':1600,'other_memories_unchanged':True,'timeline_unchanged':True,
            'existing_ledger_unchanged':True,'profile_reimported':False,
            'injection_sha256':hashlib.sha256(injection.encode()).hexdigest(),
            'injection_chars':len(injection),'preserved_counts':{
                k:v for k,v in before.items() if k.endswith('_count')}}
    finally:
        await service.aclose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest','approval','native-config','native-source','native-data','ledger','candidate-root'):
        parser.add_argument('--'+name,required=True,type=Path)
    parser.add_argument('--apply',action='store_true')
    args = parser.parse_args(); os.umask(0o077)
    try: result = asyncio.run(execute(args))
    except Exception as exc:
        result = {'state':'BLOCKED','reason':str(exc) if isinstance(exc,MemoryDenied) else type(exc).__name__,
                  'raw_content_logged':False,'model_calls':0}
    print(json.dumps(result,ensure_ascii=False))
    return int(result['state']=='BLOCKED')


if __name__=='__main__': raise SystemExit(main())
