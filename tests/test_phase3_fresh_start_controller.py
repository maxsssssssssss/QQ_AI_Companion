import importlib.util,json,sqlite3,tempfile,unittest,sys
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
spec=importlib.util.spec_from_file_location('fresh_controller',ROOT/'scripts/phase3-fresh-start.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class FreshControllerTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.dep=self.root/'deployment';self.data=self.dep/'state/data';self.data.mkdir(parents=True);self.op=self.root/'operation'
  (self.data/'config').mkdir();(self.data/'config/service.json').write_text('{"safe":"keep"}')
  db=sqlite3.connect(self.data/'data_v4.db');db.executescript('create table chats(text); insert into chats values("OLD_CHAT"); create table persona(text); insert into persona values("KEEP_PERSONA");');db.commit();db.close()
  (self.data/'temp').mkdir();(self.data/'temp/image.jpg').write_bytes(b'old-image')
 def cleaner(self,root):
  db=sqlite3.connect(root/'data_v4.db');db.execute('pragma secure_delete=on');db.execute('delete from chats');db.commit();db.execute('vacuum');db.close();return {'changed_paths':['data_v4.db']}
 def prepare(self):
  with patch.object(m,'stopped'),patch.object(m,'clean_memory_candidate',return_value={'approved_profiles':15}),patch.object(m,'clean_copy',side_effect=self.cleaner):return m.prepare(self.dep,self.op)
 def test_prepare_leaves_live_history_and_validates_clean_restore(self):
  before=m.inventory(self.data);r=self.prepare();self.assertEqual(before,m.inventory(self.data));self.assertTrue(r['clean_restore_verified'])
  db=sqlite3.connect(self.op/'clean-restore/data_v4.db');self.assertEqual(db.execute('select count(*) from chats').fetchone()[0],0);self.assertEqual(db.execute('select text from persona').fetchone()[0],'KEEP_PERSONA');db.close()
 def test_apply_changes_only_data_and_keeps_persona_configuration(self):
  config=m.digest(self.data/'config/service.json');self.prepare()
  with patch.object(m,'stopped'):r=m.apply(self.dep,self.op)
  self.assertEqual(r['status'],'PASS');self.assertEqual(config,m.digest(self.data/'config/service.json'));self.assertFalse((self.data/'temp/image.jpg').exists());self.assertTrue((self.op/'transient-old/temp/image.jpg').exists())
 def test_drift_stops_before_any_erase(self):
  self.prepare();(self.data/'config/service.json').write_text('{}')
  with patch.object(m,'stopped'),self.assertRaisesRegex(ValueError,'live_data_drift'):m.apply(self.dep,self.op)
  self.assertTrue((self.data/'temp/image.jpg').exists())
 def test_tampered_path_rejected(self):
  self.prepare();p=self.op/'plan.json';v=json.loads(p.read_text());v['before']['../../unrelated']=v['before']['data_v4.db'];p.write_text(json.dumps(v))
  with patch.object(m,'stopped'),self.assertRaisesRegex(ValueError,'invalid_plan_path'):m.apply(self.dep,self.op)
 def test_mid_apply_failure_restores_every_original(self):
  before=m.inventory(self.data);self.prepare();original=m.copy_to;calls=0
  def once(*a):
   nonlocal calls
   calls+=1
   if calls==1:raise OSError('synthetic')
   return original(*a)
  with patch.object(m,'stopped'),patch.object(m,'copy_to',side_effect=once),self.assertRaises(OSError):m.apply(self.dep,self.op)
  self.assertEqual(before,m.inventory(self.data));self.assertTrue((self.op/'apply-failure-restored.json').exists())
 def test_purge_rejects_unknown_target(self):
  self.prepare()
  with patch.object(m,'stopped'):m.apply(self.dep,self.op)
  with patch.object(m,'stopped'),self.assertRaisesRegex(ValueError,'explicit_deployment_required'):m.purge(self.dep,self.op)
 def test_integrity_does_not_create_wal_sidecars(self):
  db=sqlite3.connect(self.data/'data_v4.db');db.execute('pragma journal_mode=wal');db.close()
  before=m.inventory(self.data);m.db_integrity(self.data);self.assertEqual(before,m.inventory(self.data))
 def test_writer_guard_rejects_before_copy(self):
  with patch.object(m,'stopped',side_effect=ValueError('both_services_must_be_stopped')),self.assertRaises(ValueError):m.prepare(self.dep,self.op)
  self.assertFalse(self.op.exists())
if __name__=='__main__':unittest.main()
