"""Real ZIP/SQLite snapshot with synthetic state and fake Docker, never the VPS."""
import codecs
import fcntl
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
spec = importlib.util.spec_from_file_location('bubbles_v3_release_tests', ROOT / 'scripts/phase3-deploy-bubbles-v3.py')
deploy = importlib.util.module_from_spec(spec); sys.modules[spec.name] = deploy; spec.loader.exec_module(deploy)


class DockerFixture:
    def __init__(self, dep):
        self.calls = []; self.fail_starts = 0; self.rows = {}
        for name, token, image in [('astrbot', 'a', deploy.core.ASTRBOT_IMAGE), ('napcat', 'b', deploy.core.NAPCAT_IMAGE)]:
            self.rows[name] = {'id': token * 64, 'image': image, 'running': True, 'status': 'running',
                'started_at': name + '-initial', 'restart_count': 0, 'project': 'qq-ai-companion', 'service': name,
                'mounts': [{'Source': str(dep / 'state/data') if name == 'astrbot' else str(dep / 'ntqq'),
                    'Destination': '/AstrBot/data' if name == 'astrbot' else '/app/.config/QQ', 'RW': True}]}

    def __call__(self, argv):
        self.calls.append(list(argv))
        if argv[1] == 'compose': return self.rows[argv[-1]]['id'] + '\n'
        if argv[1] == 'inspect': return json.dumps(next(r for r in self.rows.values() if r['id'] == argv[-1]))
        if argv[1] in {'start', 'stop'}:
            if argv[-1] != self.rows['astrbot']['id']: raise AssertionError('NapCat action forbidden')
            if argv[1] == 'start' and self.fail_starts:
                self.fail_starts -= 1; raise deploy.ReleaseError('synthetic_start_failure')
            running = argv[1] == 'start'
            self.rows['astrbot'].update(running=running, status='running' if running else 'exited')
            if running: self.rows['astrbot']['started_at'] = 'new-astrbot-start'
            return argv[-1]
        raise AssertionError('unexpected Docker action')


