"""Explicit profile release, separate from the always-isolated batch adapter.

No provider calls; native MemoryCompanion remains the sole content store.
Production requires current Docker state and a byte-identical verified backup.
"""
from __future__ import annotations
import hashlib
from contextlib import contextmanager
import fcntl
import importlib.util
import importlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import tempfile

from .bridge import digest, MemoryDenied

ROOT=Path(__file__).resolve().parents[2]
IDENTITY_KEYS=('platform','session_id','user_id','bot_id','persona_id')


def identity(ctx):
    value={k:str(getattr(ctx,k,'')) for k in IDENTITY_KEYS}
    if not all(value.values()) or getattr(ctx,'scope','')!='private' or getattr(ctx,'group_id',''):
        raise MemoryDenied('complete_private_identity_required')
    return value


def validate_manifest(manifest):
    if manifest.get('schema')!='qq-companion.profile-reviewed.v1' or manifest.get('reviewed') is not True:
        raise MemoryDenied('reviewed_semantic_manifest_required')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',str(manifest.get('batch_id',''))):
        raise MemoryDenied('invalid_profile_batch')
    scope=manifest.get('identity',{})
    if set(scope)!=set(IDENTITY_KEYS) or not all(isinstance(v,str) and v for v in scope.values()):
        raise MemoryDenied('complete_private_identity_required')
    if scope['session_id'].split(':')[-2:]!=['FriendMessage',scope['user_id']]:
        raise MemoryDenied('private_session_identity_mismatch')
    if not re.fullmatch(r'[0-9a-f]{64}',str(manifest.get('source_document_sha256',''))):
        raise MemoryDenied('source_document_digest_required')
    entries=manifest.get('entries')
    if not isinstance(entries,list) or not 1<=len(entries)<=100:
        raise MemoryDenied('bounded_profile_batch_required')
    ids=set();contents=set()
    for row in entries:
        if row.get('layer') not in {'L1','L2'}:
            raise MemoryDenied('temporal_or_excluded_profile_import_denied')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',str(row.get('id',''))) or row['id'] in ids:
            raise MemoryDenied('unique_profile_entry_required')
        ids.add(row['id'])
        if row.get('confidence_status')!='user_confirmed' or row.get('shared_experience') is not False:
            raise MemoryDenied('confirmed_self_report_required')
        if not isinstance(row.get('text'),str) or not 1<=len(row['text'])<=4000:
            raise MemoryDenied('native_content_limit')
        if re.sub(r'\s+',' ',row['text'].replace('\u3000',' ')).strip()!=row['text']:
            raise MemoryDenied('review_native_normalized_content_required')
        content_hash=digest(row['text'])
        if content_hash in contents: raise MemoryDenied('merge_duplicate_profile_entries_before_review')
        contents.add(content_hash)
        if not re.fullmatch(r'[a-z_]{2,50}',str(row.get('permission_scope',''))):
            raise MemoryDenied('permission_scope_required')
        refs=row.get('source_refs')
        if not isinstance(refs,list) or not refs or any(not isinstance(r,str) or not r or len(r)>250 for r in refs):
            raise MemoryDenied('source_provenance_required')
    if sum(e['layer']=='L1' for e in entries)>8 or sum(len(e['text']) for e in entries if e['layer']=='L1')>2500:
        raise MemoryDenied('core_profile_must_be_small')
    return {'manifest_hash':digest(manifest),'batch_id':manifest['batch_id'],
        'count':len(entries),'layers':{layer:sum(e['layer']==layer for e in entries) for layer in ('L1','L2')},
        'permission_scopes':sorted({e['permission_scope'] for e in entries})}


