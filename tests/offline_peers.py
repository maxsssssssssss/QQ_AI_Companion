"""Synthetic peers on real HTTP and OneBot v11 reverse-WebSocket transports."""
import asyncio
import json
import time
import uuid
import itertools
from pathlib import Path

from aiohttp import ClientSession, web

TOKEN = "offline-fixture-token-not-a-secret"
KEY = "offline-fixture-key-not-a-secret"
MESSAGE_IDS = itertools.count(1000)


def append_event(path, kind, payload):
    with Path(path).open("a") as f:
        f.write(json.dumps({"time": time.time(), "kind": kind, **payload}, ensure_ascii=False) + "\n")


def action_text(action):
    message = action["params"].get("message", "")
    if isinstance(message, str):
        return message
    return "".join(seg["data"].get("text", "") for seg in message if seg["type"] == "text")


class MockLLM:
    def __init__(self, evidence):
        self.evidence = evidence
        self.requests = []
        self.runner = None

    async def start(self):
        app = web.Application()
        app.router.add_post("/v1/chat/completions", self.complete)
        app.router.add_get("/v1/models", self.models)
        app.router.add_get("/fixture.png", self.fixture_image)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", 18080).start()

    async def models(self, request):
        return web.json_response({"object": "list", "data": [{"id": "phase1-mock", "object": "model", "owned_by": "offline"}]})

    async def fixture_image(self, request):
        from io import BytesIO
        from PIL import Image
        buffer = BytesIO()
        Image.new("RGB", (2, 2), "blue").save(buffer, format="PNG")
        return web.Response(body=buffer.getvalue(), content_type="image/png")

    async def complete(self, request):
        if request.headers.get("Authorization") != f"Bearer {KEY}":
            return web.json_response({"error": {"message": "fixture authentication failed"}}, status=401)
        body = await request.json()
        users = [m.get("content", "") for m in body.get("messages", []) if m.get("role") == "user"]
        latest = str(users[-1]) if users else ""
        if "PHASE1_FORCE_503" in latest:
            result = {"request": body, "status": 503, "response": None}
            self.requests.append(result)
            append_event(self.evidence, "llm", result)
            return web.json_response({"error": {"message": "intentional offline outage", "type": "server_error"}}, status=503)
        if "PHASE1_FORCE_TIMEOUT" in latest or "PHASE1_FORCE_INVALID" in latest:
            mode = "timeout" if "PHASE1_FORCE_TIMEOUT" in latest else "invalid"
            result = {"request": body, "status": 200, "response": None, "fault": mode}
            self.requests.append(result)
            append_event(self.evidence, "llm", result)
            if mode == "timeout":
                await asyncio.sleep(8)
            return web.json_response({"invalid_fixture_response": True})
        reply = f"MOCK_REPLY_{uuid.uuid4().hex[:12]}: {latest}"
        response = {"id": "chatcmpl-" + uuid.uuid4().hex, "object": "chat.completion", "created": int(time.time()), "model": "phase1-mock", "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}
        result = {"request": body, "status": 200, "response": response}
        self.requests.append(result)
        append_event(self.evidence, "llm", result)
        return web.json_response(response)

    async def close(self):
        if self.runner:
            await self.runner.cleanup()


class OneBotPeer:
    def __init__(self, evidence):
        self.evidence = evidence
        self.session = None
        self.ws = None
        self.task = None
        self.actions = []
        self.outgoing = asyncio.Queue()
        self.message_id = 1000
        self.last_event = None
        self.next_send_mode = "ok"
        self.deliveries = []

    async def connect(self):
        self.session = ClientSession()
        try:
            self.ws = await self.session.ws_connect("http://127.0.0.1:6199/ws", headers={"Authorization": f"Bearer {TOKEN}", "X-Self-ID": "90000", "X-Client-Role": "Universal"})
        except BaseException:
            await self.session.close()
            raise
        self.task = asyncio.create_task(self.receive())
        await self.ws.send_json({"time": int(time.time()), "self_id": 90000, "post_type": "meta_event", "meta_event_type": "lifecycle", "sub_type": "connect"})

    async def receive(self):
        async for frame in self.ws:
            if frame.type != web.WSMsgType.TEXT:
                continue
            action = json.loads(frame.data)
            self.actions.append(action)
            append_event(self.evidence, "onebot_action", {"action": action})
            name = action.get("action", "")
            if name in ("send_msg", "send_private_msg", "send_group_msg"):
                mode, self.next_send_mode = self.next_send_mode, "ok"
                self.deliveries.append({"action": action, "status": mode})
                append_event(self.evidence, "onebot_delivery", {"action": action, "status": mode})
                await self.outgoing.put(action)
                if mode == "timeout":
                    continue
                if mode == "failed":
                    await self.ws.send_json({"status": "failed", "retcode": 100, "data": None, "wording": "synthetic send failure", "echo": action.get("echo")})
                    continue
                data = {"message_id": self.message_id + 10000}
            elif name == "get_login_info":
                data = {"user_id": 90000, "nickname": "OfflineBot"}
            elif name in ("get_stranger_info", "get_group_member_info"):
                data = {"user_id": action.get("params", {}).get("user_id", 10001), "nickname": "SyntheticUser", "card": "", "role": "member"}
            elif name in ("get_group_list", "get_friend_list", "get_group_member_list"):
                data = []
            else:
                data = {}
            await self.ws.send_json({"status": "ok", "retcode": 0, "data": data, "echo": action.get("echo")})

    async def emit(self, text, user=10001, group=None, segments=None):
        self.message_id = next(MESSAGE_IDS)
        event = {"time": int(time.time()), "self_id": 90000, "post_type": "message", "message_type": "group" if group else "private", "sub_type": "normal" if group else "friend", "message_id": self.message_id, "user_id": user, "message": [{"type": "text", "data": {"text": text}}], "raw_message": text, "font": 0, "sender": {"user_id": user, "nickname": f"Synthetic{user}", "card": "", "role": "member"}}
        if group:
            event["group_id"] = group
        if segments is not None:
            event["message"] = segments
        self.last_event = event
        append_event(self.evidence, "onebot_event", {"event": event})
        await self.ws.send_json(event)
        return event

    async def send(self, text, user=10001, group=None, timeout=30):
        await self.emit(text, user, group)
        return await asyncio.wait_for(self.outgoing.get(), timeout)

    async def close(self):
        if self.ws:
            await self.ws.close()
        if self.task:
            await asyncio.gather(self.task, return_exceptions=True)
        if self.session:
            await self.session.close()
