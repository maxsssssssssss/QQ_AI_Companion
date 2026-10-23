import asyncio
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest

from integrations.phase3_memory.summary_provider import (
    NATIVE_SYSTEM, MARKER, SummaryProvider, install,
)


class Provider:
    id = "same-native-provider"
    def __init__(self): self.calls = []
    async def text_chat(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return NS(completion_text="native response")


class SummaryProviderTests(unittest.IsolatedAsyncioTestCase):
    def request(self):
        return dict(prompt="请把下面这一段时间内的消息整理成本插件自己的长期记忆。synthetic",
                    system_prompt=NATIVE_SYSTEM, request_max_retries=0)

    async def test_one_native_call_preserves_protocol_and_response(self):
        native = Provider()
        original = self.request()
        response = await SummaryProvider(native).text_chat(**original)
        self.assertEqual(response.completion_text, "native response")
        self.assertEqual(len(native.calls), 1)
        args, got = native.calls[0]
        self.assertFalse(args)
        self.assertEqual(got["prompt"], original["prompt"])
        self.assertEqual(got["request_max_retries"], 0)
        self.assertEqual(got["system_prompt"].count(MARKER), 1)
        self.assertTrue(got["system_prompt"].startswith(NATIVE_SYSTEM))
        self.assertEqual(original["system_prompt"], NATIVE_SYSTEM)

    async def test_other_native_operations_and_user_text_unchanged(self):
        native = Provider()
        requests = [dict(prompt=MARKER, system_prompt="chat persona"),
                    dict(self.request(), system_prompt="another extractor"),
                    dict(self.request(), func_tool="native-tools")]
        wrapped = SummaryProvider(native)
        for req in requests: await wrapped.text_chat(**req)
        self.assertEqual([k for _, k in native.calls], requests)
        self.assertEqual(wrapped.id, native.id)

    async def test_service_local_async_getter_and_nested_restore(self):
        native = Provider()
        async def getter(_id): return native
        context = NS(get_provider_by_id=getter, get_using_provider=lambda _sid: native, marker="unchanged")
        service = NS(context=context)
        one, two = install(service), install(service)
        self.assertIs(await context.get_provider_by_id("id"), native)
        wrapped = await service.context.get_provider_by_id("id")
        self.assertEqual(service.context.marker, "unchanged")
        await wrapped.text_chat(**self.request())
        self.assertIn(MARKER, native.calls[0][1]["system_prompt"])
        one.restore(); one.restore()
        self.assertIsNot(service.context, context)
        two.restore()
        self.assertIs(service.context, context)

    async def test_actual_pinned_summarizer_signature_and_call(self):
        upstream = Path(__file__).resolve().parents[1] / ".runtime/upstream/memory_companion"
        if not upstream.is_dir(): self.skipTest("pinned native source required")
        package = ModuleType("summary_constraint_native")
        package.__path__ = [str(upstream)]
        sys.modules[package.__name__] = package
        module = importlib.import_module(package.__name__ + ".core.summarizer")
        Summary = module.MemorySummarizer
        summary = Summary()
        self.assertEqual(summary._system_prompt(), NATIVE_SYSTEM)
        native = Provider()
        with self.assertRaises(module.SummaryFormatError):
            await summary.summarize_with_provider(SummaryProvider(native), rows=[
                {"id":"synthetic-ref", "event_type":"user_message", "subject_id":"synthetic-user",
                 "content":"我喜欢喝清茶。", "occurred_at":"2026-10-21T12:00:00+00:00", "scope":"private"}
            ], session_label="synthetic")
        self.assertEqual(len(native.calls), 1)
        self.assertIn(MARKER, native.calls[0][1]["system_prompt"])


if __name__ == "__main__": unittest.main()
