"""Offline native capability migration: real pinned APIs, synthetic private data."""
from copy import deepcopy
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('phase3_native_authorize',ROOT/'scripts/phase3-authorize-native.py')
migration=importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)
SOURCE=ROOT/'.runtime/upstream/private_companion'
SESSION='fixture:FriendMessage:10001'
IMAGE='sha256:'+'a'*64
CONTAINER='b'*64


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    migration.write_new(path,migration.json_bytes(value))


def user():
    return {'user_id':'qq:10001:scoped', 'umo':SESSION,'identity_subject_id':'10001',
        'identity_platform_kind':'qq','identity_adapter_instance_id':'fixture','identity_bot_id':'20002',
        'proactive_daily_limit':0,'private_companion_enabled':True,'proactive_private_enabled':False,
        'chat_history':[{'text':'SENSITIVE_SYNTHETIC_BODY'}], 'private_extra':{'retain':True}}


class NativeAuthorizeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.store=self.root/'before.json';self.identity=self.root/'identity.json';self.output=self.root/'after.json'
        self.data={'users':{'qq:10001:scoped':user(),'other':{'user_id':'30003','proactive_private_enabled':False}},
                   'unknown_section':{'private':'DO_NOT_CHANGE'}}
        write(self.store,self.data)
        write(self.identity,{'admin_id':'10001','platform_id':'fixture'})

    def tearDown(self): self.temp.cleanup()

    def candidate(self, **kwargs): return migration.native_candidate(SOURCE,self.store,self.identity,self.output,**kwargs)

    def test_real_native_capability_and_atomic_backend_preserve_other_data(self):
        before=self.store.read_bytes()
        report=self.candidate()
        saved=json.loads(self.output.read_text());changed=saved['users']['qq:10001:scoped']
        self.assertEqual(self.store.read_bytes(),before)
        self.assertEqual(report['status'],'CANDIDATE_READY')
        self.assertEqual(report['daily_limit'],2)
        self.assertTrue(report['native_api_called']);self.assertTrue(report['native_atomic_save_used'])
        self.assertTrue(changed['unified_profile_capabilities']['proactive_private_enabled'])
        self.assertEqual(changed['proactive_daily_limit'],2)
        self.assertEqual(changed['chat_history'],self.data['users']['qq:10001:scoped']['chat_history'])
        self.assertEqual(saved['users']['other'],self.data['users']['other'])
        self.assertEqual(saved['unknown_section'],self.data['unknown_section'])
        self.assertEqual(changed['unified_profile_capability_audit'][-1]['reason_code'],'phase3_approved_window')
        self.assertEqual(self.output.stat().st_mode&0o777,0o600)
        serialized=json.dumps(report)
        for secret in ('10001','20002',SESSION,'SENSITIVE_SYNTHETIC_BODY','DO_NOT_CHANGE'):
            self.assertNotIn(secret,serialized)

    def test_explicit_daily_limits_reach_saved_candidate_and_validation(self):
        for daily_limit in (1,3,4,5,8):
            with self.subTest(daily_limit=daily_limit):
                report=self.candidate(daily_limit=daily_limit)
                saved=json.loads(self.output.read_text())
                self.assertEqual(report['daily_limit'],daily_limit)
                self.assertEqual(saved['users']['qq:10001:scoped']['proactive_daily_limit'],daily_limit)
                migration.validate_delta(self.data,saved,'qq:10001:scoped',daily_limit=daily_limit)
                with self.assertRaisesRegex(migration.MigrationError,'native_update_not_applied'):
                    migration.validate_delta(self.data,saved,'qq:10001:scoped')
                self.output.unlink()

    def test_invalid_daily_limits_refuse_before_native_calls_or_backup(self):
        backup=self.root/'backup'
        for daily_limit in (0,9,-1,True,False,2.0,'5',None):
            with self.subTest(daily_limit=daily_limit), \
                    patch.object(migration,'native_modules') as native, \
                    patch.object(migration,'stopped_image') as stopped, \
                    patch.object(migration,'command') as command:
                with self.assertRaisesRegex(migration.MigrationError,'invalid_daily_limit'):
                    self.candidate(daily_limit=daily_limit)
                with self.assertRaisesRegex(migration.MigrationError,'invalid_daily_limit'):
                    migration.migrate(self.root,backup,approved=True,daily_limit=daily_limit)
                with self.assertRaisesRegex(migration.MigrationError,'invalid_daily_limit'):
                    migration.run_worker(IMAGE,SOURCE,self.root,daily_limit=daily_limit)
                native.assert_not_called();stopped.assert_not_called();command.assert_not_called()
                self.assertFalse(self.output.exists());self.assertFalse(backup.exists())

    def test_second_native_authorization_is_idempotent_without_duplicate_audit(self):
        self.candidate()
        first=json.loads(self.output.read_text())
        self.store.write_bytes(self.output.read_bytes());self.output.unlink()
        second=self.candidate()
        self.assertTrue(second['previously_authorized'])
        self.assertEqual(json.loads(self.output.read_text()),first)

    def test_wrong_bot_adapter_subject_missing_or_duplicate_identity_fail_closed(self):
        # A different bot is a distinct native identity. The migration requires
        # a persisted bot binding but never invents or overwrites one.
        cases=[]
        for field,value in [('identity_adapter_instance_id','elsewhere'),('identity_subject_id','30003'),
                            ('identity_bot_id',''),('identity_platform_kind','generic')]:
            changed=deepcopy(self.data);changed['users']['qq:10001:scoped'][field]=value;cases.append(changed)
        duplicate=deepcopy(self.data);duplicate['users']['duplicate']=deepcopy(duplicate['users']['qq:10001:scoped']);cases.append(duplicate)
        for data in cases:
            self.store.write_bytes(migration.json_bytes(data))
            with self.assertRaisesRegex(migration.MigrationError,'native_identity_missing_or_ambiguous'): self.candidate()
            self.assertFalse(self.output.exists())

    def test_other_authorized_target_is_not_silently_revoked(self):
        self.data['users']['other']['proactive_private_enabled']=True
        self.data['users']['other']['unified_profile_capabilities']={'schema_version':1,'proactive_private_enabled':True}
        self.store.write_bytes(migration.json_bytes(self.data))
        with self.assertRaisesRegex(migration.MigrationError,'another_native_target'): self.candidate()
        self.assertFalse(self.output.exists())

    def test_backend_and_global_generation_must_stay_closed(self):
        good={'storage_config':{'storage_backend':'json'},'reach':{'max_daily_messages':0},
              'generation':{'enable_llm_proactive_message':False}}
        migration.check_cold_json_backend(good,{'backend':'json'})
        for bad,marker in [({**good,'storage_backend':'sqlite'},None),(good,{'backend':'sqlite'}),
            ({**good,'max_daily_messages':2},None),({**good,'enable_llm_proactive_message':True},None),({},None)]:
            with self.assertRaises(migration.MigrationError): migration.check_cold_json_backend(bad,marker)

    def test_delta_rejects_body_other_user_and_section_changes(self):
        self.candidate();good=json.loads(self.output.read_text())
        migration.validate_delta(self.data,good,'qq:10001:scoped')
        for change in ('body','other','section','new_user'):
            bad=deepcopy(good)
            if change=='body': bad['users']['qq:10001:scoped']['chat_history']=[]
            elif change=='other': bad['users']['other']['proactive_private_enabled']=True
            elif change=='section': bad['unknown_section']={}
            else: bad['users']['new']={}
            with self.assertRaisesRegex(migration.MigrationError,'unexpected_native_changes'):
                migration.validate_delta(self.data,bad,'qq:10001:scoped')

    def test_source_pin_and_file_permissions_reject_changes(self):
        with self.assertRaisesRegex(migration.MigrationError,'native_source_integrity_mismatch'):
            migration.check_source(self.root)
        self.store.chmod(0o644)
        with self.assertRaisesRegex(migration.MigrationError,'unsafe_file_permissions'):
            migration.protected_read(self.store)
        self.store.chmod(0o600)
        alias=self.root/'alias.json';alias.symlink_to(self.store)
        with self.assertRaises(OSError): migration.protected_read(alias)

    def test_stopped_check_uses_only_projected_metadata_no_service_mutation(self):
        calls=[]
        def command(args,**kw):
            calls.append(args)
            if args[1]=='compose':return (CONTAINER+'\n').encode()
            return json.dumps({'status':'exited','running':False,'image':IMAGE,'service':'astrbot'}).encode()
        with patch.object(migration,'command',command):
            self.assertEqual(migration.stopped_image(self.root),(CONTAINER,IMAGE))
        self.assertEqual([v[1] for v in calls],['compose','inspect'])
        for state in ('running','paused','restarting','dead'):
            with patch.object(migration,'command',side_effect=[CONTAINER.encode(),json.dumps({
                'status':state,'running':state=='running','image':IMAGE,'service':'astrbot'}).encode()]):
                with self.assertRaisesRegex(migration.MigrationError,'astrbot_must_be_stopped'):
                    migration.stopped_image(self.root)

    def test_worker_command_has_no_live_volume_network_provider_or_plugin_start(self):
        calls=[]
        write(self.root/'worker-result.json',{'status':'CANDIDATE_READY'})
        with patch.object(migration,'command',lambda args,**kw:calls.append(args) or b''):
            migration.run_worker(IMAGE,SOURCE,self.root)
            migration.run_worker(IMAGE,SOURCE,self.root,daily_limit=5)
        command=calls[0]
        self.assertIn('--read-only',command);self.assertIn('--cap-drop',command)
        self.assertEqual(command[command.index('--network')+1],'none')
        self.assertIn('PYTHONPATH=/AstrBot',command)
        self.assertFalse(any('/opt/phase2-runtime' in part for part in command))
        self.assertEqual(command[command.index('--entrypoint')+1],'python')
        self.assertNotIn('main.py',' '.join(command));self.assertNotIn('/AstrBot/data',' '.join(command))
        self.assertEqual(command[-4:],['/migration.py','--worker','--daily-limit','2'])
        self.assertEqual(calls[1][:-1],command[:-1])
        self.assertEqual(calls[1][-1],'5')

    def test_cli_passes_explicit_limit_to_preparation_and_worker_and_refuses_nine(self):
        with patch.object(sys,'argv',['migration','--backup-dir',str(self.root/'backup'),
                '--approved-target-migration','--daily-limit','5']), \
                patch.object(migration,'migrate',return_value={'status':'PREPARED_NOT_APPLIED'}) as prepare, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(migration.main(),0)
        self.assertEqual(prepare.call_args.kwargs['daily_limit'],5)
        self.assertFalse(prepare.call_args.kwargs['apply'])
        with patch.object(sys,'argv',['migration','--worker','--daily-limit','5']), \
                patch.object(migration,'native_candidate',return_value={'status':'CANDIDATE_READY'}) as candidate, \
                patch.object(migration,'write_new'), redirect_stdout(io.StringIO()):
            self.assertEqual(migration.main(),0)
        self.assertEqual(candidate.call_args.kwargs['daily_limit'],5)
        result=subprocess.run([sys.executable,str(ROOT/'scripts/phase3-authorize-native.py'),
            '--daily-limit','9'],capture_output=True,text=True,check=False)
        self.assertEqual(result.returncode,2)
        self.assertIn('invalid choice',result.stderr)

    def test_preparation_records_requested_limit_and_rejects_worker_count_mismatch(self):
        data=self.root/'state/data'
        live=data/'plugin_data/astrbot_plugin_private_companion/companions.json'
        identity=data/'config/phase2_policy_config.json'
        config=data/'config/astrbot_plugin_private_companion_config.json'
        source=data/'plugins/astrbot_plugin_private_companion'
        source.mkdir(parents=True)
        write(live,self.data)
        write(identity,{'admin_id':'10001','platform_id':'fixture'})
        write(config,{'storage_backend':'json','max_daily_messages':0,'enable_llm_proactive_message':False})
        before=live.read_bytes()
        for daily_limit,reported_limit in ((2,2),(5,5),(5,2)):
            backup=self.root/f'backup-{daily_limit}-{reported_limit}'
            def worker(image,unused_source,work,*,daily_limit):
                self.assertEqual(image,IMAGE)
                result=migration.native_candidate(SOURCE,work/'companions.before.json',
                    work/'identity.json',work/'companions.after.json',daily_limit=daily_limit)
                result['daily_limit']=reported_limit
                write(work/'worker-result.json',result)
                return result
            with self.subTest(daily_limit=daily_limit,reported_limit=reported_limit), \
                    patch.object(migration,'check_source'), \
                    patch.object(migration,'stopped_image',return_value=(CONTAINER,IMAGE)), \
                    patch.object(migration,'run_worker',side_effect=worker):
                kwargs={} if daily_limit==2 else {'daily_limit':daily_limit}
                if daily_limit!=reported_limit:
                    with self.assertRaisesRegex(migration.MigrationError,'invalid_worker_result'):
                        migration.migrate(self.root,backup,approved=True,**kwargs)
                    self.assertFalse((backup/'migration-result.json').exists())
                else:
                    report=migration.migrate(self.root,backup,approved=True,**kwargs)
                    self.assertEqual(report['status'],'PREPARED_NOT_APPLIED')
                    self.assertEqual(report['daily_limit'],daily_limit)
                    self.assertFalse(report['global_generation_enabled']);self.assertFalse(report['service_started'])
                    self.assertEqual(json.loads((backup/'migration-result.json').read_text()),report)
                self.assertEqual(json.loads((backup/'backup-manifest.json').read_text())['daily_limit'],daily_limit)
                saved=json.loads((backup/'companions.after.json').read_text())
                self.assertEqual(saved['users']['qq:10001:scoped']['proactive_daily_limit'],daily_limit)
                self.assertEqual(live.read_bytes(),before)

    def test_apply_approval_and_running_container_refuse_before_any_backup(self):
        backup=self.root/'backup'
        with self.assertRaisesRegex(migration.MigrationError,'explicit_target_migration_approval_required'):
            migration.migrate(self.root,backup,apply=True)
        with patch.object(migration,'stopped_image',side_effect=migration.MigrationError('astrbot_must_be_stopped')):
            with self.assertRaisesRegex(migration.MigrationError,'astrbot_must_be_stopped'):
                migration.migrate(self.root,backup,apply=True,approved=True)
        self.assertFalse(backup.exists())


if __name__=='__main__': unittest.main()