class BubblesV3ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.source = self.root / 'source'; self.dep = self.root / 'deployment'
        self.source.mkdir(); self.dep.mkdir(); self.state = self.dep / 'state'; self.state.mkdir()
        for relative in deploy.EXPECTED:
            target = self.source / relative; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        for plugin in deploy.PLUGINS:
            directory = self.state / 'data/plugins' / plugin; directory.mkdir(parents=True)
            (directory / 'untouched.py').write_text('BASELINE_SYNTHETIC = True\n')
        for name in deploy.TARGETS:
            (self.state / deploy.PREFIX / name).write_text('{}' if name.endswith('.json') else 'OLD = True\n')
        (self.state / 'data/config').mkdir()
        self.cmd = {'platform_settings': {'segmented_reply': {'enable': False}},
            'agent_runner': {'config': {'model': {'provider_id': 'deepseek/flash'}}},
            'provider_sources': [{'id': 'deepseek', 'enable': True, 'type': 'openai_chat_completion',
                'api_base': 'https://api.deepseek.com'}],
            'provider': [{'id': 'deepseek/flash', 'provider_source_id': 'deepseek', 'enable': True,
                'model': 'deepseek-flash', 'custom_extra_body': {'thinking': {'type': 'disabled'}, 'max_tokens': 8192}}]}
        (self.state / 'data/cmd_config.json').write_text(json.dumps(self.cmd))
        (self.state / 'data/config/phase3_runtime_config.json').write_text('{"proactive":false,"memory":true}')
        (self.state / 'data/config/astrbot_plugin_private_companion_config.json').write_text(
            '{"enable_llm_proactive_message":false,"nested":{"enable_llm_proactive_message":false}}')
        self.guard = {'phase3_proactive_enabled': False, 'phase3_multibubble_enabled': True, 'keep': '合成配置'}
        (self.state / deploy.GUARD_CONFIG).write_bytes(codecs.BOM_UTF8 + json.dumps(self.guard, ensure_ascii=False).encode())
        (self.state / 'data/phase3').mkdir(); (self.state / 'data/phase3/persona-v0.1.md').write_text('synthetic unchanged persona')
        self.guard_db = self.state / 'data/plugin_data/phase1_guard/guard.sqlite3'; self.guard_db.parent.mkdir(parents=True)
        with sqlite3.connect(self.guard_db) as db:
            db.executescript('CREATE TABLE turns(state TEXT); CREATE TABLE deliveries(state TEXT); CREATE TABLE outbound(state TEXT);')
        self.memory = self.state / 'data/plugin_data/memory.db'
        with sqlite3.connect(self.memory) as db:
            db.execute('CREATE TABLE chat(body TEXT)'); db.execute("INSERT INTO chat VALUES ('old synthetic chat')")
        (self.dep / 'compose.yml').write_text('synthetic compose'); (self.dep / '.env').write_text('SYNTHETIC=true')
        self.pending = self.root / 'pending-context-candidate'; self.pending.write_text('preserve pending compression and proactive')
        self.manifest = {'schema': deploy.SCHEMA, 'files': {n: deploy.sha(self.source / n) for n in deploy.EXPECTED},
            'protected_hashes': {n: deploy.sha(self.state / n) for n in deploy.inventory(self.dep)},
            'guard_config_after_sha256': __import__('hashlib').sha256(deploy.candidate_config((self.state / deploy.GUARD_CONFIG).read_bytes())).hexdigest(),
            'config_change': {deploy.FLAG: True}, 'compose_sha256': deploy.sha(self.dep / 'compose.yml'),
            'env_sha256': deploy.sha(self.dep / '.env')}
        self.write_manifest()
        self.initial = {n: (self.state / n).read_bytes() for n in self.manifest['protected_hashes']}
        self.docker = DockerFixture(self.dep); self.services = deploy.Services(self.dep, self.docker)
        self.args = SimpleNamespace(source=self.source, deployment=self.dep, run_id='v3-synthetic', apply=True, rollback=False)
        native = patch.object(deploy, 'verify_native'); native.start(); self.addCleanup(native.stop)
        old_validate = patch.object(deploy.core, 'validate', side_effect=AssertionError('old 47-file validate forbidden'))
        old_validate.start(); self.addCleanup(old_validate.stop)

    def write_manifest(self): (self.source / 'source-manifest.json').write_text(json.dumps(self.manifest))
    def execute(self): return deploy.execute(self.args, services=self.services)
    def assert_baseline(self):
        for name, data in self.initial.items(): self.assertEqual((self.state / name).read_bytes(), data)
    def assert_nap_untouched(self):
        self.assertFalse(any(c[1] in {'start', 'stop'} and c[-1] == self.docker.rows['napcat']['id'] for c in self.docker.calls))
        self.assertEqual(self.docker.rows['napcat']['started_at'], 'napcat-initial')
        self.assertTrue(self.docker.rows['napcat']['running'])

    def test_default_plan_is_read_only_and_inventory_has_no_fixed_file_count(self):
        self.args.apply = False
        result = self.execute(); self.assertEqual(result['status'], 'PLAN')
        self.assertEqual(set(result['target_files']), deploy.DESTINATIONS)
        self.assert_baseline(); self.assert_nap_untouched()
        self.assertFalse((self.dep / '.astrbot-runtime-release.lock').exists())
        self.assertFalse((self.source / 'bubbles-v3-journal').exists())
        self.assertFalse(any(c[1] in {'start', 'stop'} for c in self.docker.calls))

    def test_apply_snapshots_and_restores_before_four_writes_preserves_bom_and_state(self):
        original_memory = deploy.sha(self.memory); result = self.execute()
        self.assertEqual(result['status'], 'INSTALLED_AWAITING_HEALTH_AND_QQ')
        self.assertEqual(result['sqlite_checked_count'], 2)
        restored = self.dep / 'backups/v3-synthetic/isolated-astrbot-restore'
        for name, data in self.initial.items(): self.assertEqual((restored / name).read_bytes(), data)
        for name, data in self.initial.items():
            if name not in deploy.DESTINATIONS: self.assertEqual((self.state / name).read_bytes(), data)
        raw = (self.state / deploy.GUARD_CONFIG).read_bytes(); self.assertTrue(raw.startswith(codecs.BOM_UTF8))
        self.assertEqual(json.loads(raw.decode('utf-8-sig')), {**self.guard, deploy.FLAG: True})
        self.assertEqual(deploy.sha(self.memory), original_memory)
        self.assertEqual(self.pending.read_text(), 'preserve pending compression and proactive')
        self.assert_nap_untouched()

    def test_protected_drift_added_source_and_manifest_extra_field_refused(self):
        self.args.apply = False
        path = self.state / 'data/plugins/phase3_runtime/untouched.py'; path.write_text('drift')
        with self.assertRaisesRegex(deploy.ReleaseError, 'protected_file_drift'): self.execute()
        path.write_bytes(self.initial[path.relative_to(self.state).as_posix()])
        extra = path.with_name('unreviewed.py'); extra.write_text('new')
        with self.assertRaisesRegex(deploy.ReleaseError, 'complete_current_protected_inventory'): self.execute()
        extra.unlink(); self.manifest['config_change']['phase3_proactive_enabled'] = True; self.write_manifest()
        with self.assertRaisesRegex(deploy.ReleaseError, 'reviewed_bubbles_v3_manifest'): self.execute()
        self.assertFalse(any(c[1] in {'start', 'stop'} for c in self.docker.calls))

    def test_wrong_candidate_config_hash_refused_before_stop(self):
        self.manifest['guard_config_after_sha256'] = 'f' * 64; self.write_manifest()
        with self.assertRaisesRegex(deploy.ReleaseError, 'single_config_field_candidate'): self.execute()
        self.assertFalse(any(c[1] in {'start', 'stop'} for c in self.docker.calls))

    def test_global_segmentation_thinking_and_proactive_checked_even_if_reviewed_hashes(self):
        mutations = [('data/cmd_config.json', lambda d: d['platform_settings']['segmented_reply'].update(enable=True)),
            ('data/cmd_config.json', lambda d: d['provider'][0]['custom_extra_body'].update(thinking={'type': 'enabled'})),
            ('data/config/phase3_runtime_config.json', lambda d: d.update(proactive=True)),
            ('data/config/astrbot_plugin_private_companion_config.json', lambda d: d['nested'].update(enable_llm_proactive_message=True))]
        for name, mutation in mutations:
            with self.subTest(name=name):
                value = json.loads(self.initial[name].decode('utf-8-sig')); mutation(value)
                (self.state / name).write_text(json.dumps(value)); self.manifest['protected_hashes'][name] = deploy.sha(self.state / name); self.write_manifest()
                with self.assertRaises(deploy.ReleaseError): self.execute()
                (self.state / name).write_bytes(self.initial[name]); self.manifest['protected_hashes'][name] = deploy.sha(self.state / name); self.write_manifest()
        self.assertFalse(any(c[1] in {'start', 'stop'} for c in self.docker.calls))

    def test_locked_helper_controller_and_extra_package_file_rejected(self):
        helper = self.source / 'scripts/backup.py'; helper.write_text(helper.read_text() + '\n# drift')
        with self.assertRaisesRegex(deploy.ReleaseError, 'reviewed_source_hash'): self.execute()
        shutil.copy2(ROOT / 'scripts/backup.py', helper)
        controller = self.source / 'scripts/phase3-deploy-bubbles-v3.py'; controller.write_text(controller.read_text() + '\n# drift')
        self.manifest['files']['scripts/phase3-deploy-bubbles-v3.py'] = deploy.sha(controller); self.write_manifest()
        with self.assertRaisesRegex(deploy.ReleaseError, 'executing_controller_not_reviewed'): self.execute()
        shutil.copy2(ROOT / 'scripts/phase3-deploy-bubbles-v3.py', controller)
        self.manifest['files']['scripts/phase3-deploy-bubbles-v3.py'] = deploy.sha(controller); self.write_manifest()
        (self.source / 'scripts/extra.py').write_text('extra')
        with self.assertRaisesRegex(deploy.ReleaseError, 'source_package_inventory'): self.execute()

    def test_partial_publish_failure_rolls_back_only_four_files(self):
        real_load = deploy.core.load; publishes = []
        def loader(name, path):
            module = real_load(name, path)
            if name == 'bubbles_v3_installer':
                original = module.publish
                def publish(source, target, uid, gid):
                    publishes.append(target)
                    if len(publishes) == 2: raise OSError('synthetic disk failure')
                    return original(source, target, uid, gid)
                module.publish = publish
            return module
        original_memory = deploy.sha(self.memory)
        with patch.object(deploy.core, 'load', side_effect=loader): result = self.execute()
        self.assertEqual(result['status'], 'FAILED'); self.assertTrue(result['known_files_recovered'])
        self.assertTrue(self.docker.rows['astrbot']['running']); self.assert_baseline()
        self.assertEqual(deploy.sha(self.memory), original_memory); self.assert_nap_untouched()
        self.assertTrue(all(p.relative_to(self.state).as_posix() in deploy.DESTINATIONS for p in publishes))

    def test_start_failure_recovers_original_source_and_config(self):
        self.docker.fail_starts = 1; result = self.execute()
        self.assertEqual(result['status'], 'FAILED'); self.assertTrue(result['known_files_recovered'])
        self.assert_baseline(); self.assertTrue(self.docker.rows['astrbot']['running']); self.assert_nap_untouched()

    def test_failure_after_config_publish_restores_old_bom_config_and_modes(self):
        real_load = deploy.core.load; fired = False
        original_modes = {name: (self.state / name).stat().st_mode for name in deploy.DESTINATIONS}
        def loader(name, path):
            module = real_load(name, path)
            if name == 'bubbles_v3_installer':
                original = module.publish
                def publish(source, target, uid, gid):
                    nonlocal fired
                    original(source, target, uid, gid)
                    if target.relative_to(self.state).as_posix() == deploy.GUARD_CONFIG and not fired:
                        fired = True; raise OSError('synthetic failure after replacement')
                module.publish = publish
            return module
        with patch.object(deploy.core, 'load', side_effect=loader): result = self.execute()
        self.assertEqual(result['status'], 'FAILED'); self.assertTrue(result['known_files_recovered'])
        self.assert_baseline(); self.assert_nap_untouched()
        for name, mode in original_modes.items(): self.assertEqual((self.state / name).stat().st_mode, mode)

    def test_explicit_rollback_preserves_new_chats_and_takes_new_backup(self):
        self.assertEqual(self.execute()['status'], 'INSTALLED_AWAITING_HEALTH_AND_QQ')
        with sqlite3.connect(self.memory) as db: db.execute("INSERT INTO chat VALUES ('new chat after release')")
        self.args.rollback = True; self.args.run_id = 'v3-rollback'
        self.assertEqual(self.execute()['status'], 'ROLLED_BACK_AWAITING_HEALTH')
        self.assert_baseline(); self.assert_nap_untouched()
        for path in [self.memory, self.dep / 'backups/v3-rollback/isolated-astrbot-restore/data/plugin_data/memory.db']:
            with sqlite3.connect(path) as db: self.assertEqual(db.execute('SELECT count(*) FROM chat').fetchone()[0], 2)

    def test_active_turn_refused_and_same_lock_prevents_concurrent_release(self):
        with sqlite3.connect(self.guard_db) as db: db.execute("INSERT INTO turns VALUES ('generating')")
        with self.assertRaisesRegex(deploy.ReleaseError, 'active_turn'): self.execute()
        with sqlite3.connect(self.guard_db) as db: db.execute('DELETE FROM turns')
        with (self.dep / '.astrbot-runtime-release.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(deploy.ReleaseError, 'another_release_running'): self.execute()
        self.assertFalse(any(c[1] in {'start', 'stop'} for c in self.docker.calls))

    def test_backup_failure_never_writes_and_resumes_astrbot(self):
        with patch.object(deploy.core, 'snapshot', side_effect=deploy.ReleaseError('restore_fixture_failed')):
            result = self.execute()
        self.assertEqual(result['status'], 'FAILED'); self.assert_baseline(); self.assert_nap_untouched()
        self.assertTrue(self.docker.rows['astrbot']['running'])
        self.assertFalse((self.source / 'bubbles-v3-journal').exists())

    def test_corrupt_journal_or_post_release_drift_prevents_rollback(self):
        self.assertEqual(self.execute()['status'], 'INSTALLED_AWAITING_HEALTH_AND_QQ')
        self.args.rollback = True; self.args.run_id = 'rollback'
        old = self.source / 'bubbles-v3-journal/original' / deploy.GUARD_CONFIG
        raw = old.read_bytes(); old.write_bytes(b'{}')
        with self.assertRaisesRegex(deploy.ReleaseError, 'original_journal_corrupt'): self.execute()
        old.write_bytes(raw)
        (self.state / deploy.PREFIX / 'main.py').write_text('post release operator change')
        with self.assertRaisesRegex(deploy.ReleaseError, 'protected_file_drift'): self.execute()


if __name__ == '__main__': unittest.main()
