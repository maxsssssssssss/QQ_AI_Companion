"""Frequency-only publication: real ZIP/SQLite, synthetic Docker and private state."""
from copy import deepcopy
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

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('frequency_release_tests',ROOT/'scripts/phase3-deploy-proactive-frequency.py')
d=importlib.util.module_from_spec(spec);sys.modules[spec.name]=d;spec.loader.exec_module(d)
from test_phase3_proactive_recovery_release import DockerFixture

RUNTIME='data/config/phase3_runtime_config.json'
PRIVATE='data/config/astrbot_plugin_private_companion_config.json'
NATIVE='data/plugin_data/astrbot_plugin_private_companion/companions.json'
POLICY_CONFIG='data/config/phase2_policy_config.json'
UNEXPECTED_SOURCE='data/plugins/phase3_runtime/unexpected.py'


class ProactiveFrequencyReleaseTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.source=self.root/'source';self.dep=self.root/'deployment'
        self.source.mkdir();self.dep.mkdir();self.state=self.dep/'state'
        (self.state/'data/config').mkdir(parents=True)
        self.save_json('data/cmd_config.json',{'unrelated_model':'synthetic-existing','admins_id':['10001'],
            'platform_settings':{'enable_id_white_list':True,'id_whitelist':['test:FriendMessage:10001'],'unique_session':True}})
        self.save_json('data/config/phase1_guard_config.json',{'phase3_admin_id':'10001','phase3_platform_id':'test'})
        self.save_json(POLICY_CONFIG,{'admin_id':'10001','platform_id':'test'})
        self.runtime={'enabled':True,'persona':True,'natural_chat':True,'memory':True,'search':True,'proactive':True,
            'context_compression_fallback_turns':80,
            'proactive_window':{'mode':'continuous','window_start':0,'window_end':0,'max_per_day':8,
                'max_generations_per_day':48,'cooldown':7200,'quiet_hours':[23,9]},
            'proactive_history':{'summary_limit':8,'ordinary_retention_hours':24},
            'synthetic_keep':{'history':'must remain'}}
        self.private={'basic_config':{'quiet_hours':'23:00-09:00','unrelated':True},
            'private_followup_config':{'min_interval_minutes':120,'idle_minutes':60,'dedup_window':240},
            'proactive_generation_config':{'proactive_generation_history_limit':80,'proactive_history_context_mode':'compact',
                'proactive_history_recent_raw_count':8,'proactive_history_max_chars':16000,
                'enable_llm_proactive_message':False,'max_daily_messages':0},
            'storage_backend':'json','synthetic_keep':{'secret':'synthetic-placeholder'}}
        self.native={'users':{'user-a':{'umo':'test:FriendMessage:10001','identity_subject_id':'10001',
            'identity_platform_id':'test','identity_platform_kind':'qq','identity_adapter_instance_id':'test',
            'identity_bot_id':'20002','user_id':'10001',
            'private_companion_enabled':True,'proactive_private_enabled':True,'enabled':True,
            'unified_profile_capabilities':{'proactive_private_enabled':True,'private_companion_enabled':True},
            'proactive_daily_limit':8,'proactive_min_interval_minutes':-1,'proactive_idle_minutes':-1,'daily_count':1,
            'next_proactive_ts':123456,'pending_candidates':[{'id':'synthetic-candidate','state':'pending'}],
            'last_message':'synthetic retained history','awaiting_reply':True,'unanswered_count':1,
            'paused_until':0}},'metadata':{'schema':'synthetic-retained'}}
        for rel,value in [(RUNTIME,self.runtime),(PRIVATE,self.private),(NATIVE,self.native)]:self.save_json(rel,value)
        (self.dep/'compose.yml').write_text('synthetic fixed compose')
        (self.dep/'.env').write_text('SYNTHETIC_FIXED_IMAGE=1')
        for plugin in d.h.PLUGINS:
            p=self.state/'data/plugins'/plugin/'main.py';p.parent.mkdir(parents=True);p.write_text('SYNTHETIC_OLD = True\n')
        for rel in d.TARGETS:
            p=self.state/rel;p.parent.mkdir(parents=True,exist_ok=True)
            p.write_text('{"synthetic_old":true}' if p.suffix=='.json' else 'SYNTHETIC_OLD = True\n');p.chmod(0o640)
        self.guard=self.state/'data/plugin_data/phase1_guard/guard.sqlite3';self.guard.parent.mkdir(parents=True)
        with sqlite3.connect(self.guard) as db:
            db.executescript('CREATE TABLE turns(state TEXT); CREATE TABLE deliveries(state TEXT); CREATE TABLE outbound(state TEXT);')
        self.proactive=self.state/'data/plugin_data/phase3_runtime/proactive-controls.sqlite3';self.proactive.parent.mkdir(parents=True)
        with sqlite3.connect(self.proactive) as db:
            db.executescript('CREATE TABLE proactive_generations(state TEXT); CREATE TABLE proactive_attempts(state TEXT);')
            db.execute("INSERT INTO proactive_generations VALUES ('skipped')")
            db.execute("INSERT INTO proactive_attempts VALUES ('sent')")
        self.memory=self.state/'data/plugin_data/memory.db'
        with sqlite3.connect(self.memory) as db:
            db.execute('CREATE TABLE messages(text TEXT)');db.execute("INSERT INTO messages VALUES ('synthetic retained chat')")
        for rel in set(d.PINNED)|{d.CONTROLLER}|set(d.TARGETS.values()):
            p=self.source/rel;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/rel,p)
        self.baseline={rel:d.sha(self.state/rel) for rel in d.h.source_inventory(self.state)}
        self.cfg_baseline={rel:d.sha(self.state/rel) for rel in ('data/cmd_config.json',RUNTIME,PRIVATE,'data/config/phase1_guard_config.json',POLICY_CONFIG)}
        self.old_native=(self.state/NATIVE).read_bytes()
        template=self.state/d.TEMPLATE_TARGET;template.parent.mkdir(parents=True);template.write_text('# QQ_COMPANION_PERSONA_V0_4\nSynthetic persona unchanged\n')
        self.m={'schema':d.SCHEMA,'base_release':d.BASE,'files':{rel:d.sha(self.source/rel) for rel in set(d.PINNED)|{d.CONTROLLER}|set(d.TARGETS.values())},
            'baseline_source_files':self.baseline,'protected_config_files':self.cfg_baseline,
            'baseline_template_sha256':d.sha(self.state/d.TEMPLATE_TARGET),
            'compose_sha256':d.sha(self.dep/'compose.yml'),'env_sha256':d.sha(self.dep/'.env')}
        self.save_manifest();self.docker=DockerFixture(self.dep);self.services=d.Services(self.dep,self.docker)
        self.args=SimpleNamespace(source=self.source,deployment=self.dep,run_id='install',rollback=False,apply=True)
        native=patch.object(d,'verify_native');native.start();self.addCleanup(native.stop)

    def save_json(self,rel,value):
        p=self.state/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');p.chmod(0o600)

    def read_json(self,rel):return json.loads((self.state/rel).read_text(encoding='utf-8-sig'))
    def save_manifest(self):(self.source/'source-manifest.json').write_text(json.dumps(self.m))
    def execute(self):return d.execute(self.args,services=self.services)

    def assert_baseline(self,*,native_exact=True):
        self.assertEqual({rel:d.sha(self.state/rel) for rel in self.baseline},self.baseline)
        self.assertFalse((self.state/UNEXPECTED_SOURCE).exists())
        self.assertEqual({rel:d.sha(self.state/rel) for rel in self.cfg_baseline},self.cfg_baseline)
        if native_exact:self.assertEqual((self.state/NATIVE).read_bytes(),self.old_native)

    def nap_preserved(self):
        self.assertTrue(self.docker.rows['napcat']['running'])
        self.assertEqual(self.docker.rows['napcat']['started_at'],'napcat-original')
        self.assertFalse(any(c[1] in ('start','stop') and c[-1]=='b'*64 for c in self.docker.calls))

    def loader(self,transform):
        original=d.load
        def load(name,path):
            mod=original(name,path);transform(name,mod);return mod
        return patch.object(d,'load',side_effect=load)

    def assert_failed_recovered(self,result):
        self.assertEqual(result['status'],'FAILED',result)
        self.assertTrue(result['astrbot_running'],result)
        self.assert_baseline();self.nap_preserved()

    def test_plan_has_exact_three_sources_and_does_not_modify_files_or_stop(self):
        self.args.apply=False;r=self.execute()
        self.assertEqual(r['status'],'PLAN');self.assertEqual(set(r['target_files']),set(d.TARGETS));self.assertEqual(len(d.TARGETS),3)
        self.assertFalse((self.dep/'.astrbot-runtime-release.lock').exists())
        self.assertFalse(any(c[1] in ('stop','start') for c in self.docker.calls));self.assert_baseline();self.nap_preserved()

    def test_success_real_backup_restore_and_one_astrbot_stop_start(self):
        memory_sha=d.sha(self.memory);ledger_sha=d.sha(self.proactive)
        r=self.execute();self.assertEqual(r['status'],'INSTALLED_AWAITING_HEALTH',r)
        self.assertEqual(r['sqlite_checked_count'],3)
        self.assertEqual(d.sha(self.memory),memory_sha);self.assertEqual(d.sha(self.proactive),ledger_sha)
        for rel,old in self.baseline.items():
            self.assertEqual(d.sha(self.state/rel),self.m['files'][d.TARGETS[rel]] if rel in d.TARGETS else old)
            self.assertEqual(d.sha(self.dep/'backups/install/isolated-astrbot-restore'/rel),old)
        self.assertEqual([c[1] for c in self.docker.calls if c[1] in ('stop','start')],['stop','start'])
        self.nap_preserved()

    def test_narrow_config_values_and_native_user_field_only(self):
        r=self.execute();self.assertEqual(r['status'],'INSTALLED_AWAITING_HEALTH',r)
        runtime=self.read_json(RUNTIME);native=self.read_json(NATIVE);private=self.read_json(PRIVATE)
        self.assertEqual(runtime['proactive_window'],{'mode':'continuous','window_start':0,'window_end':0,'max_per_day':12,
            'max_generations_per_day':96,'cooldown':5400,'quiet_hours':[23,9]})
        self.assertEqual(runtime['proactive_history'],self.runtime['proactive_history'])
        expected_runtime=deepcopy(self.runtime)
        expected_runtime['proactive_window'].update(max_per_day=12,max_generations_per_day=96,cooldown=5400)
        self.assertEqual(runtime,expected_runtime)
        expected=deepcopy(self.private)
        expected['private_followup_config']['min_interval_minutes']=90
        self.assertEqual(private,expected)
        expected_native=deepcopy(self.native);expected_native['users']['user-a']['proactive_daily_limit']=12
        self.assertEqual(native,expected_native)
        self.assertEqual(d.sha(self.state/'data/cmd_config.json'),self.cfg_baseline['data/cmd_config.json'])

    def test_drift_or_added_production_source_blocks_before_stop(self):
        for rel in (next(iter(d.TARGETS)),'data/plugins/phase3_memory/main.py'):
            p=self.state/rel;old=p.read_bytes();p.write_text('SYNTHETIC_DRIFT=True')
            with self.assertRaises(d.ReleaseError):self.execute()
            p.write_bytes(old)
        p=self.state/UNEXPECTED_SOURCE;p.write_text('UNEXPECTED_NEW_SOURCE=True')
        with self.assertRaises(d.ReleaseError):self.execute()
        p.unlink();self.assertFalse(any(c[1] in ('stop','start') for c in self.docker.calls));self.assert_baseline()

    def test_protected_configuration_compose_and_environment_drift_blocks(self):
        for p in [self.state/RUNTIME,self.state/PRIVATE,self.state/'data/cmd_config.json',self.state/d.TEMPLATE_TARGET,self.dep/'compose.yml',self.dep/'.env']:
            old=p.read_bytes();p.write_text('synthetic drift')
            with self.assertRaises(d.ReleaseError):self.execute()
            p.write_bytes(old)
        self.assertFalse(any(c[1] in ('stop','start') for c in self.docker.calls));self.assert_baseline()

    def test_package_source_and_locked_helper_tampering_are_rejected(self):
        for rel in (next(iter(d.TARGETS.values())),'scripts/backup.py'):
            p=self.source/rel;old=p.read_bytes();p.write_text('SYNTHETIC_TAMPER=True')
            with self.assertRaises(d.ReleaseError):self.execute()
            if rel in d.PINNED:
                old_hash=self.m['files'][rel];self.m['files'][rel]=d.sha(p);self.save_manifest()
                with self.assertRaises(d.ReleaseError):self.execute()
                self.m['files'][rel]=old_hash;self.save_manifest()
            p.write_bytes(old)
        self.assertFalse(any(c[1] in ('stop','start') for c in self.docker.calls))

    def test_active_passive_or_proactive_work_blocks_before_stop(self):
        for path,table,state in [(self.guard,'turns','generating'),(self.guard,'deliveries','pending'),
                                (self.proactive,'proactive_generations','running'),(self.proactive,'proactive_attempts','pending')]:
            with sqlite3.connect(path) as db:db.execute('INSERT INTO '+table+' VALUES (?)',(state,))
            with self.assertRaises(d.ReleaseError):self.execute()
            with sqlite3.connect(path) as db:db.execute('DELETE FROM '+table+' WHERE state=?',(state,))
        self.assertFalse(any(c[1] in ('stop','start') for c in self.docker.calls));self.assert_baseline()

    def test_ambiguous_identity_disabled_capability_or_override_never_expands_permission(self):
        modifications=[lambda x:x['users'].update({'duplicate':deepcopy(x['users']['user-a'])}),
            lambda x:x['users']['user-a'].update(identity_subject_id='10002'),
            lambda x:x['users']['user-a'].update(proactive_private_enabled=False),
            lambda x:x['users']['user-a']['unified_profile_capabilities'].update(proactive_private_enabled=False),
            lambda x:x['users']['user-a'].update(proactive_min_interval_minutes=180)]
        for mutation in modifications:
            value=deepcopy(self.native);mutation(value);self.save_json(NATIVE,value)
            with self.assertRaises(d.ReleaseError):self.execute()
        self.save_json(NATIVE,self.native)
        self.assertFalse(any(c[1] in ('stop','start') for c in self.docker.calls));self.assert_baseline()

    def test_runtime_without_identity_uses_policy_and_guard_authority(self):
        self.assertNotIn('admin_id',self.read_json(RUNTIME));self.assertNotIn('platform_id',self.read_json(RUNTIME))
        self.args.apply=False;r=self.execute();self.assertEqual(r['status'],'PLAN')
        self.assertEqual(d.native_target(self.dep)[3],'user-a');self.assert_baseline()

    def test_policy_guard_identity_mismatch_blocks_even_if_config_hash_is_current(self):
        for rel,key,value in [(POLICY_CONFIG,'admin_id','10002'),
                              ('data/config/phase1_guard_config.json','phase3_platform_id','different')]:
            p=self.state/rel;before=p.read_bytes();document=self.read_json(rel);document[key]=value;self.save_json(rel,document)
            old_hash=self.m['protected_config_files'][rel];self.m['protected_config_files'][rel]=d.sha(p);self.save_manifest()
            with self.assertRaises(d.ReleaseError):self.execute()
            p.write_bytes(before);self.m['protected_config_files'][rel]=old_hash;self.save_manifest()
        self.assertFalse(any(c[1] in ('stop','start') for c in self.docker.calls));self.assert_baseline()

    def test_cmd_acl_mismatch_blocks_without_changing_identity_or_services(self):
        rel='data/cmd_config.json';before=(self.state/rel).read_bytes();old_hash=self.m['protected_config_files'][rel]
        for mutation in (lambda c:c.update(admins_id=['10001','10002']),
                         lambda c:c['platform_settings'].update(enable_id_white_list=False),
                         lambda c:c['platform_settings'].update(id_whitelist=['test:FriendMessage:10002']),
                         lambda c:c['platform_settings'].update(unique_session=False)):
            document=json.loads(before);mutation(document);self.save_json(rel,document)
            self.m['protected_config_files'][rel]=d.sha(self.state/rel);self.save_manifest()
            with self.assertRaises(d.ReleaseError):self.execute()
        (self.state/rel).write_bytes(before);self.m['protected_config_files'][rel]=old_hash;self.save_manifest()
        self.assertFalse(any(c[1] in ('stop','start') for c in self.docker.calls));self.assert_baseline()

    def test_failed_backup_does_not_install_and_resumes_old_service(self):
        def transform(name,mod):
            if name.endswith('_backup'):
                def fail(*args):raise RuntimeError('synthetic_backup_failure')
                mod.create_backup=fail
        with self.loader(transform):r=self.execute()
        self.assert_failed_recovered(r)

    def test_failed_restore_does_not_install_and_resumes_old_service(self):
        def transform(name,mod):
            if name.endswith('_backup'):
                def fail(*args):raise RuntimeError('synthetic_restore_failure')
                mod.restore_backup=fail
        with self.loader(transform):r=self.execute()
        self.assert_failed_recovered(r)

    def test_tampered_prepared_config_is_rejected_before_any_live_publish(self):
        original=d.prepare_journal;published=[]
        def prepare(source,dep,manifest):
            journal=original(source,dep,manifest)
            (journal/'candidate'/RUNTIME).write_text('{"synthetic_corrupt":true}')
            return journal
        def transform(name,mod):
            if not name.endswith('_installer'):return
            publish=mod.publish
            def spy(*args):published.append((str(args[0]),str(args[1])));return publish(*args)
            mod.publish=spy
        with patch.object(d,'prepare_journal',side_effect=prepare),self.loader(transform):r=self.execute()
        self.assert_failed_recovered(r)
        self.assertEqual(published,[])
        proposed_sources={str(self.source/rel) for rel in d.TARGETS.values()}
        self.assertFalse(any(source in proposed_sources or source.startswith(str(self.source/'release-journal/candidate')+'/')
                             for source,_ in published))

    def publish_failure(self,count):
        memory_sha=d.sha(self.memory);ledger_sha=d.sha(self.proactive)
        def transform(name,mod):
            if not name.endswith('_installer'):return
            original=mod.publish;calls=[]
            def publish(*args):
                original(*args);calls.append(True)
                if len(calls)==count:raise RuntimeError('synthetic_after_publish_failure')
            mod.publish=publish
        with self.loader(transform):r=self.execute()
        self.assert_failed_recovered(r);self.assertEqual(d.sha(self.memory),memory_sha);self.assertEqual(d.sha(self.proactive),ledger_sha)

    def test_first_source_publish_failure_recovers(self):self.publish_failure(1)
    def test_mid_source_publish_failure_recovers(self):self.publish_failure(2)
    def test_last_source_publish_failure_recovers(self):self.publish_failure(3)
    def test_first_configuration_publish_failure_recovers_code_and_config(self):self.publish_failure(4)
    def test_native_field_publish_failure_recovers_all_approved_changes(self):self.publish_failure(6)

    def test_native_state_changed_after_journal_is_not_overwritten(self):
        original=d.install
        def install(source,dep,manifest,installer,services):
            value=self.read_json(NATIVE);value['users']['user-a']['last_message']='synthetic concurrent state'
            self.save_json(NATIVE,value)
            return original(source,dep,manifest,installer,services)
        with patch.object(d,'install',side_effect=install):r=self.execute()
        self.assertEqual(r['status'],'FAILED',r);self.assertTrue(r['astrbot_running'])
        self.assert_baseline(native_exact=False);self.nap_preserved()
        expected=deepcopy(self.native);expected['users']['user-a']['last_message']='synthetic concurrent state'
        self.assertEqual(self.read_json(NATIVE),expected)

    def test_start_failure_recovers_sources_configs_and_native_only(self):
        self.docker.fail_starts=1
        self.assert_failed_recovered(self.execute())

    def test_rollback_preserves_new_chats_counters_candidates_and_unrelated_native_state(self):
        r=self.execute();self.assertEqual(r['status'],'INSTALLED_AWAITING_HEALTH',r)
        with sqlite3.connect(self.memory) as db:db.execute("INSERT INTO messages VALUES ('synthetic chat after install')")
        with sqlite3.connect(self.proactive) as db:db.execute("INSERT INTO proactive_generations VALUES ('skipped')")
        value=self.read_json(NATIVE);value['users']['user-a'].update(daily_count=2,next_proactive_ts=654321,
            pending_candidates=[{'id':'synthetic-after-install'}],awaiting_reply=False,last_message='synthetic new history')
        self.save_json(NATIVE,value)
        memory_sha=d.sha(self.memory);ledger_sha=d.sha(self.proactive)
        self.args.rollback=True;self.args.run_id='rollback';r=self.execute()
        self.assertEqual(r['status'],'ROLLED_BACK_AWAITING_HEALTH',r)
        self.assertEqual(d.sha(self.memory),memory_sha);self.assertEqual(d.sha(self.proactive),ledger_sha)
        value['users']['user-a']['proactive_daily_limit']=8;self.assertEqual(self.read_json(NATIVE),value)
        self.assert_baseline(native_exact=False);self.nap_preserved()
        with sqlite3.connect(self.dep/'backups/rollback/isolated-astrbot-restore/data/plugin_data/memory.db') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM messages').fetchone()[0],2)

    def test_rollback_only_reverts_approved_config_fields_after_unrelated_edits(self):
        r=self.execute();self.assertEqual(r['status'],'INSTALLED_AWAITING_HEALTH',r)
        runtime=self.read_json(RUNTIME);runtime['synthetic_keep']['history']='synthetic updated after install'
        private=self.read_json(PRIVATE);private['synthetic_keep']['secret']='synthetic replacement placeholder'
        self.save_json(RUNTIME,runtime);self.save_json(PRIVATE,private)
        self.args.rollback=True;self.args.run_id='rollback-configs';r=self.execute()
        self.assertEqual(r['status'],'ROLLED_BACK_AWAITING_HEALTH',r)
        expected=deepcopy(self.runtime);expected['synthetic_keep']=runtime['synthetic_keep']
        self.assertEqual(self.read_json(RUNTIME),expected)
        expected=deepcopy(self.private);expected['synthetic_keep']=private['synthetic_keep']
        self.assertEqual(self.read_json(PRIVATE),expected)
        self.assertEqual({rel:d.sha(self.state/rel) for rel in self.baseline},self.baseline)
        self.assertFalse((self.state/UNEXPECTED_SOURCE).exists());self.nap_preserved()

    def test_owned_configuration_drift_blocks_rollback_before_stop(self):
        r=self.execute();self.assertEqual(r['status'],'INSTALLED_AWAITING_HEALTH',r)
        runtime=self.read_json(RUNTIME);runtime['proactive_window']['cooldown']=9000;self.save_json(RUNTIME,runtime)
        self.args.rollback=True;self.args.run_id='rollback-drift';count=len(self.docker.calls)
        before={rel:d.sha(self.state/rel) for rel in d.TARGETS}
        with self.assertRaises(d.ReleaseError):self.execute()
        self.assertFalse(any(c[1] in ('start','stop') for c in self.docker.calls[count:]))
        self.assertEqual(self.read_json(RUNTIME),runtime)
        self.assertEqual({rel:d.sha(self.state/rel) for rel in d.TARGETS},before);self.nap_preserved()

    def test_symlinked_target_or_native_file_is_rejected_before_stop(self):
        for p in (self.state/next(iter(d.TARGETS)),self.state/NATIVE):
            old=p.read_bytes();external=self.root/'external';external.write_bytes(old);p.unlink();p.symlink_to(external)
            with self.assertRaises(d.ReleaseError):self.execute()
            p.unlink();p.write_bytes(old)
        self.assertFalse(any(c[1] in ('stop','start') for c in self.docker.calls));self.assert_baseline()

    def test_flattened_native_interval_updates_but_idle_and_other_values_stay(self):
        original=deepcopy(self.private);original.update(min_interval_minutes=120,idle_minutes=60)
        result=d.config_patch(original,'private')
        self.assertEqual(result['min_interval_minutes'],90)
        self.assertEqual(result['private_followup_config']['min_interval_minutes'],90)
        self.assertEqual(result['idle_minutes'],60)
        self.assertEqual(result['private_followup_config']['idle_minutes'],60)
        original['min_interval_minutes']=180
        with self.assertRaises(d.ReleaseError):d.config_patch(original,'private')

    def test_wrong_frequency_baseline_or_hours_rejected_even_when_manifest_matches(self):
        for change in ({'max_per_day':5},{'max_generations_per_day':47},{'cooldown':10800},
                       {'quiet_hours':[0,9]},{'mode':'trial'}):
            modified=deepcopy(self.runtime);modified['proactive_window'].update(change)
            with self.assertRaises(d.ReleaseError):d.config_patch(modified,'runtime')
        modified=deepcopy(self.runtime);modified['proactive_window']['keep_extension']='retained'
        result=d.config_patch(modified,'runtime')
        self.assertEqual(result['proactive_window']['keep_extension'],'retained')

    def test_rollback_plan_has_no_mutation_or_stop(self):
        self.assertEqual(self.execute()['status'],'INSTALLED_AWAITING_HEALTH')
        before={str(p.relative_to(self.state)):d.sha(p) for p in self.state.rglob('*') if p.is_file()}
        calls=len(self.docker.calls)
        self.args.rollback=True;self.args.apply=False;self.args.run_id='rollback-plan'
        result=self.execute()
        self.assertEqual(result['status'],'PLAN');self.assertEqual(result['native_daily_limit'],8)
        self.assertEqual(before,{str(p.relative_to(self.state)):d.sha(p) for p in self.state.rglob('*') if p.is_file()})
        self.assertFalse(any(c[1] in ('start','stop') for c in self.docker.calls[calls:]))


if __name__=='__main__':unittest.main(verbosity=2)
