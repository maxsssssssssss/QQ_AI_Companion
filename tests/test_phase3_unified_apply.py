"""Synthetic r4 installation/rollback tests. No Docker, network or real data."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('unified_apply', ROOT / 'scripts/phase3-apply-unified.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class UnifiedApplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.deployment, self.candidate, self.journal = [self.root / p for p in ('deployment', 'r4', 'journal')]
        self.identity = dict(admin_id='12345678', platform_id='synthetic')
        source = dict(id='deepseek', enable=True, type='openai_chat_completion', api_base='https://api.deepseek.com',
                      custom_extra_body={'thinking': {'type': 'disabled'}}, key=['synthetic-secret'])
        model = dict(id='deepseek/flash', provider_source_id='deepseek', enable=True,
                     model='deepseek-flash', max_context_tokens=32000)
        self.base = {
            'cmd': {'provider': [model], 'provider_sources': [source],
                'agent_runner': {'runner_type': 'local', 'config': {'model': {'provider_id': 'deepseek/flash'},
                    'compression': {'max_turns': 20, 'overflow_strategy': 'truncate', 'instruction': 'old'}}},
                'platform_settings': {'segmented_reply': {'enable': False}}},
            'runtime': dict(enabled=True, persona=True, natural_chat=True, memory=True, search=True, proactive=False,
                background_web=False, context_compression_fallback_turns=0,
                proactive_window=dict(mode='trial', window_start=0, window_end=0, max_per_day=2,
                    max_generations_per_day=2, cooldown=14400, quiet_hours=[23, 9])),
            'private': dict(enable_llm_proactive_message=False, max_daily_messages=0,
                enable_auto_user_profile_creation=True, enable_meal_care_proactive=True,
                enabled_proactive_actions='message,extra', legacy_compat_config={'enabled_proactive_actions': 'message,extra'},
                quiet_hours='23:00-09:00', min_interval_minutes=240, basic_config={'quiet_hours': '23:00-09:00'},
                private_followup_config={'min_interval_minutes': 240}),
            'guard': dict(phase3_admin_id='12345678', phase3_platform_id='synthetic', phase3_proactive_enabled=False,
                phase3_casual_split_fallback_enabled=True, phase3_bubble_min_interval=0.6, phase3_bubble_max_interval=1.8),
            'search': dict(enabled=True, **self.identity, scope='reviewed', public_terms=['synthetic'],
                fetch_hosts=['example.com'], daily_attempt_limit=60, limits={'max_calls': 3}),
        }
        self.next = deepcopy(self.base)
        self.next['cmd']['provider'][0]['max_context_tokens'] = 128000
        self.next['cmd']['agent_runner']['config']['compression'].update(max_turns=-1, overflow_strategy='llm_compress', instruction='new')
        self.next['runtime'].update(context_compression_fallback_turns=80,
            proactive_window=dict(mode='continuous', window_start=0, window_end=0, max_per_day=5,
                max_generations_per_day=5, cooldown=10800, quiet_hours=[0, 9]))
        self.next['private'].update(enable_auto_user_profile_creation=False, enable_meal_care_proactive=False,
            enabled_proactive_actions='message', legacy_compat_config={'enabled_proactive_actions': 'message'},
            quiet_hours='00:00-09:00', min_interval_minutes=180, basic_config={'quiet_hours': '00:00-09:00'},
            private_followup_config={'min_interval_minutes': 180})
        self.next['guard'].update(phase3_bubble_min_interval=1.0, phase3_bubble_max_interval=2.0)
        self.next['search'].update(scope='general_public', public_terms=[], fetch_hosts=[])
        self.manifest = dict(status='SYNCED_NOT_APPLIED', runtime_overlay=list(m.OVERLAY), source_files={},
            candidate_config_hashes={}, configuration_changes={}, live_baseline={})
        for name in m.OVERLAY:
            new = ('# synthetic candidate ' + name + '\n').encode()
            old = ('# synthetic old ' + name + '\n').encode()
            source_name, dest_name = 'integrations/' + name, 'state/data/plugins/' + name
            self.put(self.candidate / 'source' / source_name, new)
            self.put(self.deployment / dest_name, old)
            self.manifest['source_files'][source_name] = m.sha(new)
            self.manifest['live_baseline'][dest_name] = m.sha(old)
        for kind, (name, target) in m.CONFIGS.items():
            self.put(self.deployment / target, m.encode(self.base[kind]))
            self.manifest['live_baseline'][target] = m.sha(m.encode(self.base[kind]))
            for prefix, values in [('baseline-', self.base), ('candidate-', self.next)]:
                raw = m.encode(values[kind])
                self.put(self.candidate / 'config-candidate' / (prefix + name), raw)
                self.manifest['candidate_config_hashes'][prefix + name] = m.sha(raw)
            self.manifest['configuration_changes'][kind] = list(map(m.label, m.diff(self.base[kind], self.next[kind])))
        for name, raw in [('state/data/config/phase2_policy_config.json', m.encode(self.identity)),
                          ('state/data/phase3/persona-v0.2.md', b'preserve persona'),
                          ('compose.yml', b'preserve compose'), ('.env', b'preserve private environment')]:
            self.put(self.deployment / name, raw)
            self.manifest['live_baseline'][name] = m.sha(raw)
        self.put(self.deployment / 'state/data/chat.db', b'new chat must be preserved')
        self.put(self.deployment / 'state/data/login-state', b'preserve login')
        self.finish_candidate()

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, path, raw):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def finish_candidate(self):
        raw = m.encode(self.manifest)
        self.put(self.candidate / 'manifest.json', raw)
        self.expected = m.sha(raw)
        self.put(self.candidate / 'sync-receipt.json', m.encode(dict(status='SYNCED_NOT_APPLIED', production_applied=False,
            manifest_sha256=self.expected, source_hashes=self.manifest['source_files'],
            services={'astrbot': {'image': m.PINNED_ASTRBOT_IMAGE}})))

    def run_install(self, **kwargs):
        return m.install(self.candidate, self.deployment, self.journal,
            expected_manifest_sha256=self.expected, check_stopped=lambda *_: None, **kwargs)

    def config_path(self, kind):
        return self.deployment / m.CONFIGS[kind][1]

    def snapshot(self):
        return {p.relative_to(self.deployment).as_posix(): p.read_bytes()
                for p in self.deployment.rglob('*') if p.is_file()}

    def migration(self):
        store = self.deployment / 'state/data/plugin_data/astrbot_plugin_private_companion/companions.json'
        self.put(store, b'{"synthetic": "clean native target"}')
        receipt = self.root / 'native-result.json'
        self.put(receipt, m.encode(dict(status='APPLIED', native_api_called=True, unique_existing_target=True,
            daily_limit=5, global_generation_enabled=False, candidate_sha256=m.sha(store.read_bytes()))))
        return receipt, store

    def test_plan_read_only(self):
        before = self.snapshot()
        result = self.run_install()
        self.assertEqual(result['status'], 'PLAN')
        self.assertEqual(result['file_count'], 18)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.journal.exists())

    def test_exact_inventory_install_and_rollback_preserve_data(self):
        before = self.snapshot()
        self.assertEqual(self.run_install(apply=True)['status'], 'INSTALLED_SERVICES_STOPPED')
        after = self.snapshot()
        self.assertEqual({p for p in before if before[p] != after[p]}, m.DESTINATIONS)
        for kind in self.next:
            self.assertEqual(m.decode(self.config_path(kind).read_bytes()), self.next[kind])
        result = m.rollback(self.deployment, self.journal, apply=True, check_stopped=lambda *_: None)
        self.assertEqual(result['status'], 'ROLLED_BACK_SERVICES_STOPPED')
        self.assertEqual(before, self.snapshot())
        for path in self.journal.rglob('*'):
            if path.is_file():
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            elif path.is_dir():
                self.assertEqual(path.stat().st_mode & 0o777, 0o700)

    def test_only_reviewed_new_compression_file_may_be_created_and_removed(self):
        name = next(iter(m.OPTIONAL_NEW))
        (self.deployment / name).unlink()
        del self.manifest['live_baseline'][name]
        self.finish_candidate()
        before = self.snapshot()
        self.run_install(apply=True)
        self.assertTrue((self.deployment / name).is_file())
        m.rollback(self.deployment, self.journal, apply=True, check_stopped=lambda *_: None)
        self.assertEqual(before, self.snapshot())

    def test_new_compression_file_must_not_overwrite_unreviewed_existing(self):
        name = next(iter(m.OPTIONAL_NEW))
        del self.manifest['live_baseline'][name]
        self.finish_candidate()
        with self.assertRaisesRegex(m.ReleaseError, 'unexpected_new_live_file'):
            self.run_install()

    def test_field_merge_preserves_unrelated_latest_edit_and_credentials(self):
        current = deepcopy(self.base['cmd'])
        current['new_user_setting'] = 'preserve'
        current['provider_sources'][0]['key'] = ['replacement-private-key']
        self.put(self.config_path('cmd'), m.encode(current))
        self.run_install(apply=True)
        after = m.decode(self.config_path('cmd').read_bytes())
        self.assertEqual(after['new_user_setting'], 'preserve')
        self.assertEqual(after['provider_sources'][0]['key'], ['replacement-private-key'])
        self.assertNotIn('replacement-private-key', json.dumps(m.rollback(self.deployment, self.journal)))

    def test_conflicting_reviewed_field_blocks_without_mutation(self):
        current = deepcopy(self.base['runtime'])
        current['context_compression_fallback_turns'] = 99
        self.put(self.config_path('runtime'), m.encode(current))
        before = self.snapshot()
        with self.assertRaisesRegex(m.ReleaseError, 'reviewed_field_conflict'):
            self.run_install(apply=True)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.journal.exists())

    def test_equivalent_json_formatting_is_not_value_drift(self):
        self.put(self.config_path('guard'), json.dumps(self.base['guard'], sort_keys=True).encode())
        self.run_install(apply=True)
        after = m.decode(self.config_path('guard').read_bytes())
        self.put(self.config_path('guard'), json.dumps(after, sort_keys=True).encode())
        self.assertEqual(m.rollback(self.deployment, self.journal)['status'], 'ROLLBACK_PLAN')

    def test_boolean_numeric_conflict_not_equivalent(self):
        self.assertFalse(m.json_equal({'x': True}, {'x': 1}))
        current = deepcopy(self.base['guard'])
        current['phase3_bubble_min_interval'] = True
        self.put(self.config_path('guard'), m.encode(current))
        with self.assertRaisesRegex(m.ReleaseError, 'reviewed_field_conflict'):
            self.run_install()

    def test_provider_reordering_is_not_silently_merged_by_index(self):
        current = deepcopy(self.base['cmd'])
        current['provider'].insert(0, dict(id='unrelated', max_context_tokens=32000))
        current['provider'][1]['max_context_tokens'] = 128000
        self.put(self.config_path('cmd'), m.encode(current))
        with self.assertRaisesRegex(m.ReleaseError, 'provider_order_or_identity_changed'):
            self.run_install()

    def test_journal_rejects_git_worktree(self):
        git = self.root / '.git'
        git.mkdir()
        (git / 'HEAD').write_text('synthetic')
        with self.assertRaisesRegex(m.ReleaseError, 'private_journal_must_be_outside_git'):
            self.run_install(apply=True)

    def test_receipt_cannot_switch_pinned_image(self):
        path = self.candidate / 'sync-receipt.json'
        receipt = m.decode(path.read_bytes())
        receipt['services']['astrbot']['image'] = 'sha256:' + 'b' * 64
        self.put(path, m.encode(receipt))
        with self.assertRaisesRegex(m.ReleaseError, 'release_image_mismatch'):
            self.run_install()

    def test_source_hash_drift_rejected(self):
        self.put(self.candidate / 'source/integrations' / m.OVERLAY[0], b'tampered')
        with self.assertRaisesRegex(m.ReleaseError, 'candidate_file_digest'):
            self.run_install()

    def test_live_code_drift_rejected(self):
        self.put(self.deployment / 'state/data/plugins' / m.OVERLAY[0], b'newer code')
        with self.assertRaisesRegex(m.ReleaseError, 'live_baseline_drift'):
            self.run_install()

    def test_manifest_external_hash_required(self):
        self.manifest['extra'] = 'changed'
        self.put(self.candidate / 'manifest.json', m.encode(self.manifest))
        with self.assertRaisesRegex(m.ReleaseError, 'manifest_digest'):
            self.run_install()

    def test_config_unreviewed_field_rejected_even_if_manifest_agrees(self):
        name = 'candidate-phase1_guard_config.json'
        value = deepcopy(self.next['guard'])
        value['phase3_admin_id'] = '99999999'
        raw = m.encode(value)
        self.put(self.candidate / 'config-candidate' / name, raw)
        self.manifest['candidate_config_hashes'][name] = m.sha(raw)
        self.manifest['configuration_changes']['guard'].append('phase3_admin_id')
        self.finish_candidate()
        with self.assertRaisesRegex(m.ReleaseError, 'unreviewed_config_field'):
            self.run_install()

    def test_no_arbitrary_overlay_or_database_allowed(self):
        self.manifest['runtime_overlay'].append('state/data/chat.db')
        self.finish_candidate()
        with self.assertRaisesRegex(m.ReleaseError, 'overlay_inventory'):
            self.run_install()

    def test_symlink_rejected(self):
        path = self.candidate / 'source/integrations' / m.OVERLAY[0]
        path.unlink()
        path.symlink_to(self.deployment / 'state/data/plugins' / m.OVERLAY[0])
        with self.assertRaisesRegex(m.ReleaseError, 'symlink'):
            self.run_install()

    def test_partial_failure_restores_previous_code_and_config(self):
        before = self.snapshot()
        original = m.replace
        writes = 0
        def fail(path, raw, uid, gid):
            nonlocal writes
            if self.deployment in path.parents:
                writes += 1
                if writes == 4:
                    raise OSError('synthetic disk error')
            return original(path, raw, uid, gid)
        with patch.object(m, 'replace', fail), self.assertRaises(OSError):
            self.run_install(apply=True)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(m.decode((self.journal / 'status.json').read_bytes())['status'], 'FAILED_RESTORED')

    def test_proactive_requires_verified_applied_native_migration(self):
        with self.assertRaisesRegex(m.ReleaseError, 'native_migration_receipt_required'):
            self.run_install(enable_proactive=True)
        receipt, _ = self.migration()
        result = self.run_install(apply=True, enable_proactive=True, native_migration_result=receipt)
        self.assertTrue(result['proactive_enabled'])
        self.assertTrue(m.decode(self.config_path('runtime').read_bytes())['proactive'])
        self.assertTrue(m.decode(self.config_path('guard').read_bytes())['phase3_proactive_enabled'])
        self.assertFalse(m.decode(self.config_path('private').read_bytes())['enable_llm_proactive_message'])

    def test_changed_native_target_blocks_arming(self):
        receipt, store = self.migration()
        self.put(store, b'newer native content')
        with self.assertRaisesRegex(m.ReleaseError, 'native_store_changed'):
            self.run_install(enable_proactive=True, native_migration_result=receipt)

    def test_clean_baseline_proof_checked_and_hash_recorded(self):
        proof = self.root / 'clean-result.json'
        self.put(proof, m.encode(dict(status='PASS', old_content_removed=True, persona_profile_preserved=True)))
        self.run_install(apply=True, clean_baseline_receipt=proof)
        metadata = m.decode((self.journal / 'manifest.json').read_bytes())
        self.assertEqual(metadata['clean_baseline_receipt_sha256'], m.sha(proof.read_bytes()))

    def test_invalid_clean_baseline_proof_blocks(self):
        proof = self.root / 'clean-result.json'
        self.put(proof, m.encode(dict(status='PASS', old_content_removed=False, persona_profile_preserved=True)))
        with self.assertRaisesRegex(m.ReleaseError, 'clean_baseline_receipt_invalid'):
            self.run_install(clean_baseline_receipt=proof)

    def test_rollback_refuses_newer_config_values(self):
        self.run_install(apply=True)
        config = m.decode(self.config_path('guard').read_bytes())
        config['phase3_bubble_max_interval'] = 3
        self.put(self.config_path('guard'), m.encode(config))
        before = self.snapshot()
        with self.assertRaisesRegex(m.ReleaseError, 'rollback_would_overwrite_newer_data'):
            m.rollback(self.deployment, self.journal, apply=True, check_stopped=lambda *_: None)
        self.assertEqual(before, self.snapshot())

    def test_stopped_check_blocks_writes(self):
        before = self.snapshot()
        def running(*_):
            raise m.ReleaseError('pinned_astrbot_must_be_stopped')
        with self.assertRaisesRegex(m.ReleaseError, 'must_be_stopped'):
            m.install(self.candidate, self.deployment, self.journal, apply=True,
                expected_manifest_sha256=self.expected, check_stopped=running)
        self.assertEqual(before, self.snapshot())

    def test_crash_after_partial_apply_can_restore_matching_files(self):
        self.run_install(apply=True)
        m.replace(self.journal / 'status.json', m.encode({'status': 'APPLYING'}), os.getuid(), os.getgid())
        destination = 'state/data/plugins/' + m.OVERLAY[0]
        self.put(self.deployment / destination, (self.journal / 'before' / destination).read_bytes())
        result = m.rollback(self.deployment, self.journal, apply=True, check_stopped=lambda *_: None)
        self.assertEqual(result['status'], 'ROLLED_BACK_SERVICES_STOPPED')


if __name__ == '__main__':
    unittest.main()
