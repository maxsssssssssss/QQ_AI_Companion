"""Continuous authorization boundaries with an isolated ledger and fake clock."""
from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from integrations.phase3_runtime.proactive import ProactiveGate, parse_proactive_controls


def ts(value):
    return datetime.fromisoformat(value + '+08:00').timestamp()


class ContinuousGateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'continuous.sqlite'
        self.now = ts('2026-10-24T09:00:00')
        self.gate = self.open_gate()
        self.sent = []

    def open_gate(self, **overrides):
        options = dict(allowed_session='test:FriendMessage:10001', enabled=True,
            mode='continuous', window_start=0, window_end=0, max_per_day=5,
            max_generations_per_day=5, cooldown=10800, quiet_hours=(0, 9), clock=lambda:self.now)
        return ProactiveGate(self.path, **{**options, **overrides})

    async def asyncTearDown(self):
        self.gate.close()
        self.temp.cleanup()

    async def send(self):
        self.sent.append(self.now)
        return {'state':'sent'}

    def permitted(self, candidate='next'):
        return self.gate.permit(self.gate.session, candidate_id=candidate)

    def test_window_continues_after_72_hours_and_year_boundary(self):
        for moment in ('2026-10-24T09:00:00', '2026-10-28T23:59:59', '2027-01-01T12:00:00'):
            self.now = ts(moment)
            self.assertTrue(self.gate.window_open())
            self.assertFalse(self.gate.expired())
            self.assertEqual(self.permitted(), 'allowed')

    def test_full_2359_minute_and_shanghai_midnight(self):
        for moment, expected in (
                ('2026-10-24T08:59:59', 'quiet_hours'),
                ('2026-10-24T09:00:00', 'allowed'),
                ('2026-10-24T23:00:00', 'allowed'),
                ('2026-10-24T23:59:59.999999', 'allowed'),
                ('2026-10-25T00:00:00', 'quiet_hours'),
                ('2026-10-25T08:59:59', 'quiet_hours'),
                ('2026-10-25T09:00:00', 'allowed')):
            self.now = ts(moment)
            self.assertEqual(self.permitted(), expected)
            self.assertEqual(self.gate.continuation_allowed(self.gate.session), expected == 'allowed')

    async def test_three_hour_cooldown_has_exact_boundary(self):
        await self.gate.deliver(self.gate.session, candidate_id='first', send=self.send)
        self.now += 10799.999
        self.assertEqual(self.permitted(), 'budget_or_cooldown')
        self.now += .001
        self.assertEqual(self.permitted(), 'allowed')

    async def test_five_send_attempts_include_failed_unknown_cancelled_and_restart(self):
        for number, state in enumerate(('sent', 'failed', 'unknown', 'cancelled', 'sent')):
            self.now = ts('2026-10-24T09:00:00') + number * 10800
            async def outcome(value=state): return {'state':value}
            result = await self.gate.deliver(self.gate.session, candidate_id=str(number), send=outcome)
            self.assertEqual(result['state'], state)
        self.gate.close()
        self.gate = self.open_gate()
        self.now = ts('2026-10-24T23:59:59')
        self.assertEqual(self.permitted(), 'budget_or_cooldown')
        self.now = ts('2026-10-25T09:00:00')
        self.assertEqual(self.permitted(), 'allowed')
        self.assertEqual(self.permitted('2'), 'already_attempted')
        self.assertEqual(self.gate.db.execute('SELECT count(*) FROM proactive_attempts').fetchone()[0], 5)

    async def test_cooldown_carries_across_midnight(self):
        self.now = ts('2026-10-24T23:59:59')
        await self.gate.deliver(self.gate.session, candidate_id='late', send=self.send)
        self.now = ts('2026-10-25T00:00:00')
        self.assertEqual(self.permitted(), 'quiet_hours')
        # Inspect cooldown independently of the stricter nighttime boundary.
        self.gate.quiet = (0, 0)
        self.assertEqual(self.permitted(), 'budget_or_cooldown')
        self.now += 10799
        self.assertEqual(self.permitted(), 'allowed')

    async def test_generation_budget_is_separate_and_every_outcome_consumes(self):
        self.gate.close()
        self.gate = self.open_gate(max_per_day=1)
        for number, state in enumerate(('skipped', 'deferred', 'failed', 'empty', 'cancelled')):
            identity = 'generation-' + str(number)
            result = await self.gate.reserve_generation(self.gate.session, candidate_id=identity)
            self.assertEqual(result, 'allowed')
            self.gate.finish_generation(identity, state)
        self.gate.close()
        self.gate = self.open_gate(max_per_day=1)
        self.assertEqual(await self.gate.reserve_generation(self.gate.session, candidate_id='sixth'), 'generation_budget')
        self.assertEqual(self.gate.db.execute('SELECT count(*) FROM proactive_attempts').fetchone()[0], 0)
        self.now = ts('2026-10-25T09:00:00')
        self.assertEqual(await self.gate.reserve_generation(self.gate.session, candidate_id='sixth'), 'allowed')
        self.assertEqual(await self.gate.reserve_generation(self.gate.session, candidate_id='generation-0'), 'generation_already_attempted')

    async def test_restart_marks_interrupted_work_without_replay(self):
        await self.gate.reserve_generation(self.gate.session, candidate_id='running')
        self.gate.db.execute("INSERT INTO proactive_attempts(candidate,day,attempted,state) VALUES (?,?,?,'pending')",
            ('uncertain', '2026-10-24', self.now))
        self.gate.db.commit()
        self.gate.close()
        self.gate = self.open_gate()
        self.assertEqual(self.gate.db.execute('SELECT state FROM proactive_attempts').fetchone()[0], 'unknown')
        self.assertEqual(self.gate.db.execute('SELECT state FROM proactive_generations').fetchone()[0], 'interrupted')
        self.now += 10800
        self.assertEqual(await self.gate.reserve_generation(self.gate.session, candidate_id='running'), 'generation_already_attempted')
        result = await self.gate.deliver(self.gate.session, candidate_id='uncertain', send=self.send)
        self.assertEqual(result['reason'], 'already_attempted')
        self.assertEqual(self.sent, [])

    def test_indefinite_pause_survives_restart_and_temporary_controls(self):
        actions = parse_proactive_controls('先不要主动找我', now=self.now, window_end=0)
        self.assertEqual(actions, [{'kind':'pause', 'until':None}])
        self.gate.pause(actions[0]['until'])
        self.assertEqual(self.gate._get('pause_indefinite'), '1')
        self.gate.close()
        self.gate = self.open_gate()
        self.now += 40 * 86400
        self.assertEqual(self.permitted(), 'paused_or_busy')
        self.gate.pause(self.now + 3600)
        self.gate.set_control('busy_until', 0)
        self.now += 7200
        self.assertFalse(self.gate.continuation_allowed(self.gate.session))
        self.gate.pause(0)
        self.assertEqual(self.permitted(), 'allowed')
        values = [row[0].lower() for row in self.gate.db.execute('SELECT value FROM proactive_controls')]
        self.assertFalse(any('inf' in value or 'nan' in value for value in values))

    def test_today_pause_and_busy_have_finite_independent_expiry(self):
        self.now = ts('2026-10-24T23:59:59')
        pause = parse_proactive_controls('今天不要主动找我', now=self.now, window_end=0)[0]
        self.assertEqual(pause['until'], ts('2026-10-25T00:00:00'))
        self.gate.pause(pause['until'])
        self.assertEqual(self.permitted(), 'paused_or_busy')
        self.now = ts('2026-10-25T09:00:00')
        self.assertEqual(self.permitted(), 'allowed')
        busy = parse_proactive_controls('我现在忙，稍后聊。', now=self.now, window_end=0)[0]
        self.assertEqual(busy['until'], self.now + 14400)
        self.gate.set_control('busy_until', busy['until'])
        self.assertEqual(self.permitted(), 'paused_or_busy')
        self.gate.set_control('busy_until', parse_proactive_controls('我忙完了。', now=self.now, window_end=0)[0]['until'])
        self.assertEqual(self.permitted(), 'allowed')

    def test_trial_defaults_and_invalid_continuous_permission(self):
        trial = ProactiveGate(':memory:', allowed_session=self.gate.session, enabled=True,
            window_start=self.now - 1, window_end=self.now + 72 * 3600)
        try:
            self.assertEqual((trial.mode, trial.limit, trial.generation_limit), ('trial', 2, 2))
            trial.pause()
            self.assertEqual(float(trial._get('pause_until')), trial.end)
            self.assertNotEqual(trial._get('pause_indefinite'), '1')
        finally:
            trial.close()
        for options in ({'window_start':1}, {'window_end':1}, {'mode':'always'},
                        {'max_generations_per_day':0}, {'max_generations_per_day':49},
                        {'max_generations_per_day':True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.open_gate(**options)

    def test_candidate_expiry_stays_finite_and_never_extends(self):
        self.assertEqual(self.gate.candidate_expiry(None), 0)
        self.assertEqual(self.gate.candidate_expiry(float('inf')), 0)
        self.assertEqual(self.gate.candidate_expiry(self.now+7200, self.now+3600), self.now+3600)
        self.assertEqual(self.gate.candidate_expiry(self.now+3600, self.now+7200), self.now+3600)


if __name__ == '__main__':
    unittest.main()
