"""Native 6.7.0 preference extraction, real Guard SQLite, synthetic deliveries.

No QQ or provider I/O.  These tests run the original native extraction/recall
methods; only host persistence, transport receipts and identity inputs are
controlled fixtures.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest

from integrations.phase1_guard.store import GuardStore
from integrations.phase3_memory.self_preferences import NativeSelfPreferences, CURSOR_KEY

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / ".runtime/upstream/private_companion"
try:
    package = types.ModuleType("phase3_native_preferences_fixture")
    package.__path__ = [str(UPSTREAM)]
    sys.modules[package.__name__] = package
    native = importlib.import_module(package.__name__ + ".user_memory")
    proactive = importlib.import_module(package.__name__ + ".proactive")
    NATIVE = True
except ImportError:
    NATIVE = False


if NATIVE:
    class Host(native.UserMemoryMixin, proactive.ProactiveMixin):
        def __init__(self, target):
            self.user = {"user_id": "10001", "umo": target,
                         "identity_subject_id": "10001", "identity_platform_kind": "qq",
                         "identity_adapter_instance_id": "test", "identity_bot_id": "20002"}
            self.data = {"users": {"10001": self.user}}
            self.saved = None
            self.save_error = False
            self._data_lock = asyncio.Lock()

        def _schedule_data_save(self, *, sections):
            assert sections == {"users"}
            if self.save_error:
                raise RuntimeError("synthetic_save_failure")
            self.saved = deepcopy(self.data)


@unittest.skipUnless(NATIVE, "pinned native source/imports unavailable; not a PASS")
class SelfPreferencesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertEqual(subprocess.check_output(["git", "-C", str(UPSTREAM), "rev-parse", "HEAD"],
                                               text=True).strip(),
                         "da953559f34d28a43d8a2d44c1064b293229c1b8")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = GuardStore(Path(self.tmp.name) / "guard.sqlite")
        self.store.enable_phase3()
        self.observers = []
        self.session = "test:FriendMessage:10001"
        self.guard = types.SimpleNamespace(store=self.store, phase3_platform_id="test",
            phase3_admin_id="10001", phase3_enabled=True,
            register_delivery_observer=self.observers.append,
            unregister_delivery_observer=self.observers.remove)
        self.host = Host(self.session)
        self.wake = asyncio.Event()
        self.adapter = NativeSelfPreferences(self.host, self.guard, self.session, wake=self.wake)
        self.adapter.attach()
        self.counter = 0

    async def asyncTearDown(self):
        self.adapter.detach()
        self.store.close()

    def record(self, text, *, kind="normal", state="sent", turn=None, part=0, session=None):
        self.counter += 1
        tid = turn or f"turn-{self.counter}"
        did = f"delivery-{self.counter}"
        target = session or self.session
        if not self.store.get_turn(tid):
            is_proactive = kind == "proactive"
            sources = []
            if not is_proactive:
                mid = f"inbound-{self.counter}"
                self.store.claim("test", target, mid, "10001", "synthetic", [])
                sources = [mid]
            self.store.create_turn(tid, "test", target, sources, proactive=is_proactive)
        self.store.begin_delivery(did, tid, kind, part, text, [{"type": "Plain", "text": text}])
        if state != "pending":
            self.store.finish_delivery(did, state, receipt_source="synthetic_confirmed_transport")
            with self.store.db:
                instant = datetime.fromtimestamp(1800000000 + self.counter, timezone.utc).isoformat()
                self.store.db.execute("UPDATE deliveries SET finished_at=? WHERE delivery_id=?", (instant, did))
        return did

    async def test_one_explicit_expression_native_records_and_recalls(self):
        did = self.record("我一直很喜欢听轻爵士。")
        await self.adapter.observe({"session": self.session, "state": "sent", "text": "do not trust this"})
        self.assertEqual(self.adapter.current(), [])
        self.assertTrue(self.wake.is_set())
        self.assertEqual(await self.adapter.drain(), 1)
        current = self.adapter.current()
        self.assertEqual(len(current), 1)
        self.assertIn("轻爵士", current[0]["statement"])
        self.assertEqual(current[0]["confirmed_source"]["delivery_id"], did)
        self.assertIn("轻爵士", self.host._format_bot_self_preference_consistency(self.host.user, "你喜欢听什么歌"))
        self.assertIn("轻爵士", self.adapter.prompt())

    async def test_proactive_and_passive_share_existing_observer(self):
        self.record("我更喜欢玩解谜游戏。", kind="proactive")
        self.record("我喜欢喝茶。", kind="normal")
        self.assertEqual(await self.adapter.drain(), 2)
        self.assertEqual({v["category"] for v in self.adapter.current()}, {"game", "food"})

    async def test_only_confirmed_prefix_of_partial_group(self):
        self.record("我喜欢听爵士。", turn="partial", part=0)
        self.record("我喜欢喝咖啡。", turn="partial", part=1, state="unknown")
        self.assertEqual(await self.adapter.drain(), 2)
        self.assertEqual([v["category"] for v in self.adapter.current()], ["music"])

    async def test_failed_unknown_and_search_progress_cannot_be_preferences(self):
        for state in ("failed", "unknown"):
            self.record("我喜欢喝咖啡。", state=state)
        self.record("我喜欢玩棋牌游戏。", kind="progress")
        self.assertEqual(await self.adapter.drain(), 3)
        self.assertEqual(self.adapter.current(), [])

    async def test_search_final_is_confirmed_visible_expression(self):
        self.record("我更喜欢听轻爵士。", kind="final")
        self.assertEqual(await self.adapter.drain(), 1)
        self.assertEqual(self.adapter.current()[0]["category"], "music")

    async def test_repeat_observation_and_restart_are_idempotent(self):
        self.record("我喜欢喝茶。")
        await self.adapter.drain()
        saved = deepcopy(self.host.saved)
        await self.adapter.observe({"session": self.session, "state": "sent"})
        self.assertEqual(await self.adapter.drain(), 0)
        self.assertEqual(self.host.saved, saved)
        self.adapter.detach()
        self.host.data = json.loads(json.dumps(saved))
        self.host.user = self.host.data["users"]["10001"]
        self.adapter = NativeSelfPreferences(self.host, self.guard, self.session)
        self.adapter.attach()
        self.assertEqual(await self.adapter.drain(), 0)
        self.assertEqual(len(self.adapter.current()), 1)

    async def test_crash_before_native_snapshot_replays_confirmed_source(self):
        baseline = deepcopy(self.host.saved)
        self.record("我喜欢喝茶。")
        await self.adapter.drain()
        self.adapter.detach()
        self.host.data = baseline
        self.host.user = self.host.data["users"]["10001"]
        self.adapter = NativeSelfPreferences(self.host, self.guard, self.session)
        self.adapter.attach()
        self.assertEqual(await self.adapter.drain(), 1)
        self.assertEqual(len(self.adapter.current()), 1)

    async def test_new_preference_overrides_old_category_for_current_recall(self):
        self.record("我喜欢喝咖啡。")
        await self.adapter.drain()
        self.record("我现在换口味了，我更喜欢喝茶。")
        await self.adapter.drain()
        self.assertEqual(len(self.host.user["bot_self_preferences"]), 2)
        self.assertEqual(len(self.adapter.current()), 1)
        self.assertIn("茶", self.adapter.current()[0]["statement"])
        self.assertNotIn("咖啡", self.adapter.prompt())

    async def test_withdrawn_current_preference_does_not_revive_previous_one(self):
        self.record("我喜欢喝咖啡。")
        self.record("我更喜欢喝茶。")
        await self.adapter.drain()
        self.host.user["bot_self_preferences"][-1]["revoked"] = True
        self.assertEqual(self.adapter.current(), [])

    async def test_first_installation_does_not_backfill_historical_chats(self):
        self.adapter.detach()
        self.host.user.pop(CURSOR_KEY)
        self.record("我喜欢听爵士。")
        self.adapter.attach()
        self.assertEqual(await self.adapter.drain(), 0)
        self.assertEqual(self.adapter.current(), [])

    async def test_departure_side_effects_are_not_replayed(self):
        self.host.user.update(conversation_departure_offer_at=1800000000, episode_message_count=9)
        self.record("我喜欢听爵士。我先去休息了，回头再聊。")
        await self.adapter.drain()
        self.assertEqual(self.host.user["episode_message_count"], 9)
        self.assertNotIn("conversation_departure", self.host.user)
        self.assertEqual(len(self.adapter.current()), 1)

    async def test_other_identity_and_ambiguous_native_binding_are_denied(self):
        self.record("我喜欢喝茶。", session="test:FriendMessage:10002")
        self.assertEqual(await self.adapter.drain(), 0)
        self.host.data["users"]["duplicate"] = deepcopy(self.host.user)
        with self.assertRaises(PermissionError):
            self.adapter.current()

    async def test_missing_identity_stamp_does_not_use_cached_preference(self):
        self.record("我喜欢喝茶。")
        await self.adapter.drain()
        self.host.user.pop("identity_subject_id")
        with self.assertRaises(PermissionError):
            self.adapter.current()

    async def test_pending_row_blocks_watermark_until_settled(self):
        first = self.record("我喜欢喝咖啡。", state="pending")
        self.record("我喜欢听爵士。")
        self.assertEqual(await self.adapter.drain(), 0)
        self.assertTrue(self.adapter.pending)
        self.store.finish_delivery(first, "unknown")
        self.assertEqual(await self.adapter.drain(), 2)
        self.assertEqual([v["category"] for v in self.adapter.current()], ["music"])

    async def test_save_failure_does_not_advance_native_cursor(self):
        before = deepcopy(self.host.user[CURSOR_KEY])
        self.record("我喜欢喝茶。")
        self.host.save_error = True
        with self.assertRaises(RuntimeError):
            await self.adapter.drain()
        self.assertEqual(self.host.user[CURSOR_KEY], before)
        self.assertEqual(self.adapter.current(), [])
        self.host.save_error = False
        self.assertEqual(await self.adapter.drain(), 1)

    async def test_not_general_opinion_store_and_empty_interests_allow_topic_choice(self):
        self.record("我觉得这个理论还缺少可验证的预测。")
        await self.adapter.drain()
        self.assertEqual(self.adapter.current(), [])
        self.assertIn("自主选题", self.adapter.prompt())

    async def test_guard_disable_is_rechecked(self):
        self.record("我喜欢喝茶。")
        self.guard.phase3_enabled = False
        with self.assertRaises(PermissionError):
            await self.adapter.drain()

    async def test_detach_stops_extraction_without_erasing_native_state(self):
        self.record("我喜欢喝茶。")
        await self.adapter.drain()
        before = deepcopy(self.host.user)
        self.adapter.detach()
        self.record("我喜欢听爵士。")
        self.assertEqual(await self.adapter.drain(), 0)
        self.assertEqual(self.host.user, before)
        self.assertEqual(self.observers, [])

    async def test_no_extra_history_or_fact_store_is_written(self):
        self.host.user["native_history"] = [{"content": "synthetic preserved chat"}]
        self.record("我喜欢喝茶。")
        tables = [r[0] for r in self.store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        await self.adapter.drain()
        self.assertEqual(self.host.user["native_history"], [{"content": "synthetic preserved chat"}])
        self.assertEqual(tables, [r[0] for r in self.store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")])


if __name__ == "__main__":
    unittest.main()
