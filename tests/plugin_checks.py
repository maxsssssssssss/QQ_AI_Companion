"""Real plugin acceptance checks, invoked inside the network-disabled harness.

Only the OneBot peer and LLM HTTP endpoint are test doubles. Plugins are loaded
by AstrBot itself. No upstream modules, methods, database rows, or callbacks are
patched. All strings and numeric identities below are synthetic fixtures.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3

MESSAGE_MARKER = "PHASE1_PLUGIN_MESSAGE_SYNTHETIC_7C8E"
MEMORY_MARKER = "PHASE1_LOCAL_MEMORY_ORBIT_CODE"
NICKNAME = "Phase1SyntheticNickname"


def action_text(action: dict) -> str:
    message = action.get("params", {}).get("message", "")
    if isinstance(message, str):
        return message
    return "".join(str(part.get("data", {}).get("text", "")) for part in message if isinstance(part, dict))


def read_rows(path: Path, sql: str, params=()) -> list[dict]:
    if not path.is_file():
        raise FileNotFoundError(path)
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(sql, params)]


def plugin_storage_snapshot(state: str | Path) -> dict:
    root = Path(state) / "data/plugin_data"
    result = {}
    for name, filename, sql, params in (
        ("recorder", "astrbot_plugin_message_recorder/messages.db",
         "SELECT platform,message_type,sender_id,message_str FROM messages WHERE message_str LIKE ?", (f"%{MESSAGE_MARKER}%",)),
        ("memory", "astrbot_plugin_memory_companion/memory_companion.db",
         "SELECT content,scope,subject_id,session_id,visibility FROM memories WHERE content LIKE ?", (f"%{MEMORY_MARKER}%",)),
        ("timeline", "astrbot_plugin_memory_companion/memory_companion.db",
         "SELECT event_type,content,scope,subject_id FROM timeline WHERE content LIKE ?", (f"%{MESSAGE_MARKER}%",)),
    ):
        try:
            result[name] = read_rows(root / filename, sql, params)
        except Exception as exc:
            result[name] = {"error": f"{type(exc).__name__}: {exc}"}
    private_path = root / "astrbot_plugin_private_companion/companions.json"
    try:
        private = json.loads(private_path.read_text())
        # The exact outer key is a plugin implementation detail; inspect all
        # persisted user entries without fabricating or writing state.
        users = private.get("users", {})
        result["private"] = [{"user_key": key, "nickname": value.get("nickname"),
                              "enabled": value.get("enabled"),
                              "proactive_enabled": value.get("proactive_enabled")}
                             for key, value in users.items() if isinstance(value, dict)
                             and value.get("nickname") == NICKNAME]
    except Exception as exc:
        result["private"] = {"error": f"{type(exc).__name__}: {exc}"}
    return result


async def run_plugin_checks(peer, mock, state, record) -> dict:
    """peer.send(text,user=...) -> actual OneBot action; record(name,bool,detail)."""
    state = Path(state)
    startup = json.loads((state / "startup.json").read_text())
    loaded = startup.get("plugins", [])
    expected = {
        "message_recorder": "message_recorder",
        "memory_companion": "memory_companion",
        "private_companion": "private_companion",
    }
    loaded_flags = {}
    for name, module_fragment in expected.items():
        matches = [p for p in loaded if module_fragment in str(p.get("module", ""))]
        loaded_flags[name] = bool(matches) and all(p.get("activated") and p.get("instantiated") for p in matches)
        record(f"plugin_{name}_loaded", loaded_flags[name], matches)

    async def command(name, text, expected_text, user=10001):
        try:
            before_command = len(mock.requests)
            action = await peer.send(text, user=user)
            reply = action_text(action)
            unexpected_llm = len(mock.requests) != before_command
            record(name, expected_text in reply and not unexpected_llm, {"input": text, "reply": reply, "unexpected_llm_call": unexpected_llm})
            return reply
        except Exception as exc:
            record(name, False, f"{type(exc).__name__}: {exc}")
            return ""

    before = len(mock.requests)
    await command("plugin_recorder_command", "/msg_record stats", "总记录数")
    await command("plugin_memory_command", "/mcomp status", "我会牢牢记住你：运行中")
    await command("plugin_memory_write", f"/mcomp add {MEMORY_MARKER}：蓝色彗星最喜欢抹茶曲奇", "记住了")
    await command("plugin_memory_read", "/mcomp search 蓝色彗星", MEMORY_MARKER)
    await command("plugin_recorder_non_admin_denied", "/msg_record query 10001", "权限不足", user=10002)
    await command("plugin_memory_non_admin_denied", "/mcomp search 蓝色彗星", "权限不足", user=10002)
    await command("plugin_private_write", f"/陪伴 昵称 {NICKNAME}", f"记住了,以后我会叫你：{NICKNAME}")
    await command("plugin_private_read", "/陪伴 状态", NICKNAME)
    try:
        action = await peer.send(f"请回复此离线测试消息 {MESSAGE_MARKER}", user=10001)
        record("plugin_combined_llm_chain", all(loaded_flags.values()) and bool(action_text(action)) and len(mock.requests) > before,
               {"reply": action_text(action), "llm_request_count": len(mock.requests)})
    except Exception as exc:
        record("plugin_combined_llm_chain", False, f"{type(exc).__name__}: {exc}")

    try:
        before_query = len(mock.requests)
        await peer.send("请回忆蓝色彗星最喜欢什么点心", user=10001)
        requests = mock.requests[before_query:]
        injected_messages = []
        for item in requests:
            messages = item["request"].get("messages", [])
            users = [m for m in messages if m.get("role") == "user"]
            injected_messages.extend(m for m in messages if m.get("role") in ("system", "developer"))
            injected_messages.extend(users[-1:])
        payload = json.dumps(injected_messages, ensure_ascii=False)
        record("plugin_memory_actual_prompt_injection", loaded_flags["memory_companion"] and bool(requests) and MEMORY_MARKER in payload,
               {"llm_requests": len(requests), "memory_marker_in_request": MEMORY_MARKER in payload})
        before_other = len(mock.requests)
        await peer.send("请回忆蓝色彗星最喜欢什么点心", user=10002)
        requests = mock.requests[before_other:]
        payload = json.dumps(requests, ensure_ascii=False)
        record("plugin_memory_other_user_isolation", loaded_flags["memory_companion"] and bool(requests) and MEMORY_MARKER not in payload,
               {"llm_requests": len(requests), "private_marker_leaked": MEMORY_MARKER in payload})
        record("plugin_private_other_user_isolation", loaded_flags["private_companion"] and bool(requests) and NICKNAME not in payload,
               {"llm_requests": len(requests), "nickname_leaked": NICKNAME in payload})
    except Exception as exc:
        record("plugin_memory_injection_and_scope", False, f"{type(exc).__name__}: {exc}")

    try:
        replay_text = "PHASE1_RECORDER_REPLAY_SYNTHETIC"
        event = await peer.emit(replay_text, user=10001)
        await asyncio.wait_for(peer.outgoing.get(), 30)
        from offline_peers import append_event
        append_event(peer.evidence, "onebot_event_replay", {"event": event})
        await peer.ws.send_json(event)
        duplicate_reply = False
        try:
            await asyncio.wait_for(peer.outgoing.get(), 1)
            duplicate_reply = True
        except TimeoutError:
            pass
        recorder_path = state / "data/plugin_data/astrbot_plugin_message_recorder/messages.db"
        for _ in range(30):
            rows = read_rows(recorder_path, "SELECT message_id,sender_id,message_str FROM messages WHERE message_id=?", (str(event["message_id"]),))
            if rows:
                break
            await asyncio.sleep(0.1)
        record("plugin_recorder_replay_deduplicated", loaded_flags["message_recorder"] and len(rows) == 1,
               {"replayed_message_id": event["message_id"], "rows": rows, "duplicate_reply_observed": duplicate_reply, "scope": "End-to-end single persisted row after replay; Phase1Guard can reject before Recorder, so this does not independently exercise Recorder conflict insertion"})
        outbound_rows = read_rows(recorder_path, "SELECT message_id,sender_id,message_str FROM messages WHERE sender_id=?", ("90000",))
        record("plugin_recorder_outbound_coverage", bool(outbound_rows),
               {"bot_sender_rows": len(outbound_rows), "limitation": "Upstream records inbound events; outgoing replies are not observed unless the platform emits separate self-message events"})
    except Exception as exc:
        record("plugin_recorder_replay_and_outbound", False, f"{type(exc).__name__}: {exc}")

    # Recorder commits from its own background callback. Wait for a bounded
    # positive observation rather than guessing that a loader success suffices.
    for _ in range(30):
        snapshot = plugin_storage_snapshot(state)
        if (all(isinstance(snapshot.get(k), list) and snapshot[k] for k in ("recorder", "memory", "timeline", "private"))
                and {"user_message", "bot_response"}.issubset({row.get("event_type") for row in snapshot["timeline"]})):
            break
        await asyncio.sleep(0.1)
    for key in ("recorder", "memory", "timeline", "private"):
        record(f"plugin_{key}_durable_data", isinstance(snapshot[key], list) and bool(snapshot[key]), snapshot[key])
    timeline = snapshot["timeline"] if isinstance(snapshot["timeline"], list) else []
    for event_type in ("user_message", "bot_response"):
        matching = [row for row in timeline if row.get("event_type") == event_type]
        record(f"plugin_memory_captured_{event_type}", bool(matching), matching)
    from prepare_plugins import source_tree_sha256
    lock = json.loads((Path(__file__).resolve().parents[1] / "config/plugins-lock.json").read_text())
    for plugin in lock["plugins"]:
        observed = source_tree_sha256(state / "data/plugins" / plugin["directory"])
        record(f"plugin_{plugin['id']}_source_unchanged", observed == plugin["source_tree_sha256"],
               {"source_tree_sha256": observed, "commit": plugin["commit"]})
    (state / "plugin-storage-observation.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n")
    return snapshot


async def check_plugin_recovery(peer, state, record, label="restart") -> dict:
    """Call after a real AstrBot restart or backup restore with the same data."""
    # Fresh post-restart commands need fresh protocol IDs; persisted replay
    # rejection is validated separately by the core P0 guard tests.
    peer.message_id = max(peer.message_id, 200000 if label == "restore" else 100000)
    snapshot = plugin_storage_snapshot(state)
    for key in ("recorder", "memory", "timeline", "private"):
        record(f"plugin_{label}_{key}_persisted", isinstance(snapshot[key], list) and bool(snapshot[key]), snapshot[key])
    for name, command, marker in (
        ("memory", "/mcomp search 蓝色彗星", MEMORY_MARKER),
        ("private", "/陪伴 状态", NICKNAME),
    ):
        try:
            reply = action_text(await peer.send(command, user=10001))
            record(f"plugin_{label}_{name}_readable", marker in reply, reply)
        except Exception as exc:
            record(f"plugin_{label}_{name}_readable", False, f"{type(exc).__name__}: {exc}")
    return snapshot
