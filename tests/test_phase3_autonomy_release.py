"""AstrBot-only autonomy release: real SQLite/ZIP recovery, synthetic services."""
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
spec=importlib.util.spec_from_file_location('autonomy_release_tests',ROOT/'scripts/phase3-deploy-autonomy.py')
d=importlib.util.module_from_spec(spec);sys.modules[spec.name]=d;spec.loader.exec_module(d)
from test_phase3_proactive_recovery_release import DockerFixture


class AutonomyReleaseTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.source=self.root/'source';self.dep=self.root/'deployment'
        self.source.mkdir();self.dep.mkdir();self.state=self.dep/'state'
        (self.state/'data/config').mkdir(parents=True)
        self.cfg={'enabled':True,'persona':True,'natural_chat':True,'memory':True,'search':True,
            'proactive':True,'proactive_window':deepcopy(d.POLICY),'existing_marker':'unchanged'}
        self.save(d.CONFIG,self.cfg)
        self.save('data/config/phase2_policy_config.json',{'admin_id':'10001','platform_id':'test'})
        self.save('data/config/phase1_guard_config.json',{'phase3_admin_id':'10001','phase3_platform_id':'test'})
        self.save('data/cmd_config.json',{'admins_id':['10001'],'platform_settings':{
            'enable_id_white_list':True,'id_whitelist':['test:FriendMessage:10001'],'unique_session':True}})
        self.native={'users':{'10001':{'umo':'test:FriendMessage:10001','user_id':'10001',
            'identity_subject_id':'10001','identity_adapter_instance_id':'test','identity_bot_id':'20002',
            'identity_platform_kind':'qq','proactive_private_enabled':True,
            'unified_profile_capabilities':{'proactive_private_enabled':True},
            'proactive_daily_limit':12,'proactive_min_interval_minutes':-1,
            'bot_self_preferences':[],'retained_chat_marker':'synthetic prior chat'}}}
        self.native_path=d.f.NATIVE_STORE;self.save(self.native_path,self.native)
        (self.dep/'compose.yml').write_text('synthetic fixed compose')
        (self.dep/'.env').write_text('SYNTHETIC_FIXED_IMAGES=1')
        for plugin in d.f.h.PLUGINS:
            p=self.state/'data/plugins'/plugin/'main.py';p.parent.mkdir(parents=True)
            p.write_text('SYNTHETIC_OLD = True\n')
        existing=('data/plugins/phase3_runtime/proactive.py','data/plugins/phase3_runtime/settings.py',
                  'data/plugins/phase3_runtime/_conf_schema.json','data/plugins/phase3_search/policy.py')
        for rel in existing:
            p=self.state/rel;p.write_text('{}' if p.suffix=='.json' else 'SYNTHETIC_OLD = True\n');p.chmod(0o640)
        template=self.state/d.TEMPLATE;template.parent.mkdir(parents=True);template.write_text('Synthetic existing V0.4 persona')
        self.guard=self.state/'data/plugin_data/phase1_guard/guard.sqlite3';self.guard.parent.mkdir(parents=True)
        with sqlite3.connect(self.guard) as db:db.executescript('CREATE TABLE turns(state TEXT); CREATE TABLE deliveries(state TEXT); CREATE TABLE outbound(state TEXT);')
        self.proactive=self.state/'data/plugin_data/phase3_runtime/proactive-controls.sqlite3';self.proactive.parent.mkdir(parents=True)
        with sqlite3.connect(self.proactive) as db:
            db.executescript('CREATE TABLE proactive_generations(state TEXT); CREATE TABLE proactive_attempts(state TEXT);')
            db.execute("INSERT INTO proactive_generations VALUES ('skipped')")
            db.execute("INSERT INTO proactive_attempts VALUES ('sent')")
        self.memory=self.state/'data/plugin_data/memory.db'
        with sqlite3.connect(self.memory) as db:
            db.execute('CREATE TABLE messages(text TEXT)');db.execute("INSERT INTO messages VALUES ('synthetic existing chat')")
        for rel in set(d.PINNED)|{d.CONTROLLER}|set(d.TARGETS.values()):
            p=self.source/rel;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/rel,p)
        self.baseline={rel:d.sha(self.state/rel) for rel in d.f.h.source_inventory(self.state)}
        self.cfg_baseline={p.relative_to(self.state).as_posix():d.sha(p) for p in (self.state/'data/config').glob('*')}
        self.cfg_baseline['data/cmd_config.json']=d.sha(self.state/'data/cmd_config.json')
        self.manifest={'schema':d.SCHEMA,'base_release':d.BASE,
            'files':{rel:d.sha(self.source/rel) for rel in set(d.PINNED)|{d.CONTROLLER}|set(d.TARGETS.values())},
            'baseline_source_files':self.baseline,'protected_config_files':self.cfg_baseline,
            'baseline_template_sha256':d.sha(template),'compose_sha256':d.sha(self.dep/'compose.yml'),
            'env_sha256':d.sha(self.dep/'.env')}
        self.save_manifest();self.docker=DockerFixture(self.dep);self.services=d.Services(self.dep,self.docker)
        self.args=SimpleNamespace(source=self.source,deployment=self.dep,run_id='install',rollback=False,apply=True)
        p=patch.object(d,'verify_native');p.start();self.addCleanup(p.stop)

    def save(self,rel,value):
        p=self.state/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value));p.chmod(0o600)
    def read(self,rel):return json.loads((self.state/rel).read_text())
    def save_manifest(self):(self.source/'source-manifest.json').write_text(json.dumps(self.manifest))
    def execute(self):return d.execute(self.args,services=self.services)
    def rollback(self):
        self.args.rollback=True;self.args.run_id='rollback';return self.execute()
    def assert_nap(self):
        self.assertTrue(self.docker.rows['napcat']['running']);self.assertEqual(self.docker.rows['napcat']['started_at'],'napcat-original')
        self.assertFalse(any(c[1] in ('start','stop') and c[-1]=='b'*64 for c in self.docker.calls))
    def assert_baseline(self):
        self.assertEqual(d.f.h.source_inventory(self.state),set(self.baseline))
        for p,h in self.baseline.items():self.assertEqual(d.sha(self.state/p),h)
    def loader(self,transform):
        original=d.load
        def load(name,path):
            result=original(name,path);transform(name,result);return result
        return patch.object(d,'load',side_effect=load)

    def test_plan_readonly_exact_scope_and_frequency_preserved(self):
        self.args.apply=False;result=self.execute()
        self.assertEqual(result['status'],'PLAN');self.assertEqual(set(result['target_files']),set(d.TARGETS))
        self.assertFalse(result['frequency_policy_modified']);self.assertFalse(result['native_store_modified'])
        self.assertEqual(self.read(d.CONFIG),self.cfg);self.assertEqual(self.read(self.native_path),self.native)
        self.assertFalse((self.source/'release-journal').exists());self.assert_nap()

    def test_install_backup_restore_and_only_owned_sources_and_subtree_change(self):
        original_native=(self.state/self.native_path).read_bytes();memory=d.sha(self.memory);proactive=d.sha(self.proactive)
        result=self.execute();self.assertEqual(result['status'],'INSTALLED_AWAITING_HEALTH',result)
        self.assertEqual(result['sqlite_checked_count'],3)
        self.assertEqual(result['backup_integrity'],'PASS');self.assertEqual(result['restore_integrity'],'PASS')
        self.assertEqual((self.state/self.native_path).read_bytes(),original_native)
        self.assertEqual(d.sha(self.memory),memory);self.assertEqual(d.sha(self.proactive),proactive)
        after=self.read(d.CONFIG);self.assertEqual(after.pop('proactive_autonomy')['enabled'],True)
        self.assertEqual(after,self.cfg)
        for rel,candidate in d.TARGETS.items():self.assertEqual(d.sha(self.state/rel),d.sha(self.source/candidate))
        self.assert_nap()

    def test_rollback_preserves_new_chat_research_counters_native_preferences_and_unowned_fields(self):
        self.assertEqual(self.execute()['status'],'INSTALLED_AWAITING_HEALTH')
        with sqlite3.connect(self.memory) as db:db.execute("INSERT INTO messages VALUES ('synthetic new chat')")
        with sqlite3.connect(self.proactive) as db:
            db.execute('CREATE TABLE autonomy_pocket(id TEXT,content TEXT)');db.execute("INSERT INTO autonomy_pocket VALUES ('new','unshared new research')")
            db.execute("INSERT INTO proactive_attempts VALUES ('failed')")
        current=self.read(self.native_path);current['users']['10001']['bot_self_preferences']=[{'statement':'Synthetic new bot preference'}]
        self.save(self.native_path,current);cfg=self.read(d.CONFIG);cfg['future_unrelated_field']=123;self.save(d.CONFIG,cfg)
        result=self.rollback();self.assertEqual(result['status'],'ROLLED_BACK_AWAITING_HEALTH',result)
        self.assert_baseline();self.assert_nap()
        expected={**self.cfg,'future_unrelated_field':123};self.assertEqual(self.read(d.CONFIG),expected)
        self.assertEqual(self.read(self.native_path),current)
        with sqlite3.connect(self.memory) as db:self.assertEqual(db.execute('SELECT count(*) FROM messages').fetchone()[0],2)
        with sqlite3.connect(self.proactive) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM autonomy_pocket').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM proactive_attempts').fetchone()[0],2)

    def test_existing_frequency_other_than_approved_is_rejected_without_overwrite(self):
        self.cfg['proactive_window']['cooldown']=7200;self.save(d.CONFIG,self.cfg)
        self.manifest['protected_config_files'][d.CONFIG]=d.sha(self.state/d.CONFIG);self.save_manifest()
        with self.assertRaisesRegex(d.ReleaseError,'live_90_96_12'):self.execute()
        self.assert_nap();self.assert_baseline()

    def test_identity_or_other_active_target_rejected(self):
        current=self.read(self.native_path);current['users']['other']={'proactive_private_enabled':True}
        self.save(self.native_path,current)
        with self.assertRaises(d.ReleaseError):self.execute()
        self.assert_baseline()

    def test_protected_config_and_source_drift_are_rejected(self):
        (self.state/'data/plugins/phase1_guard/main.py').write_text('unreviewed change')
        with self.assertRaisesRegex(d.ReleaseError,'source_baseline_changed'):self.execute()
        self.assert_nap()

    def test_new_target_collision_rejected_before_backup(self):
        new=next(p for p in d.TARGETS if p not in self.baseline);(self.state/new).write_text('parallel work')
        with self.assertRaisesRegex(d.ReleaseError,'source_inventory_changed'):self.execute()
        self.assertFalse((self.dep/'backups/install').exists())

    def test_active_autonomy_work_blocks_mutation(self):
        with sqlite3.connect(self.proactive) as db:
            db.execute('CREATE TABLE autonomy_calls(state TEXT)');db.execute("INSERT INTO autonomy_calls VALUES ('running')")
        with self.assertRaisesRegex(d.ReleaseError,'active_autonomy'):self.execute()
        self.assert_baseline()

    def test_backup_failure_recovers_service_without_publication(self):
        def fail(name,module):
            if name=='autonomy_release_backup':module.create_backup=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('synthetic backup failure'))
        with self.loader(fail):result=self.execute()
        self.assertEqual(result['status'],'FAILED');self.assert_baseline();self.assert_nap()
        self.assertTrue(result['astrbot_running']);self.assertFalse((self.source/'release-journal').exists())

    def test_failure_after_each_source_or_config_publication_rolls_back_narrowly(self):
        for boundary in (1,len(d.TARGETS)//2,len(d.TARGETS),len(d.TARGETS)+1):
            with self.subTest(boundary=boundary):
                if (self.source/'release-journal').exists():shutil.rmtree(self.source/'release-journal')
                self.args.run_id='failure-'+str(boundary);counter=[0]
                def fail(name,module):
                    if name=='autonomy_release_installer':
                        original=module.publish
                        def publish(*args,**kwargs):
                            original(*args,**kwargs);counter[0]+=1
                            if counter[0]==boundary:raise RuntimeError('synthetic publication boundary')
                        module.publish=publish
                with self.loader(fail):result=self.execute()
                self.assertEqual(result['status'],'FAILED',result);self.assertTrue(result['known_baseline_recovered'])
                self.assert_baseline();self.assertEqual(self.read(d.CONFIG),self.cfg)
                self.assertEqual(self.read(self.native_path),self.native);self.assert_nap()

    def test_restart_failure_restores_owned_fields_and_resumes_baseline(self):
        self.docker.fail_starts=1;result=self.execute()
        self.assertEqual(result['status'],'FAILED');self.assertTrue(result['known_baseline_recovered'])
        self.assertTrue(result['astrbot_running']);self.assert_baseline();self.assert_nap()

    def test_rollback_refuses_later_owned_config_drift(self):
        self.execute();cfg=self.read(d.CONFIG);cfg['proactive_autonomy']['model_requests_per_day']=100;self.save(d.CONFIG,cfg)
        with self.assertRaisesRegex(d.ReleaseError,'owned_configuration_changed'):self.rollback()
        self.assertEqual(self.read(d.CONFIG)['proactive_autonomy']['model_requests_per_day'],100)
        self.assert_nap()

    def test_rollback_refuses_later_owned_source_drift(self):
        self.execute();target=self.state/next(iter(d.TARGETS));target.write_text('newer independent code')
        with self.assertRaisesRegex(d.ReleaseError,'source_baseline_changed'):self.rollback()
        self.assertEqual(target.read_text(),'newer independent code');self.assert_nap()

    def test_corrupt_candidate_is_detected_before_any_source_write(self):
        original=d.prepare_journal
        def corrupt(*args):
            journal=original(*args);(journal/'candidate'/d.CONFIG).write_text('{}');return journal
        with patch.object(d,'prepare_journal',side_effect=corrupt):result=self.execute()
        self.assertEqual(result['status'],'FAILED');self.assert_baseline();self.assert_nap()
        self.assertEqual(self.read(d.CONFIG),self.cfg)

    def test_safe_default_run_id_and_package_set(self):
        self.manifest['files']['unapproved.py']='0'*64;self.save_manifest()
        with self.assertRaisesRegex(d.ReleaseError,'exact_autonomy_package'):self.execute()
        self.manifest['files'].pop('unapproved.py');self.save_manifest();self.args.run_id='../escape'
        with self.assertRaisesRegex(d.ReleaseError,'invalid_run_id'):self.execute()


if __name__=='__main__':unittest.main()
