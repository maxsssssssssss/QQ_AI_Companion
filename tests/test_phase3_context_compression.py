"""Native AstrBot 4.29.1 context compression with synthetic content only.

Use the pinned Phase 3 venv, PYTHONPATH=.runtime/upstream/AstrBot:., and an
isolated ASTRBOT_ROOT. No real provider, QQ transport, or private data is used.
The SQLite test exercises native persistence, not a running production service.
"""
import asyncio
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

try:
    from astrbot.core.agent.context.compressor import LLMSummaryCompressor
    from astrbot.core.agent.context.config import ContextConfig
    from astrbot.core.agent.context.manager import ContextManager
    from astrbot.core.agent.context.round_utils import split_into_rounds
    from astrbot.core.agent.context.token_counter import EstimateTokenCounter
    from astrbot.core.agent.message import (
        ImageURLPart, Message, TextPart, bind_checkpoint_messages,
        dump_messages_with_checkpoints,
    )
    from astrbot.core.agent.hooks import BaseAgentRunHooks
    from astrbot.core.agent.run_context import ContextWrapper
    from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
    from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
    from astrbot.core.config.agent_runner import resolve_context_compression_config
    from astrbot.core.conversation_mgr import ConversationManager
    from astrbot.core.db.sqlite import SQLiteDatabase
    from astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal import (
        InternalAgentSubStage,
    )
    from astrbot.core.pipeline.process_stage.follow_up import (
        register_active_runner, unregister_active_runner,
    )
    from astrbot.core.provider.entities import LLMResponse, ProviderRequest
    from integrations.phase1_guard.delivery import DeliveryReceipt
    from integrations.phase1_guard.main import Phase1Guard, StarTools
    from integrations.phase3_runtime.main import Phase3Runtime
    from integrations.phase3_runtime.context_compression import FailureRoundFallback
    NATIVE = True
except ImportError:
    NATIVE = False


PROJECT = Path(__file__).resolve().parents[1]
_config_spec = importlib.util.spec_from_file_location(
    "phase3_context_config_for_native_test", PROJECT / "scripts/phase3-context-config.py")
_config_module = importlib.util.module_from_spec(_config_spec)
_config_spec.loader.exec_module(_config_module)
CONTEXT_TOKENS = _config_module.CONTEXT_TOKENS
SYNTHETIC_SUMMARY = "Synthetic history: a test plan was discussed; continue the current request."
# One synthetic 1x1 PNG. No real image or temporary URL is used.
PNG_URI = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jQ1sAAAAASUVORK5CYII="
)


