"""Real Phase1 ZIP/SQLite backup, fake Docker control; no VPS operations."""
import copy
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
spec = importlib.util.spec_from_file_location('runtime_code_release_tests', ROOT/'scripts/phase3-deploy-runtime-code.py')
deploy = importlib.util.module_from_spec(spec);sys.modules[spec.name]=deploy;spec.loader.exec_module(deploy)
VERIFY_NATIVE = deploy.verify_native


class DockerFixture:
    def __init__(self, dep):
        self.calls=[];self.fail_starts=0
        self.rows={}
        for name, token, image in [('astrbot','a',deploy.ASTRBOT_IMAGE),('napcat','b',deploy.NAPCAT_IMAGE)]:
            self.rows[name]={'id':token*64,'image':image,'running':True,'status':'running',
                'started_at':name+'-original-start','restart_count':0,'project':'qq-ai-companion',
                'service':name,'mounts':[{'Source':str(dep/'state/data') if name=='astrbot' else str(dep/'ntqq'),
                                        'Destination':'/AstrBot/data' if name=='astrbot' else '/app/.config/QQ','RW':True}]}
    def __call__(self, argv):
        self.calls.append(list(argv))
        if argv[1]=='compose':return self.rows[argv[-1]]['id']+'\n'
        if argv[1]=='inspect':return json.dumps(next(r for r in self.rows.values() if r['id']==argv[-1]))
        if argv[1] in {'start','stop'}:
            if argv[-1]!=self.rows['astrbot']['id']:raise AssertionError('NapCat action forbidden')
            if argv[1]=='start' and self.fail_starts:
                self.fail_starts-=1;raise deploy.ReleaseError('synthetic_start_failure')
            running=argv[1]=='start';self.rows['astrbot'].update(running=running,status='running' if running else 'exited')
            if running:self.rows['astrbot']['started_at']='astrbot-restarted'
            return argv[-1]
        raise AssertionError('Unexpected Docker operation')


class RuntimeCodeReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'release-source';self.dep=self.root/'deployment'
        self.source.mkdir();self.dep.mkdir();state=self.dep/'state';state.mkdir()
        (state/'data/config').mkdir(parents=True)
        (state/'data/cmd_config.json').write_text('{}')
        (state/'data/config/phase3_runtime_config.json').write_text('{"memory":true}')
        (self.dep/'compose.yml').write_text('synthetic-compose-no-ports')
        (self.dep/'.env').write_text('SYNTHETIC_IMAGE_LOCK=true')
        self.original={}
        for n in range(47):
            rel=(deploy.PREFIX+deploy.TARGETS[n] if n<2 else 'data/plugins/phase1_guard/fixture_'+str(n)+'.py')
            p=state/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('old_synthetic_'+str(n)+' = True\n')
            self.original[rel]=deploy.sha(p)
        self.legacy=state/'data/plugins/phase2_policy/plugin_permissions.py'
        self.legacy.parent.mkdir(parents=True)
        shutil.copy2(ROOT/'scripts/plugin_permissions.py',self.legacy)
        guard=state/'data/plugin_data/phase1_guard/guard.sqlite3';guard.parent.mkdir(parents=True)
        with sqlite3.connect(guard) as db:
            db.executescript('CREATE TABLE turns(state TEXT);CREATE TABLE deliveries(state TEXT);CREATE TABLE outbound(state TEXT);')
        self.memory=state/'data/plugin_data/synthetic/memory.db';self.memory.parent.mkdir()
        with sqlite3.connect(self.memory) as db:
            db.execute('CREATE TABLE messages(text TEXT)');db.execute("INSERT INTO messages VALUES ('synthetic old chat')")
        self.full_backup=self.dep/'backups/previous-full-phase2/sentinel';self.full_backup.parent.mkdir(parents=True)
        self.full_backup.write_text('preserve-original-full-backup')
        for relative in deploy.PINNED:
            p=self.source/relative;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/relative,p)
        script=self.source/'scripts/phase3-deploy-runtime-code.py';shutil.copy2(ROOT/'scripts/phase3-deploy-runtime-code.py',script)
        for name in deploy.TARGETS:
            p=self.source/'integrations/phase3_runtime'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('new_synthetic = True\n# '+name)
        self.receipt=self.root/deploy.BASE_RELEASE/'source/operation.json';self.receipt.parent.mkdir(parents=True)
        self.receipt.write_text(json.dumps({'originally_running_services':['astrbot','napcat'],
            'backup_run_id':deploy.BASE_BACKUP,'restart_owned_by':'experience_controller',
            'production_resumed':True,'candidate_applied':True}))
        source_names=set(deploy.PINNED)|{'scripts/phase3-deploy-runtime-code.py'}|{'integrations/phase3_runtime/'+n for n in deploy.TARGETS}
        self.manifest={'schema':'qq-companion.astrbot-code-release.v1','base_release':deploy.BASE_RELEASE,
            'base_receipt_sha256':deploy.sha(self.receipt),'files':{n:deploy.sha(self.source/n) for n in source_names},
            'baseline_source_files':self.original,
            'protected_config_files':{n:deploy.sha(state/n) for n in ['data/cmd_config.json','data/config/phase3_runtime_config.json']},
            'compose_sha256':deploy.sha(self.dep/'compose.yml'),'env_sha256':deploy.sha(self.dep/'.env')}
        (self.source/'source-manifest.json').write_text(json.dumps(self.manifest))
        self.docker=DockerFixture(self.dep);self.services=deploy.Services(self.dep,self.docker)
        self.args=SimpleNamespace(source=self.source,deployment=self.dep,base_receipt=self.receipt,
            run_id='synthetic-install',rollback=False,apply=True)
        # Controller/fixed helpers/locks/source comparisons/backup are real.
        # The tiny synthetic application has no redistributed upstream trees.
        self.native=patch.object(deploy,'verify_native');self.native.start();self.addCleanup(self.native.stop)

    def execute(self):return deploy.execute(self.args,services=self.services)

    def assert_old_sources(self):
        for name,digest in self.original.items():self.assertEqual(deploy.sha(self.dep/'state'/name),digest)
        for name,digest in deploy.LEGACY_PROTECTED_SOURCE.items():self.assertEqual(deploy.sha(self.dep/'state'/name),digest)
        self.assertFalse((self.dep/'state'/deploy.PREFIX/'profile_publication.py').exists())

    def assert_no_nap_action(self):
        self.assertFalse(any(call[1] in {'start','stop'} and call[-1]==self.docker.rows['napcat']['id'] for call in self.docker.calls))
        self.assertEqual(self.docker.rows['napcat']['started_at'],'napcat-original-start')
        self.assertTrue(self.docker.rows['napcat']['running'])

    def test_plan_is_readonly_and_checks_all_47_baselines(self):
        self.args.apply=False
        self.assertEqual(self.execute()['status'],'PLAN')
        self.assertFalse((self.dep/'.astrbot-runtime-release.lock').exists())
        self.assertFalse((self.source/'astrbot-code-journal').exists())
        self.assertFalse(any(c[1] in {'start','stop'} for c in self.docker.calls))
        (self.dep/'state/data/plugins/phase1_guard/fixture_46.py').write_text('drift')
        with self.assertRaisesRegex(deploy.ReleaseError,'baseline_changed'):self.execute()

    def test_success_uses_real_backup_restore_before_three_sources_and_keeps_nap(self):
        db_hash=deploy.sha(self.memory);result=self.execute()
        self.assertEqual(result['status'],'INSTALLED_AWAITING_HEALTH_AND_QQ')
        self.assertEqual(result['sqlite_checked_count'],2)
        proof=deploy.read(self.dep/'backups/synthetic-install/astrbot-only-result.json')
        self.assertEqual(proof['napcat_snapshot'],'NOT_PERFORMED')
        self.assertEqual(proof['phase2_full_backup'],'NOT_REPLACED')
        restored=self.dep/'backups/synthetic-install/isolated-astrbot-restore'
        for name,digest in self.original.items():self.assertEqual(deploy.sha(restored/name),digest)
        self.assertEqual(deploy.sha(self.memory),db_hash)
        self.assertEqual(len(deploy.read(self.source/'astrbot-code-journal/journal.json')['files']),3)
        self.assertEqual(self.full_backup.read_text(),'preserve-original-full-backup')
        self.assert_no_nap_action()

    def test_pending_or_active_input_rejected_without_stop(self):
        guard=self.dep/'state/data/plugin_data/phase1_guard/guard.sqlite3'
        for table,state in [('turns','generating'),('deliveries','pending'),('outbound','pending')]:
            with sqlite3.connect(guard) as db:db.execute(f'INSERT INTO {table} VALUES (?)',(state,))
            with self.assertRaises(deploy.ReleaseError):self.execute()
            with sqlite3.connect(guard) as db:db.execute(f'DELETE FROM {table}')
        self.assertFalse(any(c[1] in {'start','stop'} for c in self.docker.calls))

    def test_new_file_existing_or_config_drift_rejected(self):
        target=self.dep/'state'/deploy.PREFIX/'profile_publication.py';target.write_text('unexpected')
        with self.assertRaisesRegex(deploy.ReleaseError,'source_inventory_changed'):self.execute()
        target.unlink();(self.dep/'state/data/cmd_config.json').write_text('{"drift":true}')
        with self.assertRaisesRegex(deploy.ReleaseError,'configuration_changed'):self.execute()

    def test_locked_helper_or_source_tampering_rejected(self):
        path=self.source/'scripts/backup.py';path.write_text(path.read_text()+'\n# changed')
        with self.assertRaisesRegex(deploy.ReleaseError,'source_hash_mismatch'):self.execute()

    def test_exact_legacy_source_hash_is_required_and_unknown_files_still_rejected(self):
        self.legacy.write_text(self.legacy.read_text()+'\n# synthetic drift')
        with self.assertRaisesRegex(deploy.ReleaseError,'legacy_protected_source_changed'):self.execute()
        shutil.copy2(ROOT/'scripts/plugin_permissions.py',self.legacy)
        (self.legacy.parent/'unexpected.py').write_text('not_reviewed=True')
        with self.assertRaisesRegex(deploy.ReleaseError,'source_inventory_changed'):self.execute()
        self.assertFalse(any(c[1] in {'start','stop'} for c in self.docker.calls))

    def test_existing_r2_operation_receipt_requires_actual_completion_fields(self):
        self.args.apply=False
        self.assertEqual(self.execute()['status'],'PLAN')
        receipt=deploy.read(self.receipt);receipt['candidate_applied']=False
        self.receipt.write_text(json.dumps(receipt))
        self.manifest['base_receipt_sha256']=deploy.sha(self.receipt)
        (self.source/'source-manifest.json').write_text(json.dumps(self.manifest))
        with self.assertRaisesRegex(deploy.ReleaseError,'completed_r2_operation'):self.execute()

    def test_upstream_tree_mismatch_is_not_accepted_by_lockfile_presence(self):
        helper=SimpleNamespace(source_tree_sha256=lambda path:'0'*64)
        with patch.object(deploy,'load',return_value=helper):
            with self.assertRaisesRegex(deploy.ReleaseError,'upstream_source_changed'):
                VERIFY_NATIVE(self.source,self.dep)

    def test_input_arriving_before_stop_is_rechecked_before_backup(self):
        original=self.docker
        def runner(argv):
            result=original(argv)
            if argv[1]=='stop':
                with sqlite3.connect(self.dep/'state/data/plugin_data/phase1_guard/guard.sqlite3') as db:
                    db.execute("INSERT INTO turns VALUES ('generating')")
            return result
        self.services.runner=runner
        result=self.execute()
        self.assertEqual(result['status'],'FAILED')
        self.assertEqual(result['reason'],'active_turn_retry_after_completion')
        self.assertTrue(result['astrbot_running']);self.assert_old_sources();self.assert_no_nap_action()
        self.assertFalse((self.dep/'backups/synthetic-install').exists())

    def override_loader(self, transform):
        original=deploy.load
        def loader(name,path):
            module=original(name,path);transform(name,module);return module
        return patch.object(deploy,'load',side_effect=loader)

    def test_restore_failure_cannot_publish_and_resumes_old_astrbot(self):
        def transform(name,module):
            if name=='runtime_release_backup':
                def fail(*a):raise RuntimeError('synthetic_restore_failure')
                module.restore_backup=fail
        with self.override_loader(transform):result=self.execute()
        self.assertEqual(result['status'],'FAILED');self.assertTrue(result['original_astrbot_resumed'])
        self.assertFalse((self.source/'astrbot-code-journal').exists())
        self.assert_old_sources();self.assert_no_nap_action()

    def test_publish_failure_after_replace_restores_known_sources_without_database_restore(self):
        count=0;db_hash=deploy.sha(self.memory)
        def transform(name,module):
            if name!='runtime_release_installer':return
            original=module.publish
            def publish(*args):
                nonlocal count
                original(*args);count+=1
                if count==2:raise RuntimeError('synthetic_post_replace_failure')
            module.publish=publish
        with self.override_loader(transform):result=self.execute()
        self.assertEqual(result['status'],'FAILED');self.assertTrue(result['known_source_recovered'])
        self.assertTrue(result['astrbot_running']);self.assert_old_sources()
        self.assertEqual(deploy.sha(self.memory),db_hash);self.assert_no_nap_action()

    def test_start_failure_rolls_back_sources_then_starts_same_astrbot(self):
        self.docker.fail_starts=1
        result=self.execute()
        self.assertEqual(result['status'],'FAILED');self.assertTrue(result['astrbot_running'])
        self.assert_old_sources();self.assert_no_nap_action()
        self.assertTrue(all(c[-1]=='a'*64 for c in self.docker.calls if c[1] in {'start','stop'}))

    def test_corrupt_journal_leaves_astrbot_stopped_instead_of_starting_mixed_code(self):
        count=0
        def transform(name,module):
            if name!='runtime_release_installer':return
            original=module.publish
            def publish(*args):
                nonlocal count
                original(*args);count+=1
                if count==2:
                    (self.source/'astrbot-code-journal/original/main.py').write_text('corrupt')
                    raise RuntimeError('synthetic_post_replace_failure')
            module.publish=publish
        with self.override_loader(transform):result=self.execute()
        self.assertEqual(result['status'],'BLOCKED_SOURCE_RECOVERY_REQUIRED')
        self.assertFalse(result['astrbot_running']);self.assert_no_nap_action()

    def test_napcat_restart_during_backup_rejects_publication_without_restarting_nap(self):
        def transform(name,module):
            if name!='runtime_release_backup':return
            original=module.create_backup
            def create(*args):
                value=original(*args);self.docker.rows['napcat']['started_at']='external-restart';return value
            module.create_backup=create
        with self.override_loader(transform):result=self.execute()
        self.assertEqual(result['status'],'FAILED');self.assertFalse(result['napcat_identity_unchanged'])
        self.assertTrue(result['astrbot_running']);self.assert_old_sources()
        self.assertFalse(any(c[1] in {'start','stop'} and c[-1]=='b'*64 for c in self.docker.calls))

    def test_explicit_source_rollback_snapshots_new_chat_and_never_overwrites_it(self):
        self.assertTrue(self.execute()['status'].startswith('INSTALLED'))
        with sqlite3.connect(self.memory) as db:db.execute("INSERT INTO messages VALUES ('synthetic new chat after release')")
        current_hash=deploy.sha(self.memory)
        self.args.rollback=True;self.args.run_id='synthetic-rollback'
        result=self.execute()
        self.assertEqual(result['status'],'SOURCE_ROLLED_BACK_AWAITING_HEALTH')
        self.assertEqual(deploy.sha(self.memory),current_hash)
        restored=self.dep/'backups/synthetic-rollback/isolated-astrbot-restore/data/plugin_data/synthetic/memory.db'
        with sqlite3.connect(restored) as db:self.assertEqual(db.execute('SELECT count(*) FROM messages').fetchone()[0],2)
        self.assert_old_sources();self.assert_no_nap_action()

    def test_rollback_restore_failure_resumes_unchanged_running_candidate(self):
        self.assertTrue(self.execute()['status'].startswith('INSTALLED'))
        self.args.rollback=True;self.args.run_id='rollback-restore-failure'
        candidate={name:deploy.sha(self.dep/'state'/deploy.PREFIX/name) for name in deploy.TARGETS}
        db_hash=deploy.sha(self.memory)
        def transform(name,module):
            if name=='runtime_release_backup':
                def fail(*args):raise RuntimeError('synthetic_restore_failure')
                module.restore_backup=fail
        with self.override_loader(transform):result=self.execute()
        self.assertEqual(result['status'],'FAILED');self.assertTrue(result['astrbot_running'])
        self.assertEqual(result['recovered_source_version'],'candidate')
        self.assertEqual(candidate,{name:deploy.sha(self.dep/'state'/deploy.PREFIX/name) for name in deploy.TARGETS})
        self.assertEqual(deploy.sha(self.memory),db_hash);self.assert_no_nap_action()

    def test_exited_candidate_can_roll_back_without_starting_candidate(self):
        self.assertTrue(self.execute()['status'].startswith('INSTALLED'))
        self.docker.rows['astrbot'].update(running=False,status='exited')
        self.docker.calls.clear();self.args.rollback=True;self.args.run_id='rollback-exited'
        result=self.execute()
        self.assertEqual(result['status'],'SOURCE_ROLLED_BACK_AWAITING_HEALTH')
        self.assertEqual([c[1] for c in self.docker.calls if c[1] in {'stop','start'}],['start'])
        self.assertTrue(result['astrbot_running']);self.assert_old_sources();self.assert_no_nap_action()

    def test_exited_candidate_stays_exited_if_rollback_backup_fails(self):
        self.assertTrue(self.execute()['status'].startswith('INSTALLED'))
        self.docker.rows['astrbot'].update(running=False,status='exited')
        self.docker.calls.clear();self.args.rollback=True;self.args.run_id='rollback-exited-failure'
        def transform(name,module):
            if name=='runtime_release_backup':
                def fail(*args):raise RuntimeError('synthetic_create_failure')
                module.create_backup=fail
        with self.override_loader(transform):result=self.execute()
        self.assertEqual(result['status'],'FAILED');self.assertFalse(result['astrbot_running'])
        self.assertEqual(result['recovered_source_version'],'candidate')
        self.assertFalse(any(c[1] in {'stop','start'} for c in self.docker.calls));self.assert_no_nap_action()

    def test_napcat_restarting_does_not_prevent_known_old_astr_recovery(self):
        def transform(name,module):
            if name!='runtime_release_backup':return
            original=module.create_backup
            def create(*args):
                value=original(*args)
                self.docker.rows['napcat'].update(status='restarting',running=True)
                return value
            module.create_backup=create
        with self.override_loader(transform):result=self.execute()
        self.assertEqual(result['status'],'FAILED');self.assertTrue(result['astrbot_running'])
        self.assertTrue(result['final_service_state_unavailable']);self.assert_old_sources()
        self.assertFalse(any(c[1] in {'start','stop'} and c[-1]=='b'*64 for c in self.docker.calls))

    def test_late_napcat_state_unavailable_never_claims_unchanged(self):
        start=self.services.start_astr
        def start_then_nap_changes(**kwargs):
            start(**kwargs);self.docker.rows['napcat'].update(status='restarting',running=True)
        self.services.start_astr=start_then_nap_changes
        result=self.execute()
        self.assertEqual(result['status'],'BLOCKED_FINAL_SERVICE_STATE_UNAVAILABLE')
        self.assertIsNone(result['napcat_identity_unchanged'])
        self.assertTrue(result['source_operation_completed']);self.assertTrue(result['astrbot_running'])
        self.assertFalse(any(c[1] in {'start','stop'} and c[-1]=='b'*64 for c in self.docker.calls))

    def test_running_napcat_shared_state_mount_is_rejected_before_stop(self):
        self.docker.rows['napcat']['mounts'][0]['Source']=str(self.dep/'state')
        with self.assertRaisesRegex(deploy.ReleaseError,'snapshot_scope'):self.execute()
        self.assertFalse(any(c[1] in {'start','stop'} for c in self.docker.calls))


if __name__=='__main__':unittest.main()
