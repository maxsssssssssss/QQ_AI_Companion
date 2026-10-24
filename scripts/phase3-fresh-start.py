#!/usr/bin/env python3
"""Prepare and install a verified clean DATA copy; never starts services.

The owner has to authorize erasing old chats before --apply. This controller
preserves config/login state, does not upload private content, and replaces only
changed files. Old backup purge is a distinct, irreversible approved phase.
"""
from __future__ import annotations
import argparse, hashlib, json, os, shutil, sqlite3, stat, subprocess, tempfile, zipfile
from datetime import datetime, timezone
from pathlib import Path
from phase3_fresh_chat import clean_copy
from phase3_fresh_memory import clean_memory_candidate, MARKER_VALUE

SCHEMA='qq-companion.fresh-start.v1'
MARKER='.fresh-start-candidate.json'
REMOVABLE=(
 'temp','attachments','cache','home/.cache','logs',
 'plugin_data/astrbot_plugin_message_recorder/media',
 'plugin_data/astrbot_plugin_private_companion/req041_backups',
 'plugin_data/phase1_guard/guard.sqlite3.pre-phase3.sqlite3',
 'plugin_data/phase1_guard/guard.sqlite3.pre-multibubble.sqlite3',
)

def fail(code):raise ValueError(code)
def digest(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def safe(p):
 p=Path(p).absolute()
 if any(x.is_symlink() for x in (p,*p.parents)):fail('symlink_rejected')
 return p

def write_json(p,v):
 p.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
 fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as f:json.dump(v,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())

def inventory(root):
 out={}
 for p in sorted(root.rglob('*')):
  safe(p)
  if not p.is_file():continue
  if not stat.S_ISREG(p.stat().st_mode):fail('nonregular_file')
  out[p.relative_to(root).as_posix()]={'sha256':digest(p),'bytes':p.stat().st_size,'uid':p.stat().st_uid,'gid':p.stat().st_gid,'mode':stat.S_IMODE(p.stat().st_mode)}
 return out

def stopped(dep):
 prefix=['docker','compose','--project-directory',str(dep),'-f',str(dep/'compose.yml')]
 ids=subprocess.check_output(prefix+['ps','--all','--quiet','astrbot','napcat'],text=True,stderr=subprocess.DEVNULL,timeout=20).split()
 if len(ids)!=2:fail('two_existing_containers_required')
 found=set()
 for i in ids:
  v=json.loads(subprocess.check_output(['docker','inspect','--format','{"running":{{json .State.Running}},"status":{{json .State.Status}},"service":{{json (index .Config.Labels "com.docker.compose.service")}}}',i],text=True,stderr=subprocess.DEVNULL,timeout=15))
  if v['running'] or v['status']!='exited':fail('both_services_must_be_stopped')
  found.add(v['service'])
 if found!={'astrbot','napcat'}:fail('unexpected_services')

def remove(p):
 safe(p)
 if p.is_dir():shutil.rmtree(p)
 elif p.exists():p.unlink()

def copy_to(src,dst,meta):
 safe(dst);dst.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
 fd,tmp=tempfile.mkstemp(prefix='.fresh-start-',dir=dst.parent)
 try:
  with os.fdopen(fd,'wb') as f,src.open('rb') as g:
   shutil.copyfileobj(g,f);f.flush();os.fsync(f.fileno());os.fchmod(f.fileno(),meta['mode'])
   if os.geteuid()==0:os.fchown(f.fileno(),meta['uid'],meta['gid'])
  os.replace(tmp,dst)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)

def db_integrity(root):
 n=0
 for p in sorted(root.rglob('*')):
  if p.suffix not in {'.db','.sqlite','.sqlite3'} or not p.is_file():continue
  # Caller guarantees stopped/checkpointed main files. immutable avoids
  # creating fresh WAL/SHM while validating a restored, readonly baseline.
  db=sqlite3.connect(p.as_uri()+'?mode=ro&immutable=1',uri=True)
  try:
   if db.execute('pragma integrity_check').fetchone()[0]!='ok':fail('sqlite_integrity_failed')
   if db.execute('pragma foreign_key_check').fetchone():fail('sqlite_foreign_key_failed')
  finally:db.close()
  n+=1
 return n

