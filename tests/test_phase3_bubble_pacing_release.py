"""Narrow pacing release with real SQLite/ZIP snapshots and synthetic services."""
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

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bubble_pacing_release_tests', ROOT/'scripts/phase3-deploy-bubble-pacing.py')
d = importlib.util.module_from_spec(spec); sys.modules[spec.name] = d; spec.loader.exec_module(d)
from test_phase3_proactive_recovery_release import DockerFixture


class BubblePacingReleaseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.source = self.root/'source'; self.dep = self.root/'deployment'
        self.source.mkdir(); self.dep.mkdir(); self.state = self.dep/'state'
        self.cfg = {'phase3_admin_id':'10001','phase3_platform_id':'test',**d.OLD,
                    'phase3_casual_split_fallback_enabled':True,'other':{'retained':True}}
        self.save(d.CONFIG, self.cfg)
        self.runtime = {'enabled':True,'proactive':True,'proactive_window':{'cooldown':5400,
            'max_per_day':12,'max_generations_per_day':96,'quiet_hours':[23,9]},
            'proactive_autonomy':{'enabled':True,'model_requests_per_day':144}}
        self.save('data/config/phase3_runtime_config.json', self.runtime)
        self.save('data/config/phase2_policy_config.json', {'admin_id':'10001','platform_id':'test'})
        self.save('data/cmd_config.json', {'admins_id':['10001'],'platform_settings':{
            'enable_id_white_list':True,'id_whitelist':['test:FriendMessage:10001'],'unique_session':True}})
        self.native = {'users':{'10001':{'umo':'test:FriendMessage:10001','user_id':'10001',
            'identity_subject_id':'10001','identity_adapter_instance_id':'test','identity_bot_id':'20002',
            'identity_platform_kind':'qq','proactive_private_enabled':True,
            'unified_profile_capabilities':{'proactive_private_enabled':True},
            'proactive_daily_limit':12,'proactive_min_interval_minutes':-1,
            'bot_self_preferences':[]}}}
        self.native_path = d.engine.f.NATIVE_STORE; self.save(self.native_path,self.native)
        (self.dep/'compose.yml').write_text('synthetic unchanged compose')
        (self.dep/'.env').write_text('SYNTHETIC_FIXED_IMAGES=1')
        for plugin in d.engine.f.h.PLUGINS:
            p = self.state/'data/plugins'/plugin/'main.py'; p.parent.mkdir(parents=True)
            p.write_text('SYNTHETIC_BASELINE = True\n')
        for rel in d.TARGETS:
            p = self.state/rel; p.write_text('{}' if p.suffix=='.json' else 'SYNTHETIC_BASELINE = True\n')
        # Existing deployed autonomy files must remain untouched.
        p=self.state/'data/plugins/phase3_runtime/autonomy.py';p.write_text('SYNTHETIC_AUTONOMY = True\n')
        template=self.state/d.engine.TEMPLATE;template.parent.mkdir(parents=True);template.write_text('Synthetic V0.4')
        self.guard=self.state/'data/plugin_data/phase1_guard/guard.sqlite3';self.guard.parent.mkdir(parents=True)
        with sqlite3.connect(self.guard) as db:
            db.executescript('CREATE TABLE turns(state TEXT); CREATE TABLE deliveries(state TEXT); CREATE TABLE outbound(state TEXT);')
        self.proactive=self.state/'data/plugin_data/phase3_runtime/proactive-controls.sqlite3';self.proactive.parent.mkdir(parents=True)
        with sqlite3.connect(self.proactive) as db:
            db.executescript('CREATE TABLE proactive_generations(state TEXT); CREATE TABLE proactive_attempts(state TEXT); CREATE TABLE autonomy_calls(state TEXT); CREATE TABLE autonomy_evaluations(state TEXT); CREATE TABLE autonomy_pocket(content TEXT);')
            db.execute("INSERT INTO proactive_attempts VALUES ('sent')")
        self.memory=self.state/'data/plugin_data/memory.db'
        with sqlite3.connect(self.memory) as db:
            db.execute('CREATE TABLE messages(text TEXT)');db.execute("INSERT INTO messages VALUES ('existing synthetic chat')")
        for rel in set(d.PINNED)|{d.CONTROLLER}|set(d.TARGETS.values()):
            p=self.source/rel;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/rel,p)
        self.baseline={rel:d.sha(self.state/rel) for rel in d.engine.f.h.source_inventory(self.state)}
        self.manifest={'schema':d.SCHEMA,'base_release':d.BASE,
            'files':{rel:d.sha(self.source/rel) for rel in set(d.PINNED)|{d.CONTROLLER}|set(d.TARGETS.values())},
            'baseline_source_files':self.baseline,
            'protected_config_files':{p.relative_to(self.state).as_posix():d.sha(p)
                                     for p in (self.state/'data/config').glob('*')},
            'baseline_template_sha256':d.sha(template),'compose_sha256':d.sha(self.dep/'compose.yml'),
            'env_sha256':d.sha(self.dep/'.env')}
        self.manifest['protected_config_files']['data/cmd_config.json']=d.sha(self.state/'data/cmd_config.json')
        self.save_manifest();self.docker=DockerFixture(self.dep);self.services=d.Services(self.dep,self.docker)
        self.args=SimpleNamespace(source=self.source,deployment=self.dep,run_id='install',rollback=False,apply=True)
        p=patch.object(d.engine,'verify_native');p.start();self.addCleanup(p.stop)

    def save(self,rel,value):
        p=self.state/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value));p.chmod(0o600)
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
        for rel,h in self.baseline.items():self.assertEqual(d.sha(self.state/rel),h)
    def loader(self,transform):
        original=d.engine.load
        def load(name,path):
            result=original(name,path);transform(name,result);return result
        return patch.object(d.engine,'load',side_effect=load)

    def test_plan_is_readonly_and_scope_exact(self):
        self.args.apply=False;result=self.execute()
        self.assertEqual(result['status'],'PLAN');self.assertEqual(result['operation'],'install_bubble_pacing')
        self.assertEqual(len(result['target_files']),3);self.assertEqual(result['config_files'],[d.CONFIG])
        self.assertFalse(result['runtime_configuration_modified']);self.assertFalse(result['frequency_policy_modified'])
        self.assertFalse((self.source/'release-journal').exists());self.assert_baseline();self.assert_nap()

    def test_install_two_leaves_only_and_consistent_restore_proof(self):
        hashes={p:d.sha(p) for p in (self.guard,self.proactive,self.memory,self.state/self.native_path)}
        result=self.execute();self.assertEqual(result['status'],'INSTALLED_AWAITING_HEALTH',result)
        self.assertEqual(result['sqlite_checked_count'],3)
        self.assertEqual(result['backup_integrity'],'PASS');self.assertEqual(result['restore_integrity'],'PASS')
        self.assertEqual(self.read(d.CONFIG),{**self.cfg,**d.NEW})
        self.assertEqual(self.read('data/config/phase3_runtime_config.json'),self.runtime)
        for p,h in hashes.items():self.assertEqual(d.sha(p),h)
        j=d.read(self.source/'release-journal/journal.json')
        self.assertEqual({tuple(c['path']) for c in j['configs'][0]['changes']},{(k,) for k in d.OLD})
        self.assertEqual(j['schema'],d.SCHEMA);self.assert_nap()
        saved=d.read(self.source/'install-result.json');self.assertEqual(saved['operation'],'install_bubble_pacing')
        for p,h in self.baseline.items():
            self.assertEqual(d.sha(self.state/p),d.sha(self.source/d.TARGETS[p]) if p in d.TARGETS else h)

    def test_rollback_preserves_new_chat_research_counters_native_preferences_and_other_guard_fields(self):
        self.assertEqual(self.execute()['status'],'INSTALLED_AWAITING_HEALTH')
        with sqlite3.connect(self.memory) as db:db.execute("INSERT INTO messages VALUES ('new synthetic chat')")
        with sqlite3.connect(self.proactive) as db:
            db.execute("INSERT INTO autonomy_pocket VALUES ('new research')")
            db.execute("INSERT INTO proactive_attempts VALUES ('failed')")
        current=deepcopy(self.native);current['users']['10001']['bot_self_preferences']=['new preference'];self.save(self.native_path,current)
        cfg=self.read(d.CONFIG);cfg['other']['new_value']=123;self.save(d.CONFIG,cfg)
        result=self.rollback();self.assertEqual(result['status'],'ROLLED_BACK_AWAITING_HEALTH',result)
        self.assertEqual(result['operation'],'rollback_bubble_pacing')
        self.assertEqual(self.read(d.CONFIG),{**self.cfg,'other':{'retained':True,'new_value':123}})
        self.assertEqual(self.read(self.native_path),current);self.assert_baseline();self.assert_nap()
        with sqlite3.connect(self.memory) as db:self.assertEqual(db.execute('SELECT count(*) FROM messages').fetchone()[0],2)
        with sqlite3.connect(self.proactive) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM autonomy_pocket').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM proactive_attempts').fetchone()[0],2)

    def test_old_values_required_numeric_but_not_boolean(self):
        for value in (True,None,0,2,'1'):
            with self.subTest(value=value):
                cfg={**self.cfg,'phase3_bubble_min_interval':value}
                with self.assertRaisesRegex(d.ReleaseError,'reviewed_guard'):d.config_patch(cfg,self.source)
        self.assertEqual(d.config_patch({**self.cfg,**{k:int(v) for k,v in d.OLD.items()}},self.source),{**self.cfg,**d.NEW})

    def test_bom_is_preserved_and_rollback_original_exact_bytes(self):
        p=self.state/d.CONFIG;p.write_bytes(codecs.BOM_UTF8+p.read_bytes())
        original=p.read_bytes();self.manifest['protected_config_files'][d.CONFIG]=d.sha(p);self.save_manifest()
        self.assertEqual(self.execute()['status'],'INSTALLED_AWAITING_HEALTH')
        self.assertTrue(p.read_bytes().startswith(codecs.BOM_UTF8))
        self.assertEqual(self.rollback()['status'],'ROLLED_BACK_AWAITING_HEALTH');self.assertEqual(p.read_bytes(),original)

    def test_active_autonomy_blocks_before_stop(self):
        with sqlite3.connect(self.proactive) as db:db.execute("INSERT INTO autonomy_evaluations VALUES ('running')")
        with self.assertRaisesRegex(d.ReleaseError,'active_autonomy'):self.execute()
        self.assertFalse((self.dep/'backups/install').exists());self.assert_baseline();self.assert_nap()

    def test_unowned_runtime_or_source_drift_blocks(self):
        for rel in ('data/config/phase3_runtime_config.json','data/plugins/phase3_runtime/autonomy.py'):
            p=self.state/rel;raw=p.read_bytes();p.write_bytes(raw+b' ')
            with self.subTest(rel=rel),self.assertRaises(d.ReleaseError):self.execute()
            p.write_bytes(raw)
        self.assert_nap()

    def test_unlisted_package_file_and_missing_existing_guard_are_rejected(self):
        p=self.source/'integrations/phase1_guard/unreviewed.py';p.write_text('unexpected')
        with self.assertRaisesRegex(d.ReleaseError,'exact_bubble_pacing'):self.execute()
        p.unlink();self.manifest['baseline_source_files'].pop(next(iter(d.TARGETS)));self.save_manifest()
        with self.assertRaisesRegex(d.ReleaseError,'existing_three_guard'):self.execute()
        self.assert_nap()

    def test_package_tampering_rejected(self):
        p=self.source/next(iter(d.TARGETS.values()));p.write_bytes(p.read_bytes()+b'\n')
        with self.assertRaisesRegex(d.ReleaseError,'package_hash_changed'):self.execute()
        self.assert_nap()

    def test_backup_failure_starts_original_without_publication(self):
        def fail(name,module):
            if name=='autonomy_release_backup':module.create_backup=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('synthetic failure'))
        with self.loader(fail):result=self.execute()
        self.assertEqual(result['status'],'FAILED');self.assertTrue(result['astrbot_running'])
        self.assertFalse((self.source/'release-journal').exists());self.assert_baseline();self.assert_nap()

    def test_each_publication_boundary_restores_narrowly(self):
        for boundary in range(1,5):
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

    def test_restart_failure_resumes_original(self):
        self.docker.fail_starts=1;result=self.execute()
        self.assertEqual(result['status'],'FAILED');self.assertTrue(result['known_baseline_recovered'])
        self.assertTrue(result['astrbot_running']);self.assert_baseline();self.assert_nap()

    def test_owned_config_drift_blocks_rollback(self):
        self.execute();cfg=self.read(d.CONFIG);cfg['phase3_bubble_min_interval']=3;self.save(d.CONFIG,cfg)
        with self.assertRaisesRegex(d.ReleaseError,'owned_configuration_changed'):self.rollback()
        self.assertEqual(self.read(d.CONFIG)['phase3_bubble_min_interval'],3);self.assert_nap()

    def test_owned_source_drift_blocks_rollback(self):
        self.execute();p=self.state/next(iter(d.TARGETS));p.write_text('newer code')
        with self.assertRaisesRegex(d.ReleaseError,'source_baseline_changed'):self.rollback()
        self.assertEqual(p.read_text(),'newer code');self.assert_nap()

    def test_corrupt_config_candidate_never_publishes(self):
        original=d.engine.prepare_journal
        def corrupt(*args):
            journal=original(*args);(journal/'candidate'/d.CONFIG).write_text('{}');return journal
        with patch.object(d.engine,'prepare_journal',side_effect=corrupt):result=self.execute()
        self.assertEqual(result['status'],'FAILED');self.assertEqual(self.read(d.CONFIG),self.cfg)
        self.assert_baseline();self.assert_nap()


if __name__=='__main__':unittest.main()
