"""V0.3 candidate persona publication: real ZIP/SQLite, synthetic Docker, no network."""
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
spec = importlib.util.spec_from_file_location('persona_candidate_release_tests', ROOT/'scripts/phase3-deploy-persona-candidate.py')
d = importlib.util.module_from_spec(spec); sys.modules[spec.name] = d; spec.loader.exec_module(d)
from test_phase3_proactive_recovery_release import DockerFixture


class PersonaCandidateReleaseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.source = self.root/'source'; self.dep = self.root/'deployment'
        self.source.mkdir(); self.dep.mkdir(); self.state = self.dep/'state'
        (self.state/'data/config').mkdir(parents=True)
        self.configs = {'data/cmd_config.json': {'synthetic_model': 'same'},
            'data/config/phase3_runtime_config.json': {'proactive_window': {'max_per_day':8, 'max_generations_per_day':48}},
            'data/config/phase3_search_config.json': {'enabled':True,'scope':'general_public','daily_attempt_limit':60,'limits':{}}}
        for name, value in self.configs.items(): (self.state/name).write_text(json.dumps(value))
        (self.dep/'compose.yml').write_text('synthetic fixed compose'); (self.dep/'.env').write_text('SYNTHETIC_LOCK=1')
        for plugin in d.h.PLUGINS:
            p=self.state/'data/plugins'/plugin/'main.py'; p.parent.mkdir(parents=True); p.write_text('OLD=True\n')
        for relative in d.TARGETS:
            p=self.state/relative; p.parent.mkdir(parents=True,exist_ok=True)
            p.write_bytes((ROOT/'persona/phase3-v0.3.md').read_bytes() if relative==d.TEMPLATE_TARGET else b'OLD=True\n'); p.chmod(0o640)
        self.guard=self.state/'data/plugin_data/phase1_guard/guard.sqlite3'; self.guard.parent.mkdir(parents=True)
        with sqlite3.connect(self.guard) as db:
            db.executescript('CREATE TABLE turns(state TEXT); CREATE TABLE deliveries(state TEXT); CREATE TABLE outbound(state TEXT);')
        self.proactive=self.state/'data/plugin_data/phase3_runtime/proactive-controls.sqlite3'; self.proactive.parent.mkdir(parents=True)
        with sqlite3.connect(self.proactive) as db:
            db.executescript('CREATE TABLE proactive_generations(state TEXT); CREATE TABLE proactive_attempts(state TEXT);')
            db.execute("INSERT INTO proactive_generations VALUES ('skipped')")
        self.memory=self.state/'data/plugin_data/memory.db'
        with sqlite3.connect(self.memory) as db:
            db.execute('CREATE TABLE messages(text TEXT)'); db.execute("INSERT INTO messages VALUES ('synthetic existing chat')")
        self.search_budget=self.state/'data/plugin_data/phase3_search/budget.sqlite3'; self.search_budget.parent.mkdir(parents=True)
        with sqlite3.connect(self.search_budget) as db:
            db.execute('CREATE TABLE counts(attempts INTEGER)'); db.execute('INSERT INTO counts VALUES (7)')
        self.native=self.state/'data/plugin_data/companions.json'; self.native.write_text('{"daily_count":1,"history":"synthetic"}')
        for name in set(d.PINNED)|{d.CONTROLLER}:
            p=self.source/name; p.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(ROOT/name,p)
        for name in d.TARGETS.values():
            p=self.source/name; p.parent.mkdir(parents=True,exist_ok=True)
            if name in (d.TEMPLATE_SOURCE,'integrations/phase3_runtime/persona.py'): shutil.copy2(ROOT/name,p)
            else: p.write_text('NEW=True\n')
        # Frozen first-party renderer from the original reviewed release.
        renderer=self.source/'integrations/phase3_runtime/persona.py'
        shutil.copy2(ROOT/'tests/fixtures/persona-v03-candidate-renderer.py',renderer)
        self.assertEqual(d.sha(renderer),'c0b75ebf77b29ed3cf0bd5820515b0b7e8d6b167410a8ddd7352d1552c3a9051')
        self.baseline={name:d.sha(self.state/name) for name in d.source_inventory(self.state)}
        self.m={'schema':d.SCHEMA,'base_release':d.BASE_RELEASE,
            'files':{name:d.sha(self.source/name) for name in set(d.PINNED)|{d.CONTROLLER}|set(d.TARGETS.values())},
            'baseline_source_files':self.baseline,
            'baseline_template_sha256':d.sha(self.state/d.TEMPLATE_TARGET),
            'protected_config_files':{name:d.sha(self.state/name) for name in self.configs},
            'compose_sha256':d.sha(self.dep/'compose.yml'),'env_sha256':d.sha(self.dep/'.env')}
        self.save_manifest(); self.docker=DockerFixture(self.dep); self.services=d.Services(self.dep,self.docker)
        self.args=SimpleNamespace(source=self.source,deployment=self.dep,run_id='install',apply=True,rollback=False)
        native=patch.object(d,'verify_native'); native.start(); self.addCleanup(native.stop)

    def save_manifest(self): (self.source/'source-manifest.json').write_text(json.dumps(self.m))
    def execute(self): return d.execute(self.args,services=self.services)
    def data_hashes(self): return {str(p):d.sha(p) for p in (self.guard,self.memory,self.proactive,self.search_budget,self.native)}
    def assert_baseline(self):
        self.assertEqual({name:d.sha(self.state/name) for name in self.baseline},self.baseline)
        self.assertEqual(d.sha(self.state/d.TEMPLATE_TARGET),self.m['baseline_template_sha256'])
    def assert_config(self): self.assertEqual({name:d.sha(self.state/name) for name in self.configs},self.m['protected_config_files'])
    def assert_nap(self):
        self.assertTrue(self.docker.rows['napcat']['running'])
        self.assertEqual(self.docker.rows['napcat']['started_at'],'napcat-original')
        self.assertFalse(any(c[1] in ('stop','start') and c[-1]=='b'*64 for c in self.docker.calls))
    def loader(self, transform):
        original=d.load
        def load(name,path):
            module=original(name,path); transform(name,module); return module
        return patch.object(d,'load',side_effect=load)
    def assert_recovered(self,result):
        self.assertEqual(result['status'],'FAILED',result); self.assertTrue(result['astrbot_running'],result)
        self.assert_baseline(); self.assert_config(); self.assert_nap()

    def test_scope_exactly_two_persona_files_and_no_bubbles(self):
        self.assertEqual(d.TARGETS, {
            'data/plugins/phase3_runtime/persona.py':'integrations/phase3_runtime/persona.py',
            'data/phase3/persona-v0.1.md':'persona/phase3-v0.3-candidate.md'})
        plan_args=SimpleNamespace(**vars(self.args)); plan_args.apply=False
        result=d.execute(plan_args,services=self.services)
        self.assertEqual(result['persona_version'],'0.3-candidate')
        self.assertTrue(result['native_persona_update_on_start'])
        self.assertFalse(result['database_or_config_write'])

    def test_plan_readonly(self):
        self.args.apply=False; result=self.execute(); self.assertEqual(result['status'],'PLAN')
        self.assertEqual(set(result['target_files']),set(d.TARGETS)); self.assert_baseline(); self.assert_config()
        self.assertFalse((self.dep/'.astrbot-runtime-release.lock').exists())
        self.assertFalse(any(c[1] in ('stop','start') for c in self.docker.calls))

    def test_real_zip_restore_before_two_source_install(self):
        hashes=self.data_hashes(); result=self.execute(); self.assertEqual(result['status'],'INSTALLED_AWAITING_HEALTH',result)
        self.assertEqual(result['sqlite_checked_count'],4); self.assertEqual(self.data_hashes(),hashes)
        self.assert_config(); self.assert_nap()
        restored=self.dep/'backups/install/isolated-astrbot-restore'
        for rel,old in {**self.baseline,d.TEMPLATE_TARGET:self.m['baseline_template_sha256']}.items():
            self.assertEqual(d.sha(restored/rel),old)
            self.assertEqual(d.sha(self.state/rel),self.m['files'][d.TARGETS[rel]] if rel in d.TARGETS else old)
        self.assertEqual([c[1] for c in self.docker.calls if c[1] in ('stop','start')],['stop','start'])
        for rel in d.TARGETS: self.assertEqual((self.state/rel).stat().st_mode&0o777,0o640)

    def test_source_drift_or_added_file_blocks_before_stop(self):
        for rel in (next(iter(d.TARGETS)),'data/plugins/phase3_memory/main.py',d.TEMPLATE_TARGET):
            p=self.state/rel; raw=p.read_bytes(); p.write_text('DRIFT=True')
            with self.assertRaises(d.ReleaseError): self.execute()
            p.write_bytes(raw)
        p=self.state/'data/plugins/phase1_guard/extra.py'; p.write_text('EXTRA=True')
        with self.assertRaises(d.ReleaseError): self.execute()
        self.assertFalse(any(c[1] in ('stop','start') for c in self.docker.calls))

    def test_config_compose_and_image_environment_drift_blocks(self):
        for p in [*(self.state/n for n in self.configs),self.dep/'compose.yml',self.dep/'.env']:
            raw=p.read_bytes(); p.write_text('DRIFT')
            with self.assertRaises(d.ReleaseError): self.execute()
            p.write_bytes(raw)
        self.assert_baseline(); self.assertFalse(any(c[1] in ('stop','start') for c in self.docker.calls))

    def test_wrong_scope_or_budget_rejected_even_with_current_hash(self):
        rel='data/config/phase3_search_config.json'; original=self.configs[rel]
        for change in ({'scope':'reviewed'},{'daily_attempt_limit':61},{'limits':{'max_calls':4}},{'enabled':False}):
            (self.state/rel).write_text(json.dumps({**original,**change})); self.m['protected_config_files'][rel]=d.sha(self.state/rel); self.save_manifest()
            with self.assertRaisesRegex(d.ReleaseError,'existing_general_search'): self.execute()
        self.assertFalse(any(c[1] in ('stop','start') for c in self.docker.calls))

    def test_old_template_version_and_placeholder_contract_are_checked(self):
        template=self.state/d.TEMPLATE_TARGET; original=template.read_bytes()
        for body in ('[QQ_COMPANION_PERSONA_V0_2]\n'+ '{{preferred_address}}'*4,
                     '[QQ_COMPANION_PERSONA_V0_3]\n'+ '{{preferred_address}}'*3,
                     '[QQ_COMPANION_PERSONA_V0_3]\n'+ '{{preferred_address}}'*4+'[QQ_COMPANION_PERSONA_V0_3_CANDIDATE]'):
            template.write_text(body); self.m['baseline_template_sha256']=d.sha(template); self.save_manifest()
            with self.assertRaisesRegex(d.ReleaseError,'persona_template_version'): self.execute()
        template.write_bytes(original)
        self.assertFalse(any(c[1] in ('stop','start') for c in self.docker.calls))

    def test_candidate_template_must_be_exact_approved_even_with_new_hash(self):
        template=self.source/d.TEMPLATE_SOURCE; template.write_text(template.read_text()+'\nUnapproved addition.\n')
        self.m['files'][d.TEMPLATE_SOURCE]=d.sha(template); self.save_manifest()
        with self.assertRaisesRegex(d.ReleaseError,'approved_candidate_template_required'): self.execute()
        self.assertFalse(any(c[1] in ('stop','start') for c in self.docker.calls)); self.assert_baseline()

    def test_renderer_must_register_candidate_and_keep_previous_versions(self):
        p=self.source/'integrations/phase3_runtime/persona.py'; original=p.read_text()
        for text in (original.replace('phase3-v0.3-candidate.md','phase3-v0.3.md'),
                     original.replace('("0.3-candidate", 6)','("0.3-candidate", 5)'),
                     original.replace('("0.3", 4)','("0.3", 3)'),
                     original.replace('("0.2", 3)','("0.2", 4)')):
            p.write_text(text); self.m['files']['integrations/phase3_runtime/persona.py']=d.sha(p); self.save_manifest()
            with self.assertRaisesRegex(d.ReleaseError,'candidate_renderer_contract_required'): self.execute()
        self.assertFalse(any(c[1] in ('stop','start') for c in self.docker.calls)); self.assert_baseline()

    def test_template_hash_must_be_independent_of_plugin_inventory(self):
        self.m['baseline_source_files'][d.TEMPLATE_TARGET]=self.m['baseline_template_sha256']; self.save_manifest()
        with self.assertRaisesRegex(d.ReleaseError,'first_party_source_inventory_changed'): self.execute()
        self.m['baseline_source_files'].pop(d.TEMPLATE_TARGET); self.m.pop('baseline_template_sha256'); self.save_manifest()
        with self.assertRaisesRegex(d.ReleaseError,'baseline_template_hash_required'): self.execute()
        self.assertFalse(any(c[1] in ('stop','start') for c in self.docker.calls))

    def test_package_extra_target_or_pinned_helper_tampering_rejected(self):
        self.m['files']['integrations/phase3_search/main.py']='0'*64; self.save_manifest()
        with self.assertRaises(d.ReleaseError): self.execute()
        del self.m['files']['integrations/phase3_search/main.py']
        p=self.source/'scripts/backup.py'; p.write_text(p.read_text()+'\n# synthetic modification\n')
        self.m['files']['scripts/backup.py']=d.sha(p); self.save_manifest()
        with self.assertRaisesRegex(d.ReleaseError,'locked_helper'): self.execute()

    def test_active_passive_and_proactive_attempts_block(self):
        for path,table,value in ((self.guard,'turns','generating'),(self.guard,'deliveries','pending'),
                (self.guard,'outbound','pending'),(self.proactive,'proactive_generations','running'),(self.proactive,'proactive_attempts','pending')):
            with sqlite3.connect(path) as db: db.execute('INSERT INTO '+table+' VALUES (?)',(value,))
            with self.assertRaises(d.ReleaseError): self.execute()
            with sqlite3.connect(path) as db: db.execute('DELETE FROM '+table+' WHERE state=?',(value,))
        self.assertFalse(any(c[1] in ('stop','start') for c in self.docker.calls)); self.assert_baseline()

    def test_failed_restore_resumes_old_without_install(self):
        def transform(name,module):
            if name.endswith('_backup'):
                def fail(*args): raise RuntimeError('synthetic restore failure')
                module.restore_backup=fail
        before=self.data_hashes()
        with self.loader(transform): result=self.execute()
        self.assert_recovered(result); self.assertEqual(before,self.data_hashes())

    def test_corrupt_sqlite_backup_prevents_install(self):
        self.memory.write_bytes(b'SQLite format 3\x00not a database')
        self.assert_recovered(self.execute())

    def publish_failure(self,index):
        def transform(name,module):
            if name.endswith('_installer'):
                original=module.publish; calls=[]
                def publish(*args):
                    original(*args); calls.append(True)
                    if len(calls)==index: raise RuntimeError('synthetic publish failure')
                module.publish=publish
        before=self.data_hashes()
        with self.loader(transform): result=self.execute()
        self.assert_recovered(result); self.assertEqual(before,self.data_hashes())

    def test_first_publish_failure_restores_all_sources(self): self.publish_failure(1)
    def test_last_publish_failure_restores_all_sources(self): self.publish_failure(2)

    def test_failed_start_restores_all_sources_and_resumes_old(self):
        self.docker.fail_starts=1; self.assert_recovered(self.execute())

    def test_rollback_keeps_new_chats_memory_budget_and_proactive_state(self):
        self.assertEqual(self.execute()['status'],'INSTALLED_AWAITING_HEALTH')
        with sqlite3.connect(self.memory) as db: db.execute("INSERT INTO messages VALUES ('synthetic new chat')")
        with sqlite3.connect(self.search_budget) as db: db.execute('UPDATE counts SET attempts=8')
        with sqlite3.connect(self.proactive) as db: db.execute("INSERT INTO proactive_generations VALUES ('skipped')")
        self.native.write_text('{"daily_count":2,"history":"synthetic new"}'); before=self.data_hashes()
        self.args.rollback=True; self.args.run_id='rollback'; result=self.execute()
        self.assertEqual(result['status'],'SOURCE_ROLLED_BACK_AWAITING_HEALTH',result)
        self.assertEqual(before,self.data_hashes()); self.assert_baseline(); self.assert_config(); self.assert_nap()
        restored=self.dep/'backups/rollback/isolated-astrbot-restore/data/plugin_data/memory.db'
        with sqlite3.connect(restored) as db: self.assertEqual(db.execute('SELECT count(*) FROM messages').fetchone()[0],2)

    def test_rollback_source_drift_blocks_before_stop(self):
        self.assertEqual(self.execute()['status'],'INSTALLED_AWAITING_HEALTH')
        (self.state/next(iter(d.TARGETS))).write_text('DRIFT=True')
        self.args.rollback=True; self.args.run_id='rollback'; count=len(self.docker.calls)
        with self.assertRaises(d.ReleaseError): self.execute()
        self.assertFalse(any(c[1] in ('stop','start') for c in self.docker.calls[count:])); self.assert_nap()

    def test_symlink_source_rejected(self):
        p=self.state/next(iter(d.TARGETS)); external=self.root/'outside'; external.write_bytes(p.read_bytes()); p.unlink(); p.symlink_to(external)
        with self.assertRaises(d.ReleaseError): self.execute()
        self.assertFalse(any(c[1] in ('stop','start') for c in self.docker.calls))

    def test_repeated_operation_does_not_republish(self):
        self.assertEqual(self.execute()['status'],'INSTALLED_AWAITING_HEALTH'); count=len(self.docker.calls)
        with self.assertRaises(d.ReleaseError): self.execute()
        self.assertFalse(any(c[1] in ('stop','start') for c in self.docker.calls[count:]))


if __name__=='__main__': unittest.main(verbosity=2)
