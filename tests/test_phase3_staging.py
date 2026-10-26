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
        self.write('data/config/phase3_runtime_config.json', self.settings)
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
        from integrations.phase3_runtime.persona import CURRENT_TEMPLATE, render_persona, persona_version
        staged_template=(self.root/'candidate/data/phase3/persona-v0.1.md').read_text()
        self.assertEqual(staged_template,(ROOT/'persona'/CURRENT_TEMPLATE).read_text())
        self.assertEqual(persona_version(render_persona(staged_template,'合成用户'))[0],'0.4')
        self.assertNotIn('synthetic-chat-key', str(report))
        self.assertEqual((self.snapshot / 'data/cmd_config.json').read_bytes(), original)
        self.assertEqual((self.root / 'candidate/data/cmd_config.json').stat().st_mode & 0o777, 0o600)
        plugins = {p.name for p in (self.root / 'candidate/data/plugins').iterdir()}
        self.assertEqual(plugins, {'phase1_guard','phase2_policy','phase3_runtime','phase3_memory','phase3_search'})
        private = json.loads((self.root / 'candidate/data/config/astrbot_plugin_private_companion_config.json').read_text())
        self.assertEqual(private['proactive_reach_config']['max_daily_messages'], 0)
        memory = json.loads((self.root / 'candidate/data/config/astrbot_plugin_memory_companion_config.json').read_text())
        self.assertFalse(memory['memory_summary']['enabled'])
        runtime = json.loads((self.root / 'candidate/data/config/phase3_runtime_config.json').read_text())
        self.assertEqual(runtime['context_compression_fallback_turns'], 80)
        guard = json.loads((self.root / 'candidate/data/config/phase1_guard_config.json').read_text())
        self.assertEqual((guard['phase3_bubble_min_interval'], guard['phase3_bubble_max_interval']), (1.0, 2.0))
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
        search = json.loads((self.root / 'candidate/data/config/phase3_search_config.json').read_text())
        self.assertEqual(search['scope'], 'general_public')
        self.assertEqual(search['public_terms'], [])
        self.assertEqual(search['fetch_hosts'], [])
        self.assertEqual(search['daily_attempt_limit'], 60)
        self.assertEqual(search['limits']['max_calls'], 3)
    def test_continuous_policy_stages_five_attempts_without_arming_native_disk(self):
        from integrations.phase3_runtime.settings import continuous_proactive_candidate
        settings=continuous_proactive_candidate(self.settings)
        stage.stage(self.snapshot,self.root/'candidate',settings)
        runtime=json.loads((self.root/'candidate/data/config/phase3_runtime_config.json').read_text())
        guard=json.loads((self.root/'candidate/data/config/phase1_guard_config.json').read_text())
        native=json.loads((self.root/'candidate/data/config/astrbot_plugin_private_companion_config.json').read_text())
        self.assertFalse(runtime['proactive'])
        self.assertFalse(guard['phase3_proactive_enabled'])
        self.assertEqual(runtime['proactive_window']['max_per_day'],5)
        self.assertEqual(runtime['proactive_window']['max_generations_per_day'],5)
        self.assertEqual(runtime['context_compression_fallback_turns'],80)
        self.assertEqual(native['private_followup_config']['min_interval_minutes'],180)
        self.assertEqual(native['basic_config']['quiet_hours'],'00:00-09:00')
        self.assertEqual(native['proactive_reach_config']['max_daily_messages'],0)
        self.assertFalse(native['proactive_generation_config']['enable_llm_proactive_message'])
    def test_no_overwrite_or_nested_destination(self):
        with self.assertRaises(FileExistsError): stage.stage(self.snapshot, self.snapshot, self.settings)
        with self.assertRaises(ValueError): stage.stage(self.snapshot, self.snapshot / 'nested', self.settings)
    def test_current_limits_survive_policy_and_preserve_other_provider_fields(self):
        from integrations.phase2_policy.policy import validate_policy, PolicyViolation
        self.config['provider'][0]['timeout'] = 45
        candidate = stage.candidate(self.config, settings={**self.settings, 'search': True},
            admin_id='10001', platform_id='napcat', search_key='synthetic-search-key-not-real')
        self.assertEqual(candidate['provider'][0]['custom_extra_body']['max_tokens'], 8192)
        self.assertEqual(candidate['provider'][0]['timeout'], 120)
        self.assertEqual(candidate['provider_sources'][0]['timeout'], 120)
        self.assertEqual(candidate['platform_settings']['rate_limit'], {'time':60,'count':40,'strategy':'discard'})
        self.assertEqual(candidate['agent_runner']['config']['misc']['max_steps'], 10)
        self.assertEqual(candidate['provider'][1], self.config['provider'][1])
        self.assertEqual(candidate['provider_sources'][0]['key'], self.config['provider_sources'][0]['key'])
        self.assertEqual(self.config['provider'][0]['custom_extra_body']['max_tokens'], 2048)
        for key, invalid in (('max_steps', 11), ('max_tokens', 8193), ('max_tokens', True)):
            changed=copy.deepcopy(candidate)
            if key=='max_steps': changed['agent_runner']['config']['misc'][key]=invalid
            else: changed['provider'][0]['custom_extra_body'][key]=invalid
            with self.assertRaises(PolicyViolation):
                validate_policy(changed,'10001','napcat',capabilities={'search':True})
    def test_unsafe_input_permissions_or_symlink_rejected(self):
        file = self.snapshot / 'data/cmd_config.json'
        file.chmod(0o644)
        with self.assertRaises(ValueError): stage.stage(self.snapshot, self.root / 'a', self.settings)
        file.chmod(0o600)
        original = file.with_suffix('.private')
        file.rename(original)
        file.symlink_to(original)
        with self.assertRaises(OSError): stage.stage(self.snapshot, self.root / 'b', self.settings)
    def test_context_candidate_changes_only_native_scope_and_is_idempotent(self):
        context = stage.context_config
        new = context.make_candidate(self.config)
        compression = new['agent_runner']['config']['compression']
        self.assertEqual(compression['max_turns'], -1)
        self.assertEqual(compression['overflow_strategy'], 'llm_compress')
        self.assertEqual(compression['keep_recent_ratio'], .15)
        self.assertEqual(compression['provider_id'], '')
        self.assertEqual(compression['fallback_max_tokens'], 128000)
        self.assertEqual(new['provider'][0]['max_context_tokens'], 128000)
        self.assertEqual(context.make_candidate(new), new)
        self.assertEqual(self.config['agent_runner']['config']['compression']['max_turns'], 20)
        reverted = copy.deepcopy(new)
        reverted['agent_runner']['config']['compression'] = self.config['agent_runner']['config']['compression']
        reverted['provider'][0].pop('max_context_tokens')
        self.assertEqual(reverted, self.config)
        deployed = stage.candidate(self.config, settings=self.settings, admin_id='10001', platform_id='napcat')
        self.assertEqual(deployed['agent_runner']['config']['compression'], compression)
    def test_context_pending_preserves_live_bytes_secrets_and_refuses_overwrite(self):
        source = self.snapshot / 'data/cmd_config.json'
        original = b'\xef\xbb\xbf' + source.read_bytes()
        source.write_bytes(original)
        output = self.root / 'pending'
        runtime = self.snapshot / 'data/config/phase3_runtime_config.json'
        runtime_before = runtime.read_bytes()
        report = stage.context_config.save_candidate(source, output, runtime)
        self.assertEqual(report['status'], 'SAVED_NOT_APPLIED')
        self.assertTrue(report['source_unchanged'])
        self.assertFalse(report['production_applied'])
        self.assertFalse(report['service_restarted'])
        self.assertNotIn('agent_runner.config.compression', report['changed_scope'])
        self.assertEqual(len(report['changed_scope']), 8)
        self.assertEqual(report['failure_fallback_max_rounds'], 80)
        self.assertNotIn('synthetic-chat-key', json.dumps(report))
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(runtime.read_bytes(), runtime_before)
        runtime_candidate = json.loads((output / 'candidate-phase3_runtime_config.json').read_bytes())
        self.assertEqual(runtime_candidate.pop('context_compression_fallback_turns'), 80)
        self.assertEqual(runtime_candidate, self.settings)
        self.assertEqual((output / 'baseline-cmd_config.json').read_bytes(), original)
        self.assertEqual(output.stat().st_mode & 0o777, 0o700)
        for file in output.iterdir(): self.assertEqual(file.stat().st_mode & 0o777, 0o600)
        candidate = json.loads((output / 'candidate-cmd_config.json').read_text(encoding='utf-8-sig'))
        self.assertEqual(candidate['provider_sources'], self.config['provider_sources'])
        with self.assertRaises(FileExistsError): stage.context_config.save_candidate(source, output, runtime)
        with self.assertRaises(ValueError): stage.context_config.save_candidate(source, source.parent / 'pending', runtime)
    def test_context_pending_rejects_unsafe_source_and_changed_fixed_provider(self):
        source = self.snapshot / 'data/cmd_config.json'
        runtime = self.snapshot / 'data/config/phase3_runtime_config.json'
        source.chmod(0o644)
        with self.assertRaises(ValueError): stage.context_config.save_candidate(source, self.root / 'a', runtime)
        source.chmod(0o600)
        alias = source.parent / 'alias.json'
        alias.symlink_to(source)
        with self.assertRaises(OSError): stage.context_config.save_candidate(alias, self.root / 'b', runtime)
        changed = copy.deepcopy(self.config)
        changed['agent_runner']['config']['model']['provider_id'] = 'other'
        with self.assertRaises(ValueError): stage.context_config.make_candidate(changed)
        changed = copy.deepcopy(self.config)
        changed['provider'][0]['provider_source_id'] = 'other'
        with self.assertRaises(ValueError): stage.context_config.make_candidate(changed)
        changed = copy.deepcopy(self.config)
        changed['provider_sources'][0]['api_base'] = 'https://example.invalid'
        with self.assertRaises(ValueError): stage.context_config.make_candidate(changed)
        repo = self.root / 'synthetic-repo'
        repo.mkdir()
        (repo / '.git').mkdir()
        (repo / '.git/HEAD').write_text('ref: refs/heads/main\n')
        with self.assertRaises(ValueError): stage.context_config.save_candidate(source, repo / 'pending', runtime)
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
