"""Durable safety boundary around PrivateCompanion's native proactive sender.

No scheduler, persona generator or extra Agent is created here. Native candidate
selection and generation remain upstream. This adapter only owns approved text
delivery, permission windows, suppression and restart-safe attempt consumption.
"""
import asyncio
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import inspect
import math
import re
import sqlite3
import time
import uuid
from zoneinfo import ZoneInfo


DECISION_PROMPT = """[PHASE3_PROACTIVE_DECISION_V1]
这次是一个可放弃的主动聊天机会，不是必须发送的任务。结合已有候选和真实上下文，
在本次生成内决定 send、defer 或 skip；不额外查询或虚构新闻、现实经历来制造动机。
如现在值得自然地聊，首行输出 [PHASE3_PROACTIVE:send]，下一行直接写完整聊天正文；
正文仍可使用既有 PRIVATE_COMPANION_SPLIT 分段协议，不为凑数追问。
如只是时机不合适，输出 [PHASE3_PROACTIVE:defer:240]（分钟，60–240），不附正文；
如候选已无意义、用户已回答、话题重复或没有可靠依据，输出 [PHASE3_PROACTIVE:skip]。
上一条未获回复不等于全面停止联系；经过间隔可以换有依据的新话题，不催回复，
不重复询问未回答的同一件事。明确忙碌、暂停和关闭话题优先。控制首行不是聊天正文。
"""


def parse_proactive_decision(text):
    """A single native generation chooses whether it has anything to say.

    This is an optional control header, not a replacement bubble protocol.
    Ordinary text without the header remains a send candidate and must still
    pass native content checks and Guard. Invalid control cannot leak to QQ.
    """
    text = str(text or '').strip()
    match = re.match(r'^\[PHASE3_PROACTIVE:(send|defer|skip)(?::(\d{1,4}))?\](?:\r?\n|$)', text)
    if not match:
        if 'PHASE3_PROACTIVE:' in text:
            return {'decision':'skip', 'text':'', 'delay_minutes':0, 'reason':'invalid_control'}
        return {'decision':'send' if text else 'skip', 'text':text,
                'delay_minutes':0, 'reason':'plain_text' if text else 'empty'}
    decision = match[1]
    value = text[match.end():].strip() if decision == 'send' else ''
    if 'PHASE3_PROACTIVE:' in value or (decision == 'send' and not value):
        return {'decision':'skip', 'text':'', 'delay_minutes':0, 'reason':'invalid_control'}
    minutes = min(240, max(60, int(match[2] or 240))) if decision == 'defer' else 0
    return {'decision':decision, 'text':value, 'delay_minutes':minutes, 'reason':'model_decision'}


async def _call(callback, *args):
    result = callback(*args)
    return await result if inspect.isawaitable(result) else result


def approved_window_after_passive(passive_passed_at, *, passive_passed=False):
    """Compute the approved trial only from a recorded real passive PASS.

    Pure calculation: no current-clock fallback, configuration write, activation,
    or substitution of an isolated test completion time. Caller supplies the
    real QQ passive acceptance timestamp after recording its evidence.
    """
    if passive_passed is not True:
        raise PermissionError("real_passive_acceptance_required")
    if not isinstance(passive_passed_at,str) or not passive_passed_at.strip():
        raise ValueError("passive_acceptance_timestamp_required")
    passed=datetime.fromisoformat(passive_passed_at.replace("Z","+00:00"))
    if passed.tzinfo is None or passed.utcoffset() is None:
        raise ValueError("passive_acceptance_timezone_required")
    passed=passed.astimezone(ZoneInfo("Asia/Shanghai"))
    start=passed.replace(hour=9,minute=0,second=0,microsecond=0)
    if start <= passed:
        start += timedelta(days=1)
    end=start+timedelta(hours=72)
    return {"timezone":"Asia/Shanghai", "passive_passed_at":passed.isoformat(),
        "window_start_iso":start.isoformat(), "window_end_iso":end.isoformat(),
        "proactive_window":{"window_start":int(start.timestamp()),"window_end":int(end.timestamp()),
                            "max_per_day":2,"cooldown":14400,"quiet_hours":[23,9]}}


def parse_proactive_controls(text, *, now, window_end):
    """Conservative commands from literal user text; no classifier/model call."""
    text = str(text or "").strip()
    actions = []
    # A quoted/hypothetical command must not silently change permission.
    if len(text) > 500 or any(mark in text for mark in ('"', '“', '”', '「', '」', '```', '如果', '假如', '比如')):
        return actions
    stop = r"(?:(?:先)?(?:不要|别|不用|不必)(?:再|总是|经常)?|停止|暂停)(?:主动(?:找我|联系我?|聊天|发消息)?|找我|打扰我|联系我)"
    if not re.search(r"(?:不要|别)(?:停止|暂停)",text) and re.search(stop, text):
        until = window_end or None
        if re.search(r"今天|今晚", text):
            local = datetime.fromtimestamp(now, ZoneInfo("Asia/Shanghai"))
            until = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            if window_end > 0:
                until = min(window_end, until)
        actions.append({"kind": "pause", "until": until})
    if re.search(r"(?:这件事|这个话题|这事).{0,6}(?:不想|不要|别|不用).{0,5}(?:再聊|再提|说了|聊了)", text):
        actions.append({"kind": "block_last_topic"})
    if re.search(r'(?:我(?:已经|现在)?忙完了|我现在有空了|现在可以聊了)(?:[，。！？\s]|$)',text):
        actions.append({'kind':'busy','until':0})
    elif re.search(r'(?:我(?:现在|这会儿)?(?:正在忙|在忙|不方便聊(?:天)?|不方便回复)|我现在忙|我(?:正在|在)开会)(?:[，。！？\s]|$)',text):
        # A short-lived control, never a permanent profile/preference. A later
        # explicit pause remains separate and is not cleared by "I'm free".
        actions.append({'kind':'busy','until':min(window_end,now+14400) if window_end > 0 else now+14400})
    return actions


