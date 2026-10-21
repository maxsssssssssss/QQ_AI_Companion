#!/usr/bin/env python3
"""Bounded direct DeepSeek API checks using only synthetic inputs.

Run only after the account owner has supplied a protected key file and authorized
the small API budget. This does not test AstrBot, QQ, memory, or message delivery.
There are at most eight requests, no retries, and a 60 second hard subprocess
deadline per request. Secret and response data travel only in process memory and
anonymous pipes; the public report contains fixed classifications and booleans.

Verified protocol references (2026-10-20):
https://api-docs.deepseek.com/api/create-chat-completion/
https://api-docs.deepseek.com/guides/tool_calls/
https://api-docs.deepseek.com/guides/vision/
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import stat
import struct
import subprocess
import sys
import urllib.error
import urllib.request
import zlib

ENDPOINT = "https://api.deepseek.com/chat/completions"
REQUEST_LIMIT = 8
DEADLINE_SECONDS = 60
FLASH = "deepseek-flash"
PRO = "deepseek-v4-pro"
MAX_RESPONSE_BYTES = 131072


def read_key(path: Path) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "r", encoding="utf-8") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 4096:
            raise ValueError("unsafe_secret_file")
        value = stream.read().strip()
    if not 16 <= len(value) <= 512 or any(not 33 <= ord(c) <= 126 for c in value):
        raise ValueError("invalid_secret_format")
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward Authorization to another endpoint, including a redirect.
        return None


def worker():
    """Internal transport only. stdout is a captured anonymous pipe, never a log."""
    try:
        request = json.loads(sys.stdin.buffer.read(262145))
        key, payload = request["key"], request["payload"]
        opener = urllib.request.build_opener(
            NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
        req = urllib.request.Request(
            ENDPOINT, data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            method="POST")
        try:
            with opener.open(req, timeout=55) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    result = {"http": response.status, "error": "response_too_large"}
                else:
                    try:
                        result = {"http": response.status, "body": json.loads(body)}
                    except (ValueError, UnicodeError):
                        result = {"http": response.status, "error": "invalid_json"}
        except urllib.error.HTTPError as exc:
            # Error bodies can reflect request contents or credentials.
            result = {"http": int(exc.code), "error": "http_error"}
        except (TimeoutError, socket.timeout):
            result = {"http": None, "error": "timeout"}
        except urllib.error.URLError as exc:
            reason = exc.reason
            kind = ("timeout" if isinstance(reason, (TimeoutError, socket.timeout)) else
                    "tls" if isinstance(reason, ssl.SSLError) else
                    "dns" if isinstance(reason, socket.gaierror) else "connection")
            result = {"http": None, "error": kind}
        except ssl.SSLError:
            result = {"http": None, "error": "tls"}
        except (ValueError, UnicodeError):
            result = {"http": None, "error": "invalid_json"}
    except Exception:
        result = {"http": None, "error": "transport_worker_error"}
    # This output is consumed solely by transport(); never expose worker stdout.
    sys.stdout.write(json.dumps(result))


def worker_argv():
    return [sys.executable, str(Path(__file__).resolve()), "--transport-worker"]


def transport(key: str, payload: dict, *, deadline=DEADLINE_SECONDS) -> dict:
    try:
        process = subprocess.run(
            worker_argv(), input=json.dumps({"key": key, "payload": payload}),
            text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=min(deadline, DEADLINE_SECONDS), check=False)
    except subprocess.TimeoutExpired:
        # subprocess.run kills and reaps the worker, including a stalled body read.
        return {"http": None, "error": "hard_timeout"}
    except OSError:
        return {"http": None, "error": "transport_start_failed"}
    if process.returncode:
        return {"http": None, "error": "transport_worker_failed"}
    try:
        result = json.loads(process.stdout)
    except (ValueError, UnicodeError):
        return {"http": None, "error": "transport_protocol_error"}
    if not isinstance(result, dict):
        return {"http": None, "error": "transport_protocol_error"}
    return result


def png_squares(count: int, slots: list[int]) -> bytes:
    """Create a 256px RGB PNG: white background, separate red squares, no text."""
    if not 2 <= count <= 7 or len(slots) != count or len(set(slots)) != count or \
            any(not 0 <= slot <= 8 for slot in slots):
        raise ValueError("invalid_synthetic_image")
    width = height = 256
    rows = [bytearray(b"\xff\xff\xff" * width) for _ in range(height)]
    for slot in slots:
        x0, y0 = 28 + 80 * (slot % 3), 28 + 80 * (slot // 3)
        for y in range(y0, y0 + 32):
            rows[y][3 * x0:3 * (x0 + 32)] = b"\xdc\x20\x20" * 32

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + \
            struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)

    return (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(b"".join(b"\x00" + row for row in rows))) +
            chunk(b"IEND", b""))


def payload(model: str, messages: list, **extra) -> dict:
    return {"model": model, "messages": messages, "thinking": {"type": "disabled"},
            "stream": False, "max_tokens": 128, "temperature": 0, **extra}


def safe_usage(body: dict) -> dict:
    allowed = ("prompt_tokens", "completion_tokens", "total_tokens",
               "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")
    usage = body.get("usage", {})
    if not isinstance(usage, dict):
        return {}
    result = {name: usage[name] for name in allowed
              if isinstance(usage.get(name), int) and not isinstance(usage[name], bool)
              and 0 <= usage[name] <= 10000000}
    detail = usage.get("completion_tokens_details", {})
    if isinstance(detail, dict) and isinstance(detail.get("reasoning_tokens"), int) \
            and not isinstance(detail["reasoning_tokens"], bool):
        result["reasoning_tokens"] = max(0, min(detail["reasoning_tokens"], 10000000))
    return result


def error_class(result: dict) -> str | None:
    code = result.get("http")
    if code == 200 and isinstance(result.get("body"), dict):
        return None
    if code == 200:
        return (result["error"] if result.get("error") in
                {"invalid_json", "response_too_large"} else "invalid_response")
    classifications = {400: "invalid_request", 401: "authentication", 402: "billing",
                       403: "forbidden", 422: "invalid_request", 429: "rate_limit"}
    if code in classifications:
        return classifications[code]
    if isinstance(code, int):
        return "server" if code >= 500 else "unexpected_http"
    allowed = {"timeout", "hard_timeout", "tls", "dns", "connection", "invalid_json",
               "response_too_large", "transport_worker_error", "transport_start_failed",
               "transport_worker_failed", "transport_protocol_error"}
    return result.get("error") if result.get("error") in allowed else "transport_error"


def message(body: dict) -> dict:
    try:
        first = body["choices"][0]
        item = first["message"]
        if not isinstance(item, dict) or item.get("role") != "assistant":
            return {}
        return item
    except (KeyError, IndexError, TypeError):
        return {}


class Suite:
    def __init__(self, key: str, send=transport):
        self.key, self.send = key, send
        self.requests = 0
        self.tests = []
        self.halted = False

    def request(self, name: str, request: dict, *, expected_http=200):
        row = {"test": name, "status": "NOT TESTED", "model": request["model"],
               "http": None, "error": None, "usage": {}}
        self.tests.append(row)
        if self.halted:
            row["error"] = "previous_request_failed"
            return row, {}
        if self.requests >= REQUEST_LIMIT:
            row["error"] = "request_budget_exhausted"
            return row, {}
        self.requests += 1
        result = self.send(self.key, request)
        row["http"] = result.get("http") if isinstance(result.get("http"), int) else None
        body = result.get("body", {})
        body = body if isinstance(body, dict) else {}
        row["usage"] = safe_usage(body)
        row["model_matches_requested"] = body.get("model") == request["model"]
        row["error"] = error_class(result)
        if row["http"] != expected_http or (expected_http == 200 and row["error"]):
            row["status"] = "FAIL"
            self.halted = True
        else:
            row["status"] = "PASS"
            if expected_http == 200:
                row["error"] = None
        return row, body

    @staticmethod
    def evaluate(row, condition, field):
        row[field] = bool(condition)
        if row["status"] == "PASS" and not condition:
            row["status"] = "FAIL"
            row["error"] = "semantic_check_failed"

    def run(self):
        row, body = self.request("flash_chinese", payload(FLASH, [
            {"role": "user", "content": "请用一句简短自然的中文向我问好。"}]))
        self.evaluate(row, bool(re.search(r"[\u4e00-\u9fff]", str(message(body).get("content", "")))),
                      "chinese_response")

        nonce = secrets.token_hex(8)
        history = [{"role": "user", "content": f"记住这个测试标记：{nonce}。只回复：已记住。"}]
        row, body = self.request("context_first_turn", payload(FLASH, history))
        content = message(body).get("content", "")
        self.evaluate(row, isinstance(content, str) and "已记住" in content, "acknowledged")
        if row["status"] == "PASS":
            history = history + [{"role": "assistant", "content": content},
                                 {"role": "user", "content": "刚才的测试标记是什么？只输出标记本身。"}]
            row, body = self.request("context_second_turn", payload(FLASH, history))
            self.evaluate(row, str(message(body).get("content", "")).strip() == nonce, "nonce_match")
        else:
            self.tests.append({"test": "context_second_turn", "status": "NOT TESTED",
                               "model": FLASH, "error": "context_setup_failed"})

        row, body = self.request("pro_short_response", payload(PRO, [
            {"role": "user", "content": "只用中文回复两个字：你好。"}]))
        self.evaluate(row, "你好" in str(message(body).get("content", "")), "chinese_response")

        tool_nonce, result_nonce = secrets.token_hex(8), secrets.token_hex(8)
        tools = [{"type": "function", "function": {
            "name": "validation_nonce",
            "description": "An inert local validation function. It returns a synthetic result.",
            "parameters": {"type": "object", "properties": {"value": {"type": "string"}},
                           "required": ["value"], "additionalProperties": False}}}]
        history = [{"role": "user", "content": (
            f"请调用validation_nonce，value必须是{tool_nonce}。"
            "收到工具结果后，只输出工具返回的result值，不要其他内容。")}]
        row, body = self.request("tool_request", payload(
            FLASH, history, tools=tools,
            tool_choice={"type": "function", "function": {"name": "validation_nonce"}}))
        assistant = message(body)
        calls = assistant.get("tool_calls", [])
        calls = calls if isinstance(calls, list) else []
        valid = False
        try:
            call = calls[0]
            arguments = json.loads(call["function"]["arguments"])
            valid = (len(calls) == 1 and call.get("type") == "function" and
                     call["function"]["name"] == "validation_nonce" and
                     arguments == {"value": tool_nonce} and
                     isinstance(call["id"], str) and 0 < len(call["id"]) < 256)
        except (IndexError, KeyError, TypeError, ValueError, AttributeError):
            pass
        self.evaluate(row, valid, "nonce_match")
        if row["status"] == "PASS":
            # No model-requested executable code or external tool is ever run.
            assistant = {"role": "assistant", "content": assistant.get("content"),
                         "tool_calls": calls}
            history += [assistant, {"role": "tool", "tool_call_id": calls[0]["id"],
                                    "content": json.dumps({"result": result_nonce})}]
            row, body = self.request("tool_result", payload(
                FLASH, history, tools=tools, tool_choice="none"))
            self.evaluate(row, str(message(body).get("content", "")).strip() == result_nonce,
                          "nonce_match")
        else:
            self.tests.append({"test": "tool_result", "status": "NOT TESTED",
                               "model": FLASH, "error": "tool_setup_failed"})

        count = secrets.randbelow(6) + 2
        positions = secrets.SystemRandom().sample(list(range(9)), count)
        image = "data:image/png;base64," + base64.b64encode(png_squares(count, positions)).decode()
        row, body = self.request("flash_synthetic_vision", payload(FLASH, [
            {"role": "user", "content": [
                {"type": "text", "text": "图片里有几个红色正方形？只输出一个阿拉伯数字。"},
                {"type": "image_url", "image_url": {"url": image, "detail": "low"}}]}]))
        self.evaluate(row, str(message(body).get("content", "")).strip() == str(count),
                      "image_truth_match")

        # The documented messages array requires at least one item.
        self.request("invalid_empty_messages", payload(FLASH, []), expected_http=400)
        return {
            "scope": "direct_deepseek_api_only", "qq_end_to_end": "NOT TESTED",
            "status": "PASS" if all(row["status"] == "PASS" for row in self.tests) else "FAIL",
            "requests_sent": self.requests, "request_limit": REQUEST_LIMIT,
            "per_request_deadline_seconds": DEADLINE_SECONDS, "automatic_retries": 0,
            "response_bodies_logged": False, "tests": self.tests,
        }


def save_report(path: Path, report: dict):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--secret-file", type=Path)
    parser.add_argument("--execute", action="store_true",
                        help="Explicitly execute at most eight small, billable API requests")
    parser.add_argument("--output", type=Path, help="Create a new sanitized JSON report, mode 600")
    args = parser.parse_args()
    os.umask(0o077)
    if args.output and (args.output.exists() or args.output.is_symlink()):
        print(json.dumps({"status": "BLOCKED", "requests_sent": 0,
                          "reason": "report_destination_already_exists"}))
        return 1
    if not args.execute:
        report = {"status": "NOT TESTED", "scope": "direct_deepseek_api_only",
                  "requests_sent": 0, "reason": "explicit_execution_required"}
    else:
        try:
            if args.secret_file is None:
                raise ValueError("missing_secret_file")
            key = read_key(args.secret_file)
            report = Suite(key).run()
        except Exception as exc:
            # Never print exception text: it could contain a request or response.
            report = {"status": "BLOCKED", "scope": "direct_deepseek_api_only",
                      "error_type": type(exc).__name__, "detail": "local_validation_failed"}
    if args.output:
        try:
            save_report(args.output, report)
        except Exception as exc:
            report["report_save_status"] = "FAIL"
            report["report_save_error_type"] = type(exc).__name__
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] in {"PASS", "NOT TESTED"} else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--transport-worker"]:
        worker()
    else:
        raise SystemExit(main())
