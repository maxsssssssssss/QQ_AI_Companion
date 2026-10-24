"""Protected preparation only: no live config write or implicit activation."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("test_proactive_pending", ROOT / "scripts/phase3-proactive-config.py")
pending = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pending)


class ProactivePendingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qq-proactive-pending-")
        self.root = Path(self.temp.name)
        self.config = self.root / "live/data/config"
        self.config.mkdir(parents=True)
        self.runtime = self.config / "phase3_runtime_config.json"
        self.native = self.config / "private.json"
        self.identity = self.config / "identity.json"
        self.write(self.runtime, dict(enabled=True, persona=True, natural_chat=True, memory=True,
            proactive=False, memory_topic_aliases={'games':['合成主题']}, preferred_address='合成称呼'))
        self.write(self.native, {'synthetic_secret':'never-in-manifest', 'other':{'preserved':True},
                                'enable_llm_proactive_message':False,'max_daily_messages':0})
        self.write(self.identity, {'admin_id':'10001','platform_id':'fixture'})
    def tearDown(self): self.temp.cleanup()
    def write(self, path, value):
        path.write_bytes(b'\xef\xbb\xbf' + (json.dumps(value,ensure_ascii=False)+'\n').encode())
        path.chmod(0o600)
    def save(self, output, **kw):
        return pending.save_candidate(self.runtime,self.native,self.identity,output,**kw)
    def inherited(self):
        folder=self.root/'previous';folder.mkdir(mode=0o700)
        candidate=json.loads(self.runtime.read_bytes());candidate['context_compression_fallback_turns']=80
        file=folder/'candidate-phase3_runtime_config.json';self.write(file,candidate)
        manifest=folder/'manifest.json'
        self.write(manifest,{'status':'SAVED_NOT_APPLIED','production_applied':False,
            'source_runtime_config_sha256':hashlib.sha256(self.runtime.read_bytes()).hexdigest(),
            'candidate_runtime_config_sha256':hashlib.sha256(file.read_bytes()).hexdigest()})
        return manifest
    def test_preserves_prior_pending_and_live_bytes_while_staying_cold(self):
        prior=self.inherited();original={p:p.read_bytes() for p in (self.runtime,self.native,self.identity,prior,prior.parent/'candidate-phase3_runtime_config.json')}
        report=self.save(self.root/'new',pending_manifest=prior)
        self.assertEqual(report['status'],'SAVED_NOT_APPLIED')
        self.assertTrue(report['source_unchanged'])
        self.assertFalse(report['proactive_enabled'])
        self.assertFalse(report['native_capability_changed'])
        self.assertEqual(report['preserved_context_compression_fallback_turns'],80)
        self.assertNotIn('never-in-manifest',json.dumps(report))
        self.assertEqual(original,{p:p.read_bytes() for p in original})
        candidate=json.loads((self.root/'new/candidate-phase3_runtime_config.json').read_bytes())
        self.assertFalse(candidate['proactive'])
        self.assertEqual(candidate['context_compression_fallback_turns'],80)
        self.assertEqual(candidate['proactive_window']['max_generations_per_day'],5)
        native=json.loads((self.root/'new/candidate-private_companion_config.json').read_bytes())
        self.assertEqual(native['other'],{'preserved':True})
        self.assertEqual(native['synthetic_secret'],'never-in-manifest')
        self.assertEqual(native['proactive_reach_config']['max_daily_messages'],0)
        self.assertFalse(native['proactive_generation_config']['enable_llm_proactive_message'])
        self.assertEqual(native['private_followup_config']['min_interval_minutes'],180)
        self.assertEqual(native['basic_config']['quiet_hours'],'00:00-09:00')
        for file in (self.root/'new').iterdir(): self.assertEqual(file.stat().st_mode & 0o777,0o600)
        self.assertEqual((self.root/'new').stat().st_mode & 0o777,0o700)
        self.assertTrue((self.root/'new/candidate-phase3_runtime_config.json').read_bytes().startswith(b'\xef\xbb\xbf'))
    def test_stale_pending_is_rejected_instead_of_overwriting_new_progress(self):
        prior=self.inherited()
        cfg=json.loads(self.runtime.read_bytes());cfg['search']=False;self.write(self.runtime,cfg)
        with self.assertRaises(ValueError):self.save(self.root/'new',pending_manifest=prior)
        self.assertFalse((self.root/'new').exists())
    def test_tampered_prior_candidate_is_rejected(self):
        prior=self.inherited();self.write(prior.parent/'candidate-phase3_runtime_config.json',{'proactive':False})
        with self.assertRaises(ValueError):self.save(self.root/'new',pending_manifest=prior)
        self.assertFalse((self.root/'new').exists())
    def test_never_overwrites_source_or_previous_candidate(self):
        for destination in (self.config/'new',self.config.parent/'new'):
            with self.assertRaises(ValueError):self.save(destination)
        output=self.root/'new';self.save(output)
        with self.assertRaises(FileExistsError):self.save(output)
        cfg=json.loads(self.runtime.read_bytes());cfg['proactive']=True;self.write(self.runtime,cfg)
        with self.assertRaises(ValueError):self.save(self.root/'another')
    def test_rejects_unprotected_input_and_symlink(self):
        self.runtime.chmod(0o644)
        with self.assertRaises(ValueError):self.save(self.root/'new')
        self.runtime.chmod(0o600)
        alias=self.config/'alias.json';alias.symlink_to(self.runtime)
        with self.assertRaises(OSError):pending.save_candidate(alias,self.native,self.identity,self.root/'new')


if __name__=='__main__':unittest.main()
