"""Synthetic atomic overlay checks; never calls Docker or a real server."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('overlay',ROOT/'scripts/phase3-install-overlay.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class OverlayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.candidate=self.root/'candidate';self.deployment=self.root/'deployment';self.backup=self.root/'backup';self.journal=self.root/'journal'
        self.old=self.deployment/'state/data/cmd_config.json';self.old.parent.mkdir(parents=True);self.old.write_text('old synthetic config')
        (self.deployment/'state/data/chat.db').write_text('synthetic original records')
        for rel,text in [('data/cmd_config.json','new synthetic config'),('data/phase3/persona-v0.1.md','synthetic persona')]:
            p=self.candidate/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
        (self.candidate/'review-manifest.json').write_text(json.dumps({'source_config_sha256':module.sha(self.old)}))
        self.backup.mkdir()
        result={'status':'PASS','backup_integrity':'PASS','restore_integrity':'PASS','restored_copy_started':False}
        for name,key in [('astrbot-state.zip','astrbot_archive_sha256'),('napcat-opaque.zip','opaque_archive_sha256')]:
            (self.backup/name).write_bytes(b'synthetic archive');result[key]=module.sha(self.backup/name)
        (self.backup/'result.json').write_text(json.dumps(result))
    def tearDown(self):self.temp.cleanup()
    def run_install(self,**kwargs):
        return module.install(self.candidate,self.deployment,self.backup,self.journal,owner=(os.getuid(),os.getgid()),check_stopped=lambda _:None,**kwargs)
    def test_dry_run_does_not_mutate(self):
        self.assertEqual(self.run_install()['status'],'PLAN');self.assertFalse(self.journal.exists());self.assertEqual(self.old.read_text(),'old synthetic config')
    def test_overlay_keeps_original_data_and_private_journal(self):
        self.assertEqual(self.run_install(apply=True)['status'],'INSTALLED_SERVICES_STOPPED')
        self.assertEqual(self.old.read_text(),'new synthetic config')
        self.assertEqual((self.deployment/'state/data/chat.db').read_text(),'synthetic original records')
        self.assertEqual((self.journal/'original/data/cmd_config.json').read_text(),'old synthetic config')
        self.assertEqual(self.old.stat().st_mode&0o777,0o600)
        with self.assertRaises(ValueError):self.run_install(apply=True)
    def test_changed_config_and_invalid_backup_fail_before_mutation(self):
        self.old.write_text('newer user config')
        with self.assertRaisesRegex(ValueError,'config_changed'):self.run_install(apply=True)
        self.assertFalse(self.journal.exists())
        (self.backup/'astrbot-state.zip').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'backup_digest'):self.run_install(apply=True)
        self.assertEqual(self.old.read_text(),'newer user config')
    def test_database_or_symlink_overlay_rejected(self):
        file=self.candidate/'data/chat.db';file.write_text('must not replace')
        with self.assertRaises(ValueError):self.run_install()
        file.unlink();(self.candidate/'data/phase3/persona-v0.1.md').unlink()
        (self.candidate/'data/phase3/persona-v0.1.md').symlink_to(self.old)
        with self.assertRaises(ValueError):self.run_install()
    def test_partial_write_failure_restores_only_changed_files(self):
        original=module.publish;calls=0
        def failing(*args):
            nonlocal calls
            calls+=1
            if calls==2:raise OSError('synthetic failure')
            return original(*args)
        with patch.object(module,'publish',failing):
            with self.assertRaises(OSError):self.run_install(apply=True)
        self.assertEqual(self.old.read_text(),'old synthetic config')
        self.assertEqual((self.deployment/'state/data/chat.db').read_text(),'synthetic original records')


if __name__=='__main__':unittest.main()
