"""Reversible adapters around the pinned native proactive scheduler.

Only the already verified private target uses these semantics.  Native plan
identities, lifecycle, explicit controls, idle checks and transport remain
native/Guard-owned.  An exploration admission is never a send authorization.
"""
from contextvars import ContextVar
from copy import copy
from functools import wraps


class NativeAutonomyBindings:
    # These functions calculate scores/soft guidance.  They receive a view
    # without an unanswered penalty; no persisted count is cleared or faked.
    NEUTRAL_CALCULATORS = (
        "_proactive_response_readiness", "_experimental_proactive_incentive",
        "_experimental_proactive_arousal", "_random_proactive_impulse_context",
        "_score_proactive_impulse", "_proactive_candidate_semantics",
        "_maslow_motivation_profile", "_passes_proactive_moment",
    )
    ZERO = ("_friend_unanswered_downgrade_level", "_unanswered_slowdown_count")
    EMPTY = ("_friend_unanswered_silence_reason", "_proactive_unanswered_pause_reason")

    def __init__(self, bridge, opportunity_admission, is_open_opportunity):
        self.bridge = bridge
        self.private = bridge.private
        self.admission = opportunity_admission
        self.is_open = is_open_opportunity
        self.originals = {}
        self.installed = {}
        self._exploration = ContextVar("phase3_native_exploration_preflight", default=None)
        self._scheduling = ContextVar("phase3_native_opportunity_scheduling", default=None)
        self._random_creation = ContextVar("phase3_native_open_opportunity_creation", default=None)
        self._supplement_idle = ContextVar("phase3_native_confirmed_supplement_idle", default=None)
        self.last_preflight = None

    def authorized(self, user):
        if (not getattr(self.bridge, "autonomy_enabled", False)
                or not self.bridge.gate.enabled or not isinstance(user, dict)
                or user.get("umo") != self.bridge.gate.session):
            return False
        # The same exact identity check used by the existing delivery bridge.
        from .proactive import resolve_native_target
        try:
            resolve_native_target(self.private, self.bridge.gate.session)
        except PermissionError:
            return False
        return True

    @staticmethod
    def neutral_view(user):
        result = copy(user)
        result.update(ignored_streak=0, unanswered_proactive_count=0, awaiting_reply_since=0)
        return result

    @staticmethod
    def open_candidate(candidate):
        return (isinstance(candidate, dict)
                and str(candidate.get("source") or "") in {"random", "impulse", "proactive", "want_to_chat"}
                # Native assigns origin_event_id even to random impulses; it
                # is an identity, not evidence of a real external/user event.
                and not any(candidate.get(key) for key in
                            ("event_id", "source_event_id", "trigger_message_id"))
                and not (isinstance(candidate.get("context"), dict)
                         and any(candidate["context"].get(key) for key in
                                 ("event_id", "memo_id", "goal_id"))))

    def _bind(self, name, factory):
        original = getattr(self.private, name, None)
        if not callable(original):
            return
        wrapped = factory(original)
        self.originals[name], self.installed[name] = original, wrapped
        setattr(self.private, name, wrapped)

    def _constant(self, value):
        def factory(original):
            @wraps(original)
            def wrapped(user, *args, **kwargs):
                return value if self.authorized(user) else original(user, *args, **kwargs)
            return wrapped
        return factory

    def _neutral(self, original):
        @wraps(original)
        def wrapped(user, *args, **kwargs):
            return original(self.neutral_view(user) if self.authorized(user) else user, *args, **kwargs)
        return wrapped

    def _planned_repeat(self, original):
        @wraps(original)
        def wrapped(user, *args, **kwargs):
            if self.authorized(user) and self.is_open(user):
                return False
            return original(user, *args, **kwargs)
        return wrapped

    def _candidate_repeat(self, original):
        @wraps(original)
        def wrapped(user, candidate, *args, **kwargs):
            if self.authorized(user) and self.open_candidate(candidate):
                return False
            return original(user, candidate, *args, **kwargs)
        return wrapped

    def _interval(self, original):
        @wraps(original)
        def wrapped(user, *args, **kwargs):
            if self._exploration.get() == self.bridge.gate.session and self.authorized(user):
                return 0
            return original(user, *args, **kwargs)
        return wrapped

    def _idle(self, original):
        @wraps(original)
        def wrapped(user, *args, **kwargs):
            value = original(user, *args, **kwargs)
            seconds = self._supplement_idle.get()
            if (seconds is not None and self._exploration.get() == self.bridge.gate.session
                    and self.authorized(user)):
                return min(value, seconds / 60)
            return value
        return wrapped

    def _daypart(self, original):
        @wraps(original)
        def wrapped(user, *args, **kwargs):
            if self._exploration.get() == self.bridge.gate.session and self.authorized(user):
                return False
            return original(user, *args, **kwargs)
        return wrapped

    def _daily(self, original):
        @wraps(original)
        def wrapped(*args, **kwargs):
            # This helper has no user argument.  Its bypass exists only inside
            # synchronous, exact-target native _should_send evaluation.
            if (self._exploration.get() == self.bridge.gate.session
                    and self.bridge.gate.enabled and getattr(self.bridge, "autonomy_enabled", False)):
                return True
            return original(*args, **kwargs)
        return wrapped

    def _feedback(self, original):
        @wraps(original)
        def wrapped(user, source, *args, **kwargs):
            result = original(user, source, *args, **kwargs)
            if not self.authorized(user):
                return result
            feedback = user.get("proactive_source_feedback")
            bucket = feedback.get(str(source or "unknown")) if isinstance(feedback, dict) else None
            if isinstance(bucket, dict):
                # A low reply ratio alone does not mean rejection.  Actual
                # negative feedback continues to influence the native score.
                negative = bucket.get("weighted_negative", bucket.get("negative", 0))
                try:
                    if float(negative or 0) > 0:
                        return result
                except (TypeError, ValueError):
                    return result
                return max(0.0, result)
            return result
        return wrapped

    def _scheduled_too_early(self, original):
        @wraps(original)
        def wrapped(user, *args, **kwargs):
            if self._scheduling.get() == self.bridge.gate.session and self.authorized(user):
                return False
            return original(user, *args, **kwargs)
        return wrapped

    def _schedule(self, original):
        @wraps(original)
        def wrapped(user, *args, **kwargs):
            if not self.authorized(user):
                return original(user, *args, **kwargs)
            token = self._scheduling.set(self.bridge.gate.session)
            try:
                return original(user, *args, **kwargs)
            finally:
                self._scheduling.reset(token)
        return wrapped

    def _queue_random(self, original):
        @wraps(original)
        def wrapped(user, *args, **kwargs):
            if not self.authorized(user):
                return original(user, *args, **kwargs)
            token = self._random_creation.set(self.bridge.gate.session)
            try:
                return original(user, *args, **kwargs)
            finally:
                self._random_creation.reset(token)
        return wrapped

    def _vague(self, original):
        @wraps(original)
        def wrapped(*args, **kwargs):
            if (self._random_creation.get() == self.bridge.gate.session
                    and self.bridge.gate.enabled and getattr(self.bridge, "autonomy_enabled", False)):
                return False
            return original(*args, **kwargs)
        return wrapped

    def _should_send(self, original):
        @wraps(original)
        def wrapped(user, *args, **kwargs):
            if not self.authorized(user):
                return original(user, *args, **kwargs)
            reason = self.admission(user)
            if reason != "allowed":
                self.last_preflight = {"allowed": False, "reason": str(reason), "stage": "opportunity"}
                return False, str(reason)
            from .autonomy_supplement import check_supplement
            supplement = check_supplement(self.bridge, user)
            if supplement['applicable'] and not supplement['allowed']:
                self.last_preflight = {"allowed": False, "reason": supplement['reason'],
                                       "stage": "confirmed_supplement_preflight"}
                return False, supplement['reason']
            token = self._exploration.set(self.bridge.gate.session)
            idle_token = self._supplement_idle.set(supplement.get('idle_seconds'))
            try:
                # Run the original full preflight: identity/capability, due
                # time, candidate expiry, explicit rest/busy, idle, closed
                # topics, action and relationship constraints remain intact.
                result = original(user, *args, **kwargs)
                self.last_preflight = {"allowed": bool(result[0]), "reason": str(result[1]),
                                       "stage": "native_exploration_preflight"}
                return result
            finally:
                self._supplement_idle.reset(idle_token)
                self._exploration.reset(token)
        return wrapped

    def _recent_chat(self, original):
        @wraps(original)
        def wrapped(user, *args, **kwargs):
            if not self.authorized(user):
                return original(user, *args, **kwargs)
            # The pinned tick repeats this route guard both before generation
            # and just before delivery, outside _should_send. Revalidate the
            # actual parent each time; do not relax normal conversation idle.
            candidate = self.private._planned_proactive_delivery_key(user)
            scope = getattr(self.bridge, 'scopes', {}).get(self.bridge.gate.session)
            if scope is None or scope.get('candidate_id') != candidate:
                scope = getattr(self.bridge, 'pending', {}).get(candidate)
            from .autonomy_supplement import check_supplement
            supplement = check_supplement(self.bridge, user, scope=scope)
            if not supplement['applicable']:
                return original(user, *args, **kwargs)
            if not supplement['allowed']:
                return supplement['reason']
            token = self._exploration.set(self.bridge.gate.session)
            idle_token = self._supplement_idle.set(supplement['idle_seconds'])
            try:
                return original(user, *args, **kwargs)
            finally:
                self._supplement_idle.reset(idle_token)
                self._exploration.reset(token)
        return wrapped

    def attach(self):
        if self.installed:
            return
        for name in self.ZERO:
            self._bind(name, self._constant(0))
        for name in self.EMPTY:
            self._bind(name, self._constant(""))
        for name in self.NEUTRAL_CALCULATORS:
            self._bind(name, self._neutral)
        self._bind("_should_use_name_only_opener", self._constant(False))
        self._bind("_planned_proactive_recently_repeated", self._planned_repeat)
        self._bind("_recent_proactive_topic_repeated", self._planned_repeat)
        self._bind("_proactive_candidate_repeated", self._candidate_repeat)
        self._bind("_effective_min_interval_seconds", self._interval)
        self._bind("_effective_user_idle_minutes", self._idle)
        self._bind("_planned_event_exceeds_daypart_cap", self._daypart)
        self._bind("_proactive_daily_limit_is_unlimited", self._daily)
        self._bind("_proactive_source_feedback_modifier", self._feedback)
        self._bind("_friend_proactive_scheduled_too_early", self._scheduled_too_early)
        self._bind("_schedule_next_proactive", self._schedule)
        self._bind("_materialize_best_proactive_impulse", self._schedule)
        self._bind("_queue_random_proactive_impulse", self._queue_random)
        self._bind("_is_vague_seek_user_motive", self._vague)
        self._bind("_should_send", self._should_send)
        self._bind("_route_recent_chat_guard_reason", self._recent_chat)

    def send_daypart_blocked(self, user, reason, scheduled_at):
        """Final delivery uses the unmodified native daypart constraint.

        The coordinator must call this separately before Guard delivery;
        exploration preflight intentionally cannot enforce a send-only cap.
        """
        original = self.originals.get("_planned_event_exceeds_daypart_cap")
        return bool(original(user, reason, scheduled_at)) if original else False

    def detach(self):
        for name, wrapped in reversed(tuple(self.installed.items())):
            if getattr(self.private, name, None) is wrapped:
                setattr(self.private, name, self.originals[name])
        self.installed.clear()
        self.originals.clear()