def prepare(dep,op):
 stopped(dep)
 if op.exists():fail('fresh_operation_directory_required')
 op.mkdir(mode=0o700,parents=True)
 data=dep/'state/data';before=inventory(data)
 candidate=op/'clean-data';shutil.copytree(data,candidate)
 for p in candidate.rglob('*'):
  if p.is_dir():p.chmod(0o700)
  elif p.is_file():p.chmod(0o600)
 write_json(candidate/MARKER,MARKER_VALUE)
 memory=clean_memory_candidate(candidate,apply=True)
 chat=clean_copy(candidate)
 relation=candidate/'plugin_data/astrbot_plugin_memory_companion/memory_companion_relationship_phase.json'
 if relation.exists():relation.write_text('{}\n')
 for rel in REMOVABLE:
  remove(candidate/rel)
  if rel.endswith(('.db','.sqlite3')):
   remove(candidate/(rel+'-wal'));remove(candidate/(rel+'-shm'))
 # Existing SQLite sidecars may contain pre-cleanup bodies. Flush each copy's
 # WAL and remove only after closing; never touch live WAL here.
 for p in sorted(candidate.rglob('*')):
  if p.is_file() and p.suffix in {'.db','.sqlite','.sqlite3'}:
   c=sqlite3.connect(p)
   try:
    row=c.execute('pragma wal_checkpoint(TRUNCATE)').fetchone()
    if row and row[0]:fail('candidate_checkpoint_busy')
   finally:c.close()
   for suffix in ('-wal','-shm'):
    q=Path(str(p)+suffix)
    if q.exists():q.unlink()
 integrity=db_integrity(candidate)
 after=inventory(candidate);after.pop(MARKER,None)
 protected=[x for x in before if x=='cmd_config.json' or x.startswith(('config/','plugins/','phase2/','phase3/'))]
 if any(x not in after or after[x]['sha256']!=before[x]['sha256'] for x in protected):fail('protected_code_config_changed')
 changed=[x for x in after if x not in before or before[x]['sha256']!=after[x]['sha256']]
 deleted=[x for x in before if x not in after]
 if any(x not in before for x in changed):fail('cleanup_must_not_add_unexpected_files')
 stopped(dep)
 if inventory(data)!=before:fail('live_data_changed_during_preparation')
 archive=op/'clean-baseline.zip'
 with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as z:
  for rel in after:z.write(candidate/rel,rel)
 archive.chmod(0o600)
 restored=op/'clean-restore';restored.mkdir(mode=0o700)
 with zipfile.ZipFile(archive) as z:z.extractall(restored)
 ri=inventory(restored)
 if {x:r['sha256'] for x,r in ri.items()}!={x:r['sha256'] for x,r in after.items()}:fail('clean_restore_hash_mismatch')
 db_integrity(restored)
 for p in restored.rglob('*'):
  p.chmod(0o700 if p.is_dir() else 0o600)
 plan={'schema':SCHEMA,'created_at':datetime.now(timezone.utc).isoformat(),'before':before,'after':after,'changed':changed,'deleted':deleted,'memory':memory,'chat':chat,'clean_archive_sha256':digest(archive)}
 write_json(op/'plan.json',plan)
 result={'schema':SCHEMA,'status':'PASS','stage':'CLEAN_CANDIDATE_READY','old_content_removed':True,'persona_profile_preserved':True,'approved_profile_only':True,'clean_restore_verified':True,'databases_checked':integrity,'changed_files':len(changed),'removed_files':len(deleted),'live_data_modified':False,'clean_archive_sha256':digest(archive),'memory':memory,'chat':chat}
 write_json(op/'clean-baseline-receipt.json',result)
 return result

