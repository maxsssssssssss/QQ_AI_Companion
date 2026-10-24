"""Synthetic search configuration preparation only; no network or budget DB."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('search_pending_config', ROOT / 'scripts/phase3-search-config.py')
pending = importlib.util.module_from_spec(spec); spec.loader.exec_module(pending)


class SearchPendingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='qq-search-pending-'); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.config_dir = self.root / 'live/data/config'
        self.config_dir.mkdir(parents=True)
        self.source = self.config_dir / pending.CONFIG_NAME
        self.base = {'enabled': True, 'admin_id': '10001', 'platform_id': 'synthetic-onebot',
            'scope': 'reviewed', 'public_terms': ['Synthetic Topic'], 'fetch_hosts': ['example.org'],
            'daily_attempt_limit': 60, 'limits': {'max_calls': 3},
            'operator_extension': {'untouched': ['synthetic-only', {'value': 7}]}}
        self.write(self.base)

    def write(self, data, *, bom=True):
        self.source.write_bytes((b'\xef\xbb\xbf' if bom else b'') + json.dumps(data, ensure_ascii=False).encode())
        self.source.chmod(0o600)

    def test_candidate_changes_only_three_fields_and_keeps_smaller_limits_and_all_identity(self):
        self.base['enabled'] = False
        self.base['daily_attempt_limit'] = 17
        self.base['limits'] = {'max_calls': 1, 'tool_timeout': 7.5, 'turn_timeout': 15,
            'progress_timeout': 2, 'max_result_chars': 1500, 'max_progress_chars': 70, 'max_progress_tokens': 48}
        original = deepcopy(self.base); result = pending.make_candidate(self.base)
        self.assertEqual(self.base, original)
        self.assertEqual(result, {**original, 'scope': 'general_public', 'public_terms': [], 'fetch_hosts': []})
        result['operator_extension']['untouched'].append('new')
        self.assertEqual(self.base, original)

    def test_missing_default_limits_are_not_inserted_or_reset(self):
        for name in ('scope', 'daily_attempt_limit', 'limits'): self.base.pop(name)
        result = pending.make_candidate(self.base)
        self.assertEqual(result, {**self.base, 'scope': 'general_public', 'public_terms': [], 'fetch_hosts': []})
        self.assertNotIn('daily_attempt_limit', result); self.assertNotIn('limits', result)
        self.assertEqual(pending.make_candidate(result), result)

    def test_unknown_scope_and_invalid_identity_or_enabled_fail_closed(self):
        changes = [{'scope': 'unrestricted'}, {'scope': ''}, {'scope': None}, {'scope': True},
            {'admin_id': ''}, {'admin_id': 10001}, {'admin_id': '10001\n'}, {'admin_id': '0'},
            {'platform_id': ''}, {'platform_id': 'host/path'}, {'platform_id': 1}, {'enabled': 1},
            {'public_terms': 'not-a-list'}, {'fetch_hosts': [None]}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                pending.make_candidate({**self.base, **change})
        for name in ('admin_id', 'platform_id'):
            missing = deepcopy(self.base); missing.pop(name)
            with self.assertRaises(ValueError): pending.make_candidate(missing)

    def test_budgets_are_validated_without_broadening_or_clamping(self):
        for value in (0, -1, 61, 1000, True, 1.5, '60', None):
            with self.subTest(daily=value), self.assertRaises(ValueError):
                pending.make_candidate({**self.base, 'daily_attempt_limit': value})
        for value in (0, -1, 4, 6, True, 1.5, '3', None):
            with self.subTest(calls=value), self.assertRaises(ValueError):
                pending.make_candidate({**self.base, 'limits': {'max_calls': value}})
        for value in ({'tool_timeout': float('nan')}, {'tool_timeout': 121}, {'turn_timeout': 0},
                      {'progress_timeout': True}, {'max_result_chars': 127}, {'max_progress_chars': 241},
                      {'max_progress_tokens': False}, {'unknown_limit': 1}, None, []):
            with self.subTest(limits=value), self.assertRaises(ValueError):
                pending.make_candidate({**self.base, 'limits': value})

    def test_save_is_private_bom_preserving_read_only_and_metadata_has_no_identity(self):
        original = self.source.read_bytes(); before = hashlib.sha256(original).hexdigest()
        out = self.root / 'pending'
        with patch('socket.socket.connect', side_effect=AssertionError('network forbidden')):
            manifest = pending.save_candidate(self.source, out)
        self.assertEqual(manifest['status'], 'SAVED_NOT_APPLIED')
        self.assertTrue(manifest['source_unchanged']); self.assertFalse(manifest['production_applied'])
        self.assertFalse(manifest['service_restarted']); self.assertFalse(manifest['budget_database_opened'])
        self.assertFalse(manifest['credentials_read']); self.assertEqual(manifest['model_calls'], 0)
        self.assertEqual(manifest['qq_messages'], 0)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(manifest['source_config_sha256'], before)
        self.assertEqual((out / ('baseline-' + pending.CONFIG_NAME)).read_bytes(), original)
        raw = (out / ('candidate-' + pending.CONFIG_NAME)).read_bytes()
        self.assertTrue(raw.startswith(b'\xef\xbb\xbf'))
        self.assertEqual(json.loads(raw.decode('utf-8-sig')), pending.make_candidate(self.base))
        self.assertEqual(manifest['candidate_config_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(json.loads((out / 'manifest.json').read_text()), manifest)
        serialized = json.dumps(manifest)
        for private_value in ('synthetic-onebot', '10001', 'Synthetic Topic', 'example.org', 'operator_extension'):
            self.assertNotIn(private_value, serialized)
        self.assertEqual(out.stat().st_mode & 0o777, 0o700)
        for path in out.iterdir(): self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(list(out.iterdir())), 3)
        self.assertEqual(list(self.root.rglob('*.sqlite3')), [])

    def test_plain_utf8_remains_plain_and_existing_candidates_are_not_overwritten(self):
        self.write(self.base, bom=False); out = self.root / 'pending'
        pending.save_candidate(self.source, out)
        snapshots = {p: p.read_bytes() for p in out.iterdir()}
        self.assertFalse((out / ('candidate-' + pending.CONFIG_NAME)).read_bytes().startswith(b'\xef\xbb\xbf'))
        with self.assertRaises(FileExistsError): pending.save_candidate(self.source, out)
        self.assertEqual({p: p.read_bytes() for p in snapshots}, snapshots)
        other = self.root / 'pending-second'; pending.save_candidate(self.source, other)
        self.assertEqual({p: p.read_bytes() for p in snapshots}, snapshots)

    def test_output_must_be_outside_live_data_source_and_any_git_worktree(self):
        for out in (self.config_dir / 'pending', self.config_dir.parent / 'pending', ROOT / 'evidence/search-pending-forbidden'):
            with self.subTest(path=out), self.assertRaises(ValueError): pending.save_candidate(self.source, out)
            self.assertFalse(out.exists())
        for marker_is_file in (False, True):
            tree = self.root / ('git-file' if marker_is_file else 'git-dir'); tree.mkdir()
            if marker_is_file: (tree / '.git').write_text('gitdir: synthetic')
            else:
                (tree / '.git').mkdir(); (tree / '.git/HEAD').write_text('ref: refs/heads/main')
            with self.assertRaises(ValueError): pending.save_candidate(self.source, tree / 'pending')
            self.assertFalse((tree / 'pending').exists())
        with self.assertRaises(ValueError): pending.save_candidate(self.source, self.root / 'missing-parent/pending')
        self.assertFalse((self.root / 'missing-parent').exists())

    def test_unprotected_source_wrong_file_and_all_symlink_paths_are_rejected(self):
        self.source.chmod(0o644)
        with self.assertRaises(ValueError): pending.save_candidate(self.source, self.root / 'pending')
        self.source.chmod(0o600)
        wrong = self.root / 'deepseek-key'
        with patch.object(pending, '_read_private', side_effect=AssertionError('must not read wrong source')):
            with self.assertRaises(ValueError): pending.save_candidate(wrong, self.root / 'pending')
        alias_dir = self.root / 'alias'; alias_dir.symlink_to(self.config_dir, target_is_directory=True)
        with self.assertRaises(ValueError): pending.save_candidate(alias_dir / pending.CONFIG_NAME, self.root / 'pending')
        target = self.root / 'output-parent'; target.mkdir()
        output_alias = self.root / 'output-alias'; output_alias.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError): pending.save_candidate(self.source, output_alias / 'pending')
        link = self.root / 'pending'; link.symlink_to(self.root / 'nonexistent')
        with self.assertRaises(ValueError): pending.save_candidate(self.source, link)
        self.assertEqual(list(target.iterdir()), [])

    def test_source_change_after_writes_marks_candidate_stale_without_changing_source(self):
        original_read = pending._read_private; reads = 0
        def read(path):
            nonlocal reads
            reads += 1
            data = original_read(path)
            return data + b' ' if reads == 3 else data
        original = self.source.read_bytes(); out = self.root / 'stale'
        with patch.object(pending, '_read_private', side_effect=read):
            with self.assertRaisesRegex(ValueError, 'source_changed'): pending.save_candidate(self.source, out)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(json.loads((out / 'manifest.json').read_text())['status'], 'STALE_DO_NOT_APPLY')

    def test_duplicate_config_key_and_invalid_json_fail_before_creating_candidate(self):
        self.source.write_text('{"scope":"reviewed","scope":"general_public"}'); self.source.chmod(0o600)
        with self.assertRaisesRegex(ValueError, 'duplicate_configuration_key'):
            pending.save_candidate(self.source, self.root / 'pending')
        self.assertFalse((self.root / 'pending').exists())
        self.source.write_text('not json')
        with self.assertRaises(ValueError): pending.save_candidate(self.source, self.root / 'pending')
        self.assertFalse((self.root / 'pending').exists())


if __name__ == '__main__': unittest.main()
