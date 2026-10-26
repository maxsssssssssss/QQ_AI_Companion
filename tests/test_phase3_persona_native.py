"""T2 V0.4 persona update/rollback through real policy, PersonaManager and SQLite.

Reuse the existing no-network startup fixture; plugin discovery and command ACL
descriptors remain synthetic. No real model, QQ account, or production state is
used. These tests cover the changed persona boundary, not the full plugin suite.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import unittest

import test_phase3_search_policy_integration as fixture


ROOT = Path(__file__).resolve().parents[1]
CURRENT = "phase3-v0.4.md"
PREVIOUS = "phase3-v0.3-candidate.md"
ADDRESS = "合成用户"


@unittest.skipUnless(fixture.NATIVE_AVAILABLE, "pinned AstrBot runtime required")
class PersonaNativeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.native = fixture.PolicyPersistenceTests(
            "test_real_native_persona_sqlite_survives_restart_and_reapplied_policy")
        await self.native.asyncSetUp()
        self.native.settings["preferred_address"] = ADDRESS
        self.native.extension.settings["preferred_address"] = ADDRESS
        self.slot = self.native.data / "phase3/persona-v0.1.md"
        self.history = [
            {"role": "user", "content": "synthetic original question"},
            {"role": "assistant", "content": "synthetic confirmed reply"},
        ]
        self.conversation = await self.native.databases[-1].create_conversation(
            user_id="napcat:FriendMessage:10001", platform_id="napcat",
            persona_id="qq_companion", content=self.history)

    async def asyncTearDown(self):
        await self.native.asyncTearDown()

    async def apply(self, filename):
        template = (ROOT / "persona" / filename).read_text()
        self.slot.write_text(template)
        await asyncio.wait_for(self.native.policy.apply_startup_policy(), 20)
        self.assertTrue(self.native.policy.ready)
        self.assertTrue(self.native.extension.ready)
        persona = await self.native.context.persona_manager.get_persona("qq_companion")
        self.assertEqual(persona.system_prompt, template.replace("{{preferred_address}}", ADDRESS).strip())
        self.assertEqual(persona.tools, ["web_search_deepseek", "deepseek_fetch_web_page"])
        self.assertEqual(persona.skills, [])
        self.assertEqual(sum(p.persona_id == "qq_companion"
                             for p in self.native.context.persona_manager.personas), 1)
        report = json.loads(self.native.policy.report_path.read_text())
        self.assertFalse(report["real_api_tested"])
        self.assertFalse(report["real_qq_tested"])
        self.assertIsNone(self.native.search.runtime)
        return persona

    async def reopen(self):
        await self.native.extension.terminate()
        await self.native.databases[-1].engine.dispose()
        await self.native.start()

    async def assert_history(self, expected):
        saved = await self.native.databases[-1].get_conversation_by_id(
            self.conversation.conversation_id)
        self.assertEqual(saved.content, expected)
        self.assertEqual(saved.persona_id, "qq_companion")

    async def test_current_heading_replaces_same_native_record_and_exposes_authorized_core(self):
        old = await self.apply(PREVIOUS)
        await self.reopen()
        updated = await self.apply(CURRENT)
        self.assertEqual(updated.id, old.id)
        self.assertEqual(updated.begin_dialogs, old.begin_dialogs)
        self.assertTrue(updated.system_prompt.startswith("# QQ_COMPANION_PERSONA_V0_4\n"))
        self.assertEqual(updated.system_prompt.count(ADDRESS), 8)
        self.assertNotIn("QQ_COMPANION_PERSONA_V0_3", updated.system_prompt)
        self.assertEqual(self.native.context.persona_manager.get_persona_v3_by_id(
            "qq_companion")["prompt"], updated.system_prompt)
        for event, permitted in ((fixture.Event(), True),
                                 (fixture.Event("10002"), False),
                                 (fixture.Event(private=False), False)):
            await self.native.policy.restrict_inbound(event)
            await self.native.extension.expose_core(
                event, fixture.ProviderRequest(prompt="synthetic private question"))
            self.assertEqual(event.stopped, not permitted)
            if permitted:
                self.assertEqual(event.get_extra("phase3_core_persona"), updated.system_prompt)
            else:
                self.assertNotIn("phase3_core_persona", event.extra)
        await self.assert_history(self.history)

    async def test_sqlite_reopen_and_persona_only_rollback_preserve_new_chat(self):
        current = await self.apply(CURRENT)
        extended = self.history + [
            {"role": "user", "content": "synthetic chat added after persona update"},
            {"role": "assistant", "content": "synthetic newly confirmed response"},
        ]
        await self.native.databases[-1].update_conversation(
            self.conversation.conversation_id, content=extended)
        await self.reopen()
        reopened = await self.native.context.persona_manager.get_persona("qq_companion")
        self.assertEqual((reopened.id, reopened.system_prompt),
                         (current.id, current.system_prompt))
        self.assertEqual(self.native.context.persona_manager.get_persona_v3_by_id(
            "qq_companion")["prompt"], current.system_prompt)
        await self.assert_history(extended)
        restored = await self.apply(PREVIOUS)
        self.assertEqual(restored.id, current.id)
        self.assertTrue(restored.system_prompt.startswith("[QQ_COMPANION_PERSONA_V0_3_CANDIDATE]\n"))
        await self.assert_history(extended)


if __name__ == "__main__":
    unittest.main(verbosity=2)
