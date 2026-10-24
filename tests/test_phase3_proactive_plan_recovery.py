"""T1 local native-plan maintenance; no model, network, QQ or private data."""
import asyncio
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import tempfile
import types
import unittest

from integrations.phase3_runtime.proactive import NativeProactiveBridge, ProactiveGate


SESSION = 'test:FriendMessage:10001'
NOW = datetime.fromisoformat('2026-10-24T12:00:00+08:00').timestamp()


class LocalNative:
    def __init__(self, clock):
        self.clock = clock
        self.user = {'user_id': '10001', 'umo': SESSION}
        self.data = {'users': {'10001': self.user}}
        self._data_lock = asyncio.Lock()
        self.max_daily_messages = 0
        self.enable_llm_proactive_message = False
        self.enabled = self.verified = True
        self.saved = []
        self.scheduled = self.retired = self.retry_clears = self.cycles = 0
        self.during_schedule = None
        self.events = []

    def persona_setting(self, key, default=None): return getattr(self, key, default)
    def _proactive_identity_binding_is_verified(self, key, user): return self.verified
    def _user_enabled_for_proactive(self, key, user): return self.enabled
    def _proactive_generation_disabled(self, user): return not self.enable_llm_proactive_message
    def _schedule_data_save(self, **kwargs): self.saved.append(kwargs)
    def _planned_proactive_delivery_key(self, user): return user.get('planned_candidate_id', '')
    def _planned_proactive_impulse(self, user):
        return next((item for item in user.get('proactive_impulses', [])
                     if item['id'] == user.get('planned_proactive_impulse_id')), None)
    def _is_proactive_plan_stale(self, user, *, now):
        return 0 < user.get('next_proactive_at', 0) < now - 10800
    def _mark_planned_candidate_status(self, user, status, note='', **kwargs):
        assert self._data_lock.locked()
        item = self._planned_proactive_impulse(user)
        if item:
            item.update(state='blocked', last_status=status, last_note=note)
        self.retired += 1
    def _clear_pending_proactive_send_retry(self, user):
        self.retry_clears += 1
        user.pop('pending_proactive_send_retry', None)
    def _clear_pending_proactive_plan(self, user):
        for key in list(user):
            if key.startswith('planned_') or key == 'next_proactive_at':
                user.pop(key)
    def _schedule_next_proactive(self, user, *, now):
        assert self._data_lock.locked()
        self.scheduled += 1
        self.events.append('schedule')
        if self.during_schedule:
            self.during_schedule()
        else:
            self.plan('fresh-' + str(self.scheduled), next_at=now + 600)
    async def _run_scheduler_cycle(self, *, immediate=False):
        self.events.append('native_cycle')
        self.cycles += 1
    async def _send_proactive_message_chain(self, *args, **kwargs):
        raise AssertionError('maintenance must never send')
    async def _generate_proactive_message_with_llm(self, *args, **kwargs):
        raise AssertionError('maintenance must never call model')
    def plan(self, identity='existing', *, next_at=None, expiry=None, state='queued', decision=None):
        now = self.clock()
        item = {'id': identity, 'state': state, 'expire_at': expiry or now + 3600,
                'context': {'phase3_decision': decision} if decision else {}}
        self.user.setdefault('proactive_impulses', []).append(item)
        self.user.update(planned_candidate_id=identity, planned_proactive_impulse_id=identity,
            planned_proactive_source='random', planned_proactive_topic='synthetic topic',
            next_proactive_at=next_at if next_at is not None else now + 600,
            planned_proactive_expire_at=item['expire_at'])
        return item


class PlanRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = NOW
        self.busy = False
        self.private = LocalNative(lambda: self.now)
        self.gate = ProactiveGate(Path(self.tmp.name) / 'gate.sqlite', allowed_session=SESSION,
            enabled=True, mode='continuous', max_per_day=5, max_generations_per_day=5,
            cooldown=10800, quiet_hours=(0, 9), clock=lambda: self.now)
        self.guard = types.SimpleNamespace(session_busy=lambda session: self.busy)
        self.bridge = NativeProactiveBridge(self.private, self.guard, self.gate, object)
        self.bridge.attach()
        self.bridge.activate_native_config()

    async def asyncTearDown(self):
        self.bridge.detach()
        self.gate.close()
        self.tmp.cleanup()

    def add_attempt(self, candidate, state='unknown', age=10801):
        self.gate.db.execute('INSERT INTO proactive_attempts(candidate,day,attempted,state) VALUES(?,?,?,?)',
            (candidate, '2026-10-24', self.now - age, state))
        self.gate.db.commit()

    async def test_empty_plan_is_scheduled_under_native_lock_before_native_cycle(self):
        await self.private._run_scheduler_cycle(immediate=True)
        self.assertEqual(self.private.events, ['schedule', 'native_cycle'])
        self.assertEqual(self.private.user['planned_candidate_id'], 'fresh-1')
        self.assertEqual(self.private.scheduled, 1)
        self.assertEqual(self.gate.db.execute('SELECT count(*) FROM proactive_generations').fetchone()[0], 0)
        self.assertEqual(self.gate.db.execute('SELECT count(*) FROM proactive_attempts').fetchone()[0], 0)
        await self.private._run_scheduler_cycle(immediate=True)
        self.assertEqual(self.private.scheduled, 1)
        self.assertEqual(self.private.cycles, 2)

    async def test_valid_future_plan_is_byte_for_byte_unchanged(self):
        self.private.plan()
        before = deepcopy(self.private.user)
        self.assertEqual(await self.bridge.maintain_native_plan(), 'waiting')
        self.assertEqual(self.private.user, before)
        self.assertEqual(self.private.scheduled, 0)

    async def test_native_none_context_defer_persists_and_future_plan_stays_intact(self):
        item = self.private.plan(expiry=self.now + 7200)
        item['context'] = None
        expiry = item['expire_at']
        self.bridge._record_decision(self.private.user, item,
            {'decision': 'defer', 'delay_minutes': 60, 'reason': 'model_decision'})
        self.assertEqual(item['context']['phase3_decision']['decision'], 'defer')
        self.assertEqual(item['context']['phase3_decision']['expires_at'], expiry)
        self.assertEqual(item['expire_at'], expiry)
        self.assertEqual(item['state'], 'deferred')
        self.assertEqual(self.private.user['next_proactive_at'], self.now + 3600)
        self.assertTrue(any('users' in saved['sections'] for saved in self.private.saved))
        before = deepcopy(self.private.user)
        self.assertEqual(await self.bridge.maintain_native_plan(), 'waiting')
        self.assertEqual(before, self.private.user)
        self.assertEqual(self.private.scheduled, 0)

    async def test_native_none_context_skip_records_terminal_decision(self):
        item = self.private.plan()
        item['context'] = None
        self.bridge._record_decision(self.private.user, item,
            {'decision': 'skip', 'delay_minutes': 0, 'reason': 'model_decision'})
        self.assertEqual(item['context']['phase3_decision']['decision'], 'skip')
        self.assertEqual(item['context']['phase3_decision']['next_consider_at'], 0)
        self.assertEqual(item['state'], 'blocked')
        self.assertEqual(await self.bridge.maintain_native_plan(), 'scheduled')
        self.assertEqual(item['context']['phase3_decision']['decision'], 'skip')

    async def test_defer_with_none_context_cannot_extend_original_deadline(self):
        item = self.private.plan(expiry=self.now + 1800)
        item['context'] = None
        expiry = item['expire_at']
        self.bridge._record_decision(self.private.user, item,
            {'decision': 'defer', 'delay_minutes': 60, 'reason': 'model_decision'})
        self.assertEqual(item['context']['phase3_decision']['decision'], 'skip')
        self.assertEqual(item['expire_at'], expiry)
        self.assertEqual(item['state'], 'blocked')

    async def test_unexpected_non_dict_context_is_never_overwritten(self):
        for invalid in ([], 'unexpected', 4, False):
            with self.subTest(invalid=invalid):
                item = self.private.plan()
                item['context'] = invalid
                before = deepcopy(self.private.user)
                self.bridge._record_decision(self.private.user, item,
                    {'decision': 'defer', 'delay_minutes': 60, 'reason': 'model_decision'})
                self.assertEqual(self.private.user, before)

    async def test_native_none_context_keeps_actual_delivery_metadata(self):
        for state, sent_count, part_count in (('sent', 2, 2), ('unknown', 1, 2), ('failed', 0, 2)):
            with self.subTest(state=state):
                item = self.private.plan(identity='receipt-' + state)
                item['context'] = None
                self.add_attempt('receipt-' + state, state=state)
                self.gate.db.execute('UPDATE proactive_attempts SET sent_count=?,part_count=? WHERE candidate=?',
                    (sent_count, part_count, 'receipt-' + state))
                self.gate.db.commit()
                async with self.private._data_lock:
                    self.bridge.mark_candidate_status(self.private.user, 'sent')
                self.assertEqual(item['context']['phase3_delivery'],
                    {'state': state, 'sent_count': sent_count, 'part_count': part_count})
                self.assertEqual(item['last_status'], 'sent' if state == 'sent' else 'dropped')
                row = self.gate.db.execute('SELECT state,sent_count,part_count FROM proactive_attempts WHERE candidate=?',
                    ('receipt-' + state,)).fetchone()
                self.assertEqual(row, (state, sent_count, part_count))

    async def test_valid_due_plan_remains_for_native_tick(self):
        self.private.plan(next_at=self.now - 1)
        self.assertEqual(await self.bridge.maintain_native_plan(), 'plan_due')
        self.assertEqual(self.private.scheduled, 0)

    async def test_expired_plan_is_terminal_before_native_reschedule(self):
        old = self.private.plan(expiry=self.now - 1)
        self.private.user['pending_proactive_send_retry'] = {'text': 'synthetic unsent text'}
        self.assertEqual(await self.bridge.maintain_native_plan(), 'scheduled')
        self.assertEqual(old['state'], 'blocked')
        self.assertEqual(old['last_status'], 'dropped')
        self.assertEqual(self.private.user['planned_candidate_id'], 'fresh-1')
        self.assertNotIn('pending_proactive_send_retry', self.private.user)

    async def test_stale_due_plan_is_not_selected_as_an_active_impulse_again(self):
        old = self.private.plan(next_at=self.now - 10801)
        await self.bridge.maintain_native_plan()
        self.assertEqual(old['state'], 'blocked')
        self.assertEqual(self.private.user['planned_candidate_id'], 'fresh-1')

    async def test_future_model_defer_preserves_candidate_deadline_and_next_time(self):
        self.private.plan(state='deferred', next_at=self.now + 2400,
            decision={'decision': 'defer', 'next_consider_at': self.now + 2400, 'expires_at': self.now + 3600})
        self.gate.db.execute('INSERT INTO proactive_generations VALUES(?,?,?,?)',
            ('existing', '2026-10-24', self.now - 10, 'deferred'))
        self.gate.db.commit()
        before = deepcopy(self.private.user)
        await self.bridge.maintain_native_plan()
        self.assertEqual(before, self.private.user)
        self.assertEqual(self.private.scheduled, 0)

    async def test_due_model_defer_allows_one_new_evaluation_identity(self):
        self.private.plan(state='deferred', next_at=self.now - 1,
            decision={'decision': 'defer', 'next_consider_at': self.now - 1, 'expires_at': self.now + 3600})
        self.gate.db.execute('INSERT INTO proactive_generations VALUES(?,?,?,?)',
            ('existing', '2026-10-24', self.now - 1000, 'deferred'))
        self.gate.db.commit()
        self.assertEqual(await self.bridge.maintain_native_plan(), 'plan_due')
        self.assertEqual(self.private.scheduled, 0)

    async def test_generation_failed_interrupted_skipped_never_reuses_same_plan(self):
        for state in ('failed', 'interrupted', 'skipped', 'empty', 'generated', 'cancelled'):
            with self.subTest(state=state):
                self.gate.db.execute('DELETE FROM proactive_generations')
                self.private._clear_pending_proactive_plan(self.private.user)
                item = self.private.plan(identity=state)
                self.gate.db.execute('INSERT INTO proactive_generations VALUES(?,?,?,?)',
                    (state, '2026-10-24', self.now - 1, state))
                self.gate.db.commit()
                await self.bridge.maintain_native_plan()
                self.assertEqual(item['state'], 'blocked')
                self.assertNotEqual(self.private.user['planned_candidate_id'], state)
                self.assertEqual(self.gate.db.execute('SELECT count(*) FROM proactive_generations').fetchone()[0], 1)

    async def test_any_attempt_terminal_is_retired_without_erasing_ledger(self):
        for state in ('sent', 'failed', 'unknown', 'cancelled', 'pending'):
            with self.subTest(state=state):
                self.gate.db.execute('DELETE FROM proactive_attempts')
                item = self.private.plan(identity=state)
                self.add_attempt(state, state)
                await self.bridge.maintain_native_plan()
                self.assertEqual(item['state'], 'blocked')
                self.assertNotEqual(self.private.user['planned_candidate_id'], state)
                self.assertEqual(self.gate.db.execute('SELECT state FROM proactive_attempts').fetchone()[0], state)

    async def test_native_terminal_state_cannot_generate_even_without_attempt(self):
        for state in ('sent', 'blocked', 'cancelled', 'dropped', 'failed', 'unknown'):
            with self.subTest(state=state):
                self.private.plan(identity=state, state=state)
                _, identity, _, context = await self.bridge._context(self.private.user)
                self.assertEqual(self.gate.permit(SESSION, candidate_id=identity, **context), 'event_not_due')

    async def test_reselected_old_attempt_is_retired_without_a_schedule_loop(self):
        self.add_attempt('old-unknown')
        self.private.during_schedule = lambda: self.private.plan('old-unknown')
        self.assertEqual(await self.bridge.maintain_native_plan(), 'native_candidate_retired')
        self.assertFalse(self.private.user.get('planned_candidate_id'))
        self.assertEqual(self.private.scheduled, 1)
        self.assertEqual(self.gate.db.execute('SELECT count(*) FROM proactive_attempts').fetchone()[0], 1)

    async def test_model_skip_is_not_resurrected(self):
        item = self.private.plan(decision={'decision': 'skip', 'expires_at': self.now + 3600})
        await self.bridge.maintain_native_plan()
        self.assertEqual(item['state'], 'blocked')
        self.assertEqual(self.private.user['planned_candidate_id'], 'fresh-1')

    async def test_blocked_topic_is_retired_but_control_retained(self):
        item = self.private.plan()
        _, _, _, context = await self.bridge._context(self.private.user)
        self.gate.block_topic(context['topic_id'])
        await self.bridge.maintain_native_plan()
        self.assertEqual(item['state'], 'blocked')
        self.assertTrue(self.gate._get('blocked:' + context['topic_id']))
        self.assertEqual(self.private.scheduled, 1)

    async def test_pause_busy_night_and_disabled_do_not_create_plans(self):
        self.gate.pause()
        self.assertEqual(await self.bridge.maintain_native_plan(), 'paused_or_busy')
        self.gate.pause(0)
        self.busy = True
        self.assertEqual(await self.bridge.maintain_native_plan(), 'paused_or_busy')
        self.busy = False
        self.now = NOW - 5 * 3600
        self.assertEqual(await self.bridge.maintain_native_plan(), 'quiet_hours')
        self.now = NOW
        self.gate.enabled = False
        self.assertEqual(await self.bridge.maintain_native_plan(), 'not_authorized')
        self.assertEqual(self.private.scheduled, 0)

    async def test_cooldown_and_daily_attempt_limit_apply_to_plan_repair(self):
        self.add_attempt('recent', age=1)
        self.assertEqual(await self.bridge.maintain_native_plan(), 'budget_or_cooldown')
        self.gate.db.execute('DELETE FROM proactive_attempts')
        for n in range(5): self.add_attempt(str(n))
        self.assertEqual(await self.bridge.maintain_native_plan(), 'budget_or_cooldown')
        self.assertEqual(self.private.scheduled, 0)

    async def test_five_model_evaluations_block_repair_and_survive_restart(self):
        for n, state in enumerate(('skipped', 'deferred', 'failed', 'empty', 'cancelled')):
            self.gate.db.execute('INSERT INTO proactive_generations VALUES(?,?,?,?)',
                (str(n), '2026-10-24', self.now - n, state))
        self.gate.db.commit()
        self.assertEqual(await self.bridge.maintain_native_plan(), 'generation_budget')
        self.assertEqual(self.private.scheduled, 0)
        self.now += 86400
        self.assertEqual(await self.bridge.maintain_native_plan(), 'scheduled')
        self.assertEqual(self.gate.db.execute('SELECT count(*) FROM proactive_generations').fetchone()[0], 5)

    async def test_missing_or_ambiguous_identity_never_modifies_users(self):
        before = deepcopy(self.private.user)
        self.private.verified = False
        self.assertEqual(await self.bridge.maintain_native_plan(), 'identity_unavailable')
        self.private.verified = True
        self.private.data['users']['second'] = deepcopy(self.private.user)
        self.assertEqual(await self.bridge.maintain_native_plan(), 'identity_unavailable')
        self.assertEqual(before, self.private.user)
        self.assertEqual(self.private.scheduled, 0)

    async def test_native_capability_and_missing_lock_fail_closed(self):
        self.private.enabled = False
        self.assertEqual(await self.bridge.maintain_native_plan(), 'native_not_authorized')
        self.private.enabled = True
        self.private._data_lock = None
        self.assertEqual(await self.bridge.maintain_native_plan(), 'native_api_unavailable')
        self.assertEqual(self.private.scheduled, 0)

    async def test_unarmed_and_active_generation_are_not_changed(self):
        self.bridge._native_settings_active = False
        self.assertEqual(await self.bridge.maintain_native_plan(), 'native_not_active')
        self.bridge._native_settings_active = True
        self.bridge._active_generations[SESSION] = 'running'
        self.assertEqual(await self.bridge.maintain_native_plan(), 'generation_in_progress')
        self.assertEqual(self.private.scheduled, 0)

    async def test_empty_native_candidate_result_is_observed_without_forcing_model(self):
        self.private.during_schedule = lambda: None
        self.assertEqual(await self.bridge.maintain_native_plan(), 'no_candidate')
        self.assertFalse(self.private.user.get('planned_candidate_id'))
        self.assertEqual(self.private.scheduled, 1)

    async def test_observation_contains_only_fixed_status_counts_and_times(self):
        await self.bridge.maintain_native_plan()
        item = self.private.data['proactive_runtime']['phase3_plan_maintenance']
        self.assertEqual(set(item), {'reason', 'checked_at', 'next_consider_at', 'expires_at',
                                    'repair_count', 'retired_count', 'error_count'})
        self.assertEqual(item['reason'], 'scheduled')
        self.assertEqual(item['repair_count'], 1)
        self.assertNotIn('synthetic', repr(item))
        self.assertNotIn('10001', repr(item))

    async def test_error_is_local_and_native_cycle_still_runs_with_guard(self):
        def fail(): raise ValueError('synthetic-private-body-do-not-log')
        self.private.during_schedule = fail
        await self.private._run_scheduler_cycle()
        self.assertEqual(self.private.cycles, 1)
        self.assertEqual(self.bridge.metrics['plan_maintenance_reason'], 'maintenance_error')
        self.assertNotIn('synthetic-private-body', repr(self.bridge.metrics))
        observed = self.private.data['proactive_runtime']['phase3_plan_maintenance']
        self.assertEqual(observed['reason'], 'maintenance_error')
        self.assertEqual(observed['error_count'], 1)
        self.assertNotIn('synthetic-private-body', repr(observed))

    async def test_gate_rechecked_after_waiting_for_native_lock(self):
        await self.private._data_lock.acquire()
        task = asyncio.create_task(self.bridge.maintain_native_plan())
        await asyncio.sleep(0)
        self.gate.pause()
        self.private._data_lock.release()
        self.assertEqual(await task, 'paused_or_busy')
        self.assertEqual(self.private.scheduled, 0)

    async def test_restart_keeps_unknown_consumed_and_future_plan_unchanged(self):
        self.private.plan('old')
        self.add_attempt('old')
        await self.bridge.maintain_native_plan()
        before = deepcopy(self.private.user)
        self.bridge.detach()
        self.bridge.attach()
        self.bridge.activate_native_config()
        self.assertEqual(await self.bridge.maintain_native_plan(), 'waiting')
        self.assertEqual(before, self.private.user)
        self.assertEqual(self.private.scheduled, 1)


if __name__ == '__main__': unittest.main()