def apply(dep,op):
 stopped(dep);plan=json.loads((op/'plan.json').read_text());receipt=json.loads((op/'clean-baseline-receipt.json').read_text())
 if plan['schema']!=SCHEMA or receipt['status']!='PASS' or digest(op/'clean-baseline.zip')!=plan['clean_archive_sha256']:fail('clean_proof_required')
 data=dep/'state/data';candidate=op/'clean-data'
 for rel in set(plan['before'])|set(plan['after']):
  if Path(rel).is_absolute() or '..' in Path(rel).parts:fail('invalid_plan_path')
  safe(data/rel);safe(candidate/rel)
 expected_changed=sorted(x for x in plan['after'] if x not in plan['before'] or plan['after'][x]['sha256']!=plan['before'][x]['sha256'])
 expected_deleted=sorted(set(plan['before'])-set(plan['after']))
 if sorted(plan['changed'])!=expected_changed or sorted(plan['deleted'])!=expected_deleted:fail('invalid_plan_delta')
 if not set(plan['changed'])<=set(plan['before']):fail('unexpected_new_cleanup_file')
 if inventory(data)!=plan['before']:fail('live_data_drift')
 for rel,meta in plan['after'].items():
  if digest(candidate/rel)!=meta['sha256']:fail('candidate_drift')
 transient=op/'transient-old';transient.mkdir(mode=0o700)
 rows=plan['changed']+plan['deleted']
 for rel in rows:
  p=transient/rel;p.parent.mkdir(parents=True,exist_ok=True,mode=0o700);shutil.copy2(data/rel,p);p.chmod(0o600)
 write_json(op/'transient-journal.json',{'schema':SCHEMA,'paths':rows,'status':'NOT_APPLIED'})
 completed=[]
 try:
  # Drop old WAL/SHM before replacing databases, all writers are stopped.
  for rel in plan['deleted']:
   (data/rel).unlink();completed.append(rel)
  for rel in plan['changed']:
   copy_to(candidate/rel,data/rel,plan['before'][rel]);completed.append(rel)
  for rel,meta in plan['after'].items():
   if digest(data/rel)!=meta['sha256']:fail('installed_clean_hash_mismatch')
  db_integrity(data)
 except BaseException:
  for rel in reversed(completed):copy_to(transient/rel,data/rel,plan['before'][rel])
  write_json(op/'apply-failure-restored.json',{'status':'RESTORED_ORIGINAL_WHILE_STOPPED','completed_files':len(completed),'services_started':False})
  raise
 result={'schema':SCHEMA,'status':'PASS','stage':'CLEAN_DATA_INSTALLED_STOPPED','changed_files':len(plan['changed']),'deleted_files':len(plan['deleted']),'old_content_removed':True,'persona_profile_preserved':True,'new_chats_will_be_recorded':True,'services_started':False}
 write_json(op/'installed-clean-receipt.json',result)
 return result

def purge(dep,op):
 stopped(dep)
 if json.loads((op/'installed-clean-receipt.json').read_text()).get('stage')!='CLEAN_DATA_INSTALLED_STOPPED':fail('clean_install_required')
 # Only known project backup roots, with an exact location guard. The new
 # sanitized baseline lives outside this old-backup tree.
 if dep!=Path('/opt/qq-ai-companion/deployment'):fail('explicit_deployment_required_for_purge')
 targets=[dep/'backups',op/'transient-old']
 total=count=0
 for p in targets:
  if not p.exists():continue
  inv=inventory(p);total+=sum(x['bytes'] for x in inv.values());count+=len(inv)
  remove(p)
 (dep/'backups').mkdir(mode=0o700,exist_ok=True)
 result={'schema':SCHEMA,'status':'PASS','stage':'OLD_BACKUPS_PURGED','deleted_files':count,'logical_bytes_removed':total,'new_clean_baseline_retained':True}
 write_json(op/'purge-receipt.json',result)
 return result

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('prepare','apply','purge'))
 p.add_argument('--deployment',type=Path,required=True);p.add_argument('--operation',type=Path,required=True)
 p.add_argument('--approved-erasure',action='store_true');a=p.parse_args();os.umask(0o077)
 try:
  if not a.approved_erasure:fail('explicit_erasure_authorization_required')
  dep=safe(a.deployment);op=safe(a.operation)
  if op==dep or dep in op.parents:fail('operation_must_be_outside_deployment')
  result={'prepare':prepare,'apply':apply,'purge':purge}[a.action](dep,op)
  print(json.dumps(result,ensure_ascii=False));return 0
 except Exception as e:
  print(json.dumps({'status':'FAILED','error_type':type(e).__name__,'reason':str(e) if type(e) is ValueError and str(e).replace('_','').isalnum() else 'operation_failed'}));return 1
if __name__=='__main__':raise SystemExit(main())
