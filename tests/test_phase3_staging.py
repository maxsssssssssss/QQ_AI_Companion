"""Synthetic private snapshot staging and exact stopped-state rollback (T1/T5)."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]
from backup import create_backup, restore_backup
from integrations.phase1_guard.store import GuardStore


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / file)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result

stage = load('test_staging_phase3', 'phase3-stage.py')
helpers = stage.helpers

class StagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='qq-phase3-staging-')
        self.root = Path(self.temp.name)
        self.snapshot = self.root / 'snapshot'
        self.config = helpers.build_config('10001', 'napcat', 'synthetic-onebot-' + 'x' * 40, 'SyntheticPassword9')
        self.config['provider_sources'][0].update(enable=True, key=['synthetic-chat-key-not-real'])
        self.config['provider_settings']['enable'] = True
        self.config['provider'][0]['enable'] = True
        self.settings = dict(enabled=True, persona=True, natural_chat=True, memory=True, search=False, proactive=False)
        self.write('data/cmd_config.json', self.config)
        self.write('data/config/phase2_policy_config.json', dict(admin_id='10001', platform_id='napcat'))
        self.write('data/config/astrbot_plugin_private_companion_config.json', {})
        self.write('data/config/astrbot_plugin_memory_companion_config.json', {})
    def tearDown(self): self.temp.cleanup()
    def write(self, relative, value):
        helpers.write_private(self.snapshot / relative, json.dumps(value))
    def test_stage_preserves_snapshot_secrets_privately_and_licenses(self):
        original = (self.snapshot / 'data/cmd_config.json').read_bytes()
        report = stage.stage(self.snapshot, self.root / 'candidate', self.settings)
        self.assertEqual(report['mode'], 'STAGED_NOT_DEPLOYED')
        self.assertFalse(report['production_changes'])
        self.assertNotIn('synthetic-chat-key', str(report))
        self.assertEqual((self.snapshot / 'data/cmd_config.json').read_bytes(), original)
        self.assertEqual((self.root / 'candidate/data/cmd_config.json').stat().st_mode & 0o777, 0o600)
        plugins = {p.name for p in (self.root / 'candidate/data/plugins').iterdir()}
        self.assertEqual(plugins, {'phase1_guard','phase2_policy','phase3_runtime','phase3_memory','phase3_search'})
        private = json.loads((self.root / 'candidate/data/config/astrbot_plugin_private_companion_config.json').read_text())
        self.assertEqual(private['proactive_reach_config']['max_daily_messages'], 0)
        memory = json.loads((self.root / 'candidate/data/config/astrbot_plugin_memory_companion_config.json').read_text())
        self.assertFalse(memory['memory_summary']['enabled'])
    def test_search_requires_separate_protected_key_without_mutating_chat_key(self):
        settings = {**self.settings, 'search': True}
        with self.assertRaises(ValueError): stage.stage(self.snapshot, self.root / 'missing', settings)
        self.assertFalse((self.root / 'missing').exists())
        keyfile = self.root / 'search.key'
        helpers.write_private(keyfile, 'synthetic-search-key-not-real')
        stage.stage(self.snapshot, self.root / 'candidate', settings, search_key_path=keyfile)
        cfg = json.loads((self.root / 'candidate/data/cmd_config.json').read_text())
        self.assertEqual(cfg['provider_sources'][0]['key'], ['synthetic-chat-key-not-real'])
        self.assertEqual(cfg['provider_settings']['websearch_deepseek_key'], ['synthetic-search-key-not-real'])
    def test_no_overwrite_or_nested_destination(self):
        with self.assertRaises(FileExistsError): stage.stage(self.snapshot, self.snapshot, self.settings)
        with self.assertRaises(ValueError): stage.stage(self.snapshot, self.snapshot / 'nested', self.settings)
    def test_unsafe_input_permissions_or_symlink_rejected(self):
        file = self.snapshot / 'data/cmd_config.json'
        file.chmod(0o644)
        with self.assertRaises(ValueError): stage.stage(self.snapshot, self.root / 'a', self.settings)
        file.chmod(0o600)
        original = file.with_suffix('.private')
        file.rename(original)
        file.symlink_to(original)
        with self.assertRaises(OSError): stage.stage(self.snapshot, self.root / 'b', self.settings)
    def test_stopped_legacy_store_backup_migrate_restore_exact(self):
        path = self.snapshot / 'data/plugin_data/phase1_guard/guard.sqlite3'
        path.parent.mkdir(parents=True)
        old = GuardStore(path)
        key = ('aiocqhttp','napcat:FriendMessage:10001','synthetic-1')
        old.claim(*key, '10001', 'synthetic original', [])
        send = old.begin_outbound(key, 'synthetic reply', [])
        old.finish(send, 'sent')
        old.close()
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest = create_backup(self.snapshot, self.root / 'before.zip')
        self.assertTrue(manifest)
        # New tables are additive; original Phase 2 evidence survives migration.
        upgraded = GuardStore(path)
        upgraded.enable_phase3()
        self.assertEqual(upgraded.db.execute('SELECT state FROM outbound').fetchone()[0], 'sent')
        upgraded.close()
        restored = self.root / 'restored'
        restore_backup(self.root / 'before.zip', restored)
        target = restored / path.relative_to(self.snapshot)
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), before)
        with sqlite3.connect(target) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute('SELECT text FROM inbound').fetchone()[0], 'synthetic original')
        with self.assertRaises(Exception): restore_backup(self.root / 'before.zip', restored)

if __name__ == '__main__': unittest.main()
