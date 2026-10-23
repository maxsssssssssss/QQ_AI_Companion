"""Synthetic pinned-native profile imports; no real interview or production I/O."""
import copy
from dataclasses import replace
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_phase3_memory_native as native
NativeService, UPSTREAM = native.NativeService, native.UPSTREAM
from integrations.phase3_memory.profile_release import (ProfileRelease, ProductionWindow,
    validate_approval, validate_manifest, identity, digest, MemoryDenied, ROOT, _module)


def manifest(ctx):
    return {'schema':'qq-companion.profile-reviewed.v1','reviewed':True,'batch_id':'synthetic-batch',
        'identity':identity(ctx),'source_document_sha256':'a'*64,'entries':[
          {'id':'preference','layer':'L1','text':'用户自述喜欢青梅乌龙茶。',
           'permission_scope':'preferences','confidence_status':'user_confirmed',
           'shared_experience':False,'source_refs':['synthetic-interview:section-1:line-2']},
          {'id':'history','layer':'L2','text':'用户自述曾和同学在紫藤公园散步。这不是与 AI 的共同经历。',
           'permission_scope':'background','confidence_status':'user_confirmed',
           'shared_experience':False,'source_refs':['synthetic-interview:section-2:line-4']}]}


def approval(m,environment='isolated'):
    return {'approved':True,'environment':environment,'batch_id':m['batch_id'],
        'manifest_hash':digest(m),'identity':m['identity'],
        'grants':{e['permission_scope']:{'write_memory':True,'model_use':True,
                 'native_proactive_recall':True,'production_write':environment=='production'} for e in m['entries']}}


class NativeProfileReleaseTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=native.NativeMemoryTests.asyncSetUp
    asyncTearDown=native.NativeMemoryTests.asyncTearDown

    def release(self): return ProfileRelease(self.service,self.ctx,self.path/'release'/'ledger.sqlite3')

    async def apply_batch(self):
        m=manifest(self.ctx);a=approval(m)
        r=await self.release().apply(m,a,apply=True)
        self.assertEqual(r['state'],'APPLIED')
        return m,a

    async def test_dry_run_no_native_writes_or_ledger(self):
        m=manifest(self.ctx);r=await self.release().apply(m,approval(m))
        self.assertEqual(r['state'],'DRY_RUN')
        self.assertFalse((self.path/'release'/'ledger.sqlite3').exists())
        self.assertEqual(await self.service.search('青梅乌龙茶',self.ctx),[])
        self.assertEqual(self.provider.calls,[])

    async def test_native_import_source_recall_scope_and_idempotency(self):
        m,a=await self.apply_batch()
        repeated=await self.release().apply(m,a,apply=True)
        self.assertEqual(repeated['native_writes'],0)
        rows=await self.service.search('青梅乌龙茶',self.ctx)
        self.assertEqual(len(rows),1)
        r=rows[0].memory
        self.assertEqual(r.content,m['entries'][0]['text'])
        for key,value in {'manifest_hash':digest(m),'source_document_sha256':'a'*64,
              'source_refs':m['entries'][0]['source_refs'],'shared_experience':False,
              'confidence_status':'user_confirmed','owner_bot_id':self.ctx.bot_id}.items():
            self.assertEqual(r.metadata[key],value)
        for ctx in [replace(self.ctx,user_id='unrelated-user'),replace(self.ctx,bot_id='unrelated-bot'),
                    replace(self.ctx,persona_id='unrelated-persona')]:
            self.assertEqual(await self.service.search('青梅乌龙茶',ctx),[])
        self.assertEqual(self.provider.calls,[])

    async def test_reopen_recall_then_withdraw_batch_and_no_reimport(self):
        m,a=await self.apply_batch()
        await self.co.close();await self.service.aclose()
        self.service=NativeService(context=self.host,config=self.config,plugin_root=UPSTREAM,data_dir=self.path)
        self.assertEqual(len(await self.service.search('青梅乌龙茶',self.ctx)),1)
        a['withdraw_batch']=True
        r=await self.release().withdraw(m,a,apply=True)
        self.assertEqual(r['deleted_native_records'],2)
        self.assertFalse(r['raw_source_deleted'])
        self.assertEqual(await self.service.search('青梅乌龙茶',self.ctx),[])
        self.assertEqual(await self.service.search('紫藤公园',self.ctx),[])
        with self.assertRaisesRegex(MemoryDenied,'reuse'):
            await self.release().apply(m,a,apply=True)

    async def test_changed_approved_content_identity_or_grant_rejected(self):
        m=manifest(self.ctx);a=approval(m)
        changed=copy.deepcopy(m);changed['entries'][0]['text']='未经审核的新内容。'
        with self.assertRaisesRegex(MemoryDenied,'exact_manifest'):
            await self.release().apply(changed,a,apply=True)
        for field in ('model_use','write_memory','native_proactive_recall'):
            denied=copy.deepcopy(a);denied['grants']['preferences'][field]=False
            with self.assertRaisesRegex(MemoryDenied,'per_scope'):
                await self.release().apply(m,denied,apply=True)
        changed=copy.deepcopy(m);changed['identity']['bot_id']='other'
        with self.assertRaisesRegex(MemoryDenied,'runtime_identity'):
            await self.release().apply(changed,approval(changed),apply=True)

    async def test_l3_excluded_or_unconfirmed_rejected_before_writes(self):
        for layer in ('L3','EXCLUDED'):
            m=manifest(self.ctx);m['entries'][0]['layer']=layer
            with self.assertRaisesRegex(MemoryDenied,'temporal_or_excluded'):
                await self.release().apply(m,approval(m),apply=True)
        m=manifest(self.ctx);m['entries'][0]['confidence_status']='inferred'
        with self.assertRaisesRegex(MemoryDenied,'confirmed_self_report'):
            await self.release().apply(m,approval(m),apply=True)
        self.assertFalse((self.path/'release'/'ledger.sqlite3').exists())

    async def test_production_not_bypassed_by_isolated_consent_or_flags(self):
        m=manifest(self.ctx)
        with self.assertRaisesRegex(MemoryDenied,'environment_consent'):
            await self.release().apply(m,approval(m),environment='production',apply=True)
        with self.assertRaisesRegex(MemoryDenied,'stopped_backup|maintenance_model'):
            await self.release().apply(m,approval(m,'production'),environment='production',apply=True)

    async def test_edited_native_record_is_not_silently_deleted(self):
        m,a=await self.apply_batch();a['withdraw_batch']=True
        row=(await self.service.search('青梅乌龙茶',self.ctx))[0].memory
        row.content='这条资料已被后续人工修订。'
        await self.service.store.insert_memory(row)
        with self.assertRaisesRegex(MemoryDenied,'edited_profile'):
            await self.release().withdraw(m,a,apply=True)
        self.assertIsNotNone(await self.service.store.get_memory(row.id))

    async def test_concurrent_release_refused_and_native_whitespace_reviewed(self):
        m=manifest(self.ctx)
        with self.release()._ledger():
            with self.assertRaisesRegex(MemoryDenied,'in_progress'):
                await self.release().apply(m,approval(m),apply=True)
        m['entries'][0]['text']='用户自述\n\n喜欢青梅乌龙茶。'
        with self.assertRaisesRegex(MemoryDenied,'normalized_content'):
            await self.release().apply(m,approval(m),apply=True)

    async def test_cli_default_dryrun_and_explicit_isolated_native_apply(self):
        cli=_module('profile_release_cli',ROOT/'scripts/phase3-profile-import.py')
        m=manifest(self.ctx);a=approval(m)
        inputs=self.path/'private-inputs';inputs.mkdir(mode=0o700)
        for name,value in [('manifest',m),('approval',a)]:
            p=inputs/(name+'.json');p.write_text(json.dumps(value));p.chmod(0o600)
        args=SimpleNamespace(manifest=inputs/'manifest.json',approval=inputs/'approval.json',
            environment='isolated',apply=False,withdraw=False,native_source=UPSTREAM,
            native_data=self.path/'new-native',ledger=self.path/'new-controls'/'ledger.sqlite3')
        r=await cli.execute(args)
        self.assertEqual(r['state'],'DRY_RUN');self.assertFalse(args.native_data.exists())
        args.apply=True;r=await cli.execute(args)
        self.assertEqual(r['state'],'APPLIED');self.assertEqual(r['native_writes'],2)
        args.withdraw=True;a['withdraw_batch']=True;args.approval.write_text(json.dumps(a))
        r=await cli.execute(args);self.assertEqual(r['state'],'WITHDRAWN')

    async def test_production_branch_native_apply_with_fresh_backup_and_stopped_fixture(self):
        from integrations.phase3_memory import native_config
        fixture=ProductionWindowTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        data=fixture.state/'memory';data.mkdir(mode=0o700)
        cfg=native_config({},enabled=False);cfg['private_companion_bridge']['accept_external_records']=True
        cfg['startup']={'background_grace_seconds':3600}
        service=NativeService(context=self.host,config=cfg,plugin_root=UPSTREAM,data_dir=data)
        try:
            fixture.refresh_backup()
            m=manifest(self.ctx);a=approval(m,'production')
            a['backup']={'run_id':'synthetic-run','astrbot_archive_sha256':fixture.sha}
            release=ProfileRelease(service,self.ctx,fixture.state/'profile-release'/'ledger.sqlite3')
            r=await release.apply(m,a,environment='production',apply=True,window=fixture.window())
            self.assertEqual(r['state'],'APPLIED')
            self.assertEqual(len(await service.search('青梅乌龙茶',self.ctx)),1)
            # A stale pre-import snapshot cannot authorize withdrawal.
            a['withdraw_batch']=True
            with self.assertRaisesRegex(MemoryDenied,'changed_since_backup'):
                await release.withdraw(m,a,environment='production',apply=True,window=fixture.window())
            fixture.refresh_backup();a['backup']['astrbot_archive_sha256']=fixture.sha
            r=await release.withdraw(m,a,environment='production',apply=True,window=fixture.window())
            self.assertEqual(r['state'],'WITHDRAWN')
            self.assertEqual(await service.search('青梅乌龙茶',self.ctx),[])
        finally:await service.aclose()

    async def test_production_controller_native_candidate_and_narrow_install(self):
        import asyncio
        from integrations.phase3_memory import native_config
        fixture=ProductionWindowTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        data=fixture.state/'data'/'plugin_data'/'memory';data.mkdir(mode=0o700,parents=True)
        unrelated=fixture.state/'data'/'untouched-secret.txt';unrelated.write_text('synthetic unrelated sentinel')
        cfg=native_config({},enabled=False);cfg['startup']={'background_grace_seconds':3600}
        service=NativeService(context=self.host,config=cfg,plugin_root=UPSTREAM,data_dir=data)
        await service.record_external_memory(user_id=self.ctx.user_id,content='synthetic original row')
        await service.aclose()
        usage=data/'memory_companion_token_usage.json';usage.chmod(0o644)
        old_usage=usage.read_bytes();fixture.refresh_backup()
        cli=_module('profile_production_worker',ROOT/'scripts/phase3-profile-import.py')
        controller=_module('profile_production_controller',ROOT/'scripts/phase3-profile-production.py')
        m=manifest(self.ctx);a=approval(m,'production');a['runtime_image_id']=controller.IMAGE
        a['backup']={'run_id':'synthetic-run','astrbot_archive_sha256':fixture.sha}
        for name,value in [('manifest',m),('approval',a)]:
            p=fixture.root/(name+'.json');p.write_text(json.dumps(value));p.chmod(0o600)
        args=SimpleNamespace(manifest=fixture.root/'manifest.json',approval=fixture.root/'approval.json',
            apply=True,withdraw=False,native_source=UPSTREAM,native_data=data,
            ledger=fixture.state/'data'/'plugin_data'/'phase3_profile_release'/'ledger.sqlite3',deployment=fixture.root)
        def worker(_cmd,candidate):
            task=SimpleNamespace(manifest=candidate/'manifest.json',approval=candidate/'approval.json',
                apply=True,withdraw=False,environment='production',native_source=UPSTREAM,
                native_data=candidate/'native',ledger=candidate/'ledger.sqlite3',candidate_root=candidate)
            result=asyncio.run(cli.execute(task))
            (candidate/'native/memory_companion_token_usage.json').chmod(0o600)
            return result
        with patch.object(ProductionWindow,'_run',fixture.runner), patch.object(controller.os,'chown',wraps=os.chown) as owners:
            result=await asyncio.to_thread(controller.execute,args,run_worker=worker)
        owner=(data/'memory_companion.db').stat()
        expected=(owner.st_uid,owner.st_gid)
        for path in (data/'memory_companion.db.profile-pending',args.ledger.with_name('ledger.sqlite3.profile-pending'),args.ledger.parent):
            self.assertTrue(any(call.args==(path,*expected) for call in owners.call_args_list))
        self.assertEqual((args.ledger.stat().st_uid,args.ledger.stat().st_gid),expected)
        self.assertEqual(result['state'],'APPLIED');self.assertFalse(result['other_state_changed'])
        self.assertEqual(unrelated.read_text(),'synthetic unrelated sentinel')
        self.assertEqual(usage.read_bytes(),old_usage);self.assertEqual(usage.stat().st_mode&0o777,0o644)
        reopened=NativeService(context=self.host,config=cfg,plugin_root=UPSTREAM,data_dir=data)
        try:self.assertEqual(len(await reopened.search('青梅乌龙茶',self.ctx)),1)
        finally:await reopened.aclose()

    async def test_existing_native_duplicate_rejected_before_merge(self):
        m,a=await self.apply_batch()
        rows=await self.service.search('青梅乌龙茶',self.ctx)
        old=await self.service.store.get_memory(rows[0].memory.id)
        count=old.merged_count;metadata=copy.deepcopy(old.metadata)
        m['batch_id']='different-batch'
        with self.assertRaisesRegex(MemoryDenied,'duplicate_requires_review'):
            await self.release().apply(m,approval(m),apply=True)
        unchanged=await self.service.store.get_memory(old.id)
        self.assertEqual(unchanged.merged_count,count);self.assertEqual(unchanged.metadata,metadata)


class ProductionWindowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.state=self.root/'state';self.state.mkdir(mode=0o700)
        self.db=self.state/'memory_companion.db'
        with sqlite3.connect(self.db) as db:db.execute('CREATE TABLE synthetic(value TEXT)')
        self.db.chmod(0o600)
        (self.root/'backups').mkdir(mode=0o700)
        self.run=self.root/'backups'/'synthetic-run';self.run.mkdir(mode=0o700)
        self.backup=_module('profile_test_backup',ROOT/'scripts/backup.py')
        self.refresh_backup()
        self.running=False;self.mount=str(self.state/'data')

    def refresh_backup(self):
        archive=self.run/'astrbot-state.zip'
        archive.unlink(missing_ok=True)
        self.backup.create_backup(self.state,archive);archive.chmod(0o600)
        self.sha=hashlib.sha256(archive.read_bytes()).hexdigest()
        (self.run/'result.json').write_text(json.dumps({'status':'PASS','backup_integrity':'PASS',
            'restore_integrity':'PASS','astrbot_archive_sha256':self.sha}))
        (self.run/'operation.json').write_text(json.dumps({'originally_running_services':[],
            'restored_copy_must_never_start':True}))
        for f in self.run.glob('*.json'):f.chmod(0o600)

    def runner(self,args):
        if args[1]=='compose':return 'a'*64+'\n' if args[-1:] == ['astrbot'] else 'a'*64+'\n'+'b'*64+'\n'
        astrbot=args[2]=='a'*64
        return json.dumps([{'Image':'sha256:a0bf74942fc2ba504932897f7f6ba8d8bd01c1084ea6448c399b8df579bc77af','Config':{'Labels':{'com.docker.compose.project':'qq-ai-companion',
            'com.docker.compose.service':'astrbot' if astrbot else 'napcat'}},
            'State':{'Running':self.running,'Status':'running' if self.running else 'exited'},
            'Mounts':[{'Type':'bind','Source':self.mount,'Destination':'/AstrBot/data'}] if astrbot else []}])

    def window(self):return ProductionWindow(self.root,'synthetic-run',self.sha,runner=self.runner)

    def test_actual_archive_restore_and_current_tree_match(self):
        self.assertEqual(self.window().verify(self.db)['backup_sha256'],self.sha)

    def test_json_pass_cannot_override_running_or_wrong_mount(self):
        self.running=True
        with self.assertRaisesRegex(MemoryDenied,'writers_must_be_stopped'):self.window().verify(self.db)
        self.running=False;self.mount='/unrelated'
        with self.assertRaisesRegex(MemoryDenied,'mount_mismatch'):self.window().verify(self.db)

    def test_stale_snapshot_and_tampered_archive_rejected(self):
        (self.state/'newfile').write_text('new synthetic state')
        with self.assertRaisesRegex(MemoryDenied,'changed_since_backup'):self.window().verify(self.db)
        (self.state/'newfile').unlink()
        archive=self.run/'astrbot-state.zip';archive.write_bytes(archive.read_bytes()+b'corruption')
        with self.assertRaisesRegex(MemoryDenied,'digest_required'):self.window().verify(self.db)

    def test_backup_permissions_and_native_database_scope(self):
        (self.run/'result.json').chmod(0o644)
        with self.assertRaisesRegex(MemoryDenied,'private_backup'):self.window().verify(self.db)
        (self.run/'result.json').chmod(0o600)
        with self.assertRaisesRegex(MemoryDenied,'outside_verified'):self.window().verify(self.root/'memory_companion.db')