class SyntheticSummaryProvider:
    def __init__(self, mode="success"):
        self.mode = mode
        self.calls = []
        self.provider_config = {"modalities": ["text", "image", "tool_use"]}

    async def text_chat(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        if self.mode == "timeout":
            raise TimeoutError("synthetic summary timeout")
        if self.mode == "cancel":
            raise asyncio.CancelledError()
        text = "" if self.mode == "empty" else SYNTHETIC_SUMMARY
        return SimpleNamespace(completion_text=text)


class SyntheticRunnerProvider(SyntheticSummaryProvider):
    """Same synthetic provider handles the summary and the actual Agent final."""

    def __init__(self, mode):
        super().__init__(mode)
        self.main_calls = []
        self.call_order = []
        self.provider_config.update(id="synthetic-flash", max_context_tokens=CONTEXT_TOKENS)

    async def text_chat(self, **kwargs):
        if "abort_signal" not in kwargs:
            self.call_order.append("summary")
            return await super().text_chat(**kwargs)
        self.call_order.append("main")
        self.main_calls.append(copy.deepcopy(kwargs["contexts"]))
        return LLMResponse(role="assistant", completion_text="Synthetic normal final after context handling.")


class SyntheticRunnerEvent:
    def __init__(self):
        self.unified_msg_origin = "synthetic-context:FriendMessage:10001"
        self.extras = {}

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def set_extra(self, key, value):
        self.extras[key] = value


@unittest.skipUnless(NATIVE, "pinned AstrBot and locked dependency environment required")
class NativeContextCompressionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qq-context-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.network = patch("socket.socket.connect", side_effect=AssertionError("network forbidden"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def manager(self, provider=None, **changes):
        cfg = dict(
            max_context_tokens=CONTEXT_TOKENS,
            enforce_max_turns=-1,
            truncate_turns=2,
            llm_compress_keep_recent_ratio=0.15,
            llm_compress_instruction=_config_module.SUMMARY_INSTRUCTION,
            llm_compress_provider=provider or SyntheticSummaryProvider(),
        )
        cfg.update(changes)
        return ContextManager(ContextConfig(**cfg))

    def history(self, rounds=24, chars=4000):
        messages = [Message(role="system", content="Synthetic persona remains unchanged.")]
        for index in range(rounds):
            messages.extend([
                Message(role="user", content=f"old-user-{index}:" + "甲" * chars),
                Message(role="assistant", content=f"old-answer-{index}:" + "乙" * chars),
            ])
        return messages

    def tool_round(self, label="recent"):
        return [
            Message(role="user", content=[TextPart(text=f"{label} image question"),
                ImageURLPart(image_url=ImageURLPart.ImageURL(url=PNG_URI))]),
            Message(role="assistant", content=None, tool_calls=[{
                "id": f"call-{label}", "type": "function", "function": {
                    "name": "synthetic_lookup", "arguments": '{"topic":"fixture"}',
                },
            }]),
            Message(role="tool", content=f"{label} tool result", tool_call_id=f"call-{label}"),
            Message(role="assistant", content=f"{label} acknowledged result"),
        ]

    def assert_tool_pairs(self, messages):
        calls = set()
        replies = set()
        for message in messages:
            for call in message.tool_calls or []:
                calls.add(call["id"] if isinstance(call, dict) else call.id)
            if message.role == "tool":
                replies.add(message.tool_call_id)
        self.assertEqual(calls, replies)

    async def test_native_config_disables_turn_cap_before_compression(self):
        resolved = resolve_context_compression_config(
            dict(_config_module.COMPRESSION_VALUES, trim_turns=2))
        self.assertEqual(resolved["max_context_length"], -1)
        self.assertEqual(resolved["context_limit_reached_strategy"], "llm_compress")
        self.assertEqual(resolved["llm_compress_keep_recent_ratio"], 0.15)
        self.assertEqual(resolved["fallback_max_context_tokens"], CONTEXT_TOKENS)
        self.assertEqual(CONTEXT_TOKENS, 128000)
        self.assertEqual(resolved["llm_compress_instruction"], _config_module.SUMMARY_INSTRUCTION)
        self.assertIsInstance(self.manager().compressor, LLMSummaryCompressor)

    async def test_forty_short_rounds_are_not_cropped_or_summarized(self):
        provider = SyntheticSummaryProvider()
        messages = self.history(rounds=40, chars=3)
        before = dump_messages_with_checkpoints(messages)
        result = await self.manager(provider).process(messages)
        self.assertEqual(dump_messages_with_checkpoints(result), before)
        self.assertEqual(sum(row.role == "user" for row in result), 40)
        self.assertEqual(provider.calls, [])

    async def test_threshold_is_strictly_above_eighty_two_percent(self):
        provider = SyntheticSummaryProvider()
        manager = self.manager(provider)
        messages = self.history(rounds=40, chars=3)
        boundary = int(CONTEXT_TOKENS * 0.82)
        unchanged = await manager.process(messages, trusted_token_usage=boundary)
        self.assertEqual(len(unchanged), len(messages))
        self.assertEqual(provider.calls, [])
        compressed = await manager.process(messages, trusted_token_usage=boundary + 1)
        self.assertEqual(len(provider.calls), 1)
        self.assertLess(len(compressed), len(messages))
        self.assertIn(SYNTHETIC_SUMMARY, compressed[1].content)

    async def test_native_token_estimate_triggers_one_summary_and_preserves_recent_rounds(self):
        provider = SyntheticSummaryProvider()
        manager = self.manager(provider)
        messages = self.history()
        current = Message(role="user", content="current active question")
        messages.append(current)
        before = dump_messages_with_checkpoints(messages)
        tokens = EstimateTokenCounter().count_tokens(messages)
        self.assertGreater(tokens, CONTEXT_TOKENS * 0.82)
        result = await manager.process(messages)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(provider.calls[0]["request_max_retries"], 1)
        self.assertIn(_config_module.SUMMARY_INSTRUCTION, provider.calls[0]["contexts"][-1]["content"])
        self.assertIs(result[0], messages[0])
        self.assertIs(result[-1], current)
        self.assertEqual([m.role for m in result[1:3]], ["user", "assistant"])
        self.assertIn(SYNTHETIC_SUMMARY, result[1].content)
        # Every retained recent message is the original object, in original order.
        first_recent = next(i for i, msg in enumerate(messages) if msg is result[3])
        self.assertEqual(result[3:], messages[first_recent:])
        self.assertEqual(result[3].role, "user")
        self.assertEqual(dump_messages_with_checkpoints(messages), before)
        self.assertLess(EstimateTokenCounter().count_tokens(result), CONTEXT_TOKENS * 0.82)

    async def test_recent_image_and_tool_round_remain_exact_and_ordered(self):
        provider = SyntheticSummaryProvider()
        special = self.tool_round()
        current = Message(role="user", content="Follow up on that image.")
        result = await self.manager(provider).process(self.history() + special + [current])
        self.assertEqual(result[-5:], special + [current])
        self.assertIs(result[-5], special[0])
        self.assertEqual(result[-5].content[1].image_url.url, PNG_URI)
        self.assert_tool_pairs(result)
        summary_input = json.dumps(provider.calls[0]["contexts"])
        self.assertNotIn("recent image question", summary_input)

    async def test_old_tool_and_image_payload_reach_summary_with_supported_modalities(self):
        provider = SyntheticSummaryProvider()
        messages = self.history()
        special = self.tool_round("old-special")
        messages[1:1] = special
        messages.append(Message(role="user", content="current request"))
        result = await self.manager(provider).process(messages)
        summary_input = [Message.model_validate(row) for row in provider.calls[0]["contexts"]]
        self.assert_tool_pairs(summary_input)
        self.assertTrue(any(row.role == "tool" and row.tool_call_id == "call-old-special"
                            for row in summary_input))
        self.assertIn(PNG_URI, json.dumps(provider.calls[0]["contexts"]))
        self.assertNotIn("call-old-special", json.dumps(dump_messages_with_checkpoints(result)))

    async def test_summary_timeout_and_empty_result_fall_back_without_orphan_tool_parts(self):
        for mode in ("timeout", "empty"):
            with self.subTest(mode=mode):
                provider = SyntheticSummaryProvider(mode)
                recent = self.tool_round()
                current = Message(role="user", content="Still answer this request.")
                messages = self.history() + recent + [current]
                original = dump_messages_with_checkpoints(messages)
                result = await self.manager(provider).process(messages)
                self.assertEqual(len(provider.calls), 1)
                self.assertLess(len(result), len(messages))
                self.assertEqual(result[-5:], recent + [current])
                self.assert_tool_pairs(result)
                self.assertLessEqual(EstimateTokenCounter().count_tokens(result), CONTEXT_TOKENS * 0.82)
                self.assertEqual(dump_messages_with_checkpoints(messages), original)
                self.assertNotIn(SYNTHETIC_SUMMARY, json.dumps(dump_messages_with_checkpoints(result)))

    async def test_cancelled_summary_propagates_without_committing_partial_context(self):
        provider = SyntheticSummaryProvider("cancel")
        messages = self.history() + [Message(role="user", content="current request")]
        original = dump_messages_with_checkpoints(messages)
        with self.assertRaises(asyncio.CancelledError):
            await self.manager(provider).process(messages)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(dump_messages_with_checkpoints(messages), original)

    async def test_oversized_current_round_is_not_silently_discarded(self):
        provider = SyntheticSummaryProvider()
        current = Message(role="user", content="甲" * 180000)
        messages = [Message(role="system", content="Synthetic persona"), current]
        result = await self.manager(provider).process(messages)
        self.assertEqual(result, messages)
        self.assertIs(result[-1], current)
        self.assertEqual(provider.calls, [])

    async def test_native_message_serialization_round_trip_keeps_summary_media_and_tools(self):
        special = self.tool_round()
        provider = SyntheticSummaryProvider()
        messages = await self.manager(provider).process(
            self.history() + special + [Message(role="user", content="current follow-up")])
        serialized = json.dumps(dump_messages_with_checkpoints(messages))
        restored = bind_checkpoint_messages(json.loads(serialized))
        self.assertEqual(dump_messages_with_checkpoints(restored), json.loads(serialized))
        self.assert_tool_pairs(restored)
        self.assertIn(PNG_URI, serialized)
        self.assertIn(SYNTHETIC_SUMMARY, serialized)
        again = await self.manager(provider).process(restored)
        self.assertEqual(dump_messages_with_checkpoints(again), json.loads(serialized))
        self.assertEqual(len(provider.calls), 1)

    async def run_registered_native_step(self, mode, chars):
        provider = SyntheticRunnerProvider(mode)
        event = SyntheticRunnerEvent()
        with patch.object(StarTools, "get_data_dir", return_value=self.root / f"step-guard-{mode}-{chars}"):
            guard = Phase1Guard(SimpleNamespace(get_config=lambda *a, **k: {}, get_all_stars=lambda: []), {
                "phase3_enabled": True, "phase3_admin_id": "10001", "phase3_platform_id": "synthetic-context",
            })
        self.addAsyncCleanup(guard.terminate)
        key = ("synthetic-context", event.unified_msg_origin, "1")
        guard.store.claim(*key, "10001", "Synthetic current request", [])
        turn, _ = guard.turns.observe(key, "Synthetic current request", groupable=False)
        event.set_extra("phase3_turn_id", turn.turn_id)
        guard.store.record_turn_stage(turn.turn_id, "model_request", {"existing_metadata": "synthetic"})
        runtime = SimpleNamespace(
            settings={"context_compression_fallback_turns": 80}, ready=True,
            guard=guard,
        )
        hook_calls = []

        class RuntimeHooks(BaseAgentRunHooks):
            async def on_agent_begin(self, context):
                hook_calls.append("begin")
                await Phase3Runtime.prepare_context_fallback(runtime, event, context)

            async def on_agent_done(self, context, response):
                hook_calls.append("done")

        context = ContextWrapper(context=SimpleNamespace(event=event))
        history = self.history(rounds=100, chars=chars) + self.tool_round("runner-recent")
        request = ProviderRequest(prompt="Current request still needs a normal answer.",
            contexts=dump_messages_with_checkpoints(history),
            model="synthetic-flash", session_id=event.unified_msg_origin)
        resolved = resolve_context_compression_config(dict(_config_module.COMPRESSION_VALUES, trim_turns=2))
        runner = ToolLoopAgentRunner()
        await runner.reset(provider=provider, request=request, run_context=context,
            tool_executor=FunctionToolExecutor(), agent_hooks=RuntimeHooks(), streaming=False,
            enforce_max_turns=resolved["max_context_length"],
            truncate_turns=resolved["dequeue_context_length"],
            llm_compress_provider=provider,
            llm_compress_instruction=resolved["llm_compress_instruction"],
            llm_compress_keep_recent_ratio=resolved["llm_compress_keep_recent_ratio"],
            request_max_retries=1)
        original = list(context.messages)
        self.assertIsInstance(runner.request_context_manager.compressor, LLMSummaryCompressor)
        register_active_runner(event.unified_msg_origin, runner)
        try:
            responses = [response async for response in runner.step()]
        finally:
            unregister_active_runner(event.unified_msg_origin, runner)
        self.assertEqual(hook_calls, ["begin", "done"])
        self.assertIsInstance(runner.request_context_manager.compressor, FailureRoundFallback)
        self.assertEqual(runner.request_context_manager.compressor.limit, 80)
        diagnostics = json.loads(guard.store.get_turn(turn.turn_id)["diagnostics_json"])
        self.assertEqual(diagnostics["model_request"]["context_compression"],
                         {"fallback_turns": 80, "installed": True})
        self.assertEqual(diagnostics["model_request"]["existing_metadata"], "synthetic")
        self.assertTrue(runner.done())
        self.assertEqual(runner.get_final_llm_resp().completion_text,
                         "Synthetic normal final after context handling.")
        self.assertEqual(len(provider.main_calls), 1)
        self.assertTrue(any(response.type == "llm_result" for response in responses))
        return provider, original

    async def test_registered_native_step_summary_failure_delivers_at_most_eighty_complete_rounds(self):
        for mode in ("timeout", "empty"):
            with self.subTest(mode=mode):
                provider, original = await self.run_registered_native_step(mode, chars=1000)
                self.assertGreater(EstimateTokenCounter().count_tokens(original), CONTEXT_TOKENS * 0.82)
                actual = [Message.model_validate(row) for row in provider.main_calls[0]]
                expected_rounds = split_into_rounds(original[1:])[-80:]
                expected = [original[0]] + [message for row in expected_rounds for message in row]
                self.assertEqual(dump_messages_with_checkpoints(actual), dump_messages_with_checkpoints(expected))
                self.assertEqual(len(split_into_rounds(actual[1:])), 80)
                self.assert_tool_pairs(actual)
                self.assertIn(PNG_URI, json.dumps(provider.main_calls[0]))
                self.assertEqual(provider.call_order, ["summary", "main"])
                self.assertEqual(len(provider.calls), 1)
                self.assertEqual(provider.calls[0]["request_max_retries"], 1)

    async def test_registered_native_step_below_threshold_retains_more_than_eighty_short_rounds(self):
        provider, original = await self.run_registered_native_step("timeout", chars=2)
        self.assertLess(EstimateTokenCounter().count_tokens(original), CONTEXT_TOKENS * 0.82)
        actual = [Message.model_validate(row) for row in provider.main_calls[0]]
        self.assertEqual(dump_messages_with_checkpoints(actual), dump_messages_with_checkpoints(original))
        self.assertGreater(len(split_into_rounds(actual[1:])), 80)
        self.assertEqual(provider.calls, [])
        self.assertEqual(provider.call_order, ["main"])

    async def test_native_sqlite_reopen_keeps_summary_and_only_guard_confirmed_reply(self):
        store_dir = self.root / "guard"
        with patch.object(StarTools, "get_data_dir", return_value=store_dir):
            guard = Phase1Guard(SimpleNamespace(get_config=lambda *a, **k: {}, get_all_stars=lambda: []), {
                "phase3_enabled": True, "phase3_admin_id": "10001", "phase3_platform_id": "fixture",
            })
        self.addAsyncCleanup(guard.terminate)
        session = "fixture:FriendMessage:10001"
        guard.store.claim("fixture", session, "1", "10001", "synthetic original question", [])
        guard.store.create_turn("fixture-turn", "fixture", session, ["1"])
        extras = {"phase3_turn_id": "fixture-turn"}
        event = SimpleNamespace(unified_msg_origin=session,
            get_extra=lambda key, default=None: extras.get(key, default),
            set_extra=lambda key, value: extras.__setitem__(key, value))

        provider = SyntheticSummaryProvider()
        messages = await self.manager(provider).process(
            self.history() + [Message(role="user", content="current request")])
        final = Message(role="assistant", content="confirmed-prefix / unconfirmed-tail")
        messages.append(final)
        run_context = SimpleNamespace(messages=messages)
        await guard.bind_delivery_history(event, run_context, None)
        guard.store.plan_bubbles("fixture-turn", "normal", ["confirmed-prefix", "unconfirmed-tail"],
            ["fixture-first", "fixture-second"], "synthetic-generation-digest")

        async def confirmed():
            return DeliveryReceipt(True, "fixture-ack", "synthetic_transport_ack")

        async def unknown():
            return None

        first = await guard.delivery.send(turn_id="fixture-turn", text="confirmed-prefix", chain=[{"type": "text"}],
            sender=confirmed, delivery_id="fixture-first", part_index=0)
        second = await guard.delivery.send(turn_id="fixture-turn", text="unconfirmed-tail", chain=[{"type": "text"}],
            sender=unknown, delivery_id="fixture-second", part_index=1)
        guard.store.finish_bubble("fixture-turn", "normal", 0, first.state)
        guard.store.finish_bubble("fixture-turn", "normal", 1, second.state)
        self.assertEqual((first.state, second.state), ("sent", "unknown"))
        guard.update_delivery_history(event)
        projected = "".join(part.text for part in final.content)
        self.assertIn("confirmed-prefix", projected)
        self.assertNotIn("unconfirmed-tail", projected)
        self.assertFalse(final._no_save)
        self.assertIn(SYNTHETIC_SUMMARY, messages[1].content)

        db_path = str(self.root / "astrbot.sqlite3")
        database = SQLiteDatabase(db_path)
        try:
            await asyncio.wait_for(database.initialize(), timeout=20)
            native_conv = await asyncio.wait_for(
                database.create_conversation(user_id=session, platform_id="fixture", content=[]), timeout=10)
            conversation_manager = ConversationManager(database)
            conversation = await conversation_manager.get_conversation(session, native_conv.conversation_id)
            request = ProviderRequest(prompt="current request", conversation=conversation)
            stage = InternalAgentSubStage()
            stage.conv_manager = conversation_manager
            await stage._save_to_history(event, request,
                LLMResponse(role="assistant", completion_text="original generated content"),
                messages, None)
        finally:
            await asyncio.wait_for(database.engine.dispose(), timeout=10)

        reopened = SQLiteDatabase(db_path)
        try:
            saved = await asyncio.wait_for(reopened.get_conversation_by_id(native_conv.conversation_id), timeout=10)
            restored = bind_checkpoint_messages(saved.content)
            restored_json = json.dumps(dump_messages_with_checkpoints(restored), ensure_ascii=False)
            self.assertIn(SYNTHETIC_SUMMARY, restored_json)
            self.assertIn("confirmed-prefix", restored_json)
            self.assertNotIn("unconfirmed-tail", restored_json)
            self.assertNotIn("original generated content", restored_json)
            self.assertEqual(restored[0].role, "user")  # native saver omitted leading system
            self.assertEqual(guard.store.snapshot()["inbound"][0]["text"], "synthetic original question")
            self.assertEqual([row["state"] for row in guard.store.phase3_snapshot()["deliveries"]], ["sent", "unknown"])
        finally:
            await asyncio.wait_for(reopened.engine.dispose(), timeout=10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
