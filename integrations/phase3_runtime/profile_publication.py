"""Publish bounded profile evidence through the fixed native Memory boundary.

No facts or source permissions are owned here. The native publisher revalidates
every merged reference and creates the actual temporary input/source receipts.
"""
import asyncio
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import importlib
import re
from types import SimpleNamespace

PROFILE_OPEN = '<phase3_approved_profile>'
PROFILE_CLOSE = '</phase3_approved_profile>'
STRATEGY = 'phase3.approved_profile_native'


@dataclass(frozen=True)
class Publication:
    published: bool
    reason: str = ''
    statistics_deferred: bool = False
    native_ref_count: int = 0
    native_context_chars: int = 0
    native_snapshot_present: bool = False


def _modules(service):
    package = type(service).__module__.rsplit('.', 1)[0]
    return SimpleNamespace(**{key: importlib.import_module(package + '.' + key)
        for key in ('injection', 'memory_revision', 'continuity', 'astrbot_compat')})


def _current_block(req, header, footer):
    found = []
    for part in getattr(req, 'extra_user_content_parts', None) or []:
        text = getattr(part, 'text', '')
        if isinstance(text, str) and header in text and footer in text:
            if (text.count(header) != 1 or text.count(footer) != 1
                    or not text.strip().startswith(header) or not text.strip().endswith(footer)):
                raise ValueError('ambiguous_native_context')
            found.append(text)
    prompt = getattr(req, 'prompt', '') or ''
    if header in prompt and footer in prompt:
        found.extend(re.findall(re.escape(header) + '.*?' + re.escape(footer), prompt, re.S))
    if len(found) > 1:
        raise ValueError('ambiguous_native_context')
    return found[0] if found else ''


def _clear_current(service, req, event, modules, reason):
    # Only this request's temporary surface; never edit saved/old conversation.
    header = modules.injection.MEMORY_COMPANION_INJECTION_HEADER
    footer = modules.injection.MEMORY_COMPANION_INJECTION_FOOTER
    parts = getattr(req, 'extra_user_content_parts', None)
    if isinstance(parts, list):
        req.extra_user_content_parts = [part for part in parts
            if not (header in str(getattr(part, 'text', '')) and
                    footer in str(getattr(part, 'text', '')))]
    prompt = getattr(req, 'prompt', None)
    if isinstance(prompt, str):
        req.prompt = modules.astrbot_compat.remove_marked_text(prompt, header, footer)
    service._mark_memory_companion_injection_state(event, req, injected=False,
        conversation_memory=False, slot_map={}, reason_code=reason)
    setattr(req, 'phase3_profile_added_refs', [])


def _refs(values):
    result = {}
    if not isinstance(values, list):
        raise ValueError('native_refs_missing')
    for ref in values:
        if (not isinstance(ref, dict) or not isinstance(ref.get('id'), str)
                or not ref['id'] or not isinstance(ref.get('version'), str) or not ref['version']):
            raise ValueError('native_ref_invalid')
        previous = result.get(ref['id'])
        if previous and previous != ref['version']:
            raise ValueError('native_ref_version_conflict')
        result[ref['id']] = ref['version']
    return result


def _statistics_error(service, exc):
    marker = getattr(service, '_mark_injected_memories', None)
    code = getattr(getattr(marker, '__func__', marker), '__code__', None)
    trace = exc.__traceback__
    while trace:
        if code is not None and trace.tb_frame.f_code is code:
            return True
        trace = trace.tb_next
    return False


def _confirmed(req, injection, refs):
    state = getattr(req, 'memory_companion_injection_state', {})
    snapshot = getattr(req, 'memory_companion_continuity_snapshot', None)
    return bool(state.get('injected') and state.get('memory_refs') == refs
        and isinstance(snapshot, dict) and snapshot.get('snapshot_id') == state.get('snapshot_id')
        and snapshot.get('coverage', {}).get('strategy_id') == STRATEGY
        and snapshot.get('usage', {}).get('injection_digest') == hashlib.sha256(injection.encode()).hexdigest())


