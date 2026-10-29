"""Startup receipt evidence and noncritical failure behavior, no network or QQ."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

NATIVE = importlib.util.find_spec('astrbot') is not None
if NATIVE:
    os.environ.setdefault('ASTRBOT_ROOT', tempfile.mkdtemp(prefix='qq-autonomy-readiness-host-'))
    from integrations.phase3_runtime import main as runtime_module
from integrations.phase3_runtime.settings import AUTONOMY_DEFAULTS


class BridgeFixture:
    def __init__(self):
        self.options = deepcopy(AUTONOMY_DEFAULTS)
        self.options['enabled'] = True
        self.private = NS(private_chat='synthetic confidential chat', api_key='synthetic forbidden key')
        self.native_adapter = NS(installed={'native_tick':'bound','native_template':'bound'})
        self.native_activated = False

    def tick(self):
        pass

    def framework(self):
        pass

    def attach(self):
        self.private._tick_user = self.tick
        self.private._run_framework_agent_text = self.framework

    def activate_native_config(self):
        self.native_activated = True

    def detach(self):
        self.native_activated = False


class PreferenceFixture:
    attached = False
    def attach(self):
        self.attached = True
    def detach(self):
        self.attached = False


@unittest.skipUnless(NATIVE, 'pinned AstrBot required')
class AutonomyReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root/'autonomy-readiness.json'
        self.runtime = object.__new__(runtime_module.Phase3Runtime)
        self.runtime.ready = False
        self.runtime.prepared = True
        self.runtime.memory = None
        self.runtime.autonomy_state = object()
        self.runtime.proactive_bridge = BridgeFixture()
        self.runtime.proactive = NS(enabled=False)
        self.runtime.self_preferences = PreferenceFixture()
        self.runtime.tasks = set()
        self.runtime.memory_wake = asyncio.Event()
        self.runtime.admin_id = '10001'
        self.runtime.platform_id = 'test'
        p = patch.object(runtime_module.StarTools, 'get_data_dir', return_value=self.root)
        p.start()
        self.addCleanup(p.stop)

    def passive_authorized(self):
        event = NS(get_platform_name=lambda:'aiocqhttp', is_private_chat=lambda:True,
                   get_sender_id=lambda:'10001', get_platform_id=lambda:'test')
        return self.runtime._authorized(event)

    async def test_activation_receipt_is_fresh_private_and_only_reports_observed_scope(self):
        self.path.write_text('{"ready":true,"at":"2000-01-01T00:00:00+00:00"}')
        before = datetime.now(timezone.utc)
        await self.runtime.activate()
        after = datetime.now(timezone.utc)
        raw = self.path.read_text()
        receipt = json.loads(raw)
        self.assertTrue(before <= datetime.fromisoformat(receipt['at']) <= after)
        self.assertEqual(receipt['pid'], os.getpid())
        self.assertTrue(receipt['ready'])
        self.assertTrue(receipt['tick_attached'] and receipt['agent_attached'])
        self.assertTrue(receipt['self_preferences_attached'])
        self.assertTrue(self.runtime.proactive_bridge.native_activated)
        self.assertTrue(self.runtime.proactive.enabled)
        self.assertTrue(self.passive_authorized())
        for secret in ('synthetic confidential chat','synthetic forbidden key','10001','test:FriendMessage'):
            self.assertNotIn(secret, raw)
        self.assertEqual(receipt['options'], {**AUTONOMY_DEFAULTS,'enabled':True})
        if os.name == 'posix':
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertFalse(self.path.with_suffix('.tmp').exists())

    async def test_receipt_hashes_actual_selected_runtime_sources_without_arbitrary_files(self):
        await self.runtime.activate()
        receipt = json.loads(self.path.read_text())
        source = Path(runtime_module.__file__).parent
        expected = {'main.py'} | {p.name for p in source.glob('autonomy*.py')}
        self.assertEqual(set(receipt['source_hashes']), expected)
        for name, digest in receipt['source_hashes'].items():
            self.assertEqual(digest, hashlib.sha256((source/name).read_bytes()).hexdigest())
        self.assertEqual(receipt['sdk_meter_installation'], 'on_first_native_agent_build')
        self.assertNotIn('model_tested', receipt)
        self.assertNotIn('qq_tested', receipt)

    async def test_receipt_write_failure_does_not_stop_activation_or_passive_chat(self):
        with (patch.object(runtime_module.os, 'open', side_effect=PermissionError('synthetic private path')),
              patch.object(runtime_module.logger, 'warning') as warning):
            await self.runtime.activate()
        self.assertTrue(self.runtime.ready and self.runtime.proactive.enabled)
        self.assertTrue(self.runtime.proactive_bridge.native_activated)
        self.assertTrue(self.runtime.self_preferences.attached)
        self.assertTrue(self.passive_authorized())
        self.assertFalse(self.path.exists())
        self.assertEqual(warning.call_args.args[-1], 'PermissionError')
        self.assertNotIn('synthetic private path', str(warning.call_args))

    async def test_failed_rewrite_cannot_refresh_a_stale_receipt(self):
        old = '{"schema":"phase3-autonomy-ready.v1","at":"2000-01-01T00:00:00+00:00","ready":true}'
        self.path.write_text(old)
        os.utime(self.path, (946684800,946684800))
        with patch.object(runtime_module.os, 'replace', side_effect=OSError('synthetic error')):
            await self.runtime.activate()
        self.assertTrue(self.passive_authorized())
        self.assertEqual(self.path.read_text(), old)
        self.assertEqual(self.path.stat().st_mtime, 946684800)

    async def test_binding_observation_does_not_treat_other_instance_or_unready_as_active(self):
        await self.runtime.activate()
        other = BridgeFixture()
        self.runtime.proactive_bridge.private._tick_user = other.tick
        self.runtime.proactive_bridge.private._run_framework_agent_text = lambda: None
        self.runtime.self_preferences.attached = False
        self.runtime.ready = False
        self.runtime._write_autonomy_readiness()
        receipt = json.loads(self.path.read_text())
        self.assertFalse(receipt['ready'])
        self.assertFalse(receipt['tick_attached'])
        self.assertFalse(receipt['agent_attached'])
        self.assertFalse(receipt['self_preferences_attached'])


if __name__ == '__main__':
    unittest.main()
