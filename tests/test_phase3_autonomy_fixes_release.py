"""Source-only autonomy fixes: real SQLite/ZIP recovery, synthetic services."""
import ast
import codecs
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('autonomy_fixes_release_tests',ROOT/'scripts/phase3-deploy-autonomy-fixes.py')
d=importlib.util.module_from_spec(spec);sys.modules[spec.name]=d;spec.loader.exec_module(d)
from test_phase3_proactive_recovery_release import DockerFixture


class AutonomyFixesReleaseTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.source=self.root/'source';self.dep=self.root/'deployment'
        self.source.mkdir();self.dep.mkdir();self.state=self.dep/'state'
        self.cfg={'enabled':True,'persona':True,'natural_chat':True,'memory':True,'search':True,'proactive':True,
            'proactive_window':deepcopy(d.POLICY),'proactive_autonomy':{'enabled':True,**d.AUTONOMY_LIMITS},
            'retained':{'custom':'synthetic existing value'}}
        self.save(d.CONFIG,self.cfg)
        self.save(d.GUARD_CONFIG,{'phase3_admin_id':'10001','phase3_platform_id':'test',
            'phase3_bubble_min_interval':2.0,'phase3_bubble_max_interval':4.0})
        self.save('data/config/phase2_policy_config.json',{'admin_id':'10001','platform_id':'test'})
        self.save('data/cmd_config.json',{'admins_id':['10001'],'platform_settings':{
            'enable_id_white_list':True,'id_whitelist':['test:FriendMessage:10001'],'unique_session':True}})
        self.native={'users':{'10001':{'umo':'test:FriendMessage:10001','user_id':'10001',
            'identity_subject_id':'10001','identity_adapter_instance_id':'test','identity_bot_id':'20002',
            'identity_platform_kind':'qq','proactive_private_enabled':True,
            'unified_profile_capabilities':{'proactive_private_enabled':True},
            'proactive_daily_limit':12,'proactive_min_interval_minutes':-1,'bot_self_preferences':[]}}}
        self.native_path=d.engine.f.NATIVE_STORE;self.save(self.native_path,self.native)
        (self.dep/'compose.yml').write_text('synthetic fixed compose')
        (self.dep/'.env').write_text('SYNTHETIC_FIXED_IMAGES=1')
        for plugin in d.engine.f.h.PLUGINS:
            p=self.state/'data/plugins'/plugin/'main.py';p.parent.mkdir(parents=True)
            p.write_text('SYNTHETIC_EXISTING = True\n')
        for rel in set(d.TARGETS)-d.NEW_TARGETS:
            (self.state/rel).write_text('SYNTHETIC_OLD_REPAIR_TARGET = True\n')
        template=self.state/d.engine.TEMPLATE;template.parent.mkdir(parents=True)
        template.write_text('# QQ_COMPANION_PERSONA_V0_4\nSynthetic unchanged persona')
        self.guard=self.state/'data/plugin_data/phase1_guard/guard.sqlite3';self.guard.parent.mkdir(parents=True)
        with sqlite3.connect(self.guard) as db:
            db.executescript('CREATE TABLE turns(state TEXT); CREATE TABLE deliveries(state TEXT); CREATE TABLE outbound(state TEXT);')
        self.proactive=self.state/'data/plugin_data/phase3_runtime/proactive-controls.sqlite3';self.proactive.parent.mkdir(parents=True)
        with sqlite3.connect(self.proactive) as db:
            db.executescript('CREATE TABLE proactive_generations(state TEXT); CREATE TABLE proactive_attempts(state TEXT); CREATE TABLE autonomy_calls(state TEXT); CREATE TABLE autonomy_evaluations(state TEXT); CREATE TABLE autonomy_pocket(content TEXT);')
            db.execute("INSERT INTO proactive_attempts VALUES ('sent')")
        self.memory=self.state/'data/plugin_data/memory.db'
        with sqlite3.connect(self.memory) as db:
            db.execute('CREATE TABLE messages(text TEXT)');db.execute("INSERT INTO messages VALUES ('synthetic existing chat')")
        for rel in set(d.PINNED)|{d.CONTROLLER}|set(d.TARGETS.values()):
            p=self.source/rel;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/rel,p)
        self.baseline={rel:d.sha(self.state/rel) for rel in d.engine.f.h.source_inventory(self.state)}
        self.manifest={'schema':d.SCHEMA,'base_release':d.BASE,
            'files':{rel:d.sha(self.source/rel) for rel in set(d.PINNED)|{d.CONTROLLER}|set(d.TARGETS.values())},
            'baseline_source_files':self.baseline,'protected_config_files':{p.relative_to(self.state).as_posix():d.sha(p)
                for p in (self.state/'data/config').glob('*')},'baseline_template_sha256':d.sha(template),
            'compose_sha256':d.sha(self.dep/'compose.yml'),'env_sha256':d.sha(self.dep/'.env')}
        self.manifest['protected_config_files']['data/cmd_config.json']=d.sha(self.state/'data/cmd_config.json')
        self.save_manifest();self.docker=DockerFixture(self.dep);self.services=d.Services(self.dep,self.docker)
        self.args=SimpleNamespace(source=self.source,deployment=self.dep,run_id='install',rollback=False,apply=True)
        p=patch.object(d.engine,'verify_native');p.start();self.addCleanup(p.stop)

    def save(self,rel,value):
        p=self.state/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value,separators=(',',':')));p.chmod(0o600)
    def read(self,rel):return json.loads((self.state/rel).read_text(encoding='utf-8-sig'))
    def save_manifest(self):(self.source/'source-manifest.json').write_text(json.dumps(self.manifest))
    def execute(self):return d.execute(self.args,services=self.services)
    def rollback(self):self.args.rollback=True;self.args.run_id='rollback';return self.execute()
    def assert_nap(self):
        self.assertTrue(self.docker.rows['napcat']['running'])
        self.assertEqual(self.docker.rows['napcat']['started_at'],'napcat-original')
        self.assertFalse(any(c[1] in ('start','stop') and c[-1]=='b'*64 for c in self.docker.calls))
    def assert_baseline(self):
        self.assertEqual(d.engine.f.h.source_inventory(self.state),set(self.baseline))
        for p,h in self.baseline.items():self.assertEqual(d.sha(self.state/p),h)
    def loader(self,transform):
        original=d.engine.load
        def load(name,path):
            result=original(name,path);transform(name,result);return result
        return patch.object(d.engine,'load',side_effect=load)

    def test_plan_exact_thirteen_sources_zero_config_and_no_side_effects(self):
        self.args.apply=False;result=self.execute()
        self.assertEqual(result['status'],'PLAN');self.assertEqual(result['operation'],'install_autonomy_fixes')
        self.assertEqual(len(result['target_files']),13);self.assertEqual(result['config_files'],[])
        self.assertFalse(result['configuration_modified']);self.assertFalse(result['sqlite_write'])
        self.assertFalse((self.source/'release-journal').exists());self.assert_baseline();self.assert_nap()

    def test_install_and_rollback_never_publish_config_and_preserve_bytes(self):
        p=self.state/d.CONFIG;p.write_bytes(codecs.BOM_UTF8+p.read_bytes()+b' \n')
        original=p.read_bytes();self.manifest['protected_config_files'][d.CONFIG]=d.sha(p);self.save_manifest()
        hashes={p:d.sha(p) for p in (self.guard,self.proactive,self.memory,self.state/self.native_path)}
        published=[];publish=d.engine.f.publish_file
        def observe(installer,src,target,row):
            self.assertNotIn('config',target.parts);published.append(target);return publish(installer,src,target,row)
        with patch.object(d.engine.f,'publish_file',side_effect=observe):
            result=self.execute();self.assertEqual(result['status'],'INSTALLED_AWAITING_HEALTH',result)
            self.assertEqual(len(published),13)
            self.assertEqual(self.rollback()['status'],'ROLLED_BACK_AWAITING_HEALTH')
        self.assertEqual(result['backup_integrity'],'PASS');self.assertEqual(result['restore_integrity'],'PASS')
        self.assertEqual(result['sqlite_checked_count'],3);self.assertEqual(p.read_bytes(),original)
        j=d.read(self.source/'release-journal/journal.json')
        self.assertEqual(j['configs'][0]['changes'],[])
        self.assertEqual(j['configs'][0]['old_sha256'],j['configs'][0]['new_sha256'])
        for path,h in hashes.items():self.assertEqual(d.sha(path),h)
        self.assert_baseline();self.assert_nap()

    def test_rollback_preserves_new_chat_schema_counters_preferences_and_config(self):
        self.assertEqual(self.execute()['status'],'INSTALLED_AWAITING_HEALTH')
        with sqlite3.connect(self.memory) as db:db.execute("INSERT INTO messages VALUES ('new synthetic chat')")
        with sqlite3.connect(self.proactive) as db:
            db.execute('ALTER TABLE autonomy_pocket ADD COLUMN last_progress REAL')
            db.execute("INSERT INTO autonomy_pocket VALUES ('new research',123)")
            db.execute("INSERT INTO autonomy_calls VALUES ('succeeded')")
            db.execute("INSERT INTO proactive_attempts VALUES ('failed')")
        native=deepcopy(self.native);native['users']['10001']['bot_self_preferences']=['new preference'];self.save(self.native_path,native)
        cfg=self.read(d.CONFIG);cfg['subsequent_non_owned_field']='retain';self.save(d.CONFIG,cfg)
        current=(self.state/d.CONFIG).read_bytes()
        result=self.rollback();self.assertEqual(result['status'],'ROLLED_BACK_AWAITING_HEALTH',result)
        self.assertEqual((self.state/d.CONFIG).read_bytes(),current);self.assertEqual(self.read(self.native_path),native)
        with sqlite3.connect(self.memory) as db:self.assertEqual(db.execute('SELECT count(*) FROM messages').fetchone()[0],2)
        with sqlite3.connect(self.proactive) as db:
            self.assertEqual(db.execute('SELECT content,last_progress FROM autonomy_pocket').fetchone(),('new research',123))
            self.assertEqual(db.execute('SELECT count(*) FROM autonomy_calls').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM proactive_attempts').fetchone()[0],2)
        self.assert_baseline();self.assert_nap()

    def test_policy_autonomy_and_guard_mismatches_are_never_adjusted(self):
        cases=[(d.CONFIG,lambda c:c['proactive_window'].__setitem__('max_per_day',8)),
            (d.CONFIG,lambda c:c['proactive_autonomy'].__setitem__('model_requests_per_day',143)),
            (d.CONFIG,lambda c:c['proactive_autonomy'].__setitem__('enabled',False)),
            (d.GUARD_CONFIG,lambda c:c.__setitem__('phase3_bubble_max_interval',2))]
        for rel,change in cases:
            with self.subTest(rel=rel,change=change):
                old=(self.state/rel).read_bytes();value=self.read(rel);change(value);self.save(rel,value)
                changed=(self.state/rel).read_bytes();self.manifest['protected_config_files'][rel]=d.sha(self.state/rel);self.save_manifest()
                with self.assertRaises(d.ReleaseError):self.execute()
                self.assertEqual((self.state/rel).read_bytes(),changed)
                (self.state/rel).write_bytes(old);self.manifest['protected_config_files'][rel]=d.sha(self.state/rel);self.save_manifest()
        self.assert_baseline();self.assert_nap()

    def test_persona_and_unowned_source_config_are_protected(self):
        for rel in (d.engine.TEMPLATE,'data/plugins/phase1_guard/main.py','data/config/phase2_policy_config.json'):
            p=self.state/rel;raw=p.read_bytes();p.write_bytes(raw+b' ')
            with self.subTest(rel=rel),self.assertRaises(d.ReleaseError):self.execute()
            p.write_bytes(raw)
        self.assert_nap()

    def test_new_module_collision_and_unlisted_package_are_rejected(self):
        rel=next(iter(d.NEW_TARGETS));p=self.state/rel;p.write_text('parallel edit')
        with self.assertRaisesRegex(d.ReleaseError,'source_inventory'):self.execute()
        p.unlink();extra=self.source/'integrations/phase3_runtime/not_reviewed.py';extra.write_text('extra')
        with self.assertRaisesRegex(d.ReleaseError,'exact_autonomy_fixes'):self.execute()
        self.assert_nap()

    def test_new_local_relative_dependencies_are_in_targets_or_protected_baseline(self):
        runtime_files={Path(src).stem for src in d.TARGETS.values() if '/phase3_runtime/' in src}
        for rel in d.TARGETS.values():
            if '/phase3_runtime/' not in rel:continue
            for node in ast.walk(ast.parse((ROOT/rel).read_text())):
                if isinstance(node,ast.ImportFrom) and node.level==1 and node.module and node.module.startswith('autonomy'):
                    self.assertIn(node.module,runtime_files)
        self.assertEqual(len(d.NEW_TARGETS),3)

    def test_active_calculation_or_agent_blocks_stop(self):
        with sqlite3.connect(self.proactive) as db:db.execute("INSERT INTO autonomy_calls VALUES ('running')")
        with self.assertRaisesRegex(d.ReleaseError,'active_autonomy'):self.execute()
        self.assertFalse((self.dep/'backups/install').exists());self.assert_baseline();self.assert_nap()

    def test_backup_failure_does_not_publish_and_resumes_service(self):
        def fail(name,module):
            if name=='autonomy_release_backup':module.create_backup=lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('synthetic'))
        with self.loader(fail):result=self.execute()
        self.assertEqual(result['status'],'FAILED');self.assertTrue(result['astrbot_running'])
        self.assertFalse((self.source/'release-journal').exists());self.assert_baseline();self.assert_nap()

    def test_mid_install_failures_restore_existing_and_remove_only_new_sources(self):
        for boundary in (1,6,8,13):
            with self.subTest(boundary=boundary):
                if (self.source/'release-journal').exists():shutil.rmtree(self.source/'release-journal')
                self.args.run_id='failure-'+str(boundary);counter=[0]
                def fail(name,module):
                    if name=='autonomy_release_installer':
                        original=module.publish
                        def publish(*args,**kwargs):
                            original(*args,**kwargs);counter[0]+=1
                            if counter[0]==boundary:raise RuntimeError('synthetic publication failure')
                        module.publish=publish
                with self.loader(fail):result=self.execute()
                self.assertEqual(result['status'],'FAILED',result);self.assertTrue(result['known_baseline_recovered'])
                self.assertEqual(self.read(d.CONFIG),self.cfg);self.assert_baseline();self.assert_nap()

    def test_restart_failure_recovers_original_source_without_touching_configuration(self):
        original=(self.state/d.CONFIG).read_bytes();self.docker.fail_starts=1;result=self.execute()
        self.assertEqual(result['status'],'FAILED');self.assertTrue(result['astrbot_running'])
        self.assertEqual((self.state/d.CONFIG).read_bytes(),original);self.assert_baseline();self.assert_nap()

    def test_source_drift_blocks_rollback_and_config_candidate_tampering_blocks_install(self):
        prepare=d.engine.prepare_journal
        def corrupt(*args):
            journal=prepare(*args);(journal/'candidate'/d.CONFIG).write_text('{}');return journal
        with patch.object(d.engine,'prepare_journal',side_effect=corrupt):result=self.execute()
        self.assertEqual(result['status'],'FAILED');self.assert_baseline()
        shutil.rmtree(self.source/'release-journal');self.args.run_id='clean-install'
        self.assertEqual(self.execute()['status'],'INSTALLED_AWAITING_HEALTH')
        source=self.state/next(iter(d.TARGETS));source.write_text('newer independent edit')
        with self.assertRaisesRegex(d.ReleaseError,'source_baseline_changed'):self.rollback()
        self.assertEqual(source.read_text(),'newer independent edit');self.assert_nap()

    def test_locked_helpers_are_immutable(self):
        self.assertEqual(d.sha(ROOT/d.HELPER),d.HELPER_SHA)
        candidate=self.source/'scripts/phase3-deploy-autonomy.py';candidate.write_bytes(candidate.read_bytes()+b'\n')
        with self.assertRaisesRegex(d.ReleaseError,'package_hash_changed'):self.execute()
        self.assert_baseline();self.assert_nap()


if __name__=='__main__':unittest.main()
