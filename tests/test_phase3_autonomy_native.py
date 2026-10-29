"""Binding contracts plus pinned native unanswered calculations; no model/QQ."""
from copy import deepcopy
import importlib
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

from integrations.phase3_runtime.autonomy_native import NativeAutonomyBindings


SESSION = "test:FriendMessage:10001"


class BindingHost:
    def __init__(self):
        self.user = {"umo": SESSION, "user_id": "10001", "ignored_streak": 5,
                     "unanswered_proactive_count": 5, "awaiting_reply_since": 100,
                     "planned_proactive_source": "random", "sent_today": 12,
                     "last_sent": 999, "planned_proactive_impulse_id": "same-native-id"}
        self.data = {"users": {"10001": self.user}}
        self.calls = []
        self.raise_preflight = False

    def _proactive_identity_binding_is_verified(self, key, user):
        return user.get("user_id") == "10001"

    def _friend_unanswered_downgrade_level(self, user, **kwargs): return user["ignored_streak"]
    def _friend_unanswered_silence_reason(self, user, **kwargs): return "unanswered"
    def _proactive_unanswered_pause_reason(self, user, **kwargs): return "unanswered_pause"
    def _unanswered_slowdown_count(self, user): return user["ignored_streak"]
    def _proactive_response_readiness(self, user, **kwargs):
        return {"score": 10-user["ignored_streak"], "awaiting": user["awaiting_reply_since"]}
    def _effective_min_interval_seconds(self, user, **kwargs): return 5400
    def _effective_user_idle_minutes(self, user): return 45
    def _planned_proactive_delivery_key(self, user): return user['planned_proactive_impulse_id']
    def _route_recent_chat_guard_reason(self, user, **kwargs):
        if self.raise_preflight: raise RuntimeError('synthetic_route_failure')
        return 'native_recent_chat' if user.get('idle_seconds',99999)<self._effective_user_idle_minutes(user)*60 else ''
    def _proactive_daily_limit_is_unlimited(self, limit): return False
    def _planned_event_exceeds_daypart_cap(self, user, reason, scheduled_at): return True
    def _should_use_name_only_opener(self, user, **kwargs): return True
    def _planned_proactive_recently_repeated(self, user): return True
    def _recent_proactive_topic_repeated(self, user, signature, **kwargs): return True
    def _proactive_candidate_repeated(self, user, candidate): return True
    def _proactive_source_feedback_modifier(self, user, source): return -.08
    def _friend_proactive_scheduled_too_early(self, user, scheduled_at): return True
    def _is_vague_seek_user_motive(self, *args): return True
    def _queue_random_proactive_impulse(self, user, **kwargs):
        return {"vague": self._is_vague_seek_user_motive("check_in", "message", "want to chat"),
                "native_interval_base": self._effective_min_interval_seconds(user)}
    def _schedule_next_proactive(self, user, **kwargs):
        return {"too_early": self._friend_proactive_scheduled_too_early(user, 1000),
                "random": self._queue_random_proactive_impulse(user, **kwargs)}
    def _materialize_best_proactive_impulse(self, user, **kwargs):
        return self._friend_proactive_scheduled_too_early(user, 1000)

    def _should_send(self, user):
        self.calls.append(user.get("umo"))
        if self.raise_preflight:
            raise RuntimeError("synthetic_native_failure")
        for key in ("paused", "busy", "explicit_rest", "closed_topic", "not_due", "expired", "too_recent_inbound"):
            if user.get(key):
                return False, key
        if user["sent_today"] >= 12 and not self._proactive_daily_limit_is_unlimited(12):
            return False, "native_daily"
        if self._effective_min_interval_seconds(user) > 0:
            return False, "native_cooldown"
        if self._planned_event_exceeds_daypart_cap(user, "random", 1000):
            return False, "native_daypart"
        if self._planned_proactive_recently_repeated(user):
            return False, "native_label_repeat"
        if user.get('idle_seconds', 99999) < self._effective_user_idle_minutes(user)*60:
            return False, "native_idle"
        return True, "ok"


