"""Shutdown must let native-owned work settle before closing its shared ledger."""
import asyncio
from datetime import datetime
import importlib.util
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

NATIVE = importlib.util.find_spec('astrbot') is not None
if NATIVE:
    os.environ.setdefault('ASTRBOT_ROOT', tempfile.mkdtemp(prefix='qq-autonomy-lifecycle-host-'))
    from integrations.phase3_runtime import main as runtime_module
from integrations.phase3_runtime.autonomy_state import AutonomyState


@unittest.skipUnless(NATIVE, 'pinned AstrBot required')
class AutonomyLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'gate.sqlite3'
        self.db = sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        now = datetime.fromisoformat('2026-10-28T10:00:00+08:00').timestamp()
        self.state = AutonomyState(self.db, clock=lambda:now)
        self.state.begin_opportunity('candidate',evaluation_id='eval',candidate_id='candidate',
            session='test:FriendMessage:10001',deadline=now+600,input_version='v1')
        self.call = self.state.reserve_model_request('eval')['id']
        self.runtime = object.__new__(runtime_module.Phase3Runtime)
        self.runtime.ready = self.runtime.prepared = True
        self.runtime.memory = self.runtime.guard = self.runtime.self_preferences = None
        self.runtime.tasks = set()
        self.detached = asyncio.Event()
        self.bridge = NS(scopes={'candidate':{'evaluation_id':'eval'}}, detached=False)
        def detach():
            self.bridge.detached = True
            self.detached.set()
        self.bridge.detach = detach
        self.runtime.proactive_bridge = self.bridge
        self.closed = False
        def close():
            self.closed = True
            self.db.close()
        self.runtime.proactive = NS(close=close)

    async def test_native_owned_scope_commits_cancellation_before_shared_ledger_closes(self):
        settled = asyncio.Event()
        async def native_model_monitor():
            await self.detached.wait()
            await asyncio.sleep(.02)
            self.assertFalse(self.closed)
            self.assertFalse(self.runtime.ready)
            self.state.finish_call(self.call,'cancelled',metadata={'suspended_reason':'service_stopping'})
            self.state.finalize_evaluation('eval','cancelled',reason='service_stopping')
            self.bridge.scopes.clear()
            settled.set()
        task = asyncio.create_task(native_model_monitor())
        self.assertNotIn(task,self.runtime.tasks)  # the native scheduler owns it
        await self.runtime.terminate()
        await task
        self.assertTrue(settled.is_set() and self.closed and self.bridge.detached)
        self.assertIsNone(self.runtime.proactive_bridge)
        with sqlite3.connect(self.path) as restored:
            self.assertEqual(restored.execute('SELECT state FROM autonomy_calls').fetchone()[0],'cancelled')
            self.assertEqual(restored.execute('SELECT effective_state FROM autonomy_evaluations').fetchone()[0],'cancelled')

    async def test_slow_native_scope_keeps_ledger_available_after_bounded_shutdown_wait(self):
        started = asyncio.get_running_loop().time()
        with patch.object(runtime_module.logger,'warning') as warning:
            await asyncio.wait_for(self.runtime.terminate(),timeout=10)
        elapsed = asyncio.get_running_loop().time()-started
        self.assertGreaterEqual(elapsed,7.8)
        self.assertLess(elapsed,9.5)
        self.assertFalse(self.closed)
        self.assertTrue(self.bridge.detached)
        self.assertIsNone(self.runtime.proactive)
        self.assertIn('ledger kept open',warning.call_args.args[0])
        # A late native finally block can still persist its terminal outcome.
        self.assertTrue(self.state.finish_call(self.call,'cancelled'))
        self.assertTrue(self.state.finalize_evaluation('eval','cancelled',reason='service_stopping'))
        self.bridge.scopes.clear()


if __name__ == '__main__':
    unittest.main()