def validate_approval(manifest,approval,environment):
    plan=validate_manifest(manifest)
    if environment not in {'isolated','production'} or approval.get('environment')!=environment:
        raise MemoryDenied('explicit_environment_consent_required')
    if approval.get('manifest_hash')!=plan['manifest_hash'] or approval.get('identity')!=manifest['identity']:
        raise MemoryDenied('exact_manifest_and_identity_consent_required')
    if approval.get('approved') is not True or approval.get('batch_id')!=manifest['batch_id']:
        raise MemoryDenied('explicit_batch_consent_required')
    # The pinned native external-memory path cannot separate passive versus
    # proactive model recall. Do not store denied data with decorative flags.
    for name in plan['permission_scopes']:
        grant=approval.get('grants',{}).get(name,{})
        if any(grant.get(k) is not True for k in ('write_memory','model_use','native_proactive_recall')):
            raise MemoryDenied('per_scope_native_recall_consent_required')
        if environment=='production' and grant.get('production_write') is not True:
            raise MemoryDenied('per_scope_production_consent_required')
    return plan


def _module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProductionWindow:
    """Actual stopped-state and backup verification, never a JSON boolean."""
    def __init__(self,deployment,run_id,expected_archive_hash,*,runner=None):
        self.deployment=Path(deployment).absolute()
        if self.deployment.resolve()!=self.deployment: raise MemoryDenied('real_deployment_path_required')
        self.run_id=run_id
        self.expected_hash=expected_archive_hash
        self.runner=runner or self._run
        self.validated=False
        self.native_database=None

    @staticmethod
    def _run(args):
        result=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=30)
        if result.returncode or len(result.stdout)>65536: raise MemoryDenied('docker_state_unavailable')
        return result.stdout

    def assert_stopped(self):
        prefix=['docker','compose','--project-directory',str(self.deployment),'-f',str(self.deployment/'compose.yml')]
        ids=self.runner(prefix+['ps','--all','--quiet','astrbot','napcat']).split()
        if len(ids)!=2 or any(not re.fullmatch(r'[0-9a-f]{12,64}',x) for x in ids):
            raise MemoryDenied('existing_stopped_containers_required')
        found=set()
        for cid in ids:
            rows=json.loads(self.runner(['docker','inspect',cid]))
            if len(rows)!=1: raise MemoryDenied('unexpected_container_state')
            row=rows[0]; labels=row.get('Config',{}).get('Labels',{})
            service=labels.get('com.docker.compose.service')
            if labels.get('com.docker.compose.project')!='qq-ai-companion' or service not in {'astrbot','napcat'} or service in found:
                raise MemoryDenied('unexpected_container_identity')
            found.add(service)
            if row.get('State',{}).get('Running') is not False or row.get('State',{}).get('Status') not in {'exited','created'}:
                raise MemoryDenied('all_state_writers_must_be_stopped')
            if service=='astrbot' and not any(m.get('Type')=='bind' and Path(m.get('Source','')).resolve()==self.deployment/'state'/'data' and m.get('Destination')=='/AstrBot/data' for m in row.get('Mounts',[])):
                raise MemoryDenied('astrbot_state_mount_mismatch')

    def verify(self,service_database):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',self.run_id): raise MemoryDenied('invalid_backup_run')
        self.assert_stopped()
        run=self.deployment/'backups'/self.run_id
        for p in (self.deployment/'backups',run,run/'result.json',run/'operation.json',run/'astrbot-state.zip'):
            info=p.lstat()
            if p.is_symlink() or info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)&0o077:
                raise MemoryDenied('private_backup_permissions_required')
            if p.is_file() and (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1):
                raise MemoryDenied('regular_private_backup_required')
        result=json.loads((run/'result.json').read_text())
        operation=json.loads((run/'operation.json').read_text())
        if (result.get('backup_integrity')!='PASS' or result.get('restore_integrity')!='PASS'
            or result.get('status')!='PASS' or operation.get('restored_copy_must_never_start') is not True
            or not isinstance(operation.get('originally_running_services'),list)):
            raise MemoryDenied('verified_backup_restore_required')
        archive=run/'astrbot-state.zip'
        actual=hashlib.sha256(archive.read_bytes()).hexdigest()
        if actual!=self.expected_hash or result.get('astrbot_archive_sha256')!=actual:
            raise MemoryDenied('exact_backup_digest_required')
        backup=_module('phase3_profile_backup',ROOT/'scripts/backup.py')
        with tempfile.TemporaryDirectory(prefix='.profile-restore-',dir=run) as temp:
            manifest=backup.restore_backup(archive,Path(temp)/'state')
            live_files,live_dirs=backup._tree(self.deployment/'state')
            if live_files!=manifest['files'] or live_dirs!=manifest['directories']:
                raise MemoryDenied('live_state_changed_since_backup')
        database=Path(service_database).resolve()
        if not database.is_relative_to(self.deployment/'state') or database.name!='memory_companion.db':
            raise MemoryDenied('native_database_outside_verified_state')
        self.assert_stopped()
        self.native_database=database
        self.validated=True
        return {'backup_run_id':self.run_id,'backup_sha256':actual}


