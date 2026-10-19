"""Call unchanged native DeepSeek tools with synthetic config input, offline.

This verifies configuration/input guards, not a successful DeepSeek request.
No core methods, HTTP clients or return values are patched.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest

from astrbot.core.tools.web_search_tools import (
    DeepSeekFetchWebPageTool,
    DeepSeekWebSearchTool,
)


OBSERVATIONS = []


class ConfigInput:
    """Minimal SDK input fixture; the tools under test are real upstream code."""

    def __init__(self, keys):
        self.config = {"provider_settings": {"websearch_deepseek_key": keys}}

    def get_config(self, *, umo):
        assert umo == "offline:FriendMessage:10001"
        return self.config


def scope(keys):
    return SimpleNamespace(context=SimpleNamespace(
        context=ConfigInput(keys),
        event=SimpleNamespace(unified_msg_origin="offline:FriendMessage:10001"),
    ))


class NativeDeepSeekGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_missing_key(self):
        result = await asyncio.wait_for(
            DeepSeekWebSearchTool().call(scope([]), query="Synthetic public query"), 2)
        self.assertEqual(result, "Error: DeepSeek API key is not configured in AstrBot.")
        OBSERVATIONS.append({"case": "search_missing_key", "result": result})

    async def test_fetch_missing_key(self):
        result = await asyncio.wait_for(
            DeepSeekFetchWebPageTool().call(scope([]), url="https://example.com/"), 2)
        self.assertEqual(result, "Error: DeepSeek API key is not configured in AstrBot.")
        OBSERVATIONS.append({"case": "fetch_missing_key", "result": result})

    async def test_search_rejects_invalid_input_before_network(self):
        result = await asyncio.wait_for(
            DeepSeekWebSearchTool().call(scope(["synthetic-not-a-real-key"]), query=" "), 2)
        self.assertIn("query must be", result)
        OBSERVATIONS.append({"case": "search_invalid_query", "result": result})

    async def test_fetch_rejects_embedded_credentials_before_network(self):
        result = await asyncio.wait_for(
            DeepSeekFetchWebPageTool().call(
                scope(["synthetic-not-a-real-key"]), url="https://synthetic:fixture@example.com/"), 2)
        self.assertIn("no embedded credentials", result)
        OBSERVATIONS.append({"case": "fetch_invalid_url", "result": result})


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(NativeDeepSeekGuardTests))
    evidence = {"upstream": "AstrBot v4.29.1",
                "commit": "dffe922773725e9c178392c423676ad65044748c",
                "tests_run": result.testsRun, "tests_passed": result.wasSuccessful(),
                "scope": "native missing-key and input guards only; synthetic scope wrapper; no successful API request",
                "real_deepseek_api": "NOT_RUN", "observations": OBSERVATIONS}
    if destination := os.environ.get("QQ_DEEPSEEK_EVIDENCE"):
        Path(destination).write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    raise SystemExit(0 if result.wasSuccessful() else 1)