class NativeBindingTests(unittest.TestCase):
    def setUp(self):
        self.private = BindingHost()
        self.gate = types.SimpleNamespace(enabled=True, session=SESSION)
        self.bridge = types.SimpleNamespace(private=self.private, gate=self.gate, autonomy_enabled=True)
        self.admit = "allowed"
        self.binding = NativeAutonomyBindings(self.bridge, lambda user: self.admit,
            lambda user: user.get("planned_proactive_source") == "random")
        self.binding.attach()

    def tearDown(self): self.binding.detach()

    def test_cooldown_exploration_preserves_all_native_send_state(self):
        before = deepcopy(self.private.user)
        self.assertEqual(self.private._should_send(self.private.user), (True, "ok"))
        self.assertEqual(self.private.user, before)
        self.assertEqual(self.binding.last_preflight["stage"], "native_exploration_preflight")
        self.assertEqual(self.private._effective_min_interval_seconds(self.private.user), 5400)
        self.assertFalse(self.private._proactive_daily_limit_is_unlimited(12))
        self.assertTrue(self.binding.send_daypart_blocked(self.private.user, "random", 1000))

    def test_explicit_controls_due_expiry_and_idle_are_not_bypassed(self):
        for key in ("paused", "busy", "explicit_rest", "closed_topic", "not_due", "expired", "too_recent_inbound"):
            with self.subTest(key=key):
                self.private.user[key] = True
                self.assertEqual(self.private._should_send(self.private.user), (False, key))
                self.private.user.pop(key)

    def test_opportunity_budget_quiet_or_interruption_denies_before_native(self):
        for reason in ("quiet_hours", "actual_model_budget", "tool_budget", "user_interrupted"):
            self.admit = reason
            self.assertEqual(self.private._should_send(self.private.user), (False, reason))
        self.assertEqual(self.private.calls, [])

    def test_other_target_uses_original_preflight_and_slowdown(self):
        other = dict(self.private.user, umo="test:FriendMessage:10002")
        self.assertEqual(self.private._should_send(other), (False, "native_daily"))
        self.assertEqual(self.private._unanswered_slowdown_count(other), 5)
        self.assertEqual(self.private._friend_unanswered_silence_reason(other), "unanswered")

    def test_unanswered_policies_removed_but_counts_and_waiting_state_preserved(self):
        before = deepcopy(self.private.user)
        self.assertEqual(self.private._friend_unanswered_downgrade_level(self.private.user), 0)
        self.assertEqual(self.private._friend_unanswered_silence_reason(self.private.user), "")
        self.assertEqual(self.private._proactive_unanswered_pause_reason(self.private.user), "")
        self.assertEqual(self.private._unanswered_slowdown_count(self.private.user), 0)
        self.assertEqual(self.private._proactive_response_readiness(self.private.user), {"score": 10, "awaiting": 0})
        self.assertEqual(self.private.user, before)

    def test_specific_event_duplicate_policy_kept_while_open_label_is_not_fact(self):
        self.assertFalse(self.private._recent_proactive_topic_repeated(self.private.user, "activity_share"))
        self.assertFalse(self.private._proactive_candidate_repeated(self.private.user, {"source": "random"}))
        self.assertFalse(self.private._proactive_candidate_repeated(self.private.user,
            {"source": "random", "origin_event_id": "native-random-derived-id"}))
        self.assertTrue(self.private._proactive_candidate_repeated(self.private.user,
            {"source": "random", "event_id": "real-event"}))
        self.private.user["planned_proactive_source"] = "event"
        self.assertTrue(self.private._planned_proactive_recently_repeated(self.private.user))
        self.assertTrue(self.private._recent_proactive_topic_repeated(self.private.user, "same-event"))

    def test_silence_only_source_penalty_removed_but_negative_feedback_kept(self):
        self.private.user["proactive_source_feedback"] = {"random": {"sent": 10, "replied": 0}}
        self.assertEqual(self.private._proactive_source_feedback_modifier(self.private.user, "random"), 0)
        self.private.user["proactive_source_feedback"]["random"]["negative"] = 1
        self.assertEqual(self.private._proactive_source_feedback_modifier(self.private.user, "random"), -.08)

    def test_name_only_template_disabled_without_requiring_reply(self):
        self.assertFalse(self.private._should_use_name_only_opener(self.private.user))
        self.bridge.autonomy_enabled = False
        self.assertTrue(self.private._should_use_name_only_opener(self.private.user))

    def test_native_exception_does_not_leak_exploration_scope(self):
        self.private.raise_preflight = True
        with self.assertRaises(RuntimeError):
            self.private._should_send(self.private.user)
        self.assertEqual(self.private._effective_min_interval_seconds(self.private.user), 5400)
        self.assertFalse(self.private._proactive_daily_limit_is_unlimited(12))

    def test_short_idle_applies_only_to_current_confirmed_supplement_preflight(self):
        self.private.user['idle_seconds']=600
        self.assertEqual(self.private._should_send(self.private.user),(False,'native_idle'))
        result={'applicable':True,'allowed':True,'reason':'allowed','idle_seconds':300}
        with patch('integrations.phase3_runtime.autonomy_supplement.check_supplement',return_value=result):
            self.assertEqual(self.private._should_send(self.private.user),(True,'ok'))
            self.assertEqual(self.private._effective_user_idle_minutes(self.private.user),45)
            self.private.user['idle_seconds']=120
            self.assertEqual(self.private._should_send(self.private.user),(False,'native_idle'))
            self.private.raise_preflight=True
            with self.assertRaises(RuntimeError):self.private._should_send(self.private.user)
            self.assertEqual(self.private._effective_user_idle_minutes(self.private.user),45)

    def test_invalid_parent_denies_before_original_native_preflight(self):
        with patch('integrations.phase3_runtime.autonomy_supplement.check_supplement',
                return_value={'applicable':True,'allowed':False,'reason':'supplement_no_longer_current'}):
            self.assertEqual(self.private._should_send(self.private.user),(False,'supplement_no_longer_current'))
        self.assertEqual(self.private.calls,[])

    def test_native_route_rechecks_short_idle_before_generation_and_delivery(self):
        self.private.user['idle_seconds']=600
        self.assertEqual(self.private._route_recent_chat_guard_reason(self.private.user),'native_recent_chat')
        result={'applicable':True,'allowed':True,'reason':'allowed','idle_seconds':300}
        with patch('integrations.phase3_runtime.autonomy_supplement.check_supplement',return_value=result) as check:
            self.assertEqual(self.private._route_recent_chat_guard_reason(self.private.user),'')
            scope={'candidate_id':'same-native-id','interest_attempt':'same-native-id:interest'}
            self.bridge.pending={'same-native-id':scope}
            self.private.user['idle_seconds']=1
            result['idle_seconds']=0
            self.assertEqual(self.private._route_recent_chat_guard_reason(self.private.user),'')
            self.assertIs(check.call_args.kwargs['scope'],scope)
            self.private.raise_preflight=True
            with self.assertRaises(RuntimeError):self.private._route_recent_chat_guard_reason(self.private.user)
        self.assertEqual(self.private._effective_user_idle_minutes(self.private.user),45)

    def test_native_route_rechecks_parent_epoch_not_just_previous_preflight(self):
        with patch('integrations.phase3_runtime.autonomy_supplement.check_supplement',
                return_value={'applicable':True,'allowed':False,'reason':'supplement_no_longer_current'}):
            self.assertEqual(self.private._route_recent_chat_guard_reason(self.private.user),'supplement_no_longer_current')

    def test_detach_restores_original_bound_methods(self):
        self.binding.detach()
        self.assertEqual(self.private._unanswered_slowdown_count(self.private.user), 5)
        self.assertEqual(self.private._should_send(self.private.user), (False, "native_daily"))

    def test_detach_does_not_overwrite_another_later_adapter(self):
        replacement = lambda user: 99
        self.private._unanswered_slowdown_count = replacement
        self.binding.detach()
        self.assertIs(self.private._unanswered_slowdown_count, replacement)

    def test_identity_conflict_and_disabled_gate_do_not_get_bypass(self):
        self.private.data["users"]["other-key"] = deepcopy(self.private.user)
        self.assertEqual(self.private._should_send(self.private.user), (False, "native_daily"))
        self.private.data["users"].pop("other-key")
        self.gate.enabled = False
        self.assertEqual(self.private._should_send(self.private.user), (False, "native_daily"))

    def test_native_schedule_can_offer_during_cooldown_without_changing_sampling_base(self):
        before = deepcopy(self.private.user)
        result = self.private._schedule_next_proactive(self.private.user, now=1000, delay_hours=(.65, 2.25))
        self.assertEqual(result, {"too_early": False, "random": {"vague": False, "native_interval_base": 5400}})
        self.assertFalse(self.private._materialize_best_proactive_impulse(self.private.user))
        self.assertTrue(self.private._friend_proactive_scheduled_too_early(self.private.user, 1000))
        self.assertTrue(self.private._is_vague_seek_user_motive("check_in", "message", "want to chat"))
        self.assertEqual(self.private.user, before)

    def test_other_target_schedule_keeps_native_too_early_and_vague_checks(self):
        other = dict(self.private.user, umo="test:FriendMessage:10002")
        result = self.private._schedule_next_proactive(other, now=1000)
        self.assertTrue(result["too_early"])
        self.assertTrue(result["random"]["vague"])