def native_proactive_config(original, admin_id, enabled=False, *, policy=None):
    """Stage the pinned 6.7.0 text-only configuration, never mutate live data.

    Disk settings stay disabled even when activation was requested. Runtime may
    enable only instance attributes after policy validation and bridge attachment.
    Existing per-user capabilities are deliberately preserved. Activation also
    requires a separately approved native user capability update and Gate window.
    ``off`` is the native manual preset, not a master disable switch.
    """
    if not str(admin_id).isdigit() or type(enabled) is not bool:
        raise ValueError("invalid_proactive_identity_or_switch")
    mode = (policy or {}).get('mode', 'trial')
    if mode not in {'trial', 'continuous'}:
        raise ValueError('invalid_proactive_mode')
    result = deepcopy(original)
    groups = {
        "basic_config": {"target_user_ids": [str(admin_id)], "target_platform": "aiocqhttp",
            "enable_auto_user_profile_creation": False, "default_proactive_enabled": False,
            "default_proactive_daily_limit": 0, "enable_worldview_perception": False,
            "quiet_hours": ("%02d:00-%02d:00" % tuple(policy.get('quiet_hours', [0, 9]))) if mode == 'continuous' else "23:00-09:00"},
        "proactive_reach_config": {"proactive_intensity_preset": "off", "max_daily_messages": 0},
        "private_followup_config": {"enable_group_conversation_followup": False,
            "min_interval_minutes": math.ceil(policy.get('cooldown', 10800) / 60) if mode == 'continuous' else 240, "enable_proactive_burst": False,
            "proactive_unanswered_pause_after": 0},
        "proactive_generation_config": {"enable_llm_proactive_message": False,
            "enable_llm_proactive_persona_judge": False},
        "emotion_relationship_config": {"enable_proactive_message_review": False},
        "skill_growth_config": {"enable_skill_growth_simulation": False},
        "memory_habit_config": {"enable_meal_care_proactive": False},
        "life_schedule_config": {"enable_daily_plan": False},
        "weather_config": {"enable_environment_change_proactive": False},
        "timer_config": {"enable_proactive_quote_trigger_message": False,"enable_quote_private_proactive": False},
        "experimental_motivation_config": {"reaction_expression_proactive_enabled": False,
            "enable_emotion_simulation": False},
        "legacy_compat_config": {"enabled_proactive_actions": "message", "enable_segmented_proactive_reply": False},
    }
    for group, values in groups.items():
        result.setdefault(group, {}).update(values)
        # Older snapshots also contain flattened legacy keys. Keep them equal.
        for key, value in values.items():
            if key in result:
                result[key] = deepcopy(value)
    return result


def resolve_native_target(private, session):
    """Select the existing exact transport identity, including scoped user keys."""
    data = getattr(private, "data", {})
    users = data.get("users", {}) if isinstance(data,dict) else {}
    if not isinstance(users,dict):
        raise PermissionError("native_user_store_unavailable")
    matches = [(str(key), user) for key, user in users.items()
               if isinstance(user, dict) and user.get("umo") == session
               and private._proactive_identity_binding_is_verified(str(key), user)]
    if len(matches) != 1:
        raise PermissionError("native_identity_missing_or_ambiguous")
    return matches[0]


def authorize_native_target(private, session, *, approved=False, daily_limit=2):
    """Apply the explicit approved user migration through native capability API.

    No route/identity is invented: the target must already have a verified native
    private-chat UMO. Caller must back up the native store before this operation.
    This is not invoked merely because a configuration file enables the bridge.
    """
    if approved is not True or type(daily_limit) is not int or not 1 <= daily_limit <= 12:
        raise PermissionError("explicit_target_migration_approval_required")
    parts = str(session).split(":")
    if len(parts) != 3 or parts[1] != "FriendMessage" or not parts[2].isdigit():
        raise ValueError("invalid_native_target")
    native_id, user = resolve_native_target(private, session)
    outcome = private._req036_update_capabilities(user, {"proactive_private_enabled": True},
        actor_id="phase3_approved_migration", target_identity=native_id, reason_code="phase3_approved_window")
    if not outcome.get("ok"):
        raise PermissionError("native_capability_update_rejected")
    user["proactive_daily_limit"] = daily_limit
    private._schedule_data_save(sections={"users"})
    return {"target": session, "daily_limit": daily_limit, "native_capability_updated": True}


