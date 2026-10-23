"""Synthetic native promotion plus stopped publication/rollback; no real VPS."""
import asyncio
from contextlib import closing
from copy import deepcopy
import importlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_phase3_memory_native as native
from integrations.phase3_memory.bridge import native_config, MemoryDenied
from integrations.phase3_memory.profile_release import ProfileRelease, ProductionWindow, _module, digest, identity

ROOT=Path(__file__).resolve().parents[1]
controller=_module('experience_test_profile_controller',ROOT/'scripts/phase3-profile-production.py')
deploy=_module('experience_test_deploy',ROOT/'scripts/phase3-deploy-experience.py')
worker=_module('experience_test_worker',ROOT/'scripts/phase3-core-profile-worker.py')
installer=_module('experience_test_installer',ROOT/'scripts/phase3-install-overlay.py')
backup=_module('experience_test_backup',ROOT/'scripts/phase2-backup.py')
prepare=_module('experience_test_prepare',ROOT/'scripts/prepare_plugins.py')


def reviewed(ctx, long=False):
    rows=[]
    for i in range(15):
        text=('合成核心资料'+str(i)+'：'+('尊重明确边界，保持自然交流。'*12) if i<3 else '用户自述第'+str(i)+'项合成背景。')
        if long and i==0: text='合成长度边界：'+('保持自然。'*270)
        rows.append({'id':'synthetic_'+str(i),'layer':'L1' if i<3 else 'L2','text':text,
            'permission_scope':'core' if i<3 else 'background','confidence_status':'user_confirmed',
            'shared_experience':False,'source_refs':['synthetic-fixture:line:'+str(i)]})
    m={'schema':'qq-companion.profile-reviewed.v1','reviewed':True,'batch_id':'synthetic-existing',
       'identity':identity(ctx),'source_document_sha256':'d'*64,'entries':rows}
    a={'approved':True,'environment':'production','batch_id':m['batch_id'],'identity':m['identity'],
       'manifest_hash':digest(m),'runtime_image_id':controller.IMAGE,
       'grants':{name:{'write_memory':True,'model_use':True,'native_proactive_recall':True,'production_write':True}
                 for name in ('core','background')}}
    return m,a


class ReleaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.root.chmod(0o700)
        self.dep=self.root/'deployment';self.dep.mkdir(mode=0o700)
        self.state=self.dep/'state';self.state.mkdir(mode=0o700)
        self.data=self.state/'data';self.data.mkdir(mode=0o700)
        self.native=self.data/'plugin_data/astrbot_plugin_memory_companion';self.native.mkdir(parents=True,mode=0o700)
        self.ledger=self.data/'plugin_data/phase3_profile_release/ledger.sqlite3'
        config=native_config({},enabled=False);config['private_companion_bridge']['accept_external_records']=True
        config['startup']={'background_grace_seconds':3600}
        def deny(*_a,**_k):raise AssertionError('model_forbidden')
        self.host=SimpleNamespace(get_provider_by_id=deny,get_using_provider=deny)
        self.config=config
        self.service=native.NativeService(context=self.host,config=config,plugin_root=native.UPSTREAM,data_dir=self.native)
        Context=importlib.import_module(type(self.service).__module__.rsplit('.',1)[0]+'.models').SessionContext
        self.ctx=Context(scope='private',session_id='fixture:FriendMessage:synthetic-user',
            user_id='synthetic-user',bot_id='synthetic-bot',platform='fixture',persona_id='synthetic-persona')
        self.m,self.a=reviewed(self.ctx)
        isolated=deepcopy(self.a);isolated['environment']='isolated'
        await ProfileRelease(self.service,self.ctx,self.ledger).apply(self.m,isolated,apply=True)
        await self.service.record_external_memory(user_id=self.ctx.user_id,content='原有未导入的合成记忆。')
        await self.service.aclose()
        self.config.setdefault('core_memory',{}).update(enabled=True,max_chars=1600,max_blocks=8)
        self.config.setdefault('memory_injection',{})['max_chars']=4000

    def make_offline(self):
        offline=self.root/'offline';offline.mkdir(mode=0o700)
        shutil.copytree(self.native,offline/'native')
        shutil.copy2(self.ledger,offline/'ledger.sqlite3')
        approval=deepcopy(self.a);approval['backup']={'run_id':'synthetic','astrbot_archive_sha256':'b'*64}
        for filename,value in [('manifest.json',self.m),('approval.json',approval),('core-config.json',self.config),
            ('candidate.json',{'schema':'qq-companion.offline-profile-candidate.v1',
                'manifest_hash':digest(self.m),'backup':approval['backup'],'operation':'apply'})]:
            controller.write_private(offline/filename,value)
        return SimpleNamespace(manifest=offline/'manifest.json',approval=offline/'approval.json',
            native_config=offline/'core-config.json',native_source=native.UPSTREAM,native_data=offline/'native',
            ledger=offline/'ledger.sqlite3',candidate_root=offline,apply=True)

    async def test_worker_exact_existing_rows_full_native_injection_and_no_model(self):
        args=self.make_offline();before=deploy.sha(self.native/'memory_companion.db')
        result=await worker.execute(args)
        self.assertEqual(result['native_core_injection_full_body_count'],3)
        self.assertEqual(result['state'],'CANDIDATE_PREPARED');self.assertEqual(result['model_calls'],0)
        self.assertTrue(result['timeline_unchanged']);self.assertTrue(result['other_memories_unchanged'])
        self.assertTrue(result['existing_ledger_unchanged']);self.assertFalse(result['profile_reimported'])
        self.assertEqual(deploy.sha(self.native/'memory_companion.db'),before)
        again=await worker.execute(args);self.assertEqual(again['changed_ids'],[])
        with closing(sqlite3.connect(args.native_data/'memory_companion.db')) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM memories WHERE memory_type='core_memory'").fetchone()[0],3)
            self.assertEqual(db.execute('SELECT count(*) FROM memories').fetchone()[0],16)
        svc=native.NativeService(context=self.host,config=self.config,plugin_root=native.UPSTREAM,data_dir=args.native_data)
        try:
            record=(await svc.core_memories_for_context(self.ctx))[0]
            metadata=dict(record.metadata);metadata['core_enabled']=False
            await svc.store.update_memory_payload(record.id,metadata=metadata)
        finally: await svc.aclose()
        with self.assertRaisesRegex(MemoryDenied,'changed_or_unavailable'):
            await worker.execute(args)

    async def test_default_worker_plan_never_opens_native_database(self):
        args=self.make_offline();args.apply=False;args.native_data=self.root/'absent'
        result=await worker.execute(args)
        self.assertEqual(result['state'],'PLAN');self.assertFalse(args.native_data.exists())

    async def test_withdrawn_ledger_or_source_change_rejected(self):
        args=self.make_offline()
        with sqlite3.connect(args.ledger) as db:db.execute("UPDATE entries SET state='withdrawn' WHERE entry_id='synthetic_1'")
        with self.assertRaisesRegex(MemoryDenied,'ledger_changed_or_withdrawn'):await worker.execute(args)

    async def test_actual_native_per_block_truncation_is_not_count_pass(self):
        args=self.make_offline()
        changed=deepcopy(self.m);changed['entries'][0]['text']='合成长核心：'+('自然交流。'*270)
        # Build the permitted fixture in isolation; no old approval is repurposed.
        tempdata=self.root/'long-native'
        svc=native.NativeService(context=self.host,config=self.config,plugin_root=native.UPSTREAM,data_dir=tempdata)
        try:
            m,a=reviewed(self.ctx,long=True);a['environment']='isolated'
            await ProfileRelease(svc,self.ctx,self.root/'long-ledger.sqlite3').apply(m,a,apply=True)
        finally:await svc.aclose()
        shutil.rmtree(args.native_data);shutil.copytree(tempdata,args.native_data)
        shutil.copy2(self.root/'long-ledger.sqlite3',args.ledger)
        a['environment']='production';a['backup']=self.a.get('backup',{'run_id':'synthetic','astrbot_archive_sha256':'b'*64})
        controller.write_private(args.manifest,m);controller.write_private(args.approval,a)
        marker=deploy.read(args.candidate_root/'candidate.json');marker['manifest_hash']=digest(m)
        controller.write_private(args.candidate_root/'candidate.json',marker)
        with self.assertRaisesRegex(MemoryDenied,'injection_truncated'):await worker.execute(args)

    def make_bundle(self):
        source=self.root/'release';source.mkdir(mode=0o700)
        for plugin in deploy.PLUGINS:
            shutil.copytree(ROOT/'integrations'/plugin,source/'integrations'/plugin,
                ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        for name in deploy.SCRIPTS:
            dest=source/'scripts'/name;dest.parent.mkdir(exist_ok=True)
            shutil.copy2(ROOT/'scripts'/name,dest)
        config=self.data/'config';config.mkdir(mode=0o700)
        values={'phase3_runtime_config.json':{'enabled':True,'persona':True,'natural_chat':True,'memory':True,'search':True,'proactive':False},
            'phase1_guard_config.json':{'phase3_proactive_enabled':False},'phase2_policy_config.json':{'admin_id':'10001','platform_id':'fixture'},
            'phase3_search_config.json':{'enabled':True,'daily_attempt_limit':60},
            'astrbot_plugin_memory_companion_config.json':self.config,
            'astrbot_plugin_private_companion_config.json':{'private_followup_config':{'proactive_unanswered_pause_after':1}}}
        for name,value in values.items():controller.write_private(config/name,value)
        controller.write_private(self.data/'cmd_config.json',{'synthetic_secret':'do-not-log-or-change'})
        self.dep.joinpath('compose.yml').write_text('synthetic locked compose')
        guard=self.data/'plugin_data/phase1_guard/guard.sqlite3';guard.parent.mkdir(mode=0o700)
        with sqlite3.connect(guard) as db:db.execute('CREATE TABLE turns(id TEXT PRIMARY KEY,state TEXT)')
        for path in ('napcat/config','ntqq'):(self.dep/path).mkdir(parents=True,mode=0o700)
        (self.dep/'ntqq/opaque.db').write_bytes(b'opaque synthetic login state')
        self.upstream=self.data/'plugins/astrbot_plugin_memory_companion'
        shutil.copytree(native.UPSTREAM,self.upstream,ignore=shutil.ignore_patterns('.git','__pycache__','*.pyc'))
        file_map={p.relative_to(source).as_posix():deploy.sha(p) for p in source.rglob('*') if p.is_file()}
        package={'files':file_map,'compose_sha256':deploy.sha(self.dep/'compose.yml'),
            'approved_profile_hash':digest(self.m),'approved_profile_identity_hash':digest(self.m['identity'])}
        targets=deploy.package_files(source,package)
        package['baseline']={name:deploy.sha(self.state/name) if (self.state/name).is_file() else None
            for name in set(targets)|set(deploy.CONFIGS)|set(deploy.BASELINE_EXTRA)}
        controller.write_private(source/'source-manifest.json',package)
        controller.write_private(self.root/'manifest.json',self.m);controller.write_private(self.root/'approval.json',self.a)
        self.running={'astrbot','napcat'};self.order=[]
        self.source=source
        return SimpleNamespace(source=source,deployment=self.dep,manifest=self.root/'manifest.json',
            approval=self.root/'approval.json',run_id='synthetic-release',apply=True)

    def runner(self,args):
        if args[1]=='compose':
            services=[name for name in args if name in {'astrbot','napcat'}]
            return '\n'.join(('a' if name=='astrbot' else 'b')*64 for name in services)
        service='astrbot' if args[-1]=='a'*64 else 'napcat'
        return json.dumps([{'Image':controller.IMAGE if service=='astrbot' else deploy.NAPCAT_IMAGE,
            'Config':{'Labels':{'com.docker.compose.project':'qq-ai-companion','com.docker.compose.service':service}},
            'State':{'Running':service in self.running,'Status':'running' if service in self.running else 'exited'},
            'Mounts':[{'Type':'bind','Source':str(self.data),'Destination':'/AstrBot/data'}] if service=='astrbot' else []}])

    def invoke(self,args,fail_overlay=False):
        owner=self
        class Services:
            def __init__(self,_dep):pass
            def running(self):return set(owner.running)
            def stop(self):owner.order.append('stop');owner.running.clear()
            def start(self,items):owner.order.append('start');owner.running.update(items)
        def offline(_cmd,path):
            self.assertFalse(self.running);self.order.append('worker')
            task=SimpleNamespace(manifest=path/'manifest.json',approval=path/'approval.json',
                native_config=path/'core-config.json',native_source=self.upstream,native_data=path/'native',
                ledger=path/'ledger.sqlite3',candidate_root=path,apply=True)
            return asyncio.run(worker.execute(task))
        original_install=installer.install
        def install(*a,**kw):
            self.assertFalse(self.running);self.order.append('overlay')
            if fail_overlay:raise ValueError('synthetic_overlay_failure')
            return original_install(*a,**kw,check_stopped=lambda _dep:self.assertFalse(self.running),owner=(os.geteuid(),os.getegid()))
        modules={'phase3-profile-production.py':controller,'phase3-install-overlay.py':installer,
                 'phase2-backup.py':backup,'prepare_plugins.py':prepare}
        old_imagecheck=deploy.require_locked_images
        with patch.object(deploy,'ROOT',args.source),patch.object(deploy,'_module',lambda _name,path:modules[path.name]), \
             patch.object(backup,'ComposeServices',Services),patch.object(ProductionWindow,'_run',self.runner), \
             patch.object(deploy,'require_locked_images',lambda dep,ctrl:old_imagecheck(dep,ctrl,runner=self.runner)), \
             patch.object(installer,'install',install),patch.object(installer,'verify_stopped',lambda _dep:self.assertFalse(self.running)):
            return deploy.run(args,run_worker=offline)

    async def test_complete_release_order_and_only_targeted_state(self):
        args=self.make_bundle();old_cmd=(self.data/'cmd_config.json').read_bytes()
        result=await asyncio.to_thread(self.invoke,args)
        self.assertEqual(result['status'],'INSTALLED_AWAITING_HEALTH_AND_QQ')
        self.assertEqual(self.order,['stop','stop','worker','overlay','start'])
        self.assertEqual(self.running,{'astrbot','napcat'})
        self.assertEqual((self.data/'cmd_config.json').read_bytes(),old_cmd)
        with closing(sqlite3.connect(self.native/'memory_companion.db')) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM memories WHERE memory_type='core_memory'").fetchone()[0],3)
        self.assertEqual((self.dep/'ntqq/opaque.db').read_bytes(),b'opaque synthetic login state')

    async def test_overlay_failure_restores_pre_resume_database_and_services(self):
        args=self.make_bundle();before=deploy.sha(self.native/'memory_companion.db')
        with self.assertRaisesRegex(ValueError,'synthetic_overlay_failure'):
            await asyncio.to_thread(self.invoke,args,True)
        self.assertEqual(deploy.sha(self.native/'memory_companion.db'),before)
        self.assertEqual(self.running,{'astrbot','napcat'})
        self.assertEqual(self.order[-1],'start')

    async def test_default_plan_and_baseline_drift(self):
        args=self.make_bundle();args.apply=False
        result=await asyncio.to_thread(self.invoke,args)
        self.assertEqual(result['status'],'PLAN');self.assertEqual(self.order,[])
        self.assertFalse((self.dep/'backups').exists())
        (self.data/'cmd_config.json').write_text('{}')
        with self.assertRaisesRegex(MemoryDenied,'changed_since_release_review'):
            await asyncio.to_thread(self.invoke,args)

    async def test_new_post_release_data_is_never_silently_rolled_back(self):
        args=self.make_bundle()
        result=await asyncio.to_thread(self.invoke,args)
        self.running.clear()
        with sqlite3.connect(self.native/'memory_companion.db') as db:
            db.execute("UPDATE memories SET content='发布之后的新合成内容' WHERE source_plugin='external'")
        fresh=deploy.sha(self.native/'memory_companion.db')
        window=ProductionWindow(self.dep,args.run_id,'unused-for-assert-stopped',runner=self.runner)
        with self.assertRaisesRegex(MemoryDenied,'changed_after_install'):
            deploy.rollback_database(window,Path(result['native_db_journal']).parent,controller)
        self.assertEqual(deploy.sha(self.native/'memory_companion.db'),fresh)

    async def test_active_turn_rejected_before_service_stop(self):
        args=self.make_bundle()
        with sqlite3.connect(self.data/'plugin_data/phase1_guard/guard.sqlite3') as db:
            db.execute("INSERT INTO turns VALUES('synthetic-live-turn','generating')")
        with self.assertRaisesRegex(MemoryDenied,'active_private_turn'):
            await asyncio.to_thread(self.invoke,args)
        self.assertEqual(self.order,[]);self.assertEqual(self.running,{'astrbot','napcat'})


if __name__=='__main__':unittest.main()
