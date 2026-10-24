"""One-file release with real ZIP/SQLite recovery and synthetic Docker only."""
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
spec = importlib.util.spec_from_file_location('proactive_release_tests', ROOT / 'scripts/phase3-deploy-proactive-recovery.py')
deploy = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = deploy
spec.loader.exec_module(deploy)


class DockerFixture:
    def __init__(self, deployment):
        self.calls = []
        self.fail_starts = 0
        self.rows = {}
        for name, token, image in [('astrbot', 'a', deploy.ASTRBOT_IMAGE), ('napcat', 'b', deploy.NAPCAT_IMAGE)]:
            self.rows[name] = {'id': token * 64, 'image': image, 'running': True, 'status': 'running',
                'started_at': name + '-original', 'restart_count': 0, 'project': 'qq-ai-companion',
                'service': name, 'mounts': [{'Source': str(deployment / ('state/data' if name == 'astrbot' else 'ntqq')),
                    'Destination': '/AstrBot/data' if name == 'astrbot' else '/app/.config/QQ', 'RW': True}]}

    def __call__(self, argv):
        self.calls.append(list(argv))
        if argv[1] == 'compose':
            return self.rows[argv[-1]]['id'] + '\n'
        if argv[1] == 'inspect':
            return json.dumps(next(row for row in self.rows.values() if row['id'] == argv[-1]))
        if argv[1] in {'start', 'stop'}:
            if argv[-1] != self.rows['astrbot']['id']:
                raise AssertionError('NapCat must never be controlled')
            if argv[1] == 'start' and self.fail_starts:
                self.fail_starts -= 1
                raise deploy.ReleaseError('synthetic_start_failure')
            running = argv[1] == 'start'
            self.rows['astrbot'].update(running=running, status='running' if running else 'exited')
            if running:
                self.rows['astrbot']['started_at'] = 'new-start'
            return argv[-1]
        raise AssertionError('Unexpected Docker operation')


class ProactiveRecoveryReleaseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source, self.dep = self.root / 'source', self.root / 'deployment'
        self.source.mkdir(); self.dep.mkdir()
        self.state = self.dep / 'state'
        (self.state / 'data/config').mkdir(parents=True)
        (self.state / 'data/cmd_config.json').write_text('{}')
        (self.state / 'data/config/phase3_runtime_config.json').write_text('{"daily_limit":5}')
        (self.dep / 'compose.yml').write_text('synthetic compose')
        (self.dep / '.env').write_text('SYNTHETIC_IMAGE_LOCK=1')
        for name in deploy.PLUGINS:
            path = self.state / 'data/plugins' / name / 'main.py'
            path.parent.mkdir(parents=True)
            path.write_text('SYNTHETIC_UNCHANGED = True\n')
        self.target = self.state / deploy.TARGET
        self.target.write_text('SYNTHETIC_OLD = True\n')
        self.target.chmod(0o640)
        self.guard = self.state / 'data/plugin_data/phase1_guard/guard.sqlite3'
        self.guard.parent.mkdir(parents=True)
        with sqlite3.connect(self.guard) as db:
            db.executescript('CREATE TABLE turns(state TEXT); CREATE TABLE deliveries(state TEXT); CREATE TABLE outbound(state TEXT);')
        self.memory = self.state / 'data/plugin_data/memory.db'
        with sqlite3.connect(self.memory) as db:
            db.execute('CREATE TABLE messages(text TEXT)')
            db.execute("INSERT INTO messages VALUES ('synthetic retained chat')")
        for name in set(deploy.PINNED) | {deploy.CONTROLLER}:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, path)
        candidate = self.source / deploy.CANDIDATE
        candidate.parent.mkdir(parents=True)
        candidate.write_text('SYNTHETIC_NEW = True\n')
        self.baseline = {name: deploy.sha(self.state / name) for name in deploy.source_inventory(self.state)}
        self.manifest = {'schema': deploy.SCHEMA, 'base_release': deploy.BASE_RELEASE,
            'files': {name: deploy.sha(self.source / name)
                      for name in set(deploy.PINNED) | {deploy.CONTROLLER, deploy.CANDIDATE}},
            'baseline_source_files': self.baseline,
            'protected_config_files': {name: deploy.sha(self.state / name)
                for name in ['data/cmd_config.json', 'data/config/phase3_runtime_config.json']},
            'compose_sha256': deploy.sha(self.dep / 'compose.yml'), 'env_sha256': deploy.sha(self.dep / '.env')}
        self.save_manifest()
        self.docker = DockerFixture(self.dep)
        self.services = deploy.Services(self.dep, self.docker)
        self.args = SimpleNamespace(source=self.source, deployment=self.dep, run_id='synthetic-install', apply=True, rollback=False)
        # The tiny test application has no redistributed upstream source trees.
        native = patch.object(deploy, 'verify_native')
        self.native_mock = native.start(); self.addCleanup(native.stop)

    def save_manifest(self):
        (self.source / 'source-manifest.json').write_text(json.dumps(self.manifest))

    def execute(self):
        return deploy.execute(self.args, services=self.services)

    def assert_baseline(self):
        for name, digest in self.baseline.items():
            self.assertEqual(deploy.sha(self.state / name), digest)

    def assert_nap_preserved(self):
        self.assertEqual(self.docker.rows['napcat']['started_at'], 'napcat-original')
        self.assertTrue(self.docker.rows['napcat']['running'])
        self.assertFalse(any(call[1] in {'start', 'stop'} and call[-1] == 'b' * 64 for call in self.docker.calls))

    def loader(self, transform):
        original = deploy.load
        def load(name, path):
            module = original(name, path)
            transform(name, module)
            return module
        return patch.object(deploy, 'load', side_effect=load)

    def test_plan_is_readonly_and_restricts_target(self):
        self.args.apply = False
        result = self.execute()
        self.assertEqual(result['status'], 'PLAN')
        self.assertEqual(result['target_files'], [deploy.TARGET])
        self.assertFalse((self.dep / '.astrbot-runtime-release.lock').exists())
        self.assertFalse(any(call[1] in {'start', 'stop'} for call in self.docker.calls))
        self.assert_baseline()

    def test_success_changes_only_source_after_real_backup_and_isolated_restore(self):
        memory_hash = deploy.sha(self.memory)
        result = self.execute()
        self.assertEqual(result['status'], 'INSTALLED_AWAITING_HEALTH')
        self.assertEqual(result['sqlite_checked_count'], 2)
        self.assertEqual(deploy.sha(self.memory), memory_hash)
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o640)
        for name, digest in self.baseline.items():
            if name != deploy.TARGET:
                self.assertEqual(deploy.sha(self.state / name), digest)
        restored = self.dep / 'backups/synthetic-install/isolated-astrbot-restore'
        for name, digest in self.baseline.items():
            self.assertEqual(deploy.sha(restored / name), digest)
        journal = deploy.read(self.source / 'astrbot-code-journal/journal.json')
        self.assertEqual([row['path'] for row in journal['files']], [deploy.TARGET])
        self.assert_nap_preserved()

    def test_changed_other_source_or_added_source_is_rejected(self):
        other = self.state / 'data/plugins/phase1_guard/main.py'
        original = other.read_bytes()
        other.write_text('DRIFT = True')
        with self.assertRaisesRegex(deploy.ReleaseError, 'protected_source_changed'):
            self.execute()
        other.write_bytes(original)
        (other.parent / 'unexpected.py').write_text('EXTRA = True')
        with self.assertRaisesRegex(deploy.ReleaseError, 'source_inventory_changed'):
            self.execute()

    def test_configuration_cmd_config_and_compose_are_protected(self):
        for path in [self.state / 'data/cmd_config.json', self.state / 'data/config/phase3_runtime_config.json', self.dep / 'compose.yml', self.dep / '.env']:
            original = path.read_bytes()
            path.write_text('changed')
            with self.assertRaises(deploy.ReleaseError):
                self.execute()
            path.write_bytes(original)
        self.assertFalse(any(call[1] in {'start', 'stop'} for call in self.docker.calls))

    def test_extra_package_target_and_traversal_baseline_are_rejected(self):
        self.manifest['files']['integrations/phase3_runtime/main.py'] = '0' * 64
        self.save_manifest()
        with self.assertRaisesRegex(deploy.ReleaseError, 'exact_proactive_only_package'):
            self.execute()
        del self.manifest['files']['integrations/phase3_runtime/main.py']
        self.manifest['baseline_source_files']['../outside.py'] = '0' * 64
        self.save_manifest()
        with self.assertRaises(deploy.ReleaseError):
            self.execute()

    def test_helper_tampering_even_with_manifest_updated_is_rejected(self):
        path = self.source / 'scripts/backup.py'
        path.write_text(path.read_text() + '\n# changed\n')
        self.manifest['files']['scripts/backup.py'] = deploy.sha(path)
        self.save_manifest()
        with self.assertRaisesRegex(deploy.ReleaseError, 'locked_helper'):
            self.execute()

    def test_symlinked_target_is_rejected_before_any_stop(self):
        self.target.unlink()
        self.target.symlink_to(self.root / 'external.py')
        with self.assertRaises(deploy.ReleaseError):
            self.execute()
        self.assertFalse(any(call[1] in {'start', 'stop'} for call in self.docker.calls))

    def test_pending_delivery_and_new_input_after_stop_abort_without_mutation(self):
        with sqlite3.connect(self.guard) as db:
            db.execute("INSERT INTO deliveries VALUES ('pending')")
        with self.assertRaisesRegex(deploy.ReleaseError, 'pending_delivery'):
            self.execute()
        with sqlite3.connect(self.guard) as db:
            db.execute('DELETE FROM deliveries')
        original = self.docker
        def runner(argv):
            value = original(argv)
            if argv[1] == 'stop':
                with sqlite3.connect(self.guard) as db:
                    db.execute("INSERT INTO turns VALUES ('generating')")
            return value
        self.services.runner = runner
        result = self.execute()
        self.assertEqual(result['status'], 'FAILED')
        self.assertTrue(result['astrbot_running'])
        self.assertFalse((self.dep / 'backups/synthetic-install').exists())
        self.assert_baseline(); self.assert_nap_preserved()

    def test_isolated_restore_failure_resumes_original_without_publish(self):
        def transform(name, module):
            if name.endswith('_backup'):
                def fail(*args):
                    raise RuntimeError('synthetic_restore_failure')
                module.restore_backup = fail
        with self.loader(transform):
            result = self.execute()
        self.assertEqual(result['status'], 'FAILED')
        self.assertTrue(result['astrbot_running'])
        self.assertFalse((self.source / 'astrbot-code-journal').exists())
        self.assert_baseline(); self.assert_nap_preserved()

    def test_publish_failure_after_replace_rolls_back_and_preserves_data(self):
        memory_hash = deploy.sha(self.memory)
        def transform(name, module):
            if not name.endswith('_installer'):
                return
            original, calls = module.publish, []
            def publish(*args):
                original(*args); calls.append(True)
                if len(calls) == 1:
                    raise RuntimeError('synthetic_post_replace_failure')
            module.publish = publish
        with self.loader(transform):
            result = self.execute()
        self.assertEqual(result['status'], 'FAILED')
        self.assertTrue(result['known_source_recovered'])
        self.assertTrue(result['astrbot_running'])
        self.assertEqual(deploy.sha(self.memory), memory_hash)
        self.assert_baseline(); self.assert_nap_preserved()

    def test_start_failure_rolls_back_then_resumes_same_astrbot(self):
        self.docker.fail_starts = 1
        result = self.execute()
        self.assertEqual(result['status'], 'FAILED')
        self.assertTrue(result['astrbot_running'])
        self.assert_baseline(); self.assert_nap_preserved()

    def test_napcat_external_restart_aborts_publication_without_controlling_napcat(self):
        def transform(name, module):
            if not name.endswith('_backup'):
                return
            original = module.create_backup
            def create(*args):
                result = original(*args)
                self.docker.rows['napcat']['started_at'] = 'external-restart'
                return result
            module.create_backup = create
        with self.loader(transform):
            result = self.execute()
        self.assertEqual(result['status'], 'FAILED')
        self.assertFalse(result['napcat_identity_unchanged'])
        self.assertTrue(result['astrbot_running'])
        self.assert_baseline()
        self.assertFalse(any(call[1] in {'start', 'stop'} and call[-1] == 'b' * 64 for call in self.docker.calls))

    def test_source_only_rollback_snapshots_new_chats_without_restoring_data(self):
        self.assertEqual(self.execute()['status'], 'INSTALLED_AWAITING_HEALTH')
        with sqlite3.connect(self.memory) as db:
            db.execute("INSERT INTO messages VALUES ('synthetic chat after release')")
        latest_hash = deploy.sha(self.memory)
        self.args.rollback, self.args.run_id = True, 'synthetic-rollback'
        result = self.execute()
        self.assertEqual(result['status'], 'SOURCE_ROLLED_BACK_AWAITING_HEALTH')
        self.assertEqual(deploy.sha(self.memory), latest_hash)
        restored = self.dep / 'backups/synthetic-rollback/isolated-astrbot-restore/data/plugin_data/memory.db'
        with sqlite3.connect(restored) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM messages').fetchone()[0], 2)
        self.assert_baseline(); self.assert_nap_preserved()

    def test_rollback_cannot_redirect_journal_to_configuration(self):
        self.execute()
        path = self.source / 'astrbot-code-journal/journal.json'
        journal = deploy.read(path)
        journal['files'][0]['path'] = 'data/cmd_config.json'
        deploy.private_json(path, journal)
        self.args.rollback, self.args.run_id = True, 'bad-journal-rollback'
        config_hash = deploy.sha(self.state / 'data/cmd_config.json')
        result = self.execute()
        self.assertEqual(result['status'], 'BLOCKED_SOURCE_RECOVERY_REQUIRED')
        self.assertEqual(deploy.sha(self.state / 'data/cmd_config.json'), config_hash)
        self.assertFalse(result['astrbot_running'])
        self.assert_nap_preserved()

    def test_native_lock_checks_real_tree_hash_not_just_manifest_presence(self):
        fake = SimpleNamespace(source_tree_sha256=lambda path: '0' * 64)
        with patch.object(deploy._helper, 'load', return_value=fake):
            with self.assertRaisesRegex(deploy.ReleaseError, 'locked_upstream_source_changed'):
                deploy._helper.verify_native(self.source, self.dep)


if __name__ == '__main__':
    unittest.main()