class SemanticPreviewTests(unittest.TestCase):
    def setUp(self):self.preview=_module('semantic_profile_preview',ROOT/'scripts/phase3-profile-semantic-preview.py')

    def test_topics_merge_whole_answers_with_all_source_refs(self):
        source='## 三、兴趣\n### 3.1 体验\n- 喜欢合成游戏甲。\n- 偏好探索。\n### 3.2 其他\n- 喜欢合成游戏乙。\n'
        result=self.preview.preview(source)
        self.assertEqual(result['counts'],{'L2':1})
        row=result['entries'][0]
        self.assertEqual(len(row['source_refs']),3)
        self.assertIn('合成游戏甲',row['text']);self.assertIn('合成游戏乙',row['text'])
        self.assertFalse(row['production_write_allowed']);self.assertFalse(row['model_use_allowed'])
        self.assertEqual(result['model_calls'],0)

    def test_duplicate_blocks_merge_but_negation_kept(self):
        source='## 三、兴趣\n### 3.1 体验\n- 喜欢合成游戏甲。\n### 3.2 其他\n- 喜欢合成游戏甲。\n### 3.3 区别\n- 不喜欢合成游戏甲。\n'
        result=self.preview.preview(source)
        self.assertEqual(result['duplicate_answer_blocks_merged'],1)
        row=result['entries'][0]
        self.assertIn('不喜欢',row['text']);self.assertEqual(len(row['source_refs']),3)

    def test_temporal_excluded_chunk_ids_and_owner_only_output(self):
        source='## 十三、近期\n'+('\n'.join('- '+str(i)+'号合成状态'+('甲'*1000) for i in range(5)))+'\n## 十七、推断\n### 17.1 待确认\n- 未确认分析。\n'
        result=self.preview.preview(source)
        self.assertEqual(len({x['id'] for x in result['entries']}),len(result['entries']))
        self.assertEqual({x['layer'] for x in result['entries']},{'L3','EXCLUDED'})
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'private.json'
            self.preview.save_private(path,json.dumps(result))
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            with self.assertRaises(FileExistsError):self.preview.save_private(path,'replace')


if __name__=='__main__':unittest.main()
