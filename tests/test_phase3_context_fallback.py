"""Pinned native context fallback, synthetic providers/media and no network."""
import asyncio
import copy
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from integrations.phase3_runtime.context_compression import (
    FailureRoundFallback, install_for_run,
)

try:
    import astrbot
    from astrbot.core.agent.context.compressor import LLMSummaryCompressor
    from astrbot.core.agent.context.config import ContextConfig
    from astrbot.core.agent.context.manager import ContextManager
    from astrbot.core.agent.context.round_utils import split_into_rounds
    from astrbot.core.agent.context.token_counter import EstimateTokenCounter
    from astrbot.core.agent.message import ImageURLPart, Message, TextPart
    from astrbot.core.pipeline.process_stage.follow_up import (
        _ACTIVE_AGENT_RUNNERS, register_active_runner, unregister_active_runner,
    )
    from integrations.phase3_runtime.main import Phase3Runtime
    NATIVE = True
except ImportError:
    NATIVE = False


PNG_URI = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jQ1sAAAAASUVORK5CYII="
)


class SyntheticProvider:
    def __init__(self, mode="success"):
        self.mode, self.calls = mode, []
        self.provider_config = {"modalities": ["text", "image", "tool_use"]}

    async def text_chat(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        if self.mode == "failed":
            raise TimeoutError("synthetic timeout")
        if self.mode == "cancelled":
            raise asyncio.CancelledError()
        return NS(completion_text="" if self.mode == "empty" else "Synthetic summary.")


class SyntheticEvent:
    def __init__(self, umo="synthetic:FriendMessage:10001"):
        self.unified_msg_origin = umo


@unittest.skipUnless(NATIVE, "pinned AstrBot and locked dependency environment required")
class ContextFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.network = patch("socket.socket.connect", side_effect=AssertionError("network forbidden"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def history(self, count=100, chars=5):
        rows = [Message(role="system", content="Synthetic persona.")]
        for index in range(count):
            rows.extend([Message(role="user", content=f"u{index}:" + "甲" * chars),
                         Message(role="assistant", content=f"a{index}:" + "乙" * chars)])
        return rows

    def manager(self, provider=None, **kwargs):
        values = dict(max_context_tokens=128000, enforce_max_turns=-1,
                      llm_compress_provider=provider or SyntheticProvider(),
                      llm_compress_keep_recent_ratio=.15)
        values.update(kwargs)
        return ContextManager(ContextConfig(**values))

    def registered(self, manager, event=None):
        event = event or SyntheticEvent()
        context = NS(context=NS(event=event), messages=[])
        runner = NS(run_context=context, request_context_manager=manager, request_stop=lambda: None)
        register_active_runner(event.unified_msg_origin, runner)
        self.addCleanup(unregister_active_runner, event.unified_msg_origin, runner)
        return event, context, runner

    def round_count(self, messages):
        index = next((i for i, msg in enumerate(messages) if msg.role != "system"), len(messages))
        return len(split_into_rounds(messages[index:]))

    async def test_no_threshold_means_no_summary_or_eighty_turn_cap(self):
        provider = SyntheticProvider("failed")
        manager = self.manager(provider)
        event, ctx, _ = self.registered(manager)
        self.assertTrue(install_for_run(event, ctx, 80))
        messages = self.history(120)
        self.assertIs(await manager.process(messages), messages)
        self.assertEqual(self.round_count(messages), 120)
        self.assertEqual(provider.calls, [])

    async def test_successful_summary_does_not_receive_eighty_turn_limit(self):
        provider = SyntheticProvider()
        # 15% of 600 equally small rounds leaves more than 80 recent rounds.
        native = LLMSummaryCompressor(provider, keep_recent_ratio=.15)
        wrapped = FailureRoundFallback(native, 80)
        messages = self.history(600, 12)
        result = await wrapped(messages)
        self.assertEqual(len(provider.calls), 1)
        self.assertGreater(self.round_count(result), 80)
        self.assertIn("Synthetic summary.", result[1].content)
        self.assertIs(result[0], messages[0])

    async def test_failed_or_empty_summary_retains_eighty_complete_rounds(self):
        for mode in ("failed", "empty"):
            with self.subTest(mode=mode):
                provider = SyntheticProvider(mode)
                manager = self.manager(provider)
                event, ctx, _ = self.registered(manager)
                self.assertTrue(install_for_run(event, ctx, 80))
                messages = self.history(100)
                before = copy.deepcopy(messages)
                result = await manager.process(messages, trusted_token_usage=110000)
                self.assertEqual(len(provider.calls), 1)
                self.assertEqual(provider.calls[0]["request_max_retries"], 1)
                self.assertEqual(self.round_count(result), 80)
                self.assertEqual(result, messages[:1] + messages[-160:])
                self.assertEqual(messages, before)
                self.assertIs(result[-1], messages[-1])

    async def test_native_no_summary_no_work_and_small_histories_stay_exact(self):
        native = LLMSummaryCompressor(SyntheticProvider("failed"))
        wrapped = FailureRoundFallback(native, 80)
        one = [Message(role="system", content="Synthetic."), Message(role="user", content="Current.")]
        self.assertIs(await wrapped(one), one)
        self.assertEqual(native.provider.calls, [])
        short = self.history(20)
        self.assertIs(await wrapped(short), short)

    async def test_eighty_rounds_still_over_budget_use_native_token_truncation(self):
        provider = SyntheticProvider("empty")
        manager = self.manager(provider, max_context_tokens=1000)
        event, ctx, _ = self.registered(manager)
        self.assertTrue(install_for_run(event, ctx, 80))
        messages = self.history(100, 100)
        result = await manager.process(messages)
        self.assertEqual(len(provider.calls), 1)
        self.assertLess(self.round_count(result), 80)
        self.assertLessEqual(EstimateTokenCounter().count_tokens(result), 820)
        self.assertIs(result[-1], messages[-1])

    async def test_tool_calls_results_and_images_are_retained_as_whole_rounds(self):
        provider = SyntheticProvider("empty")
        wrapped = FailureRoundFallback(LLMSummaryCompressor(provider), 80)
        messages = self.history(21)
        special = [
            Message(role="user", content=[TextPart(text="Synthetic image question"),
                ImageURLPart(image_url=ImageURLPart.ImageURL(url=PNG_URI))]),
            Message(role="assistant", content=None, tool_calls=[{
                "id":"fixture-call", "type":"function",
                "function":{"name":"synthetic_tool", "arguments":"{}"}}]),
            Message(role="tool", content="Synthetic result.", tool_call_id="fixture-call"),
            Message(role="assistant", content="Synthetic reply."),
        ]
        messages += special + self.history(79)[1:]
        result = await wrapped(messages)
        self.assertEqual(self.round_count(result), 80)
        self.assertEqual(result[1:5], special)
        self.assertTrue(all(a is b for a, b in zip(result[1:5], special)))
        self.assertEqual(result[1].content[1].image_url.url, PNG_URI)
        self.assertEqual(result[3].tool_call_id, "fixture-call")

    async def test_cancellation_propagates_without_round_fallback(self):
        provider = SyntheticProvider("cancelled")
        manager = self.manager(provider)
        event, ctx, _ = self.registered(manager)
        self.assertTrue(install_for_run(event, ctx, 80))
        messages = self.history()
        before = copy.deepcopy(messages)
        with self.assertRaises(asyncio.CancelledError):
            await manager.process(messages, trusted_token_usage=110000)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(messages, before)

    async def test_unexpected_compressor_exception_also_gets_bounded_fallback(self):
        class BrokenCompressor(LLMSummaryCompressor):
            async def __call__(self, messages):
                raise ValueError("synthetic failure before native request")
        messages = self.history()
        result = await FailureRoundFallback(BrokenCompressor(SyntheticProvider()), 80)(messages)
        self.assertEqual(self.round_count(result), 80)

    async def test_registry_event_and_run_context_must_match_exactly(self):
        manager = self.manager()
        event, context, runner = self.registered(manager)
        native = manager.compressor
        self.assertFalse(install_for_run(SyntheticEvent(event.unified_msg_origin), context, 80))
        self.assertFalse(install_for_run(event, NS(context=NS(event=event)), 80))
        unregister_active_runner(event.unified_msg_origin, runner)
        self.assertFalse(install_for_run(event, context, 80))
        self.assertIs(manager.compressor, native)
        register_active_runner(event.unified_msg_origin, runner)
        self.assertTrue(install_for_run(event, context, 80))
        self.assertIs(manager.compressor.native, native)

    async def test_install_is_idempotent_and_disable_restores_same_native_instance(self):
        manager = self.manager()
        event, context, _ = self.registered(manager)
        original = manager.compressor
        self.assertFalse(install_for_run(event, context, 0))
        self.assertIs(manager.compressor, original)
        self.assertTrue(install_for_run(event, context, 80))
        first = manager.compressor
        self.assertTrue(install_for_run(event, context, 80))
        self.assertIs(manager.compressor, first)
        self.assertFalse(install_for_run(event, context, 0))
        self.assertIs(manager.compressor, original)

    async def test_other_strategies_versions_and_invalid_limits_are_untouched(self):
        for changes in ({"enforce_max_turns":20}, {"llm_compress_provider":None}):
            manager = self.manager(**changes)
            event, context, _ = self.registered(manager)
            original = manager.compressor
            self.assertFalse(install_for_run(event, context, 80))
            self.assertIs(manager.compressor, original)
        manager = self.manager()
        event, context, _ = self.registered(manager)
        original = manager.compressor
        for limit in (-1, True, 201, "80", None):
            self.assertFalse(install_for_run(event, context, limit))
        with patch.object(astrbot, "__version__", "different-version"):
            self.assertFalse(install_for_run(event, context, 80))
        self.assertIs(manager.compressor, original)

    async def test_runtime_begin_hook_installs_after_registry_registration(self):
        manager = self.manager()
        event, context, _ = self.registered(manager)
        guard = NS(turn_is_current=Mock(return_value=True), trace=Mock())
        runtime = NS(settings={"context_compression_fallback_turns":80}, ready=True, guard=guard)
        await Phase3Runtime.prepare_context_fallback(runtime, event, context)
        self.assertIsInstance(manager.compressor, FailureRoundFallback)
        self.assertEqual(manager.compressor.limit, 80)
        guard.trace.assert_called_once_with(event, "model_request", {
            "context_compression":{"fallback_turns":80, "installed":True}})

    async def test_runtime_begin_hook_disabled_unready_or_stale_never_installs(self):
        for limit, ready, current in ((0, True, True), (80, False, True), (80, True, False)):
            with self.subTest(limit=limit, ready=ready, current=current):
                manager = self.manager()
                event, context, _ = self.registered(manager)
                native = manager.compressor
                guard = NS(turn_is_current=Mock(return_value=current), trace=Mock())
                runtime = NS(settings={"context_compression_fallback_turns":limit},
                             ready=ready, guard=guard)
                await Phase3Runtime.prepare_context_fallback(runtime, event, context)
                self.assertIs(manager.compressor, native)
                guard.trace.assert_not_called()


if __name__ == "__main__":
    unittest.main()
