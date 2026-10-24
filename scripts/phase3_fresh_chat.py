#!/usr/bin/env python3
"""Remove pre-cutover chat content from an explicitly marked, stopped DATA COPY.

This module never connects to SSH, starts a service, installs the copy, touches
MemoryCompanion's facts/ACL database, or removes backups. The release operator
must coordinate those separately. It preserves delivery tombstones and budgets
so a fresh conversational context is not a fresh send/API allowance.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import stat


MARKER = '.fresh-start-candidate.json'
MARKER_SCHEMA = 'qq-companion.fresh-start-candidate.v1'
PRIVATE = Path('plugin_data/astrbot_plugin_private_companion')
GUARD = Path('plugin_data/phase1_guard/guard.sqlite3')
RUNTIME = Path('plugin_data/phase3_runtime')
PRIVATE_MEMORY_KEY = '_req041_private_memory'
PRIVATE_MEMORY_SCHEMA = 'req041.person_private_memory.v1'

# Administrative settings, routing/identity and charge/attempt counters. All
# other Private user fields are chat-derived continuity and become empty using
# their existing JSON type. This preserves explicit pauses and restrictions.
PRIVATE_USER_KEEP = frozenset('''
enabled auto_enabled auto_profile_created auto_profile_created_at profile_origin
relationship_role relationship_mode
relationship_score_schema_version relationship_positive_stage_cap_key
req041_relationship_source_revision normal_interaction_band_cap
proactive_daily_limit proactive_idle_minutes proactive_min_interval_minutes
photo_daily_limit screen_peek_daily_limit poke_daily_limit proactive_boundary_note
nickname style umo user_id manual_enabled manual_disabled
identity_subject_id identity_platform_kind identity_adapter_instance_id identity_bot_id
private_memory_enabled cross_group_memory_enabled unified_profile_capabilities
private_companion_enabled proactive_private_enabled last_inbound_umo
unified_person_id unified_profile_projection_revision last_display_name
observed_display_names preferred_delivery_umo
last_sent sent_day sent_today last_proactive_sent_at proactive_sent_count
proactive_route_sent_counts proactive_daypart_counts proactive_daypart_day
photo_sent_today photo_sent_day photo_generated_today photo_generated_day
screen_peek_today screen_peek_day screen_peek_last_at last_unanswered_screen_peek_at
poke_echo_suppress_until poke_action_inflight_until last_poke_action_at
greeting_sent_day greetings_sent greetings_suppressed_by_inbound
morning_greeting_sent_at meal_care_day meal_care_asked meal_care_satisfied
pause_until busy_until paused proactive_paused blocked_topics closed_topics
'''.split())

PRIVATE_TOP_KEEP = frozenset('''
version primary_store_ownership agenda_version agenda_contract_version calendar_version
body_cycle_strategy_mode token_usage bookshelf_secret bookshelf_store_revision
runtime_settings manual_diagnosis_pending_config smart_message_debounce
_req041_memory_scope_state persona_lifecycle
troubleshooting_suppressed_warning_types extension_migration_notice_preferences
setup_guide_completed_at setup_guide_completed_version req036_capability_migration
_req041_group_reset_sagas _req041_persona_reset_saga
worldbook_deleted_member_ids worldbook_deleted_group_ids
photo_generation_scope_attempts hunger_window_attempts
'''.split())

RECEIPT_KEEP = frozenset('''
id event_id candidate_id operation_id idempotency_key dedupe_key delivery_id
state status result_code target user_id umo session_id created_at updated_at
created_ts updated_ts attempted_at finished_at sent_at retry_count attempts
transport_attempts message_id platform_message_id source_revision target_revision
'''.split())


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def empty(value):
    if value is None:
        return None
    if type(value) in (dict, list, str, int, float, bool):
        return type(value)()
    raise ValueError('unsupported_json_type')


def receipt_shells(value):
    """Keep non-content delivery evidence without reviving unfinished work."""
    if isinstance(value, list):
        return [receipt_shells(v) for v in value if isinstance(v, dict)]
    if not isinstance(value, dict):
        return empty(value)
    if any(k in value for k in ('idempotency_key', 'state', 'status', 'event_id')):
        result = {k: v for k, v in value.items() if k in RECEIPT_KEEP and not isinstance(v, (dict, list))}
        for k in ('state', 'status'):
            if result.get(k) in {'pending', 'retry', 'running', 'queued', 'sending'}:
                result[k] = 'cancelled'
        return result
    return {k: receipt_shells(v) for k, v in value.items() if isinstance(v, (dict, list))}


def clean_private_user(user, relationship_watermarks=None):
    if not isinstance(user, dict):
        raise ValueError('private_user_shape')
    result = {}
    for key, value in user.items():
        if key in PRIVATE_USER_KEEP:
            result[key] = value
        elif key == 'relationship_daily_totals':
            # Fixed native replay validates this exact shape, even at zero.
            result[key] = {'day': '', 'positive': 0, 'negative': 0}
        elif key == 'unified_profile_capability_audit':
            allowed = {'changed', 'actor_id', 'reason_code', 'created_at', 'updated_at',
                       'operation_id', 'target_identity', 'at'}
            result[key] = [{k: v for k, v in row.items() if k in allowed}
                           for row in value if isinstance(row, dict)] if isinstance(value, list) else []
        elif key == 'private_delivery_routes':
            if not isinstance(value, dict):
                raise ValueError('private_route_shape')
            result[key] = {route: {k: v for k, v in record.items() if k != 'last_error'}
                           for route, record in value.items() if isinstance(record, dict)}
        elif key in {'relationship_violation', 'suspended_proactive'}:
            # Keep prohibition state but remove the triggering transcript.
            allowed = {'active', 'blocked', 'paused', 'until', 'suspended_until',
                       'state', 'status', 'reason_code', 'created_at', 'updated_at'}
            result[key] = {k: v for k, v in value.items() if k in allowed} if isinstance(value, dict) else empty(value)
        elif key in {'pending_proactive_send_retry'}:
            result[key] = empty(value)
        else:
            result[key] = empty(value)
    person_id = user.get('unified_person_id')
    if person_id in (relationship_watermarks or {}):
        result['req041_relationship_source_revision'] = relationship_watermarks[person_id]
    return result


def clean_private_payload(payload, relationship_watermarks=None):
    if not isinstance(payload, dict):
        raise ValueError('private_store_shape')
    result = {}
    for key, value in payload.items():
        if key == PRIVATE_MEMORY_KEY:
            # Native authoritative reads distinguish absent from corrupt. An
            # empty dict is corrupt and breaks scoped projection in the rebind
            # worker. Erase content while retaining its required container.
            result[key] = {'schema': PRIVATE_MEMORY_SCHEMA, 'records': {}}
        elif key == 'primary_store_ownership':
            if not isinstance(value, dict):
                raise ValueError('ownership_shape')
            result[key] = {k: ([] if k == 'history' else v) for k, v in value.items()}
        elif key == 'persona_lifecycle':
            result[key] = {k: v for k, v in value.items() if k in {'generation', 'reset_at'}} if isinstance(value, dict) else {}
            result[key]['previous_backup'] = ''
        elif key == '_req041_memory_scope_state':
            result[key] = {k: v for k, v in value.items() if k in {'ever_bound', 'bound_at'}} if isinstance(value, dict) else {}
        elif key in PRIVATE_TOP_KEEP:
            result[key] = value
        elif key == 'users':
            if not isinstance(value, dict):
                raise ValueError('private_users_shape')
            result[key] = {identity: clean_private_user(user, relationship_watermarks) for identity, user in value.items()}
        elif key == 'groups':
            # A non-empty group needs its own approved, reviewed scope.
            if value:
                raise ValueError('group_data_not_in_reviewed_fresh_start_scope')
            result[key] = value
        elif key == 'unified_person':
            if not isinstance(value, dict):
                raise ValueError('unified_person_shape')
            result[key] = json.loads(canonical(value))
            result[key]['audit_events'] = []
            for profile in result[key].get('profiles', {}).values():
                if isinstance(profile, dict):
                    if 'affinity_score' in profile:
                        profile['affinity_score'] = 0
                    if 'affinity_band' in profile:
                        profile['affinity_band'] = 'neutral'
        elif key in {'bot_personal_outbox', 'atrelay_send_log', 'pending_atrelay_receipts'}:
            result[key] = receipt_shells(value)
        else:
            result[key] = empty(value)
    return result


def tables(db):
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def columns(db, table):
    return {row[1] for row in db.execute('PRAGMA table_info("' + table + '")')}


def need(db, table, required):
    if table not in tables(db) or not set(required) <= columns(db, table):
        raise ValueError('unreviewed_schema:' + table)


def remove_rows(db, names):
    result = {}
    for name in names:
        if name in tables(db):
            result[name] = db.execute('SELECT count(*) FROM "' + name + '"').fetchone()[0]
            db.execute('DELETE FROM "' + name + '"')
    return result


@contextmanager
def database(path):
    db = sqlite3.connect(path, timeout=1)
    try:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA secure_delete=ON')
        db.execute('BEGIN IMMEDIATE')
        yield db
        if db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('foreign_key_check_failed')
        db.commit()
        if db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] != 0:
            raise ValueError('checkpoint_busy')
        db.execute('VACUUM')
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('integrity_check_failed')
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def clean_astrbot(path):
    with database(path) as db:
        need(db, 'conversations', {'conversation_id', 'content', 'title'})
        result = remove_rows(db, ('webchat_threads', 'platform_message_history', 'conversations'))
        if 'preferences' in tables(db):
            need(db, 'preferences', {'key'})
            result['selected_conversations'] = db.execute("DELETE FROM preferences WHERE key='sel_conv_id'").rowcount
        # Current production has no tasks/attachments/ChatUI project content.
        # Fail instead of silently deleting other capabilities if that changes.
        for table in ('cron_jobs', 'attachments', 'chatui_projects', 'platform_sessions'):
            if table in tables(db) and db.execute('SELECT count(*) FROM ' + table).fetchone()[0]:
                raise ValueError('additional_astrbot_content_requires_review:' + table)
    return result


def clean_recorder(path):
    with database(path) as db:
        need(db, 'messages', {'id', 'message_str', 'message_chain', 'raw_message'})
        result = remove_rows(db, ('messages',))
        if 'messages_fts' in tables(db):
            db.execute("INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')")
            db.execute("INSERT INTO messages_fts(messages_fts) VALUES ('optimize')")
    return result


def clean_guard(path, now):
    with database(path) as db:
        for table in ('inbound', 'outbound', 'deliveries'):
            need(db, table, {'text', 'chain_json'})
        result = {t: db.execute('SELECT count(*) FROM ' + t).fetchone()[0]
                  for t in ('inbound', 'outbound', 'deliveries')}
        for table in ('inbound', 'outbound', 'deliveries'):
            db.execute('UPDATE ' + table + " SET text='',chain_json='[]'")
        for table in ('outbound', 'deliveries'):
            db.execute('UPDATE ' + table + " SET state='unknown',error_type='FreshStartInterrupted',finished_at=? WHERE state='pending'", (now,))
        if 'turns' in tables(db):
            db.execute("UPDATE turns SET state='interrupted',finished_at=? WHERE state IN ('collecting','ready','generating')", (now,))
            if 'diagnostics_json' in columns(db, 'turns'):
                db.execute("UPDATE turns SET diagnostics_json='{}'")
        if 'bubble_parts' in tables(db):
            db.execute("""UPDATE bubble_parts SET state=CASE
                WHEN EXISTS(SELECT 1 FROM deliveries d WHERE d.delivery_id=bubble_parts.delivery_id)
                THEN (SELECT d.state FROM deliveries d WHERE d.delivery_id=bubble_parts.delivery_id)
                WHEN transport_attempts>0 THEN 'unknown' ELSE 'cancelled' END,
                finished_at=?,reason='fresh_start_no_replay' WHERE state='planned'""", (now,))
    # VACUUM may move implicit rowids on TEXT-primary-key tables. Set the
    # source cutoff only AFTER compaction, never from a pre-VACUUM snapshot.
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as db:
        result['cutoffs'] = {stream: db.execute('SELECT COALESCE(MAX(rowid),0) FROM ' + table).fetchone()[0]
                             for stream, table in (('inbound', 'inbound'), ('delivery', 'deliveries'))}
    return result


def clean_runtime_memory(path, cutoffs):
    with database(path) as db:
        for table, cols in [('identities', {'scope'}), ('archive_baselines', {'stream', 'row_id'}),
                            ('archive_cursors', {'scope', 'stream', 'row_id'})]:
            need(db, table, cols)
        result = remove_rows(db, ('sources', 'jobs', 'events', 'event_history', 'event_prompts',
                                 'identity_reviews'))
        # approved_history is a body-free authorization tombstone. Activation
        # recognizes the already-approved historical manifest through it;
        # deleting it would revalidate the now-deleted 22 legacy source rows.
        # Native timeline/queues are separately erased and cursors rebased.
        scopes = {row[0] for row in db.execute('SELECT scope FROM identities')}
        scopes.update(row[0] for row in db.execute('SELECT scope FROM archive_cursors'))
        for stream, maximum in cutoffs.items():
            db.execute('INSERT INTO archive_baselines VALUES (?,?) ON CONFLICT(stream) DO UPDATE SET row_id=excluded.row_id', (stream, maximum))
            for scope in scopes:
                db.execute('INSERT INTO archive_cursors VALUES (?,?,?) ON CONFLICT(scope,stream) DO UPDATE SET row_id=excluded.row_id', (scope, stream, maximum))
    return result


def clean_proactive(path):
    with database(path) as db:
        need(db, 'proactive_attempts', {'candidate', 'day', 'attempted', 'state'})
        before = db.execute('SELECT count(*) FROM proactive_attempts').fetchone()[0]
        db.execute("UPDATE proactive_attempts SET state='unknown' WHERE state='pending'")
        if 'topic_id' in columns(db, 'proactive_attempts'):
            db.execute("UPDATE proactive_attempts SET topic_id=''")
        if 'proactive_generations' in tables(db):
            db.execute("UPDATE proactive_generations SET state='interrupted' WHERE state='running'")
        if 'proactive_controls' in tables(db):
            db.execute("DELETE FROM proactive_controls WHERE name='last_topic' OR name LIKE 'spoken:%'")
        remove_rows(db, ('proactive_event_offers',))
    return {'attempts_preserved': before}


def clean_private_sqlite(path, relationship_watermarks=None):
    with database(path) as db:
        need(db, 'store_sections', {'section_name', 'payload_json', 'checksum', 'revision', 'is_deleted'})
        rows = db.execute('SELECT section_name,payload_json,revision,is_deleted FROM store_sections').fetchall()
        payload = {name: json.loads(raw) for name, raw, rev, deleted in rows if not deleted}
        cleaned = clean_private_payload(payload, relationship_watermarks)
        highest = max([r[2] for r in rows] + [0])
        changed = 0
        for name, raw, rev, deleted in rows:
            next_raw = 'null' if deleted else canonical(cleaned[name])
            if next_raw != raw:
                highest += 1
                changed += 1
                db.execute('UPDATE store_sections SET payload_json=?,checksum=?,revision=? WHERE section_name=?',
                           (next_raw, hashlib.sha256(next_raw.encode()).hexdigest(), highest, name))
        if 'store_meta' in tables(db):
            old = db.execute("SELECT meta_value FROM store_meta WHERE meta_key='next_revision'").fetchone()
            next_revision = max(highest + 1, int(old[0]) if old else 1)
            db.execute("INSERT INTO store_meta VALUES ('next_revision',?) ON CONFLICT(meta_key) DO UPDATE SET meta_value=excluded.meta_value", (str(next_revision),))
    return {'sections': len(rows), 'sections_changed': changed}


def clean_private_outbox(path):
    with database(path) as db:
        need(db, 'outbox', {'payload_json', 'payload_hash', 'state', 'source_revision'})
        # An applied migration event is not a pending delivery. Retain its
        # revision/idempotency shell; erased payloads must never be replayed.
        unsettled = db.execute("SELECT rowid,payload_json,state FROM outbox WHERE state NOT IN ('applied','discarded')").fetchall()
        discarded = []
        watermarks = {}
        for rowid, raw, state in unsettled:
            payload = json.loads(raw)
            # The reviewed production backlog contains only old relationship
            # increments. Never discard an unfinished identity/ACL migration.
            if (state not in {'pending','failed'} or not isinstance(payload, dict)
                    or payload.get('operation') != 'relationship_legacy_event'):
                raise ValueError('unsettled_native_migration_requires_reconciliation')
            need(db, 'outbox', {'stream_key','migration_epoch','error_code'})
            need(db, 'revisions', {'stream_key','migration_epoch','revision'})
            stream, epoch = db.execute('SELECT stream_key,migration_epoch FROM outbox WHERE rowid=?',(rowid,)).fetchone()
            if not stream.startswith('relationship:') or payload.get('identity_ref') != stream.split(':',1)[1]:
                raise ValueError('unsettled_relationship_identity_mismatch')
            applied = db.execute("SELECT COALESCE(MAX(source_revision),0) FROM outbox WHERE stream_key=? AND migration_epoch=? AND state='applied'",(stream,epoch)).fetchone()[0]
            # Nonzero target relationship history needs a coordinated native
            # account migration. This release's reviewed relationship store is
            # empty; do not broaden that assumption silently.
            if applied != 0:
                raise ValueError('applied_relationship_reset_requires_review')
            discarded.append((rowid,stream,epoch))
            watermarks[stream.split(':',1)[1]] = applied
        for rowid,stream,epoch in discarded:
            payload=json.loads(db.execute('SELECT payload_json FROM outbox WHERE rowid=?',(rowid,)).fetchone()[0])
            payload['fresh_start_discarded_stream']=stream
            # Native applied_revision excludes discarded, but latest_for_stream
            # does not. Detach the cancelled tail from the active sequence;
            # preserve its old event/revision/hash as a non-replayable tombstone.
            db.execute("UPDATE outbox SET state='discarded',stream_key='',error_code='fresh_start_source_revoked',payload_json=? WHERE rowid=?",(canonical(payload),rowid))
            db.execute('UPDATE revisions SET revision=? WHERE stream_key=? AND migration_epoch=?',
                       (watermarks[stream.split(':',1)[1]],stream,epoch))
        count = db.execute('SELECT count(*) FROM outbox').fetchone()[0]
        # Identity checkpoints participate in native restart reconciliation;
        # dropping them would manufacture a migration gap. They are routing
        # metadata, not chat. Relationship accumulations become neutral just
        # like the retained user identity's newly cleaned continuity state.
        keep = {'operation', 'identity_ref', 'identity_key_ref', 'identity_assurance',
                'profile_status', 'projection_revision', 'projection_checkpoint_hash',
                'relationship_role', 'relationship_mode', 'positive_stage_cap_key',
                'policy_version', 'reason_code', 'fresh_start_discarded_stream'}
        for rowid, raw in db.execute('SELECT rowid,payload_json FROM outbox').fetchall():
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError('outbox_payload_shape')
            cleaned = {k: v for k, v in payload.items()
                       if k in keep and not isinstance(v, (dict, list))}
            for k in ('relationship_score', 'score_after', 'last_effective_at'):
                if k in payload:
                    cleaned[k] = 0
            if 'daily_totals' in payload:
                cleaned['daily_totals'] = {'day': '', 'positive': 0, 'negative': 0}
            db.execute('UPDATE outbox SET payload_json=? WHERE rowid=?', (canonical(cleaned), rowid))
        # Preserve the original digest as a tombstone for existing event IDs;
        # it is not a checksum assertion for the erased payload.
    return {'event_shells': count, 'discarded_legacy_events':len(discarded),
            '_relationship_watermarks':watermarks}


def checked_copy(path):
    path = Path(path).absolute()
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError('symlink_copy_path')
    if not path.is_dir() or path.resolve() == Path('/opt/qq-ai-companion/deployment/state/data'):
        raise ValueError('isolated_data_copy_required')
    marker = path / MARKER
    if marker.is_symlink() or not marker.is_file():
        raise ValueError('copy_marker_required')
    payload = json.loads(marker.read_text())
    if payload.get('schema') != MARKER_SCHEMA or payload.get('stopped_copy') is not True:
        raise ValueError('stopped_copy_marker_required')
    return path


def repair_empty_private_memory_copy(data_copy):
    """Only repair the known empty-root defect; never repeat chat deletion.

    Operates on a stopped marked copy. Refuses any populated or unknown root,
    preserving post-cutover messages, migration state, budgets and all other
    sections. The caller installs only changed files after checking results.
    """
    root = checked_copy(data_copy)
    paths = [PRIVATE/'companions.json', PRIVATE/'companions.db']
    profiles = root/PRIVATE/'persona_profiles'
    if profiles.exists():
        paths.extend(p.relative_to(root) for p in profiles.iterdir() if p.suffix in {'.db', '.json'})
    empty_root = {'schema': PRIVATE_MEMORY_SCHEMA, 'records': {}}
    changes = []
    for relative in paths:
        path = root/relative
        for segment in (path, *path.parents):
            if segment == root:
                break
            if segment.is_symlink():
                raise ValueError('symlink_target')
        for member in (path, Path(str(path)+'-wal'), Path(str(path)+'-shm')):
            if member.is_symlink():
                raise ValueError('symlink_target')
            if member.exists() and (not member.is_file() or member.stat().st_nlink != 1):
                raise ValueError('regular_unshared_copy_files_required')
        if not path.exists():
            continue
        if path.suffix == '.json':
            payload = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(payload, dict):
                raise ValueError('private_store_shape')
            if PRIVATE_MEMORY_KEY not in payload:
                continue
            value = payload[PRIVATE_MEMORY_KEY]
            if value == {}:
                changes.append((relative, payload, None))
            elif value != empty_root:
                raise ValueError('repair_requires_exact_empty_private_memory_root')
        else:
            with sqlite3.connect(path) as db:
                need(db, 'store_sections', {'section_name','payload_json','checksum','revision','is_deleted'})
                row = db.execute('SELECT payload_json,is_deleted FROM store_sections WHERE section_name=?',
                                 (PRIVATE_MEMORY_KEY,)).fetchone()
                if row is None or row[1]:
                    continue
                value = json.loads(row[0])
                if value == {}:
                    changes.append((relative, None, row[0]))
                elif value != empty_root:
                    raise ValueError('repair_requires_exact_empty_private_memory_root')
    changed_paths = []
    for relative, payload, original_raw in changes:
        path = root/relative
        if payload is not None:
            payload[PRIVATE_MEMORY_KEY] = empty_root
            mode = stat.S_IMODE(path.stat().st_mode)
            path.write_text(canonical(payload)+'\n', encoding='utf-8')
            path.chmod(mode)
        else:
            with database(path) as db:
                revision = db.execute('SELECT COALESCE(MAX(revision),0)+1 FROM store_sections').fetchone()[0]
                raw = canonical(empty_root)
                updated = db.execute('UPDATE store_sections SET payload_json=?,checksum=?,revision=? '
                    'WHERE section_name=? AND payload_json=? AND is_deleted=0',
                    (raw,hashlib.sha256(raw.encode()).hexdigest(),revision,PRIVATE_MEMORY_KEY,original_raw))
                if updated.rowcount != 1:
                    raise ValueError('private_memory_root_changed_during_repair')
                if 'store_meta' in tables(db):
                    old = db.execute("SELECT meta_value FROM store_meta WHERE meta_key='next_revision'").fetchone()
                    next_revision = max(revision+1,int(old[0]) if old else 1)
                    db.execute("INSERT INTO store_meta VALUES ('next_revision',?) ON CONFLICT(meta_key) "
                               'DO UPDATE SET meta_value=excluded.meta_value',(str(next_revision),))
        changed_paths.append(str(relative))
    return {'changed_paths':changed_paths,'repaired_roots':len(changed_paths),
            'no_chat_deletion':True,'no_network_or_service_actions':True}


def clean_copy(data_copy, *, now=None):
    root = checked_copy(data_copy)
    now = now or datetime.now(timezone.utc).isoformat()
    paths = [Path('data_v4.db'), Path('plugin_data/astrbot_plugin_message_recorder/messages.db'),
             GUARD, RUNTIME/'memory-controls.sqlite3', RUNTIME/'proactive-controls.sqlite3',
             PRIVATE/'companions.json', PRIVATE/'companions.db', PRIVATE/'req041_migration_outbox.db']
    profiles = root / PRIVATE / 'persona_profiles'
    if profiles.exists():
        paths.extend(p.relative_to(root) for p in profiles.iterdir() if p.suffix in {'.db', '.json'})
    for relative in paths:
        path = root / relative
        for segment in (path, *path.parents):
            if segment == root:
                break
            if segment.is_symlink():
                raise ValueError('symlink_target')
        for member in (path, Path(str(path)+'-wal'), Path(str(path)+'-shm')):
            if member.is_symlink():
                raise ValueError('symlink_target')
            if member.exists() and (not member.is_file() or member.stat().st_nlink != 1):
                raise ValueError('regular_unshared_copy_files_required')
    result = {'changed_paths': [], 'databases': {}, 'no_network_or_service_actions': True}
    relationship_watermarks = {}
    outbox = PRIVATE/'req041_migration_outbox.db'
    if (root/outbox).exists():
        item=clean_private_outbox(root/outbox)
        relationship_watermarks=item.pop('_relationship_watermarks')
        result['databases'][str(outbox)]=item
        result['changed_paths'].append(str(outbox))
    # Validate every Private JSON shape before modifying chat databases.
    private_json = {}
    for relative in paths:
        if relative.suffix == '.json' and (root / relative).exists():
            private_json[relative] = clean_private_payload(json.loads((root / relative).read_text(encoding='utf-8')),relationship_watermarks)
    cutoffs = None
    for relative, callback in [
        (Path('data_v4.db'), clean_astrbot),
        (Path('plugin_data/astrbot_plugin_message_recorder/messages.db'), clean_recorder),
        (GUARD, lambda p: clean_guard(p, now)),
    ]:
        path = root / relative
        if path.exists():
            result['databases'][str(relative)] = callback(path)
            result['changed_paths'].append(str(relative))
            if relative == GUARD:
                cutoffs = result['databases'][str(relative)]['cutoffs']
    runtime = RUNTIME / 'memory-controls.sqlite3'
    if (root / runtime).exists():
        if cutoffs is None:
            raise ValueError('guard_cutoffs_required')
        result['databases'][str(runtime)] = clean_runtime_memory(root/runtime, cutoffs)
        result['changed_paths'].append(str(runtime))
    proactive = RUNTIME / 'proactive-controls.sqlite3'
    if (root/proactive).exists():
        result['databases'][str(proactive)] = clean_proactive(root/proactive)
        result['changed_paths'].append(str(proactive))
    for relative in paths:
        path = root / relative
        if not path.exists() or not relative.is_relative_to(PRIVATE):
            continue
        if relative in private_json:
            mode = stat.S_IMODE(path.stat().st_mode)
            path.write_text(canonical(private_json[relative])+'\n', encoding='utf-8')
            path.chmod(mode)
            result['changed_paths'].append(str(relative))
        elif relative.name == 'req041_migration_outbox.db':
            continue
        elif relative.suffix == '.db':
            result['databases'][str(relative)] = clean_private_sqlite(path,relationship_watermarks)
            result['changed_paths'].append(str(relative))
    result['guard_cutoffs'] = cutoffs
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-copy', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(clean_copy(args.data_copy), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
