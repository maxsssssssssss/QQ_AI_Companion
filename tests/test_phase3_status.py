"""Read-only aggregate status using synthetic secrets/content, never a VPS."""
import hashlib
import importlib.util
import json
from datetime import datetime
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('phase3_status_fixture',ROOT/'scripts/phase3-status.py')
status=importlib.util.module_from_spec(spec);spec.loader.exec_module(status)
SECRET='SYNTHETIC_SECRET_DO_NOT_EMIT'
IDENTITY='9988776655'


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.deployment=Path(self.temp.name)
        self.data=self.deployment/'state/data'
        self.now=datetime.fromisoformat('2026-10-22T10:00:00+08:00')
    def tearDown(self): self.temp.cleanup()
    def write(self,relative,obj):
        path=self.data/relative;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(obj));path.chmod(0o600)
        return path
    def db(self,relative,sql):
        path=self.data/relative;path.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(path) as db:db.executescript(sql)
        return path
    def fixtures(self):
        start=int(datetime.fromisoformat('2026-10-22T09:00:00+08:00').timestamp())
        self.write('config/phase3_runtime_config.json',{'enabled':True,'persona':True,'natural_chat':True,
            'memory':True,'search':True,'proactive':True,'background_web':False,'preferred_address':SECRET,
            'user_id':IDENTITY,'api_key':SECRET,'proactive_window':{'window_start':start,
            'window_end':start+72*3600,'max_per_day':2,'cooldown':14400,'quiet_hours':[23,9]}})
        self.write('config/phase1_guard_config.json',{'phase3_enabled':True,'phase3_proactive_enabled':True,'phase3_admin_id':IDENTITY})
        self.write('config/phase3_search_config.json',{'enabled':True,'private_query':SECRET})
        self.write('config/astrbot_plugin_memory_companion_config.json',{'memory_summary':{'enabled':False,'key':SECRET}})
        self.write('config/astrbot_plugin_private_companion_config.json',{'enable_llm_proactive_message':False,'max_daily_messages':0,'token':SECRET})
        self.write('plugin_data/phase2_policy/readiness.json',{'scope':'startup_policy_only','status':'PASS','plugins':{SECRET:IDENTITY}})
        self.guard=self.db('plugin_data/phase1_guard/guard.sqlite3',f"""
            CREATE TABLE inbound(content TEXT);INSERT INTO inbound VALUES('{SECRET}');
            CREATE TABLE outbound(state TEXT);INSERT INTO outbound VALUES('sent');
            CREATE TABLE deliveries(kind TEXT,state TEXT,content TEXT);
            INSERT INTO deliveries VALUES('progress','sent','{SECRET}'),('final','unknown','{IDENTITY}'),('{SECRET}','{SECRET}','{SECRET}');
            CREATE TABLE turns(state TEXT);INSERT INTO turns VALUES('done');""")
        self.db('plugin_data/astrbot_plugin_memory_companion/memory_companion.db',f"""
            CREATE TABLE timeline(content TEXT,summarized_at TEXT);
            INSERT INTO timeline VALUES('{SECRET}',''),('{IDENTITY}','2026-10-22T01:00:00Z');
            CREATE TABLE memories(memory_type TEXT,validity_status TEXT,content TEXT);
            INSERT INTO memories VALUES('conversation_summary','active','{SECRET}'),('assertion','superseded','{IDENTITY}');
            CREATE TABLE summary_batches(content TEXT);INSERT INTO summary_batches VALUES('{SECRET}');
            CREATE TABLE summary_batch_calls(content TEXT);INSERT INTO summary_batch_calls VALUES('{SECRET}');""")
        self.db('plugin_data/phase3_search/budget.sqlite3',f"""
            CREATE TABLE web_daily_attempts(day TEXT,attempts INTEGER);
            INSERT INTO web_daily_attempts VALUES('2026-10-22',3),('2026-10-21',4),('2026-10-20 {SECRET}',999);""")
        self.proactive=self.db('plugin_data/phase3_runtime/proactive-controls.sqlite3',f"""
            CREATE TABLE proactive_controls(name TEXT,value TEXT);
            INSERT INTO proactive_controls VALUES('pause_until','{start+7200}'),('last_control_source','{IDENTITY}'),('last_topic','{SECRET}');
            CREATE TABLE proactive_attempts(day TEXT,state TEXT,candidate TEXT);
            INSERT INTO proactive_attempts VALUES('2026-10-22','unknown','{SECRET}');
            CREATE TABLE proactive_generations(day TEXT,state TEXT,candidate TEXT);
            INSERT INTO proactive_generations VALUES('2026-10-22','generated','{SECRET}');""")

    def test_full_report_only_aggregates_and_separates_actual_usage_from_limits(self):
        self.fixtures()
        raw={'service':'astrbot','resources':{'memory_bytes':2*1024**3},'status':'running','running':True,'environment':SECRET}
        containers={'astrbot':status.phase2.filter_container(raw),'napcat':{'present':False}}
        actual={'astrbot':status.filter_stats({'CPUPerc':'12.5%','MemUsage':'321MiB / 2GiB','PIDs':'15','Name':SECRET})}
        before=hashlib.sha256(self.guard.read_bytes()).hexdigest()
        with patch.object(status,'host_usage',return_value={'memory_used_bytes':123}), \
             patch.object(status.phase2,'inspect_containers',return_value=containers), \
             patch.object(status,'container_usage',return_value=actual):
            result=status.build_report(self.deployment,now=self.now)
        encoded=json.dumps(result)
        self.assertNotIn(SECRET,encoded);self.assertNotIn(IDENTITY,encoded)
        self.assertEqual(result['status'],'INSPECTED')
        self.assertEqual(result['sqlite_mode'],'ro')
        self.assertFalse(result['services_changed']);self.assertFalse(result['qq_login_requested'])
        self.assertEqual(result['containers']['astrbot']['configured_limits']['memory_bytes'],2*1024**3)
        self.assertEqual(result['container_actual_usage']['astrbot']['memory_used_bytes'],321*1024**2)
        self.assertEqual(result['guard']['deliveries']['progress']['sent'],1)
        self.assertEqual(result['guard']['deliveries']['final']['unknown'],1)
        self.assertEqual(result['memory']['timeline_unsummarized'],1)
        self.assertEqual(result['memory']['assertion']['validity']['superseded'],1)
        self.assertEqual(result['search']['today_attempts'],3)
        self.assertEqual(len(result['search']['recent_days']),2)
        self.assertEqual(result['proactive']['window_state'],'PAUSED')
        self.assertEqual(result['proactive']['attempts_today']['unknown'],1)
        self.assertFalse(result['configured_flags']['native_disk']['memory_summary_enabled'])
        self.assertEqual(before,hashlib.sha256(self.guard.read_bytes()).hexdigest())

    def test_sqlite_connection_is_readonly_and_does_not_create_missing_database(self):
        path=self.db('synthetic.sqlite','CREATE TABLE sample(value INTEGER);INSERT INTO sample VALUES(1);')
        before=path.read_bytes()
        with status.readonly_db(path) as db:
            self.assertEqual(db.execute('PRAGMA query_only').fetchone()[0],1)
            with self.assertRaises(sqlite3.OperationalError):db.execute('INSERT INTO sample VALUES(2)')
        self.assertEqual(path.read_bytes(),before)
        missing=self.data/'missing.sqlite'
        with self.assertRaises(FileNotFoundError):
            with status.readonly_db(missing):pass
        self.assertFalse(missing.exists())
        linked=self.data/'linked.sqlite';linked.symlink_to(path)
        with self.assertRaises(status.phase2.StatusError):
            with status.readonly_db(linked):pass

    def test_docker_stats_selects_service_and_drops_arbitrary_strings(self):
        commands=[]
        def command(args):
            commands.append(args)
            if args[1]=='compose':return 'a'*64
            if args[1]=='inspect':return '"astrbot"'
            return json.dumps({'CPUPerc':'203.5%','MemUsage':'1.25GiB / 4GiB','PIDs':'8',
                               'Name':IDENTITY,'Error':SECRET,'NetIO':SECRET})
        with patch.object(status.phase2,'command',side_effect=command):result=status.container_usage(self.deployment)
        self.assertEqual(result['astrbot']['cpu_percent'],203.5)
        self.assertEqual(result['astrbot']['memory_used_bytes'],int(1.25*1024**3))
        self.assertNotIn(SECRET,json.dumps(result));self.assertNotIn(IDENTITY,json.dumps(result))
        self.assertEqual([c[1] for c in commands],['compose','inspect','stats'])
        self.assertIn('--no-stream',commands[-1])
        bad=status.filter_stats({'CPUPerc':SECRET,'MemUsage':SECRET,'PIDs':IDENTITY,'Name':SECRET})
        self.assertIsNone(bad['cpu_percent']);self.assertIsNone(bad['memory_used_bytes']);self.assertIsNone(bad['pids'])

    def test_raw_exceptions_and_invalid_policy_reasons_never_escape(self):
        self.write('plugin_data/phase2_policy/readiness.json',{'scope':'startup_policy_only','status':'BLOCKED','reason':SECRET})
        with patch.object(status,'host_usage',side_effect=OSError(SECRET)), \
             patch.object(status.phase2,'inspect_containers',side_effect=status.phase2.StatusError(SECRET)), \
             patch.object(status,'container_usage',side_effect=ValueError(IDENTITY)):
            result=status.build_report(self.deployment,now=self.now)
        self.assertEqual(result['status'],'PARTIAL')
        self.assertEqual(result['astrbot_policy']['reason'],'unrecognized_reason')
        self.assertNotIn(SECRET,json.dumps(result));self.assertNotIn(IDENTITY,json.dumps(result))
        self.assertEqual(result['guard']['status'],'NOT_INITIALIZED')

    def test_proactive_config_boundaries_and_private_controls_remain_filtered(self):
        self.fixtures()
        for stamp,expected in (('2026-10-22T08:59:59+08:00','WAITING'),
            ('2026-10-22T12:00:00+08:00','IN_WINDOW'),('2026-10-22T23:00:00+08:00','QUIET_HOURS'),
            ('2026-10-25T09:00:00+08:00','EXPIRED')):
            report=status.proactive_status(self.data,datetime.fromisoformat(stamp))
            self.assertEqual(report['window_state'],expected)
            self.assertNotIn(SECRET,json.dumps(report));self.assertNotIn(IDENTITY,json.dumps(report))

    def test_cpu_and_units_are_actual_measurements_not_limits(self):
        self.assertEqual(status.cpu_percent((1000,400),(1200,450)),75)
        self.assertIsNone(status.cpu_percent((1000,400),(1000,400)))
        self.assertEqual(status.parse_bytes('10.5 MB'),10500000)
        self.assertEqual(status.parse_bytes('1KiB'),1024)
        self.assertIsNone(status.parse_bytes(SECRET))
        self.assertIsNone(status.iso_time(float('nan')))


if __name__=='__main__':unittest.main(verbosity=2)
