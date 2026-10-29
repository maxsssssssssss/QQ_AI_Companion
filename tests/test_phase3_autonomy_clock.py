"""A moving clock must not turn a bounded 600s scope into 600.001s."""
import unittest
from test_phase3_autonomy_bridge import BridgeTests
from integrations.phase3_search.proactive import ProactiveSearchContext


class MovingClockTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = BridgeTests.asyncSetUp

    async def test_exact_persisted_start_used_by_bridge_and_tool_context(self):
        def advancing():
            self.now += .001
            return self.now
        self.gate.clock = advancing
        self.state.clock = advancing
        await self.bridge.generate(self.host.user, '测试者', 'activity_share')
        scope = self.bridge.pending['candidate-1']
        self.assertEqual(scope['started_at'], self.state.evaluation(scope['evaluation_id'])['started'])
        value = ProactiveSearchContext(scope['session'], scope['opportunity_id'], scope['evaluation_id'],
            scope['started_at'], scope['deadline'], scope['input_version'])
        self.assertLessEqual(value.deadline-value.started_at, 600)