async def publish_profile(service, ctx, event, req, evidence, *, timeout=1.5):
    """Merge full current evidence once, with native permissions and receipts.

    Failed checks remove the temporary block without restoring a stale copy.
    The caller may give a record-free unavailable explanation.
    """
    modules = _modules(service)
    header = modules.injection.MEMORY_COMPANION_INJECTION_HEADER
    footer = modules.injection.MEMORY_COMPANION_INJECTION_FOOTER
    try:
        async with asyncio.timeout(timeout):
            actual_ctx = await service.identity.resolve_event_context(event)
            identity = ('scope', 'session_id', 'user_id', 'bot_id', 'platform', 'persona_id', 'group_id')
            if any(getattr(actual_ctx, key, '') != getattr(ctx, key, '') for key in identity):
                raise ValueError('publication_identity_changed')
            # The coordinator stores identity only. Bind continuity to the
            # native current event/message rather than an empty routing context.
            ctx = actual_ctx
            state = deepcopy(getattr(req, 'memory_companion_injection_state', {}) or {})
            snapshot = deepcopy(getattr(req, 'memory_companion_continuity_snapshot', None))
            old = _current_block(req, header, footer)
            # Native remove_temp_text also scans contexts. Refuse this rare shape
            # rather than let a copied marker delete any historical user row.
            for row in getattr(req, 'contexts', None) or []:
                text = modules.astrbot_compat._plain_request_text(row)
                if header in text and footer in text:
                    raise ValueError('historical_context_marker')
            old_refs = _refs(state.get('memory_refs', []))
            if old and not state.get('injected'):
                raise ValueError('native_context_unattested')
            if old_refs and not old:
                raise ValueError('native_context_missing')
            old_continuity = snapshot.get('continuity_refs', []) if isinstance(snapshot, dict) else []
            if old and (not isinstance(snapshot, dict) or
                    {(r.get('ref_id'), r.get('version')) for r in old_continuity}
                    != set(old_refs.items())):
                raise ValueError('native_continuity_mismatch')
            # Replacing this request-scoped sub-block must also retire its own
            # previous refs. Original native refs remain, even if the profile
            # happened to quote the same record. No fact cache is introduced.
            added = _refs(getattr(req, 'phase3_profile_added_refs', []))
            if any(old_refs.get(key) != value for key, value in added.items()):
                raise ValueError('native_continuity_mismatch')
            for key in added:
                old_refs.pop(key)
            old_continuity = [r for r in old_continuity if r['ref_id'] not in added]
            merged = _refs([{'id': key, 'version': value} for key, value in old_refs.items()]
                + [{'id': r['id'], 'version': r['version']} for r in evidence.records])
            refs = [{'id': key, 'version': value} for key, value in merged.items()]
            gate = await service._p5_gate(event=event, sink='memory_recall')
            if not gate.get('ok') or not service._scope_feature_enabled(ctx, 'recall'):
                raise ValueError('recall_disabled')
            # Rebuild only the native validator's timeline/feedback slot inputs.
            # The store read here grants nothing: publication performs ACL and
            # exact version checks before appending any text.
            records = await service.store.get_memories_by_ids(list(merged)) if merged else {}
            slots = {}
            for ref in refs:
                record = records.get(ref['id'])
                slot = 'approved_profile' if any(r['id'] == ref['id'] for r in evidence.records) else 'existing_native'
                if record is None and ref['id'].startswith('timeline_'):
                    raw_id = ref['id'].removeprefix('timeline_')
                    rows = await service.store.get_timeline_by_ids([raw_id])
                    row = rows.get(raw_id)
                    if row and row.get('session_id') == ctx.session_id and row.get('scope') == ctx.scope:
                        record = service._timeline_row_as_memory(ctx, row)
                        slot = 'time_window_timeline'
                if record is not None:
                    if state.get('conversation_memory') and record.memory_type == 'conversation_summary':
                        slot = 'conversation_summary'
                    slots.setdefault(slot, []).append(SimpleNamespace(memory=record, reason='mention'))
            continuity = {(r['ref_id'], r['version']): r for r in old_continuity}
            raw_refs = [continuity.get((r['id'], r['version']), {**r, 'ref_kind': 'fact'}) for r in refs]
            rendered = modules.continuity.build_context_snapshot(ctx, memory_refs=raw_refs,
                coverage={'strategy_id': STRATEGY, 'complete': False}, state='pending').to_dict()
            body = old if old else header + '\n' + footer
            body = re.sub(re.escape(PROFILE_OPEN) + '.*?' + re.escape(PROFILE_CLOSE), '', body, flags=re.S)
            if evidence.records:
                body = body.replace('- 没有检索到足够相关的长期记忆；只依据当前用户消息回复。', '')
            profile = PROFILE_OPEN + '\n' + evidence.render() + '\n' + PROFILE_CLOSE
            body = body.replace(footer, profile + '\n' + footer, 1)
            if body.count(header) != 1 or body.count(footer) != 1:
                raise ValueError('ambiguous_native_context')
            injection = modules.memory_revision.MemoryContext(body, refs, continuity_snapshot=rendered,
                # Unknown optional native text must never gain a complete proof.
                role_input_complete=not old or bool(getattr(old, 'role_input_complete', False)))
            statistics_deferred = False
            try:
                published = await service._publish_reply_memory_context(ctx, req, event=event,
                    injection=injection, slot_map=slots, strategy_id=STRATEGY)
            except Exception as exc:
                if not _statistics_error(service, exc) or not _confirmed(req, injection, refs):
                    raise
                published, statistics_deferred = True, True
            if not published or not _confirmed(req, injection, refs):
                raise ValueError('native_publication_rejected')
            # Bookkeeping awaits after native append must not allow a concurrent
            # correction/revocation to leave stale facts in the final request.
            current, _ = await service._validate_reply_memory_refs(ctx, refs, slots)
            if not current:
                raise ValueError('native_publication_rejected')
            if not service._scope_feature_enabled(ctx, 'recall'):
                raise ValueError('recall_disabled')
            req.phase3_profile_added_refs = [r for r in refs if r['id'] not in old_refs]
            return Publication(True, statistics_deferred=statistics_deferred,
                native_ref_count=len(refs), native_context_chars=len(injection),
                native_snapshot_present=True)
    except asyncio.CancelledError:
        _clear_current(service, req, event, modules, 'profile_publication_cancelled')
        raise
    except Exception as exc:
        reason = 'publication_timeout' if isinstance(exc, TimeoutError) else (
            str(exc) if isinstance(exc, ValueError) else 'native_publication_error')
        # Reasons are fixed locally/native contract values, not private content.
        known = {'ambiguous_native_context', 'native_refs_missing', 'native_ref_invalid',
            'native_ref_version_conflict', 'historical_context_marker', 'native_context_unattested',
            'native_context_missing', 'native_continuity_mismatch', 'recall_disabled',
            'publication_identity_changed',
            'native_publication_rejected', 'publication_timeout', 'native_publication_error'}
        reason = reason if reason in known else 'native_publication_error'
        _clear_current(service, req, event, modules, reason)
        return Publication(False, reason)