class ProactiveGate:
    def __init__(self, path, *, allowed_session, enabled=False, window_start=0,
                 window_end=0, max_per_day=2, cooldown=14400,
                 quiet_hours=(23, 9), mode='trial', max_generations_per_day=None, clock=time.time):
        for value in (window_start, window_end, cooldown):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("invalid_proactive_limit")
        if type(max_per_day) is not int or not 1 <= max_per_day <= 12 or cooldown < 60:
            raise ValueError("invalid_proactive_limit")
        if max_generations_per_day is None:
            max_generations_per_day = max_per_day
        if type(max_generations_per_day) is not int or not 1 <= max_generations_per_day <= 96:
            raise ValueError('invalid_proactive_generation_limit')
        if mode not in {'trial', 'continuous'}:
            raise ValueError('invalid_proactive_mode')
        if mode == 'continuous' and (window_start != 0 or window_end != 0):
            raise ValueError('continuous_requires_unbounded_window')
        if mode == 'trial' and ((enabled and not 0 < window_start < window_end)
                                or window_end - window_start > 7 * 86400):
            raise ValueError("invalid_approval_window")
        if len(quiet_hours) != 2 or any(type(n) is not int or not 0 <= n <= 23 for n in quiet_hours):
            raise ValueError("invalid_quiet_hours")
        self.session, self.enabled, self.clock = allowed_session, enabled, clock
        self.start, self.end, self.limit, self.cooldown, self.quiet = window_start, window_end, max_per_day, cooldown, quiet_hours
        self.mode, self.generation_limit = mode, max_generations_per_day
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS proactive_attempts(candidate TEXT PRIMARY KEY, day TEXT, attempted REAL, state TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS proactive_controls(name TEXT PRIMARY KEY, value TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS proactive_generations(candidate TEXT PRIMARY KEY, day TEXT, started REAL, state TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS proactive_event_offers(event_id TEXT, revision INTEGER, offered REAL, PRIMARY KEY(event_id,revision))")
        # Add observations to the existing safety ledger. Per-bubble identity
        # and transport truth still belong to Guard, not another history store.
        columns = {r[1] for r in self.db.execute('PRAGMA table_info(proactive_attempts)')}
        for name, kind in (('sent_count','INTEGER NOT NULL DEFAULT 0'),
                           ('part_count','INTEGER NOT NULL DEFAULT 0'), ('finished','REAL'),
                           ('topic_id',"TEXT NOT NULL DEFAULT ''")):
            if name not in columns:
                self.db.execute('ALTER TABLE proactive_attempts ADD COLUMN '+name+' '+kind)
        self.db.execute("UPDATE proactive_generations SET state='interrupted' WHERE state='running'")
        self.db.execute("UPDATE proactive_attempts SET state='unknown' WHERE state='pending'")
        self.db.commit()
        self._lock = asyncio.Lock()

    def _get(self, key, default=""):
        row = self.db.execute("SELECT value FROM proactive_controls WHERE name=?", (key,)).fetchone()
        return row[0] if row else default

    def set_control(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO proactive_controls VALUES (?,?)", (key, str(value)))
        self.db.commit()

    def pause(self, until=None):
        if until is None and self.mode == 'continuous':
            self.set_control('pause_indefinite', '1')
            return
        until = self.end if until is None else until
        if isinstance(until, bool) or not isinstance(until, (int, float)) or not math.isfinite(until) or until < 0:
            raise ValueError('invalid_pause_deadline')
        if until == 0:
            self.set_control('pause_indefinite', '0')
        self.set_control("pause_until", until)

    def approved_window_valid(self):
        if self.mode == 'continuous':
            return self.start == self.end == 0
        return 0 < self.start < self.end and self.end - self.start <= 7 * 86400

    def window_open(self, now=None):
        now = self.clock() if now is None else now
        return self.approved_window_valid() and (self.mode == 'continuous' or self.start <= now < self.end)

    def expired(self, now=None):
        now = self.clock() if now is None else now
        return self.mode == 'trial' and now >= self.end

    def candidate_expiry(self, deadline, *other_deadlines):
        """A permanent permission never gives a candidate a permanent lifetime."""
        deadline = deadline or (self.end if self.mode == 'trial' else 0)
        try:
            expiry = float(deadline)
            if not math.isfinite(expiry) or expiry <= 0:
                return 0
            limits = other_deadlines + ((self.end,) if self.mode == 'trial' else ())
            for limit in limits:
                if limit is None:
                    continue
                limit = float(limit)
                if not math.isfinite(limit) or limit <= 0:
                    return 0
                expiry = min(expiry, limit)
            return expiry
        except (TypeError, ValueError):
            return 0

    def _paused(self, now):
        return (self._get('pause_indefinite') == '1'
                or now < max(float(self._get('pause_until', '0')), float(self._get('busy_until', '0'))))

    def block_topic(self, topic_id):
        self.set_control("blocked:" + topic_id, "1")

    def permit(self, session, *, candidate_id, topic_id="", event_state="", source="conversation", source_verified=False, busy=False,
               last_user_reply_at=0):
        now = self.clock()
        if not self.enabled or session != self.session:
            return "not_authorized"
        if not self.window_open(now):
            return "outside_approved_window"
        if busy or self._paused(now):
            return "paused_or_busy"
        if event_state in {"completed", "answered", "responded", "cancelled", "do_not_discuss", "expired", "not_yet"}:
            return "event_not_due"
        if topic_id and self._get("blocked:" + topic_id):
            return "topic_suppressed"
        if topic_id:
            spoken_at = float(self._get('spoken:'+topic_id, '0'))
            if spoken_at and last_user_reply_at <= spoken_at and now-spoken_at < 86400:
                return 'unanswered_same_topic'
        if source not in {"conversation", "event_followup", "own_opinion", "public_discovery", "want_to_chat"}:
            return "unsupported_source"
        if source == "public_discovery" and not source_verified:
            return "unverified_discovery"
        local = datetime.fromtimestamp(now, ZoneInfo("Asia/Shanghai"))
        a, b = self.quiet
        if (a < b and a <= local.hour < b) or (a > b and (local.hour >= a or local.hour < b)):
            return "quiet_hours"
        if not candidate_id:
            return "missing_candidate_identity"
        if self.db.execute("SELECT 1 FROM proactive_attempts WHERE candidate=?", (candidate_id,)).fetchone():
            return "already_attempted"
        day = local.date().isoformat()
        rows = self.db.execute("SELECT attempted FROM proactive_attempts WHERE day=?", (day,)).fetchall()
        last = self.db.execute("SELECT MAX(attempted) FROM proactive_attempts").fetchone()[0]
        if len(rows) >= self.limit or last is not None and now - last < self.cooldown:
            return "budget_or_cooldown"
        return "allowed"

    async def deliver(self, session, *, candidate_id, send, topic_id="", observe_delivery=None, **context):
        async with self._lock:
            reason = self.permit(session, candidate_id=candidate_id, topic_id=topic_id, **context)
            if reason != "allowed":
                return {"state": "blocked", "reason": reason}
            now = self.clock()
            day = datetime.fromtimestamp(now, ZoneInfo("Asia/Shanghai")).date().isoformat()
            self.db.execute("INSERT INTO proactive_attempts(candidate,day,attempted,state,topic_id) VALUES (?,?,?,'pending',?)", (candidate_id, day, now,topic_id))
            self.db.commit()
            state = "unknown"
            sent_count = part_count = 0
            try:
                result = await send()
                state = result.get("state", "unknown") if isinstance(result, dict) else "unknown"
                if state not in {"sent", "failed", "unknown", "cancelled"}:
                    state = "unknown"
                if isinstance(result, dict):
                    sent_count = max(0, int(result.get('sent_count', 1 if state=='sent' else 0)))
                    part_count = max(sent_count, int(result.get('part_count', sent_count)))
                return {"state": state, "delivery": result}
            finally:
                if observe_delivery is not None:
                    # A task can be cancelled after a prefix was acknowledged.
                    # Read Guard's durable result before settling the aggregate;
                    # this is not a second send or a fabricated acknowledgement.
                    observed = observe_delivery()
                    if isinstance(observed,dict) and observed.get('parts'):
                        state = observed['state']
                        sent_count = observed['sent_count']
                        part_count = observed['part_count']
                self.db.execute("UPDATE proactive_attempts SET state=?,sent_count=?,part_count=?,finished=? WHERE candidate=?",
                                (state,sent_count,part_count,self.clock(),candidate_id))
                self.db.commit()
                # Failed/unknown attempts also consume the allowance: never
                # trigger a model or network loop to chase a missing receipt.
                if topic_id and sent_count:
                    self.set_control("last_topic", topic_id)
                    self.set_control('spoken:'+topic_id, self.clock())

    async def reserve_generation(self, session, *, candidate_id, evaluation_id=None, **context):
        """Consume one generation allowance before invoking the native model path."""
        async with self._lock:
            reason = self.permit(session, candidate_id=candidate_id, **context)
            if reason != "allowed":
                return reason
            evaluation_id = evaluation_id or candidate_id
            if self.db.execute("SELECT 1 FROM proactive_generations WHERE candidate=?", (evaluation_id,)).fetchone():
                return "generation_already_attempted"
            now = self.clock()
            day = datetime.fromtimestamp(now, ZoneInfo("Asia/Shanghai")).date().isoformat()
            count = self.db.execute("SELECT count(*) FROM proactive_generations WHERE day=?", (day,)).fetchone()[0]
            if count >= self.generation_limit:
                return "generation_budget"
            self.db.execute("INSERT INTO proactive_generations VALUES (?,?,?,'running')", (evaluation_id, day, now))
            self.db.commit()
            return "allowed"

    def finish_generation(self, candidate_id, state):
        if state not in {"generated", "empty", "failed", "cancelled", "expired", "deferred", "skipped"}:
            raise ValueError("invalid_generation_state")
        self.db.execute("UPDATE proactive_generations SET state=? WHERE candidate=? AND state='running'", (state, candidate_id))
        self.db.commit()

    def continuation_allowed(self, session, *, topic_id="", busy=False):
        # Dynamic pre-network recheck omits consumed candidate/budget counters.
        now = self.clock()
        hour = datetime.fromtimestamp(now, ZoneInfo("Asia/Shanghai")).hour
        a, b = self.quiet
        quiet = (a < b and a <= hour < b) or (a > b and (hour >= a or hour < b))
        return (self.enabled and session == self.session and self.window_open(now)
                and not quiet and not busy and not self._paused(now)
                and not (topic_id and self._get("blocked:" + topic_id)))

    def close(self):
        self.db.close()


class NativeProactiveBridge:
    """Reversible instance-local binding at native generation and send entries.

    Candidate selection and the framework Agent remain native. Only admission,
    accounting, lifecycle lookup and the final text send are first-party.
    """
    SOURCE_MAP = {"followup": "event_followup", "pending_followup": "event_followup",
                  "event": "event_followup", "story": "conversation", "conversation": "conversation",
                  "random": "want_to_chat", "impulse": "want_to_chat", "proactive": "want_to_chat",
                  "interest": "own_opinion", "own_opinion": "own_opinion", "news": "public_discovery",
                  "public_discovery": "public_discovery"}
    CONTINUOUS_REASONS = frozenset({'followup', 'pending_followup', 'open_loop_followup',
        'activity_followup', 'conversation', 'story', 'random', 'impulse', 'proactive',
        'interest', 'own_opinion', 'want_to_chat', 'memory_echo', 'mood_checkin',
        'absence_miss', 'state_share', 'quiet_care', 'check_in'})

    def __init__(self, private, guard, gate, outcome_type, *, event_resolver=None, event_source=None,
                 event_delivered=None, generation_timeout=60, history_policy=None):
        if not math.isfinite(generation_timeout) or not 0 < generation_timeout <= 120:
            raise ValueError("invalid_generation_timeout")
        self.private, self.guard, self.gate, self.outcome_type = private, guard, gate, outcome_type
        self.event_resolver, self.event_delivered = event_resolver, event_delivered
        self.event_source = event_source
        self.generation_timeout = generation_timeout
        from .proactive_history import NativeProactiveHistory
        self._history_adapter = NativeProactiveHistory(private, gate.session, history_policy)
        self.original = None
        self.original_generate = None
        self.original_framework = None
        self._additional_bindings = {}
        self._active_generations = {}
        self._decisions = {}
        self._semantic_scope = ContextVar('phase3_proactive_semantic_scope',default=False)
        self._native_settings_active = False
        self._native_settings_before = {}
        self.metrics = {"generation_attempts": 0, "generation_blocked": 0, "generation_errors": 0,
                        "generation_seconds": 0.0, "delivery_blocked": 0, "delivered": 0}

    def attach(self):
        if self.original is not None:
            return
        required = ("_send_proactive_message_chain", "_generate_proactive_message_with_llm",
                    "_planned_proactive_delivery_key", "_planned_proactive_impulse")
        if any(not callable(getattr(self.private, name, None)) for name in required):
            raise ValueError("pinned_private_proactive_api_missing")
        self._history_adapter.check_api()
        self._reconcile_guard_attempts()
        self.original = self.private._send_proactive_message_chain
        self.original_generate = self.private._generate_proactive_message_with_llm
        self.private._send_proactive_message_chain = self.send
        self.private._generate_proactive_message_with_llm = self.generate
        if callable(getattr(self.private, "_run_framework_agent_text", None)):
            self.original_framework = self.private._run_framework_agent_text
            self.private._run_framework_agent_text = self.framework
        for name, callback in (("_tick_user", self.tick_user),
                               ("_run_scheduler_cycle", self.scheduler_cycle),
                               ("_filter_incompatible_proactive_framework_tools", self.clear_framework_tools),
                               ("_execute_proactive_action", self.execute_action),
                               ("_generate_proactive_message_direct_fallback", self.no_second_generation),
                               ("_proactive_unanswered_pause_reason", self.unanswered_pause_reason),
                               ("_reason_windows", self.reason_windows),
                               ("_mark_planned_candidate_status", self.mark_candidate_status),
                               ("_proactive_llm_segmenting_allowed", self.semantic_proactive_allowed),
                               ("_visible_text_without_tts_reading", self.visible_semantic_text),
                               ("_normalize_proactive_sentence_flow", self.normalize_semantic_text),
                               ("_remove_unbacked_media_claims", self.remove_semantic_media_claims),
                               ("_maybe_run_pre_message_poke", self.pre_message_poke)):
            original = getattr(self.private, name, None)
            if callable(original):
                self._additional_bindings[name] = original
                setattr(self.private, name, callback)
        self._history_adapter.attach(self._additional_bindings)

    def _reconcile_guard_attempts(self):
        """Observe acknowledged prefixes after restart; never resume a send."""
        store = getattr(self.guard,'store',None)
        if store is None:
            return
        rows = self.gate.db.execute(
            "SELECT candidate,attempted,topic_id FROM proactive_attempts WHERE state='unknown' ORDER BY attempted").fetchall()
        for candidate_id, attempted, topic_id in rows:
            turn_id = str(uuid.uuid5(uuid.NAMESPACE_URL,'qq-companion-proactive:proactive:'+candidate_id))
            observed = store.bubble_result(turn_id,'proactive')
            if not observed.get('parts'):
                continue
            with self.gate.db:
                self.gate.db.execute('UPDATE proactive_attempts SET state=?,sent_count=?,part_count=? WHERE candidate=?',
                    (observed['state'],observed['sent_count'],observed['part_count'],candidate_id))
            if topic_id and observed['sent_count']:
                self.gate.set_control('spoken:'+topic_id,max(attempted,float(self.gate._get('spoken:'+topic_id,'0'))))
        latest = self.gate.db.execute(
            "SELECT topic_id FROM proactive_attempts WHERE sent_count>0 AND topic_id!='' ORDER BY attempted DESC LIMIT 1").fetchone()
        if latest:
            self.gate.set_control('last_topic',latest[0])

    def semantic_proactive_allowed(self,*,umo=''):
        # Only native generation/finalization gets the existing marker protocol.
        # Its original sender stays replaced by Guard for the entire attachment.
        # No native passive segment sender or global segmentation switch is armed.
        return bool(getattr(self.guard,'multibubble_enabled',False)
                    and umo in self._active_generations
                    and self.gate.continuation_allowed(umo,busy=self.guard.session_busy(umo)))

    def _transform_semantic_text(self,name,text,**kwargs):
        original=self._additional_bindings[name]
        marked='PRIVATE_COMPANION_SPLIT' in str(text)
        if marked:
            field='semantic_marked_in_scope' if self._semantic_scope.get() else 'semantic_marked_outside_scope'
            self.metrics[field]=self.metrics.get(field,0)+1
        if not self._semantic_scope.get() or not getattr(self.guard,'multibubble_enabled',False):
            return original(text,**kwargs)
        from astrbot.api.event import MessageEventResult
        from ..phase1_guard.bubbles import MARKER
        parts=self.guard.semantic_parts(MessageEventResult().message(str(text or '')))
        if marked:self.metrics['semantic_last_part_count']=len(parts)
        if len(parts)<2:return original(text,**kwargs)
        # Preserve boundaries while still applying each original cleanup. Never
        # reinsert raw text removed by a later safety filter or exceed its cap.
        remaining=int(kwargs.get('limit',260));cleaned=[]
        for part in parts:
            if remaining<=0:break
            value=str(original(part.get_plain_text(),**kwargs) or '')[:remaining].strip()
            if value:cleaned.append(value);remaining-=len(value)
        return ('\n'+MARKER+'\n').join(cleaned)

    def visible_semantic_text(self,text,*,limit=1000):
        return self._transform_semantic_text('_visible_text_without_tts_reading',text,limit=limit)

    def normalize_semantic_text(self,text):
        return self._transform_semantic_text('_normalize_proactive_sentence_flow',text)

    def remove_semantic_media_claims(self,text):
        return self._transform_semantic_text('_remove_unbacked_media_claims',text)

    def activate_native_config(self):
        """Arm only the pinned instance after all policy checks; never save config."""
        if self.original is None or not self.gate.enabled or not self.gate.approved_window_valid():
            raise PermissionError("attached_approved_window_required")
        # The pinned primary-persona accessor reads instance attributes. Keeping
        # the AstrBotConfig untouched prevents any UI/save/restart from arming it.
        if not callable(getattr(self.private,"persona_setting",None)):
            raise ValueError("pinned_persona_runtime_accessor_required")
        if not self._native_settings_active and (getattr(self.private,"max_daily_messages",0) != 0
                or getattr(self.private,"enable_llm_proactive_message",False)):
            raise PermissionError("native_cold_start_must_be_disabled")
        if self.gate.expired():
            # An expired approval must not break the passive service on restart.
            self.gate.enabled=False
            self._disable_native_config()
            return False
        if not self._native_settings_active:
            self._native_settings_before = {key: (hasattr(self.private, key), getattr(self.private, key, None))
                for key in ('quiet_hours', 'min_interval_minutes')}
        a, b = self.gate.quiet
        self.private.quiet_hours = f'{a:02d}:00-{b:02d}:00' if a != b else ''
        self.private.min_interval_minutes = math.ceil(self.gate.cooldown / 60)
        self.private.max_daily_messages=self.gate.limit
        self.private.enable_llm_proactive_message=True
        self._native_settings_active=True
        return True

    def _disable_native_config(self):
        if self._native_settings_active:
            self.private.max_daily_messages=0
            self.private.enable_llm_proactive_message=False
            for key, (present, value) in self._native_settings_before.items():
                if present:
                    setattr(self.private, key, value)
                elif hasattr(self.private, key):
                    delattr(self.private, key)
            self._native_settings_before.clear()
            self._native_settings_active=False

    def detach(self):
        # Disable before restoring native methods; there is no unguarded gap.
        self._disable_native_config()
        if self.original is not None:
            self.private._send_proactive_message_chain = self.original
            self.private._generate_proactive_message_with_llm = self.original_generate
            if self.original_framework is not None:
                self.private._run_framework_agent_text = self.original_framework
            self.original = self.original_generate = self.original_framework = None
            for name, original in self._additional_bindings.items():
                setattr(self.private, name, original)
            self._additional_bindings.clear()

    async def scheduler_cycle(self, *, immediate=False):
        if self.gate.expired():
            self.gate.enabled=False
            self._disable_native_config()
        # Native max_daily=0 stops its proactive path while preserving its
        # passive-cache maintenance. The per-user/model gates still reject all
        # attempts outside the approved daytime/window; no second timer exists.
        await self.offer_due_event()
        await self.maintain_native_plan()
        return await self._additional_bindings["_run_scheduler_cycle"](immediate=immediate)

    def _plan_observation(self, reason, *, user=None, repaired=False, retired=False):
        """Fixed status metadata in the existing native runtime, never content."""
        self.metrics['plan_maintenance_reason'] = reason
        data = getattr(self.private, 'data', None)
        if not isinstance(data, dict):
            return reason
        runtime = data.setdefault('proactive_runtime', {})
        if not isinstance(runtime, dict):
            return reason
        previous = runtime.get('phase3_plan_maintenance') or {}
        if not isinstance(previous, dict):
            previous = {}
        def count(key):
            value = previous.get(key, 0)
            return value if type(value) is int and value >= 0 else 0
        def timestamp(value):
            try:
                value = float(value or 0)
                return value if math.isfinite(value) and value >= 0 else 0
            except (TypeError, ValueError):
                return 0
        runtime['phase3_plan_maintenance'] = {
            'reason': reason, 'checked_at': self.gate.clock(),
            'next_consider_at': timestamp((user or {}).get('next_proactive_at')),
            'expires_at': timestamp((user or {}).get('planned_proactive_expire_at')),
            'repair_count': count('repair_count') + int(repaired),
            'retired_count': count('retired_count') + int(retired),
            'error_count': count('error_count') + int(reason == 'maintenance_error'),
        }
        self.private._schedule_data_save(sections={'proactive_runtime'})
        return reason

    def _plan_retirement_reason(self, user, native, candidate_id, context):
        """A terminal/consumed plan can only be retired, never regenerated."""
        if candidate_id and self.gate.db.execute(
                'SELECT 1 FROM proactive_attempts WHERE candidate=?', (candidate_id,)).fetchone():
            return 'attempt_consumed'
        if context['event_state'] in {'expired', 'completed', 'answered', 'responded',
                                      'cancelled', 'do_not_discuss'}:
            return 'plan_expired' if context['event_state'] == 'expired' else 'plan_terminal'
        if context['topic_id'] and self.gate._get('blocked:' + context['topic_id']):
            return 'topic_suppressed'
        if candidate_id:
            evidence = native.get('context') or {}
            decision = evidence.get('phase3_decision', {}) if isinstance(evidence, dict) else {}
            evaluation_id = candidate_id
            if decision.get('decision') == 'defer':
                evaluation_id += ':reconsider:' + str(int(decision.get('next_consider_at', 0)))
            generated = self.gate.db.execute(
                'SELECT state FROM proactive_generations WHERE candidate=?', (evaluation_id,)).fetchone()
            if generated:
                return 'generation_consumed'
        stale = getattr(self.private, '_is_proactive_plan_stale', None)
        if callable(stale) and stale(user, now=self.gate.clock()):
            return 'plan_expired'
        return ''

    def _retire_native_plan(self, user, reason):
        # Preserve original IDs and Guard/generation ledger entries. Native
        # "failed" maps an impulse back to queued, so use terminal "dropped".
        self.private._mark_planned_candidate_status(user, 'dropped', 'phase3_' + reason)
        self.private._clear_pending_proactive_send_retry(user)
        self.private._clear_pending_proactive_plan(user)
        self.private._schedule_data_save(sections={'users', 'proactive_candidate_pool'})

    async def maintain_native_plan(self):
        """Repair native cold/expired plans before the guarded tick, without I/O.

        Private cold-start settings deliberately disable generation and clear
        plans. Once approved instance settings are armed, the delivery gate must
        not stop its *local* planning entry from ever becoming reachable. This
        runs once per existing native cycle and only under its own data lock;
        generation, timing, eligibility and delivery still pass their old gates.
        """
        if not self._native_settings_active:
            return 'native_not_active'
        required = ('_schedule_next_proactive', '_clear_pending_proactive_plan',
                    '_mark_planned_candidate_status', '_clear_pending_proactive_send_retry',
                    '_user_enabled_for_proactive', '_proactive_generation_disabled',
                    '_schedule_data_save')
        lock = getattr(self.private, '_data_lock', None)
        if lock is None or not hasattr(lock, '__aenter__') or any(
                not callable(getattr(self.private, name, None)) for name in required):
            self.metrics['plan_maintenance_reason'] = 'native_api_unavailable'
            return 'native_api_unavailable'
        try:
            async with lock:
                try:
                    user_id, user = resolve_native_target(self.private, self.gate.session)
                except PermissionError:
                    return self._plan_observation('identity_unavailable')
                # This sentinel is only a read-only admission query, never a
                # candidate, generation, send attempt or quota reservation.
                admission = self.gate.permit(self.gate.session,
                    candidate_id='phase3_local_plan_maintenance', source='want_to_chat',
                    busy=self.guard.session_busy(self.gate.session))
                if admission != 'allowed':
                    return self._plan_observation(admission, user=user)
                if self.gate.session in self._active_generations:
                    return self._plan_observation('generation_in_progress', user=user)
                if (not self.private._user_enabled_for_proactive(user_id, user)
                        or self.private._proactive_generation_disabled(user)):
                    return self._plan_observation('native_not_authorized', user=user)
                day = datetime.fromtimestamp(self.gate.clock(), ZoneInfo('Asia/Shanghai')).date().isoformat()
                count = self.gate.db.execute(
                    'SELECT count(*) FROM proactive_generations WHERE day=?', (day,)).fetchone()[0]
                if count >= self.gate.generation_limit:
                    return self._plan_observation('generation_budget', user=user)
                session, candidate_id, _, context = await self._context(user)
                native = self.private._planned_proactive_impulse(user) or {}
                reason = self._plan_retirement_reason(user, native, candidate_id, context)
                next_at = float(user.get('next_proactive_at') or 0)
                if not reason and candidate_id and next_at > 0:
                    return self._plan_observation(
                        'waiting' if next_at > self.gate.clock() else 'plan_due', user=user)
                retired = bool(reason and candidate_id)
                if retired:
                    self._retire_native_plan(user, reason)
                else:
                    self.private._clear_pending_proactive_plan(user)
                # No delay override, synthetic topic or forced due time. Fixed
                # native candidate selection chooses the next local opportunity.
                self.private._schedule_next_proactive(user, now=self.gate.clock())
                self.private._schedule_data_save(sections={'users', 'proactive_candidate_pool'})
                _, new_id, _, new_context = await self._context(user)
                new_native = self.private._planned_proactive_impulse(user) or {}
                # Native may pick a different stored candidate. Do not give an
                # already-consumed candidate a new send/retry opportunity.
                invalid = self._plan_retirement_reason(user, new_native, new_id, new_context)
                if new_id and invalid:
                    self._retire_native_plan(user, invalid)
                    return self._plan_observation('native_candidate_retired', user=user,
                        repaired=True, retired=True)
                scheduled = bool(new_id and float(user.get('next_proactive_at') or 0) > 0)
                return self._plan_observation('scheduled' if scheduled else 'no_candidate',
                    user=user, repaired=True, retired=retired)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A local maintenance failure cannot enable a send or break passive
            # chat. Do not publish exception text (it can contain private data).
            self.metrics['plan_maintenance_reason'] = 'maintenance_error'
            self.metrics['plan_maintenance_errors'] = self.metrics.get('plan_maintenance_errors', 0) + 1
            try:
                async with lock:
                    self._plan_observation('maintenance_error')
            except asyncio.CancelledError:
                raise
            except Exception:
                pass  # If the native store itself failed, retain the metric.
            return 'maintenance_error'

    async def offer_due_event(self):
        """Feed confirmed due facts into the existing native candidate admission."""
        if not self.event_source or not self.gate.continuation_allowed(
                self.gate.session, busy=self.guard.session_busy(self.gate.session)):
            return False
        offer = getattr(self.private, "_offer_proactive_candidate", None)
        if not callable(offer):
            return False
        try:
            user_id, user = resolve_native_target(self.private,self.gate.session)
        except PermissionError:
            return False
        # Do not consume an event/revision at night only to have the native
        # candidate expire before its own follow-up reason window opens.
        if not self.private._is_reason_allowed_now("open_loop_followup",user):
            return False
        for item in (await _call(self.event_source,self.gate.session))[:20]:
            event_id, revision = str(item.get("event_id") or ""), item.get("revision")
            quote = str(item.get("user_quote") or "").strip()
            if not event_id or len(event_id)>80 or type(revision) is not int or revision<1 or not quote:
                continue
            if self.gate.db.execute("SELECT 1 FROM proactive_event_offers WHERE event_id=? AND revision=?",(event_id,revision)).fetchone():
                continue
            status = await _call(self.event_resolver,self.gate.session,event_id) if self.event_resolver else {}
            if not status.get("eligible") or status.get("revision") != revision:
                continue
            now=self.gate.clock()
            expiry=self.gate.candidate_expiry(now+3600)
            if item.get("expires_at"):
                try:
                    parsed=datetime.fromisoformat(item["expires_at"])
                    if parsed.tzinfo is None: continue
                    expiry=min(expiry,parsed.timestamp())
                except (ValueError,TypeError): continue
            if expiry<=now: continue
            candidate={"source":"followup","reason":"open_loop_followup","action":"message",
                "topic":quote[:80],"motive":"用户确认的待结果事项："+quote[:150],
                "scheduled_ts":now,"window_start_at":now,"preferred_ts":now,
                "best_until_at":expiry,"expire_at":expiry,"window_timezone":"Asia/Shanghai",
                "origin_event_id":event_id,"trigger_message_id":str(item.get("source_id") or ""),
                "trigger_umo":self.gate.session,
                "context":{"phase3_event_revision":revision,"user_quote":quote[:1000]}}
            accepted=bool(offer(user_id,user,candidate))
            if accepted:
                self.private._schedule_data_save(sections={"users"})
                with self.gate.db:
                    self.gate.db.execute("INSERT OR IGNORE INTO proactive_event_offers VALUES (?,?,?)",(event_id,revision,now))
            return accepted
        return False

    async def tick_user(self, user_id, user):
        """Guard the native tick before its review/model/action side effects."""
        if not isinstance(user,dict):
            return
        session, candidate_id, _, context = await self._context(user)
        if self.gate.permit(session, candidate_id=candidate_id, **context) != "allowed":
            self.metrics["generation_blocked"] += 1
            return
        native = self.private._planned_proactive_impulse(user)
        token=self._semantic_scope.set(True)
        try:return await self._additional_bindings["_tick_user"](user_id,user)
        finally:
            self._semantic_scope.reset(token)
            # Native treats an empty generation as a failed render. Restore only
            # this candidate's deliberate defer/skip metadata after its tick;
            # never restore text, clear attempts, or overwrite another plan.
            decision = self._decisions.pop(candidate_id, None)
            if decision and decision.get('reason')=='model_decision' and decision['decision'] in {'defer','skip'}:
                self._record_decision(user, native, decision)

    def unanswered_pause_reason(self, user):
        if isinstance(user,dict) and user.get('umo') == self.gate.session:
            # Explicitly approved preference: unanswered does not disable all
            # new topics. The gate separately enforces spoken-topic cooldown,
            # pauses, busy state, candidate lifecycle and the approved cap.
            return ''
        return self._additional_bindings['_proactive_unanswered_pause_reason'](user)

    def reason_windows(self, reason, user=None):
        # Native general conversation/follow-up reasons otherwise stop at 22:00
        # or 23:00. Keep greeting/meal/birthday reasons' own semantic windows.
        if (self.gate.mode == 'continuous' and self.gate.enabled
                and self._native_settings_active and reason in self.CONTINUOUS_REASONS
                and (user is None or isinstance(user, dict) and user.get('umo') == self.gate.session)):
            return [(self.gate.quiet[1] * 60, (self.gate.quiet[0] or 24) * 60)]
        return self._additional_bindings['_reason_windows'](reason, user)

    async def no_second_generation(self, user, *args, **kwargs):
        if str(user.get('umo') or '') in self._active_generations:
            return ''
        return await self._additional_bindings['_generate_proactive_message_direct_fallback'](user,*args,**kwargs)

    def mark_candidate_status(self, user, status, note='', **kwargs):
        candidate_id = str(self.private._planned_proactive_delivery_key(user) or '')
        attempted = self.gate.db.execute(
            'SELECT state,sent_count,part_count FROM proactive_attempts WHERE candidate=?',
            (candidate_id,)).fetchone()
        if attempted is not None:
            native = self.private._planned_proactive_impulse(user)
            if isinstance(native,dict):
                context = native.get('context')
                if context is None:
                    context = native['context'] = {}
                if isinstance(context,dict):
                    context['phase3_delivery'] = {'state':attempted[0], 'sent_count':attempted[1],
                                                 'part_count':attempted[2]}
            if status=='sent' and attempted[0]!='sent':
                # Upstream uses delivered-prefix truthiness for archiving and
                # then marks the candidate sent. Keep its correct archive but
                # retire the incomplete plan without claiming whole-plan SENT.
                status='dropped'
                note='phase3_incomplete_'+attempted[0]
            clear_retry = getattr(self.private,'_clear_pending_proactive_send_retry',None)
            if callable(clear_retry):
                clear_retry(user)
        return self._additional_bindings['_mark_planned_candidate_status'](user,status,note,**kwargs)

    def _record_decision(self, user, native, decision):
        if not isinstance(native,dict):
            return
        now = self.gate.clock()
        previous = native.get('context') or {}
        previous = previous.get('phase3_decision', {}) if isinstance(previous, dict) else {}
        expiry = self.gate.candidate_expiry(native.get('expire_at'),
            decision.get('expires_at'), previous.get('expires_at'))
        next_at = now + decision.get('delay_minutes',0)*60
        state = decision['decision']
        if state == 'defer' and next_at >= expiry:
            state = 'skip'
        # Pinned random impulses legitimately start with context=None. This
        # is an empty native metadata slot, not an invalid payload to overwrite.
        context = native.get('context')
        if context is None:
            context = native['context'] = {}
        if not isinstance(context,dict):
            return
        context['phase3_decision'] = {'decision':state, 'decided_at':now,
            'next_consider_at':next_at if state=='defer' else 0,
            'expires_at':expiry, 'reason':decision.get('reason','model_decision')}
        if state in {'defer','skip'}:
            native['expire_at'] = expiry
            native['state'] = 'deferred' if state=='defer' else 'blocked'
            native['last_status'] = 'deferred' if state=='defer' else 'dropped'
            native['last_note'] = 'phase3_model_'+state
            native['updated_ts'] = now
            if state == 'defer':
                native['window_start_at'] = next_at
                native['preferred_ts'] = next_at
                native['best_until_at'] = min(expiry,max(float(native.get('best_until_at') or 0),next_at))
            # No deadline extension. If the native plan still points to this
            # impulse, keep its existing scheduler's time aligned.
            if user.get('planned_proactive_impulse_id') == native.get('id'):
                if state=='defer':
                    user['next_proactive_at'] = next_at
                    user['planned_proactive_window_start_at'] = next_at
                    user['planned_proactive_best_until_at'] = native['best_until_at']
                    user['planned_proactive_expire_at'] = expiry
                user['planned_proactive_delivery_state'] = native['last_status']
        self.private._schedule_data_save(sections={'users'})

    async def execute_action(self, action, *args, **kwargs):
        if action != "message":
            return {"success": False, "effective_action": action, "context": "phase3_text_only_boundary"}
        return await self._additional_bindings["_execute_proactive_action"](action, *args, **kwargs)

    async def pre_message_poke(self, *args, **kwargs):
        return 0, ""

    def clear_framework_tools(self, req, names=None):
        # Pinned _run_framework_agent_text invokes this after build_main_agent
        # and before step_until_done. ToolLoopAgentRunner.reset stores this same
        # ProviderRequest object, so clearing it also clears provider/executor
        # access, including Search/Fetch/cron/file and phase3_event tools.
        tools = getattr(req,"func_tool",None)
        removed=[str(tool.name) for tool in getattr(tools,"tools",[]) or []]
        req.func_tool=None
        self.metrics["proactive_tools_removed"]=self.metrics.get("proactive_tools_removed",0)+len(removed)
        return removed

    async def _context(self, user):
        session = str(user.get("umo") or "")
        candidate_id = str(self.private._planned_proactive_delivery_key(user) or "")
        native = self.private._planned_proactive_impulse(user) or {}
        if not native and not user.get("planned_candidate_id"):
            candidate_id = ""
        topic = str(user.get("planned_proactive_topic") or "")
        signature = getattr(self.private,'_proactive_topic_signature',None)
        if topic and callable(signature):
            topic = str(signature(topic) or topic)
        source = self.SOURCE_MAP.get(str(user.get("planned_proactive_source") or ""), "unsupported")
        event_id = str(native.get("origin_event_id") or "") if source == "event_followup" else ""
        event_state = ""
        if source == "event_followup":
            if not event_id or self.event_resolver is None:
                event_state = "not_yet"
            else:
                status = await _call(self.event_resolver,session,event_id)
                event_state = str(status.get("state") or "not_yet")
                evidence = native.get("context")
                revision = evidence.get("phase3_event_revision") if isinstance(evidence,dict) else None
                if not status.get("eligible", False) or status.get("revision") != revision:
                    event_state = "not_yet"
        if native:
            now = self.gate.clock()
            evidence = native.get('context') or {}
            decision = evidence.get('phase3_decision',{}) if isinstance(evidence,dict) else {}
            expiry = self.gate.candidate_expiry(native.get('expire_at'), decision.get('expires_at'))
            if str(native.get('state') or '') in {'sent', 'blocked', 'cancelled', 'dropped', 'failed', 'unknown'}:
                event_state = 'completed' if native.get('state') == 'sent' else 'cancelled'
            elif expiry <= now:
                event_state = 'expired'
            elif decision.get('decision') == 'skip':
                event_state = 'cancelled'
            elif decision.get('decision') == 'defer' and now < float(decision.get('next_consider_at',0)):
                event_state = 'not_yet'
        elif self.gate.mode == 'continuous' and self.gate.candidate_expiry(
                user.get('planned_proactive_expire_at')) <= self.gate.clock():
            event_state = 'expired'
        reply_at = max((float(user.get(key) or 0) for key in
            ('last_user_message_at','last_private_seen','last_private_activity_at')),default=0)
        # Background discovery is a separate release. A native string claiming
        # it searched is not verified provenance and cannot unlock this path.
        return session, candidate_id, event_id, {
            "topic_id": hashlib.sha256(topic.encode()).hexdigest() if topic else "",
            "event_state": event_state, "source": source, "source_verified": False,
            "busy": self.guard.session_busy(session), 'last_user_reply_at':reply_at}

    async def generate(self, user, name, reason, action_context="", action="message", motive=""):
        session, candidate_id, event_id, context = await self._context(user)
        if action != "message" or not candidate_id:
            self.metrics["generation_blocked"] += 1
            return ""
        native = self.private._planned_proactive_impulse(user) or {}
        evidence = native.get('context') or {}
        old_decision = evidence.get('phase3_decision',{}) if isinstance(evidence,dict) else {}
        evaluation_id = candidate_id
        if old_decision.get('decision') == 'defer':
            evaluation_id += ':reconsider:'+str(int(old_decision.get('next_consider_at',0)))
        admitted = await self.gate.reserve_generation(session, candidate_id=candidate_id,
                                                       evaluation_id=evaluation_id, **context)
        if admitted != "allowed":
            self.metrics["generation_blocked"] += 1
            return ""
        self.metrics["generation_attempts"] += 1
        started = time.monotonic()
        self._active_generations[session] = candidate_id
        state = "failed"
        token=self._semantic_scope.set(True)
        try:
            async with asyncio.timeout(self.generation_timeout):
                text = await self.original_generate(user, name, reason, action_context=action_context,
                                                    action=action, motive=motive)
            # Both lifecycle and permission can change during generation.
            fresh_session, fresh_id, _, fresh = await self._context(user)
            if fresh_id != candidate_id or self.gate.permit(fresh_session, candidate_id=candidate_id, **fresh) != "allowed":
                state = "expired"
                return ""
            decision = self._decisions.get(candidate_id)
            if decision and decision.get('reason') in {'empty','invalid_control'}:
                state = 'empty' if decision['reason']=='empty' else 'failed'
                return ''
            if decision and decision['decision'] in {'defer','skip'}:
                decision['expires_at'] = self.gate.candidate_expiry(native.get('expire_at'), old_decision.get('expires_at'))
                self._record_decision(user,native,decision)
                recorded = (native.get('context') or {}).get('phase3_decision', {})
                state = 'deferred' if recorded.get('decision')=='defer' else 'skipped'
                return ''
            state = "generated" if str(text or "").strip() else "empty"
            return str(text or "")
        except asyncio.CancelledError:
            state = "cancelled"
            raise
        except Exception:
            self.metrics["generation_errors"] += 1
            return ""
        finally:
            self.gate.finish_generation(evaluation_id, state)
            self.metrics["generation_seconds"] += time.monotonic() - started
            self._active_generations.pop(session, None)
            self._semantic_scope.reset(token)

    async def framework(self, *args, **kwargs):
        session = str(kwargs.get("umo") or "")
        task = str(kwargs.get("task") or "")
        if task.startswith("proactive"):
            if session not in self._active_generations or not self.gate.continuation_allowed(
                    session, busy=self.guard.session_busy(session)):
                return ""
            kwargs["max_steps"] = min(4, int(kwargs.get("max_steps", 4)))
            # Native prompt building clips action_context as an event excerpt.
            # Append the protocol after that build so the actual request sees
            # the complete contract (not merely its first 500 characters).
            prompt = str(kwargs.get('prompt') or '')
            if getattr(self.guard,'multibubble_enabled',False):
                from ..phase1_guard.bubbles import PROMPT
                prompt += '\n\n'+PROMPT
            kwargs['prompt'] = prompt+'\n\n'+DECISION_PROMPT
        text = await self.original_framework(*args, **kwargs)
        if task.startswith('proactive') and session in self._active_generations:
            decision = parse_proactive_decision(text)
            if len(self._decisions)>=32:
                self._decisions.pop(next(iter(self._decisions)))
            self._decisions[self._active_generations[session]] = {
                k:v for k,v in decision.items() if k!='text'}
            metric = 'decision_'+decision['decision'] if decision['reason'] in {'model_decision','plain_text'} else 'output_'+decision['reason']
            self.metrics[metric] = self.metrics.get(metric,0)+1
            return decision['text']
        return text

    async def send(self, umo, text, image_path="", *, extra_components=None, quote_message_id="", disable_segmenting=False):
        if image_path or extra_components or quote_message_id or not str(text).strip() or 'PHASE3_PROACTIVE:' in str(text):
            return self.outcome_type(False, False, note="phase3_text_only_boundary")
        try:
            _, user = resolve_native_target(self.private,umo)
        except PermissionError:
            return self.outcome_type(False,False,note="phase3_native_identity_missing_or_ambiguous")
        session, candidate_id, event_id, context = await self._context(user)
        if session != umo or not candidate_id:
            return self.outcome_type(False, False, note="phase3_candidate_scope_missing")
        if not await self.private._proactive_persona_delivery_allowed(umo):
            return self.outcome_type(False, False, note="persona_route_denied")
        native = self.private._planned_proactive_impulse(user) or {}
        evidence = native.get('context') or {}
        decision = evidence.get('phase3_decision', {}) if isinstance(evidence, dict) else {}
        expiry = self.gate.candidate_expiry(native.get('expire_at') if native else user.get('planned_proactive_expire_at'),
                                            decision.get('expires_at'))
        async def dispatch():
            return await self.guard.send_proactive(umo, text, delivery_id="proactive:" + candidate_id,
                is_current=lambda: self.gate.clock() < expiry and self.gate.continuation_allowed(umo, topic_id=context["topic_id"],
                                                                 busy=self.guard.session_busy(umo)))
        def observe():
            store = getattr(self.guard,'store',None)
            if store is None:
                return None  # lightweight transport fixture has no Guard DB
            turn_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'qq-companion-proactive:proactive:'+candidate_id))
            return store.bubble_result(turn_id,'proactive')
        result = await self.gate.deliver(umo, candidate_id=candidate_id, send=dispatch,
                                       observe_delivery=observe, **context)
        sent = result["state"] == "sent"
        delivery=result.get('delivery') or {}
        delivered_text=delivery.get('delivered_text',text if sent else '')
        any_sent=bool(delivered_text)
        self.metrics["delivered" if sent else "delivery_blocked"] += 1
        if any_sent and event_id and self.event_delivered:
            # Observation failure cannot retract or retry a successful send.
            try:
                native = self.private._planned_proactive_impulse(user) or {}
                evidence = native.get("context")
                revision = evidence.get("phase3_event_revision") if isinstance(evidence,dict) else None
                await _call(self.event_delivered,umo,event_id,revision,result.get("delivery", {}))
            except Exception:
                pass
        # Native history/memory may archive partial success, but only the text
        # acknowledged by Guard. Candidate allowance remains consumed once.
        return self.outcome_type(any_sent, sent, delivered_text=delivered_text, note="phase3_" + result["state"],
                                 primary_complete=sent, delivery_umo=umo if any_sent else "")
