"""Promote approved L1 records in place to the pinned native core-memory type.

No duplicate profile, content rewrite, or import. IDs, source refs, approval and
original body remain identical. Production caller must hold a stopped, verified
backup window. Reapplying is idempotent; withdrawal retains the same IDs.
"""
from .profile_release import validate_approval, identity, digest, ProductionWindow
from .bridge import MemoryDenied
import json
from pathlib import Path
import sqlite3


def validate_candidate(release, manifest, approval, candidate_root):
    """Authorize only a host-prepared offline copy, never a live state mount."""
    plan = validate_approval(manifest, approval, 'production')
    root = Path(candidate_root).resolve()
    marker = root / 'candidate.json'
    if (not root.is_dir() or root.stat().st_mode & 0o077 or marker.is_symlink()
        or Path(release.service.store.db_path).resolve() != root / 'native' / 'memory_companion.db'
        or release.path.resolve() != root / 'ledger.sqlite3'):
        raise MemoryDenied('private_offline_candidate_required')
    if json.loads(marker.read_text()) != {
        'schema':'qq-companion.offline-profile-candidate.v1',
        'manifest_hash':plan['manifest_hash'], 'backup':approval.get('backup'), 'operation':'apply'}:
        raise MemoryDenied('exact_offline_candidate_required')
    if plan['layers'] != {'L1':3, 'L2':12}:
        raise MemoryDenied('existing_approved_three_l1_twelve_l2_required')
    if not release.path.is_file() or release.path.is_symlink():
        raise MemoryDenied('existing_profile_ledger_required')
    with sqlite3.connect(release.path.as_uri()+'?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute('SELECT * FROM entries WHERE batch_id=?', (plan['batch_id'],)).fetchall()
    expected = {entry['id']:entry for entry in manifest['entries']}
    if len(rows) != len(expected):
        raise MemoryDenied('complete_existing_profile_ledger_required')
    for row in rows:
        entry = expected.get(row['entry_id'])
        key = release.service.stable_id('phase3_profile_release', digest(manifest['identity']),
                                       manifest['batch_id'], row['entry_id'])
        if (not entry or row['state'] != 'applied' or row['manifest_hash'] != plan['manifest_hash']
            or row['identity_hash'] != digest(manifest['identity']) or row['memory_id'] != key
            or row['content_hash'] != digest(entry['text'])):
            raise MemoryDenied('existing_profile_ledger_changed_or_withdrawn')
    return plan


async def promote_l1(release, manifest, approval, *, environment='isolated', apply=False, window=None,
                     candidate_root=None):
    plan = validate_approval(manifest, approval, environment)
    if identity(release.ctx) != manifest['identity']:
        raise MemoryDenied('runtime_identity_mismatch')
    if environment == 'production' and apply:
        release._maintenance_service()
        if candidate_root is not None:
            validate_candidate(release, manifest, approval, candidate_root)
        elif not isinstance(window, ProductionWindow):
            raise MemoryDenied('verified_stopped_backup_required')
        else:
            proof = window.verify(release.service.store.db_path)
            if approval.get('backup') != {'run_id':proof['backup_run_id'],
                    'astrbot_archive_sha256':proof['backup_sha256']}:
                raise MemoryDenied('approved_backup_mismatch')
    records = []
    for entry in manifest['entries']:
        if entry['layer'] != 'L1':
            continue
        key = release.service.stable_id('phase3_profile_release', digest(manifest['identity']),manifest['batch_id'],entry['id'])
        record = await release.service.store.get_memory(key)
        if (not record or record.source_plugin != 'phase3_profile_release'
            or record.metadata.get('manifest_hash') != plan['manifest_hash']
            or record.metadata.get('source_refs') != entry['source_refs']
            or record.metadata.get('profile_layer') != 'L1'
            or record.metadata.get('source_document_sha256') != manifest['source_document_sha256']
            or digest(record.content) != digest(entry['text'])
            or not release.service._retrieval_validation_engine(admin_read_all=False).policy.is_visible(record, release.ctx)[0]
            or record.validity_status != 'active'
            or (record.memory_type == 'core_memory' and record.metadata.get('core_enabled', True) is not True)
            or record.memory_type not in {'external_memory','core_memory'}):
            raise MemoryDenied('approved_l1_record_changed_or_unavailable')
        records.append((entry,record))
    if not apply:
        return {'state':'PLAN','count':len(records),'ids':[r.id for _,r in records],'content_changed':False}
    changed = []
    for entry, record in records:
        metadata = dict(record.metadata)
        metadata.update(core_memory=True, core_label=entry['id'],core_kind='profile',
            core_scope='private',core_priority=80,core_enabled=True,
            target_id=release.ctx.user_id,core_revision=metadata.get('core_revision',1))
        if record.memory_type == 'core_memory' and record.metadata == metadata:
            continue
        if not await release.service.store.update_memory_payload(record.id,
                memory_type='core_memory',metadata=metadata):
            raise MemoryDenied('native_core_promotion_failed')
        current = await release.service.store.get_memory(record.id)
        if digest(current.content) != digest(record.content) or current.metadata.get('source_refs') != record.metadata.get('source_refs'):
            raise MemoryDenied('native_core_promotion_changed_source')
        changed.append(record.id)
    if window: window.assert_stopped()
    return {'state':'APPLIED','count':len(records),'changed_ids':changed,'content_changed':False,'duplicate_created':False}