class ProfileRelease:
    def __init__(self,service,ctx,ledger_path):
        self.service,self.ctx,self.path=service,ctx,Path(ledger_path)

    def _maintenance_service(self):
        for key in ('memory_summary.enabled','memory_capture.enabled','retrieval.embedding_enabled',
                    'retrieval.embedding_backfill_enabled','portrait.daily_dispatch_enabled'):
            if self.service.config.bool(key,False):
                raise MemoryDenied('native_maintenance_model_tasks_must_be_disabled')

    def _metadata(self,manifest,entry,plan):
        return {'profile_layer':entry['layer'],'source_document_sha256':manifest['source_document_sha256'],
            'source_refs':entry['source_refs'],'confidence_status':'user_confirmed','shared_experience':False,
            'owner_bot_id':self.ctx.bot_id,'bot_id':self.ctx.bot_id,'persona_id':self.ctx.persona_id,
            'import_batch':manifest['batch_id'],'manifest_hash':plan['manifest_hash'],
            'permission_scope':entry['permission_scope'],'original_experience_owner':'user_or_named_third_party'}

    async def _native_preflight(self,manifest,plan):
        # The upstream insert API merges matching fingerprints into older IDs.
        # Refuse that merge BEFORE writing so a later batch cannot take ownership
        # of an existing record and delete it during withdrawal.
        rows=await self.service.store.list_memories(limit=10001,memory_type='external_memory',
            scope='private',entity_id=self.ctx.user_id)
        if len(rows)>10000: raise MemoryDenied('native_duplicate_review_limit')
        models=importlib.import_module(type(self.service).__module__.rsplit('.',1)[0]+'.models')
        fingerprints={r.content_fingerprint:r.id for r in rows if r.lifecycle!='archived'}
        for entry in manifest['entries']:
            key=self.service.stable_id('phase3_profile_release',digest(manifest['identity']),manifest['batch_id'],entry['id'])
            existing=await self.service.store.get_memory(key)
            if existing and (digest(existing.content)!=digest(entry['text']) or existing.metadata.get('manifest_hash')!=plan['manifest_hash']):
                raise MemoryDenied('native_profile_id_conflict')
            prototype=self.service.classifier.external_record(content=entry['text'],memory_type='external_memory',
                subject=models.EntityRef(kind='user',id=self.ctx.user_id,role='external_memory_owner'),
                object=models.EntityRef.bot_self(),scope='private',
                session_id=f'external:phase3_profile_release:{self.ctx.user_id}',platform='',
                visibility='private_pair',sayability='direct',reality_level='external_observation',
                lifecycle='stable_memory',confidence=.82,importance=.62,review_status='auto',
                tags=['external','external_memory','long_term'],metadata=self._metadata(manifest,entry,plan),
                source_plugin='phase3_profile_release',occurred_at='')
            prototype.ensure_defaults()
            conflict=fingerprints.get(prototype.content_fingerprint)
            if conflict and conflict!=key: raise MemoryDenied('existing_native_duplicate_requires_review')
            fingerprints[prototype.content_fingerprint]=key

    @contextmanager
    def _ledger(self):
        self.path.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
        if (self.path.parent.resolve()!=self.path.parent.absolute() or self.path.parent.stat().st_mode&0o077
            or self.path.is_symlink()): raise MemoryDenied('unsafe_profile_ledger')
        fd=os.open(self.path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_nlink!=1 or info.st_mode&0o077:
            os.close(fd)
            raise MemoryDenied('unsafe_profile_ledger')
        db=None
        try:
            try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise MemoryDenied('profile_release_in_progress')
            db=sqlite3.connect(self.path);db.row_factory=sqlite3.Row
            db.execute('CREATE TABLE IF NOT EXISTS entries(batch_id TEXT,entry_id TEXT,manifest_hash TEXT,identity_hash TEXT,memory_id TEXT,content_hash TEXT,state TEXT,PRIMARY KEY(batch_id,entry_id))')
            yield db
        finally:
            if db: db.close()
            os.close(fd)

    async def apply(self,manifest,approval,*,environment='isolated',apply=False,window=None):
        plan=validate_approval(manifest,approval,environment)
        if identity(self.ctx)!=manifest['identity']: raise MemoryDenied('runtime_identity_mismatch')
        if not apply: return {**plan,'state':'DRY_RUN','native_writes':0}
        if environment=='production':
            self._maintenance_service()
            if not isinstance(window,ProductionWindow): raise MemoryDenied('verified_stopped_backup_required')
            if not self.path.resolve().is_relative_to(window.deployment/'state'):
                raise MemoryDenied('production_ledger_must_share_backed_up_state')
            proof=window.verify(self.service.store.db_path)
            if approval.get('backup')!={'run_id':proof['backup_run_id'],'astrbot_archive_sha256':proof['backup_sha256']}:
                raise MemoryDenied('approved_backup_mismatch')
        return await self._apply_records(manifest,plan,window)

    async def _apply_records(self,manifest,plan,window=None):
        with self._ledger() as db:
            writes=0
            # Preflight all IDs before any native content mutation. Crash-time
            # partial writes remain pending and may resume only this exact hash.
            for entry in manifest['entries']:
                previous=db.execute('SELECT * FROM entries WHERE batch_id=? AND entry_id=?',(manifest['batch_id'],entry['id'])).fetchone()
                if previous and (previous['manifest_hash']!=plan['manifest_hash'] or previous['identity_hash']!=digest(manifest['identity']) or previous['state']=='withdrawn'):
                    raise MemoryDenied('profile_batch_reuse_denied')
            await self._native_preflight(manifest,plan)
            for entry in manifest['entries']:
                key=self.service.stable_id('phase3_profile_release',digest(manifest['identity']),manifest['batch_id'],entry['id'])
                body_hash=digest(entry['text'])
                previous=db.execute('SELECT * FROM entries WHERE batch_id=? AND entry_id=?',(manifest['batch_id'],entry['id'])).fetchone()
                if previous and (previous['manifest_hash']!=plan['manifest_hash'] or previous['identity_hash']!=digest(manifest['identity']) or previous['state']=='withdrawn'):
                    raise MemoryDenied('profile_batch_reuse_denied')
                with db:
                    db.execute("INSERT OR IGNORE INTO entries VALUES(?,?,?,?,?,?,'pending')",(manifest['batch_id'],entry['id'],plan['manifest_hash'],digest(manifest['identity']),key,body_hash))
                existing=await self.service.store.get_memory(key)
                if existing:
                    if digest(existing.content)!=body_hash or existing.metadata.get('manifest_hash')!=plan['manifest_hash']:
                        raise MemoryDenied('native_profile_id_conflict')
                else:
                    result=await self.service.record_external_memory(user_id=self.ctx.user_id,content=entry['text'],
                        memory_id=key,idempotency_key=key,source_plugin='phase3_profile_release',memory_type='external_memory',
                        metadata=self._metadata(manifest,entry,plan))
                    if not result.get('ok'): raise MemoryDenied('native_profile_write_rejected')
                    if result.get('memory_id')!=key: raise MemoryDenied('native_profile_unexpected_deduplication')
                    stored=await self.service.store.get_memory(key)
                    if not stored or digest(stored.content)!=body_hash:
                        raise MemoryDenied('native_profile_content_changed')
                    writes+=1
                with db: db.execute("UPDATE entries SET state='applied' WHERE batch_id=? AND entry_id=?",(manifest['batch_id'],entry['id']))
            if window: window.assert_stopped()
            return {**plan,'state':'APPLIED','native_writes':writes,'model_calls':0,'raw_source_deleted':False}

    async def withdraw(self,manifest,approval,*,environment='isolated',apply=False,window=None):
        plan=validate_approval(manifest,approval,environment)
        if identity(self.ctx)!=manifest['identity']: raise MemoryDenied('runtime_identity_mismatch')
        if approval.get('withdraw_batch') is not True: raise MemoryDenied('explicit_batch_withdrawal_required')
        if not apply: return {**plan,'state':'DRY_RUN','native_writes':0}
        if environment=='production':
            self._maintenance_service()
            if not isinstance(window,ProductionWindow): raise MemoryDenied('verified_stopped_backup_required')
            if not self.path.resolve().is_relative_to(window.deployment/'state'):
                raise MemoryDenied('production_ledger_must_share_backed_up_state')
            proof=window.verify(self.service.store.db_path)
            if approval.get('backup')!={'run_id':proof['backup_run_id'],'astrbot_archive_sha256':proof['backup_sha256']}:
                raise MemoryDenied('approved_backup_mismatch')
        return await self._withdraw_records(manifest,plan,window)

    async def _withdraw_records(self,manifest,plan,window=None):
        with self._ledger() as db:
            rows=db.execute('SELECT * FROM entries WHERE batch_id=?',(manifest['batch_id'],)).fetchall()
            if len(rows)!=plan['count']: raise MemoryDenied('complete_batch_ledger_required')
            for row in rows:
                if row['manifest_hash']!=plan['manifest_hash'] or row['identity_hash']!=digest(manifest['identity']):
                    raise MemoryDenied('profile_batch_identity_mismatch')
                record=await self.service.store.get_memory(row['memory_id'])
                if record and (digest(record.content)!=row['content_hash'] or record.metadata.get('manifest_hash')!=plan['manifest_hash']):
                    raise MemoryDenied('edited_profile_requires_new_review')
            for row in rows:
                await self.service.store.delete_memory(row['memory_id'])
                with db: db.execute("UPDATE entries SET state='withdrawn' WHERE batch_id=? AND entry_id=?",(manifest['batch_id'],row['entry_id']))
            self.service.store.invalidate_registered_caches(reason='phase3_profile_release_withdrawal')
            if window: window.assert_stopped()
            return {**plan,'state':'WITHDRAWN','deleted_native_records':len(rows),'raw_source_deleted':False,
                'prior_chat_and_backups_erased':False,'fresh_chat_context_required_before_resume':True,'model_calls':0}

    async def prepare_candidate(self,manifest,approval,*,candidate_root,withdraw=False):
        """Build an offline candidate; only the host installer may publish it.

        This is an explicitly production-approved candidate path, not isolated
        authorization reused for production. The worker has no production mount.
        """
        plan=validate_approval(manifest,approval,'production')
        if identity(self.ctx)!=manifest['identity']: raise MemoryDenied('runtime_identity_mismatch')
        root=Path(candidate_root).resolve()
        marker=root/'candidate.json'
        if not root.is_dir() or root.stat().st_mode&0o077 or marker.is_symlink():
            raise MemoryDenied('private_offline_candidate_required')
        receipt=json.loads(marker.read_text())
        if receipt!={'schema':'qq-companion.offline-profile-candidate.v1','manifest_hash':plan['manifest_hash'],
                     'backup':approval.get('backup'),'operation':'withdraw' if withdraw else 'apply'}:
            raise MemoryDenied('exact_offline_candidate_required')
        if Path(self.service.store.db_path).resolve()!=root/'native'/'memory_companion.db' or self.path.resolve()!=root/'ledger.sqlite3':
            raise MemoryDenied('candidate_only_paths_required')
        self._maintenance_service()
        if withdraw and approval.get('withdraw_batch') is not True:
            raise MemoryDenied('explicit_batch_withdrawal_required')
        result=await (self._withdraw_records(manifest,plan) if withdraw else self._apply_records(manifest,plan))
        result['state']='CANDIDATE_PREPARED'
        result['production_writes']=0
        return result
