"""Direct API runner safety tests; every network transport is replaced locally."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zlib

PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "phase2_test_deepseek", PROJECT / "scripts/phase2-test-deepseek.py")
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)
SYNTHETIC_KEY = "synthetic-key-not-for-real-requests"


def image_red_square_count(image: bytes) -> int:
    if not image.startswith(b"\x89PNG\r\n\x1a\n"):
        raise AssertionError("Not a PNG")
    offset, compressed = 8, b""
    while offset < len(image):
        length = struct.unpack(">I", image[offset:offset + 4])[0]
        kind, data = image[offset + 4:offset + 8], image[offset + 8:offset + 8 + length]
        crc = struct.unpack(">I", image[offset + 8 + length:offset + 12 + length])[0]
        if crc != zlib.crc32(kind + data) & 0xffffffff:
            raise AssertionError("PNG checksum mismatch")
        if kind == b"IDAT":
            compressed += data
        offset += 12 + length
    raw = zlib.decompress(compressed)
    if len(raw) != 256 * (1 + 256 * 3):
        raise AssertionError("Unexpected image dimensions")
    count, remainder = divmod(raw.count(b"\xdc\x20\x20"), 32 * 32)
    if remainder:
        raise AssertionError("Not separate complete squares")
    return count


class FakeTransport:
    def __init__(self):
        self.requests = []

    def __call__(self, key, request):
        self.requests.append(json.loads(json.dumps(request)))
        number = len(self.requests)
        assistant = {"role": "assistant", "content": "你好"}
        if number == 2:
            assistant["content"] = "已记住"
        elif number == 3:
            assistant["content"] = re.search(r"[a-f0-9]{16}", request["messages"][0]["content"])[0]
        elif number == 5:
            nonce = re.search(r"[a-f0-9]{16}", request["messages"][0]["content"])[0]
            assistant = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "synthetic_call", "type": "function",
                "function": {"name": "validation_nonce",
                             "arguments": json.dumps({"value": nonce})}}]}
        elif number == 6:
            assistant["content"] = json.loads(request["messages"][-1]["content"])["result"]
        elif number == 7:
            url = request["messages"][0]["content"][1]["image_url"]["url"]
            assistant["content"] = str(image_red_square_count(base64.b64decode(url.split(",", 1)[1])))
        elif number == 8:
            return {"http": 400, "body": {
                "error": key, "usage": {"total_tokens": key, "Authorization": key},
                "model": key}}
        return {"http": 200, "body": {
            "model": request["model"], "choices": [{"message": assistant, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18,
                      "untrusted_reflected_value": key}}}


class DeepSeekRunnerTests(unittest.TestCase):
    def test_eight_request_protocol_and_sanitized_results(self):
        fake = FakeTransport()
        report = api.Suite(SYNTHETIC_KEY, send=fake).run()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["requests_sent"], 8)
        self.assertEqual(len(fake.requests), 8)
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn(SYNTHETIC_KEY, serialized)
        self.assertNotIn("你好", serialized)
        self.assertNotIn("data:image", serialized)
        for request in fake.requests:
            self.assertEqual(request["thinking"], {"type": "disabled"})
            self.assertFalse(request["stream"])
            self.assertLessEqual(request["max_tokens"], 128)
        self.assertEqual(fake.requests[-1]["messages"], [])
        self.assertEqual(report["qq_end_to_end"], "NOT TESTED")

    def test_tool_result_is_unpredictable_from_first_request(self):
        fake = FakeTransport()
        api.Suite(SYNTHETIC_KEY, send=fake).run()
        first, second = fake.requests[4], fake.requests[5]
        result_nonce = json.loads(second["messages"][-1]["content"])["result"]
        self.assertNotIn(result_nonce, json.dumps(first))
        self.assertEqual(first["tool_choice"]["function"]["name"], "validation_nonce")
        self.assertEqual(second["tool_choice"], "none")
        self.assertEqual(second["messages"][-1]["role"], "tool")

    def test_generated_image_encodes_count_without_text_hint(self):
        for count in range(2, 8):
            self.assertEqual(image_red_square_count(api.png_squares(count, list(range(count)))), count)
        fake = FakeTransport()
        api.Suite(SYNTHETIC_KEY, send=fake).run()
        prompt = fake.requests[6]["messages"][0]["content"][0]["text"]
        self.assertIsNone(re.search(r"[0-9]", prompt))
        self.assertNotIn("Pro", fake.requests[6]["model"])

    def test_auth_failure_stops_without_retry(self):
        calls = []

        def unauthorized(key, request):
            calls.append(request)
            return {"http": 401, "body": {"error": SYNTHETIC_KEY}}

        report = api.Suite(SYNTHETIC_KEY, send=unauthorized).run()
        self.assertEqual(len(calls), 1)
        self.assertEqual(report["requests_sent"], 1)
        self.assertEqual(report["tests"][0]["error"], "authentication")
        self.assertTrue(all(row["status"] == "NOT TESTED" for row in report["tests"][1:]))
        self.assertNotIn(SYNTHETIC_KEY, json.dumps(report))

    def test_budget_exhaustion_never_calls_transport(self):
        fake = FakeTransport()
        suite = api.Suite(SYNTHETIC_KEY, send=fake)
        suite.requests = 8
        row, _ = suite.request("extra", api.payload(api.FLASH, []))
        self.assertEqual(row["status"], "NOT TESTED")
        self.assertEqual(fake.requests, [])

    def test_key_uses_stdin_not_argv_or_environment(self):
        def fake_run(argv, **kwargs):
            self.assertNotIn(SYNTHETIC_KEY, repr(argv))
            self.assertNotIn("env", kwargs)
            self.assertEqual(json.loads(kwargs["input"])["key"], SYNTHETIC_KEY)
            self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
            self.assertEqual(kwargs["timeout"], 60)
            return subprocess.CompletedProcess(argv, 0, '{"http":401,"error":"http_error"}')

        with patch.object(api.subprocess, "run", side_effect=fake_run):
            self.assertEqual(api.transport(SYNTHETIC_KEY, {})["http"], 401)

    def test_actual_stalled_process_is_killed_by_hard_deadline(self):
        started = time.monotonic()
        with patch.object(api, "worker_argv", return_value=[
            sys.executable, "-c", "import time; time.sleep(5)"]):
            result = api.transport(SYNTHETIC_KEY, {}, deadline=0.05)
        self.assertEqual(result["error"], "hard_timeout")
        self.assertLess(time.monotonic() - started, 2)

    def test_secret_permissions_symlink_and_controls_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "key"
            path.write_text(SYNTHETIC_KEY)
            path.chmod(0o600)
            self.assertEqual(api.read_key(path), SYNTHETIC_KEY)
            link = Path(directory) / "link"
            link.symlink_to(path)
            with self.assertRaises(OSError):
                api.read_key(link)
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                api.read_key(path)
            path.chmod(0o600)
            path.write_text(SYNTHETIC_KEY + "\0")
            with self.assertRaises(ValueError):
                api.read_key(path)

    def test_redirects_never_forward_authorization(self):
        self.assertIsNone(api.NoRedirect().redirect_request(
            None, None, 302, "", {}, "https://unexpected.invalid"))

    def test_bad_success_response_is_not_counted_as_api_success(self):
        self.assertEqual(api.error_class({"http": 200, "error": "invalid_json"}), "invalid_json")
        self.assertEqual(api.error_class({"http": 200, "body": []}), "invalid_response")
        report = api.Suite(SYNTHETIC_KEY, send=lambda *_: {
            "http": 200, "error": "invalid_json"}).run()
        self.assertEqual(report["requests_sent"], 1)
        self.assertEqual(report["status"], "FAIL")

    def test_cli_requires_execution_and_preserves_existing_reports(self):
        command = [sys.executable, str(PROJECT / "scripts/phase2-test-deepseek.py")]
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["requests_sent"], 0)
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            report.write_text("original evidence")
            result = subprocess.run(command + ["--execute", "--output", str(report)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout)["requests_sent"], 0)
            self.assertEqual(report.read_text(), "original evidence")


if __name__ == "__main__":
    unittest.main()