ROOT = Path(__file__).resolve().parents[1]
try:
    package = types.ModuleType("phase3_native_autonomy_binding_fixture")
    package.__path__ = [str(ROOT / ".runtime/upstream/private_companion")]
    sys.modules[package.__name__] = package
    scheduler = importlib.import_module(package.__name__ + ".proactive")
    engine = importlib.import_module(package.__name__ + ".proactive_engine")
    NATIVE = True
except ImportError:
    NATIVE = False


@unittest.skipUnless(NATIVE, "fixed native imports unavailable; not a PASS")
class PinnedNativeCalculationTests(unittest.TestCase):
    def setUp(self):
        class NativeHost(BindingHost):
            _friend_unanswered_downgrade_level = scheduler.ProactiveMixin._friend_unanswered_downgrade_level
            _friend_unanswered_silence_reason = scheduler.ProactiveMixin._friend_unanswered_silence_reason
            _unanswered_slowdown_count = scheduler.ProactiveMixin._unanswered_slowdown_count
            _unanswered_interval_multiplier = scheduler.ProactiveMixin._unanswered_interval_multiplier
            _proactive_response_readiness = scheduler.ProactiveMixin._proactive_response_readiness
            _random_proactive_impulse_context = scheduler.ProactiveMixin._random_proactive_impulse_context
            _proactive_source_feedback_modifier = engine.ProactiveEngineMixin._proactive_source_feedback_modifier
            def _private_user_role(self, user): return "friend"
            def _effective_proactive_int(self, name, value, **kwargs): return value
            def _effective_proactive_float(self, name, value, **kwargs): return value
            def _proactive_quota_policy(self, user): return {"unanswered_interval_weight": .42}
            def persona_setting(self, key, default=None): return default
        self.private = NativeHost()
        self.bridge = types.SimpleNamespace(private=self.private, autonomy_enabled=True,
            gate=types.SimpleNamespace(enabled=True, session=SESSION))
        self.binding = NativeAutonomyBindings(self.bridge, lambda user: "allowed", lambda user: True)

    def tearDown(self): self.binding.detach()

    def test_real_native_unanswered_multiplier_drops_to_one_and_restores(self):
        before = self.private._unanswered_interval_multiplier(self.private.user)
        self.assertGreater(before, 1)
        self.binding.attach()
        self.assertEqual(self.private._unanswered_interval_multiplier(self.private.user), 1)
        self.assertEqual(self.private.user["unanswered_proactive_count"], 5)
        self.binding.detach()
        self.assertEqual(self.private._unanswered_interval_multiplier(self.private.user), before)

    def test_native_readiness_and_random_context_no_longer_penalize_unanswered(self):
        self.private.data["daily_state"] = {"energy": 70}
        before = deepcopy(self.private.user)
        self.binding.attach()
        actual = self.private._proactive_response_readiness(self.private.user, now=100000)
        self.assertEqual(actual["score"], .54)
        random_context = self.private._random_proactive_impulse_context(self.private.user, now=100000)
        self.assertFalse(random_context["suggest_soft_reason"])
        self.assertEqual(self.private.user, before)

    def test_native_latest_count_remains_available_to_decision_context(self):
        self.assertEqual(self.private._friend_unanswered_downgrade_level(self.private.user, now=100000), 3)
        self.binding.attach()
        self.assertEqual(self.private._friend_unanswered_downgrade_level(self.private.user, now=100000), 0)
        self.assertEqual(self.private.user["ignored_streak"], 5)
        self.assertEqual(self.private.user["awaiting_reply_since"], 100)


if __name__ == "__main__":
    unittest.main()
