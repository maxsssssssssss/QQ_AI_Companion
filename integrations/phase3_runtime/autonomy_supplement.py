"""Read-only admission for one confirmed exchange's optional supplement.

This grants only a short native idle threshold, never a transport permission.
The existing Gate still owns quiet hours, explicit controls and attempt budgets.
"""
from datetime import datetime
import math
import uuid


def check_supplement(bridge, user, *, scope=None, interest=False):
    """Revalidate durable parent evidence before native evaluation and sending.

    A scheduled supplement uses the configured 5–60 minute age window. The
    result of an interest preface in the same active research opportunity can
    follow immediately: it is already tied to that confirmed preface and input
    epoch, not a fresh contact opportunity. Neither path relaxes send budgets.
    """
    result = {'applicable': False, 'allowed': True, 'reason': 'not_supplement'}
    if interest:
        return result
    getter = getattr(bridge.private, '_planned_proactive_impulse', None)
    native = getter(user) if callable(getter) else None
    context = (native or {}).get('context') or {}
    if not isinstance(context, dict):
        return {**result, 'allowed': False, 'reason': 'supplement_invalid_native_context'}
    metadata = scope.get('supplement') if scope is not None else context.get('phase3_supplement')
    inline = bool(scope is not None and not metadata and scope.get('interest_attempt'))
    if metadata is None and not inline:
        return result
    result.update(applicable=True, allowed=False, reason='supplement_invalid_metadata')
    if not bridge.options.get('supplements'):
        return {**result, 'reason': 'supplement_disabled'}
    session = bridge.gate.session
    if user.get('umo') != session or scope is not None and scope.get('session') != session:
        return {**result, 'reason': 'supplement_wrong_identity'}
    candidate = (scope.get('candidate_id') if scope is not None else
                 bridge.private._planned_proactive_delivery_key(user))
    if not isinstance(candidate, str) or not candidate:
        return result
    if inline:
        parent = scope['interest_attempt']
        parent_kind, exchange, version = 'interest', candidate, scope.get('input_version')
    elif isinstance(metadata, dict):
        parent = metadata.get('parent_attempt')
        parent_kind, exchange = metadata.get('parent_kind'), metadata.get('exchange_id')
        version = metadata.get('input_version')
    else:
        return result
    if (not isinstance(parent, str) or not parent or not isinstance(exchange, str)
            or not exchange or parent_kind not in {'passive', 'proactive', 'interest'}
            or not isinstance(version, str) or not version.isdecimal()):
        return result
    current_version = bridge.input_version(session)
    if version != current_version or scope is not None and scope.get('input_version') != current_version:
        return {**result, 'reason': 'supplement_no_longer_current'}
    if parent_kind == 'passive':
        if not parent.startswith('turn:'):
            return result
        turn_id = parent.removeprefix('turn:')
    else:
        if (parent_kind == 'interest') != parent.endswith(':interest'):
            return result
        attempted = bridge.gate.db.execute(
            'SELECT state FROM proactive_attempts WHERE candidate=?', (parent,)).fetchone()
        if not attempted or attempted[0] != 'sent':
            return {**result, 'reason': 'supplement_parent_unconfirmed'}
        # Current autonomy parents retain their generation epoch independently
        # of candidate metadata. Legacy parents without this audit still need
        # the timestamp and exact captured-epoch checks below.
        parent_epoch = bridge.gate.db.execute('''SELECT e.input_version
            FROM autonomy_evaluations e JOIN autonomy_opportunities o ON o.id=e.opportunity_id
            WHERE o.session=? AND (e.delivery_attempt=? OR e.id=? OR o.candidate_id=?)
            ORDER BY e.started DESC LIMIT 1''',
            (session, parent, parent.removesuffix(':interest'), parent.removesuffix(':interest'))).fetchone()
        if parent_epoch and parent_epoch[0] != current_version:
            return {**result, 'reason': 'supplement_no_longer_current'}
        turn_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'qq-companion-proactive:proactive:'+parent))
    turn = bridge.guard.store.get_turn(turn_id)
    if (not turn or turn['session'] != session or turn['platform'] != bridge.guard.phase3_platform_id
            or bool(turn['proactive']) != (parent_kind != 'passive')):
        return {**result, 'reason': 'supplement_parent_wrong_identity'}
    rows = bridge.guard.store.db.execute('''SELECT kind,MAX(finished_at) finished
        FROM deliveries WHERE turn_id=? AND kind!='progress'
        GROUP BY kind ORDER BY MAX(rowid) DESC''', (turn_id,)).fetchall()
    if not rows:
        return {**result, 'reason': 'supplement_parent_unconfirmed'}
    kind, finished = rows[0]
    if kind not in ({'normal', 'final'} if parent_kind == 'passive' else {'proactive'}):
        return {**result, 'reason': 'supplement_parent_wrong_kind'}
    receipt = bridge.guard.store.bubble_result(turn_id, kind)
    if receipt.get('state') != 'sent' or not receipt.get('delivered_text', '').strip():
        return {**result, 'reason': 'supplement_parent_unconfirmed'}
    try:
        finished = datetime.fromisoformat(finished).timestamp()
        minimum, maximum = (float(bridge.options[k]) for k in
                            ('supplement_min_seconds', 'supplement_max_seconds'))
        now = float(bridge.gate.clock())
        if not all(math.isfinite(v) for v in (finished, minimum, maximum, now)) or not 0 < minimum < maximum:
            return result
    except (ValueError, TypeError):
        return result
    age = now-finished
    if age < (0 if inline else minimum):
        return {**result, 'reason': 'supplement_too_soon'}
    if age >= maximum:
        return {**result, 'reason': 'supplement_expired'}
    # Passive parents have exact archived inbound identities. A later inbound
    # invalidates them even if a timestamp was adjusted or received out of order.
    if parent_kind == 'passive':
        row = bridge.guard.store.db.execute('''SELECT MAX(i.rowid) FROM inbound i
            JOIN turn_sources s ON s.platform=i.platform AND s.session=i.session AND s.message_id=i.message_id
            WHERE s.turn_id=?''', (turn_id,)).fetchone()
        if str(row[0] or 0) != current_version:
            return {**result, 'reason': 'supplement_no_longer_current'}
    latest = bridge.guard.store.db.execute('''SELECT received_at FROM inbound
        WHERE platform=? AND session=? ORDER BY rowid DESC LIMIT 1''',
        (bridge.guard.phase3_platform_id, session)).fetchone()
    if latest:
        try:
            if datetime.fromisoformat(latest[0]).timestamp() > finished:
                return {**result, 'reason': 'supplement_no_longer_current'}
        except (ValueError, TypeError):
            return {**result, 'reason': 'supplement_invalid_input_time'}
    eligibility = bridge.state.supplement_eligibility(exchange, parent_attempt=parent,
        parent_kind=parent_kind, attempt_id=candidate)
    if not eligibility['allowed']:
        return {**result, 'reason': eligibility['reason']}
    return {**result, 'allowed': True, 'reason': 'allowed', 'idle_seconds': 0 if inline else minimum,
            'parent_turn_id': turn_id, 'parent_finished_at': finished,
            'same_opportunity_interest': inline}
