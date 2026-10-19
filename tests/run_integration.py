"""Run real AstrBot in an offline container; emit evidence, never infer a PASS.

This is a candidate baseline. Formal Phase 1 P0 remains unassessed until the
missing original task specification is supplied.
"""
import asyncio
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import sys
import time
import traceback
import uuid

from aiohttp import ClientSession, WSServerHandshakeError
from offline_peers import MockLLM, OneBotPeer, action_text

ROOT = Path("/harness")
STATE = Path("/state")
EVIDENCE = Path("/evidence")
sys.path.insert(0, str(ROOT / "scripts"))


class Suite:
    def __init__(self):
        self.results = []
        self.process = None
        self.log_handle = None
        self.peer = None
        self.mock = MockLLM(EVIDENCE / "transcript.jsonl")
        self.launch_count = 0

    def record(self, name, passed, detail):
        self.results.append({"id": name, "status": "PASS" if passed else "FAIL", "detail": detail})
        print(f"{self.results[-1]['status']} {name}: {detail}", flush=True)
        self.write_results()

    def write_results(self):
        (EVIDENCE / "results.json").write_text(json.dumps({"formal_phase1_status": "SEE_P0_ACCEPTANCE_REPORT", "integration_checks_status": "PASS" if self.results and all(r["status"] == "PASS" for r in self.results) else "FAIL", "results": self.results}, ensure_ascii=False, indent=2))

    async def check(self, name, fn):
        try:
            detail = await fn()
            self.record(name, True, detail)
        except Exception as e:
            self.record(name, False, f"{type(e).__name__}: {e}")
            (EVIDENCE / f"{name}.traceback.txt").write_text(traceback.format_exc())

    def configure(self, state):
        (state / "data").mkdir(parents=True, exist_ok=False)
        shutil.copyfile(ROOT / "config/offline.json", state / "data/cmd_config.json")
        config = json.loads((ROOT / "config/offline.json").read_text())
        (state / "phase1.json").write_text(json.dumps(config["phase1"], indent=2))
        (state / "persona.md").write_text("你是中文 AI 伙伴。使用自然中文。测试人格标识 PERSONA_ALPHA，不虚构人类生活。")
        guard = ROOT / "integrations/phase1_guard"
        if guard.exists():
            shutil.copytree(guard, state / "data/plugins/phase1_guard", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    async def start(self, state):
        self.current_state = state
        self.launch_count += 1
        env = dict(os.environ, ASTRBOT_ROOT=str(state), PYTHONDONTWRITEBYTECODE="1")
        self.log_handle = (EVIDENCE / f"astrbot-{self.launch_count}.log").open("w")
        (state / "startup.json").unlink(missing_ok=True)
        self.process = await asyncio.create_subprocess_exec(sys.executable, str(ROOT / "launch_astrbot.py"), env=env, cwd="/AstrBot", stdout=self.log_handle, stderr=asyncio.subprocess.STDOUT)
        for _ in range(180):
            if self.process.returncode is not None:
                raise RuntimeError(f"AstrBot exited {self.process.returncode}; see astrbot-{self.launch_count}.log")
            if (state / "startup.json").exists():
                try:
                    candidate = OneBotPeer(EVIDENCE / "transcript.jsonl")
                    await candidate.connect()
                    self.peer = candidate
                    info = json.loads((state / "startup.json").read_text())
                    shutil.copyfile(state / "startup.json", EVIDENCE / f"startup-{self.launch_count}.json")
                    return info
                except (OSError, WSServerHandshakeError):
                    pass
            await asyncio.sleep(0.5)
        raise TimeoutError("AstrBot did not expose authenticated OneBot WS within 90s")

    async def stop(self):
        if self.peer:
            await self.peer.close()
            self.peer = None
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 20)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
                raise RuntimeError("AstrBot required SIGKILL; graceful shutdown failed")
        if self.log_handle:
            self.log_handle.close()

    async def roundtrip(self, text, user=10001, group=None):
        before = len(self.mock.requests)
        action = await self.peer.send(text, user, group)
        new = self.mock.requests[before:]
        assert len(new) == 1, f"Expected one real HTTP request, got {len(new)}"
        assert new[0]["status"] == 200
        expected = new[0]["response"]["choices"][0]["message"]["content"]
        assert action_text(action) == expected, (action_text(action), expected)
        target = "group_id" if group else "user_id"
        assert int(action["params"][target]) == (group or user), "Reply routed to wrong session"
        assert new[0]["request"]["model"] == "phase1-mock"
        assert self.peer.deliveries[-1]["status"] == "ok", "Send attempt was not acknowledged"
        if group is None:
            message_id = str(self.peer.last_event["message_id"])
            for _ in range(100):
                sent = self.guard_rows("outbound", "message_id=?", (message_id,))
                if sent and sent[0]["state"] != "pending":
                    break
                await asyncio.sleep(0.02)
            assert len(sent) == 1 and sent[0]["state"] == "sent", sent
            assert sent[0]["text"] == expected, sent
        # OneBot acknowledgement precedes the framework's DB commit. A real
        # durable-state barrier avoids killing an in-flight pipeline at shutdown.
        await self.wait_persisted(expected)
        return new[0]

    async def wait_persisted(self, text):
        def strings(value):
            if isinstance(value, str):
                yield value
            elif isinstance(value, list):
                for item in value:
                    yield from strings(item)
            elif isinstance(value, dict):
                for item in value.values():
                    yield from strings(item)
        for _ in range(100):
            with sqlite3.connect(self.current_state / "data/data_v4.db") as db:
                rows = db.execute("SELECT content FROM conversations").fetchall()
            if any(text in s for row in rows for s in strings(json.loads(row[0]) if isinstance(row[0], str) else row[0])):
                return
            await asyncio.sleep(0.05)
        raise AssertionError("Reply did not reach durable AstrBot conversation storage")

    def guard_rows(self, table, where="1=1", params=()):
        assert table in {"inbound", "outbound"}
        path = self.current_state / "data/plugin_data/phase1_guard/guard.sqlite3"
        with sqlite3.connect(path) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute(f"SELECT * FROM {table} WHERE {where}", params)]

    async def run_base(self):
        state = STATE / "base"
        self.configure(state)
        await self.mock.start()
        info = await self.start(state)
        self.record("B01_REAL_START", info["version"] == "4.29.1" and "mock/phase1" in info["providers"], info)
        marker = "PHASE1_A_" + uuid.uuid4().hex

        async def e2e():
            r = await self.roundtrip(marker)
            assert marker in json.dumps(r["request"]["messages"])
            tools = {t["function"]["name"] for t in r["request"].get("tools", [])}
            assert tools <= {"send_message_to_user"}, f"Unexpected tools: {tools}"
            event = self.peer.last_event
            rows = self.guard_rows("inbound", "message_id=?", (str(event["message_id"]),))
            assert len(rows) == 1
            assert rows[0]["sender_id"] == "10001" and rows[0]["session"] == "offline:FriendMessage:10001"
            assert rows[0]["text"] == marker and marker in rows[0]["chain_json"]
            return "OneBot event -> AstrBot pipeline -> HTTP Mock LLM -> exact correlated OneBot reply"
        await self.check("B02_PRIVATE_E2E", e2e)

        async def isolation():
            r = await self.roundtrip("PHASE1_USER_B", user=10002)
            assert marker not in json.dumps(r["request"])
            r = await self.roundtrip("/PHASE1_GROUP_A", user=10001, group=20001)
            assert marker not in json.dumps(r["request"])
            r = await self.roundtrip("/PHASE1_GROUP_B", user=10002, group=20001)
            assert "PHASE1_GROUP_A" not in json.dumps(r["request"])
            r = await self.roundtrip("/PHASE1_OTHER_GROUP", user=10001, group=20002)
            assert "PHASE1_GROUP_A" not in json.dumps(r["request"])
            return "Private users, private/group and group members/groups have separate LLM contexts"
        await self.check("B03_SESSION_ISOLATION", isolation)

        async def auth():
            async with ClientSession() as session:
                for token in (None, "wrong-fixture-token"):
                    headers = {"X-Self-ID": "90001", "X-Client-Role": "Universal"}
                    if token:
                        headers["Authorization"] = "Bearer " + token
                    try:
                        ws = await session.ws_connect("http://127.0.0.1:6199/ws", headers=headers)
                    except WSServerHandshakeError as e:
                        assert e.status in (401, 403), e.status
                    else:
                        await ws.close()
                        raise AssertionError("Unauthenticated OneBot connection accepted")
            return "Missing and invalid OneBot tokens rejected; valid token exercises B02"
        await self.check("B04_ONEBOT_AUTH", auth)

        async def whitelist():
            n = len(self.mock.requests)
            await self.peer.emit("PHASE1_DENIED_PRIVATE", user=10003)
            await self.peer.emit("/PHASE1_DENIED_GROUP", group=20003)
            await asyncio.sleep(0.8)
            assert len(self.mock.requests) == n
            assert self.peer.outgoing.empty()
            r = await self.roundtrip("PHASE1_ALLOWLIST_BARRIER", user=10002)
            assert "PHASE1_DENIED" not in json.dumps(r["request"])
            return "Non-allowlisted private/group messages produce no LLM call or reply; allowed control succeeds"
        await self.check("B05_ALLOWLIST", whitelist)

        async def consecutive():
            texts = ["今天好累 😮‍💨", "公司那边又出问题了", "而且我本来还想早点睡 🌙"]
            before = len(self.mock.requests)
            events = [await self.peer.emit(t) for t in texts]
            actions = [await asyncio.wait_for(self.peer.outgoing.get(), 30) for _ in texts]
            calls = self.mock.requests[before:]
            assert len(calls) == 3, len(calls)
            replies = [c["response"]["choices"][0]["message"]["content"] for c in calls]
            assert Counter(action_text(a) for a in actions) == Counter(replies)
            processed = [next(m["content"] for m in reversed(c["request"]["messages"]) if m["role"] == "user") for c in calls]
            assert Counter(processed) == Counter(texts), processed
            for i, call in enumerate(calls):
                messages = json.dumps(call["request"]["messages"], ensure_ascii=False)
                assert all(t in messages for t in processed[:i + 1]), messages
                assert all(r in messages for r in replies[:i]), "Prior assistant turn missing"
            await self.wait_persisted(replies[-1])
            with sqlite3.connect(self.current_state / "data/data_v4.db") as db:
                histories = [json.loads(r[0]) for r in db.execute("SELECT content FROM conversations")]
            history = next(h for h in histories if any(m.get("content") == texts[0] for m in h))
            stored = [m.get("content") for m in history if m["role"] == "user" and isinstance(m.get("content"), str) and m["content"] in texts]
            assert Counter(stored) == Counter(texts)
            assistant_texts = [seg["text"] for m in history if m["role"] == "assistant" for seg in m.get("content", []) if isinstance(seg, dict) and seg.get("type") == "text"]
            assert all(assistant_texts.count(r) == 1 for r in replies)
            rows = self.guard_rows("inbound", "message_id IN (?,?,?)", tuple(str(e["message_id"]) for e in events))
            assert len(rows) == 3
            assert {r["text"] for r in rows} == set(texts)
            deliveries = self.guard_rows("outbound", "message_id IN (?,?,?)", tuple(str(e["message_id"]) for e in events))
            assert len(deliveries) == 3 and all(r["state"] == "sent" for r in deliveries)
            return {"arrival_order": texts, "processing_order": processed, "raw_records": 3, "acknowledged_replies": 3, "complete_context_and_history": True, "strict_arrival_fifo": "not guaranteed by upstream", "debounce": "not implemented in baseline"}
        await self.check("B12_CHINESE_BURST", consecutive)

        async def dedup():
            r = await self.roundtrip("PHASE1_DUPLICATE")
            self.duplicate_event = dict(self.peer.last_event)
            n = len(self.mock.requests)
            await self.peer.ws.send_json(self.duplicate_event)
            await asyncio.sleep(0.5)
            assert len(self.mock.requests) == n and self.peer.outgoing.empty()
            rows = self.guard_rows("inbound", "message_id=?", (str(self.duplicate_event["message_id"]),))
            assert len(rows) == 1 and rows[0]["duplicate_count"] == 1, rows
            out = self.guard_rows("outbound", "message_id=?", (str(self.duplicate_event["message_id"]),))
            assert len(out) == 1 and out[0]["state"] == "sent", out
            return "Exact OneBot retransmit: one raw archive, one LLM call, one acknowledged send"
        await self.check("B13_DEDUP", dedup)

        async def image_structure():
            # A synthetic 1px image hosted by the local fixture HTTP endpoint.
            text = "PHASE1_IMAGE_STRUCTURE 图片结构 📷"
            event = await self.peer.emit(text, segments=[{"type": "text", "data": {"text": text}}, {"type": "image", "data": {"file": "fixture.png", "url": "http://127.0.0.1:18080/fixture.png"}}])
            action = await asyncio.wait_for(self.peer.outgoing.get(), 30)
            await self.wait_persisted(action_text(action))
            rows = self.guard_rows("inbound", "message_id=?", (str(event["message_id"]),))
            assert len(rows) == 1 and "image" in rows[0]["chain_json"].lower(), rows
            return "Real aiocqhttp parsed synthetic image segment; raw archive retains structure; no real vision claim"
        await self.check("B14_IMAGE_STRUCTURE", image_structure)

        async def reconnect():
            await self.peer.close()
            self.peer = OneBotPeer(EVIDENCE / "transcript.jsonl")
            await self.peer.connect()
            r = await self.roundtrip("PHASE1_RECONNECT")
            assert marker in json.dumps(r["request"]["messages"])
            return "Reverse WebSocket reconnect succeeds with previous conversation context"
        await self.check("B06_RECONNECT", reconnect)

        async def persistence():
            await asyncio.sleep(0.4)
            with sqlite3.connect(state / "data/data_v4.db") as db:
                assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                rows = db.execute("SELECT user_id,content FROM conversations").fetchall()
                assert len(rows) >= 5, len(rows)
                assert any(marker in str(row) for row in rows)
                (EVIDENCE / "conversations.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2))
            return f"Real AstrBot SQLite integrity_check=ok; {len(rows)} sessions persisted"
        await self.check("B07_PERSISTENCE", persistence)

        async def restart():
            old = self.process.pid
            await self.stop()
            new = await self.start(state)
            assert new["pid"] != old
            if hasattr(self, "duplicate_event"):
                n = len(self.mock.requests)
                await self.peer.ws.send_json(self.duplicate_event)
                await asyncio.sleep(0.3)
                assert len(self.mock.requests) == n and self.peer.outgoing.empty()
            r = await self.roundtrip("PHASE1_AFTER_RESTART")
            assert marker in json.dumps(r["request"]["messages"])
            return {"old_pid": old, "new_pid": new["pid"], "history_reused_by_llm": True}
        await self.check("B08_RESTART", restart)

        async def backup_restore():
            from backup import create_backup, restore_backup
            await self.stop()
            archive = STATE / "base-backup.zip"
            create_backup(state, archive)
            restored = STATE / "restored"
            restore_backup(archive, restored)
            await self.start(restored)
            r = await self.roundtrip("PHASE1_AFTER_RESTORE")
            messages = json.dumps(r["request"]["messages"])
            assert marker in messages and "PHASE1_AFTER_RESTART" in messages
            return "Stopped full-state backup, checksummed restore to fresh directory, real restart and retained history"
        await self.check("B09_BACKUP_RESTORE", backup_restore)

        async def failure_recovery():
            outcomes = []
            for fault in ("503", "TIMEOUT", "INVALID"):
                n = len(self.mock.requests)
                action = await self.peer.send("PHASE1_FORCE_" + fault, user=10002, timeout=45)
                calls = self.mock.requests[n:]
                assert calls
                assert "MOCK_REPLY_" not in action_text(action)
                assert "错误" in action_text(action) or "error" in action_text(action).lower(), action
                outcomes.append({"fault": fault, "calls": len(calls), "error_reply": action_text(action)})
            await self.roundtrip("PHASE1_HEALTHY_AGAIN", user=10001)
            return {"faults": outcomes, "subsequent_reply_succeeded": True}
        await self.check("B10_LLM_FAILURE_RECOVERY", failure_recovery)

        async def send_failures():
            outcomes = []
            for mode, expected in (("failed", "failed"), ("timeout", "unknown")):
                self.peer.next_send_mode = mode
                await self.peer.send("PHASE1_SEND_" + mode.upper())
                message_id = str(self.peer.last_event["message_id"])
                for _ in range(80):
                    rows = self.guard_rows("outbound", "message_id=?", (message_id,))
                    if rows and rows[0]["state"] != "pending":
                        break
                    await asyncio.sleep(0.05)
                assert len(rows) == 1 and rows[0]["state"] == expected, rows
                assert rows[0]["error_type"], rows
                outcomes.append({"injected": mode, "recorded": expected, "error_type": rows[0]["error_type"]})
            await self.roundtrip("PHASE1_SEND_RECOVERED")
            return outcomes
        await self.check("B15_SEND_FAILURES", send_failures)

        async def persona_switch():
            before = await self.roundtrip("PHASE1_PERSONA_BEFORE")
            assert "PERSONA_ALPHA" in json.dumps([m for m in before["request"]["messages"] if m["role"] == "system"])
            state = self.current_state
            await self.stop()
            (state / "persona.md").write_text("你是合成测试人格 PERSONA_BETA。请简洁严谨地用中文交流。")
            await self.start(state)
            after = await self.roundtrip("PHASE1_PERSONA_AFTER")
            system = json.dumps([m for m in after["request"]["messages"] if m["role"] == "system"])
            assert "PERSONA_BETA" in system and "PERSONA_ALPHA" not in system
            return "External persona file changed; real AstrBot provider request uses new system prompt after restart"
        await self.check("B16_PERSONA_SWITCH", persona_switch)

        async def isolation_boundary():
            assert os.geteuid() != 0
            assert set(socket.if_nameindex()) == {(1, "lo")}, socket.if_nameindex()
            assert not Path("/var/run/docker.sock").exists()
            try:
                assert not Path("/root/.ssh").exists()
            except PermissionError:
                pass
            status = Path("/proc/self/status").read_text()
            assert "CapEff:\t0000000000000000" in status
            assert "NoNewPrivs:\t1" in status
            try:
                Path("/AstrBot/phase1-write-probe").write_text("probe")
            except OSError:
                pass
            else:
                raise AssertionError("Upstream code filesystem writable")
            sock = socket.socket()
            sock.settimeout(1)
            try:
                sock.connect(("198.51.100.1", 443))
            except OSError:
                pass
            else:
                raise AssertionError("External network unexpectedly reachable")
            finally:
                sock.close()
            for port in (6185,):
                sock = socket.socket()
                try:
                    assert sock.connect_ex(("127.0.0.1", port)) != 0, "Dashboard unexpectedly listening"
                finally:
                    sock.close()
            return "non-root, network=none (loopback only), zero capabilities, no-new-privileges, read-only source, no Docker socket, WebUI disabled"
        await self.check("B11_RUNTIME_ISOLATION", isolation_boundary)
        await self.stop()

        async def export():
            from export_data import export_data
            target = EVIDENCE / "export.synthetic.json"
            summary = export_data(self.current_state, target)
            payload = json.loads(target.read_text())
            assert marker in json.dumps(payload, ensure_ascii=False)
            assert "failed" in json.dumps(payload) and "unknown" in json.dumps(payload)
            assert "offline-fixture-key" not in json.dumps(payload)
            return summary
        await self.check("B17_EXPORT", export)

    async def run_plugins(self):
        from prepare_plugins import prepare_plugins
        from plugin_checks import run_plugin_checks, check_plugin_recovery
        state = STATE / "plugins"
        self.configure(state)
        prepare_plugins(state, Path("/plugins"))
        config_path = state / "data/cmd_config.json"
        config = json.loads(config_path.read_text())
        config["admins_id"] = ["10001"]
        config["phase1"]["admin_fixture_enabled"] = True
        (state / "phase1.json").write_text(json.dumps(config["phase1"], indent=2))
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2))
        info = await self.start(state)
        (EVIDENCE / "plugin-startup.json").write_text(json.dumps(info, ensure_ascii=False, indent=2))
        await run_plugin_checks(self.peer, self.mock, state, self.record)
        await self.stop()
        await self.start(state)
        await check_plugin_recovery(self.peer, state, self.record, label="restart")
        await self.stop()
        from backup import create_backup, restore_backup
        archive = STATE / "plugins-backup.zip"
        restored = STATE / "plugins-restored"
        create_backup(state, archive)
        restore_backup(archive, restored)
        await self.start(restored)
        await check_plugin_recovery(self.peer, restored, self.record, label="restore")
        await self.stop()

    async def run(self):
        try:
            await self.check("BASE_SUITE", self.run_base)
            if (ROOT / "tests/plugin_checks.py").exists():
                await self.check("PLUGIN_SUITE", self.run_plugins)
            else:
                self.record("PLUGIN_SUITE", False, "Plugin integration checks not available")
        finally:
            await self.stop()
            await self.mock.close()
            self.write_results()
            manifest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in EVIDENCE.iterdir() if p.is_file() and p.name != "SHA256SUMS.json"}
            (EVIDENCE / "SHA256SUMS.json").write_text(json.dumps(manifest, indent=2))
        # P1 compatibility failures are findings, not a reason to discard P0.
        base = [r for r in self.results if r["id"].startswith("B")]
        return 0 if base and all(r["status"] == "PASS" for r in base) else 1


if __name__ == "__main__":
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    sys.exit(asyncio.run(Suite().run()))
