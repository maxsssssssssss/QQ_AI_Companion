#!/usr/bin/env python3
"""Stage a fresh production state, or inject a key while AstrBot is stopped.

The official AstrBot main.py remains the entrypoint. No upstream code is changed.
Secrets are generated/read locally and never printed. Do not run init over an
existing state. Secret-bearing configuration and the entire state must stay out
of Git and be readable only by the container/service owner.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import sys
import tempfile

from prepare_plugins import PROJECT, prepare_plugins, source_tree_sha256

sys.path.insert(0, str(PROJECT))
from integrations.phase2_policy.policy import validate_policy  # noqa: E402


def write_private(path: Path, value: str, *, replace=False, owner=None):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or (path.exists() and not replace):
        raise FileExistsError("Refusing to overwrite an existing output")
    previous = None
    if replace and path.exists():
        existing_fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            previous = os.fstat(existing_fd)
            if not stat.S_ISREG(previous.st_mode) or previous.st_mode & 0o077:
                raise ValueError("Replacement target must be an owner-only regular file")
        finally:
            os.close(existing_fd)
    fd, temporary = tempfile.mkstemp(prefix=".phase2-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            target_owner = ((previous.st_uid, previous.st_gid)
                            if previous is not None else owner)
            if target_owner is not None:
                current = os.fstat(output.fileno())
                if (current.st_uid, current.st_gid) != target_owner:
                    os.fchown(output.fileno(), *target_owner)
            if previous is not None:
                os.fchmod(output.fileno(), stat.S_IMODE(previous.st_mode) & 0o777)
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        if previous is not None:
            observed = path.lstat()
            if (observed.st_dev, observed.st_ino) != (previous.st_dev, previous.st_ino):
                raise RuntimeError("Replacement target changed during generation")
        if replace:
            os.replace(temporary, path)
        else:
            # Atomic no-clobber publication, including if another writer created
            # the target after our initial check. A symlink also yields EEXIST.
            os.link(temporary, path, follow_symlinks=False)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_private(path: Path) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "r", encoding="utf-8-sig") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError("Secret must be a regular file with owner-only permissions")
        if info.st_size > 1024 * 1024:
            raise ValueError("Protected input is unexpectedly large")
        return source.read()


def password_hash(password: str) -> str:
    # Matches AstrBot 4.29.1 astrbot/core/utils/auth_password.py.
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600000).hex()
    return f"pbkdf2_sha256$600000${salt}${digest}"


def valid_key(value):
    return (isinstance(value, str) and 16 <= len(value) <= 512
            and all(33 <= ord(character) <= 126 for character in value))


def build_config(admin_id: str, platform_id: str, onebot_token: str, dashboard_password: str):
    config = {
        "config_version": 5,
        "dashboard": {
            "enable": True, "host": "0.0.0.0", "port": 6185, "username": "astrbot",
            "password": hashlib.md5(dashboard_password.encode()).hexdigest(),
            "pbkdf2_password": password_hash(dashboard_password),
            "password_storage_upgraded": True, "password_change_required": False,
            "jwt_secret": secrets.token_urlsafe(48), "disable_access_log": True,
            "trust_proxy_headers": False,
            "auth_rate_limit": {"enable": True, "average_interval": 1.0, "max_burst": 3},
        },
        "platform": [{"id": platform_id, "type": "aiocqhttp", "enable": True,
                      "ws_reverse_host": "0.0.0.0", "ws_reverse_port": 6199,
                      "ws_reverse_token": onebot_token}],
        "platform_settings": {
            "unique_session": True, "enable_id_white_list": True,
            "id_whitelist": [f"{platform_id}:FriendMessage:{admin_id}"],
            "id_whitelist_log": False,
            "wl_ignore_admin_on_group": False, "wl_ignore_admin_on_friend": False,
            "reply_with_mention": False, "reply_with_quote": False,
            "no_permission_reply": False, "ignore_bot_self_message": True,
            "ignore_at_all": True, "friend_message_needs_wake_prefix": False,
            "segmented_reply": {"enable": False},
            "rate_limit": {"time": 60, "count": 12, "strategy": "discard"},
        },
        "provider_sources": [{
            "id": "deepseek", "provider": "deepseek", "type": "openai_chat_completion",
            "provider_type": "chat_completion", "enable": False, "key": [],
            "api_base": "https://api.deepseek.com", "timeout": 45, "proxy": "",
            "custom_headers": {},
        }],
        "provider": [
            {"id": "deepseek/flash", "provider_source_id": "deepseek",
             "model": "deepseek-flash", "enable": False,
             "modalities": ["text", "image", "tool_use"],
             "custom_extra_body": {"max_tokens": 2048, "thinking": {"type": "disabled"}}},
            {"id": "deepseek/pro", "provider_source_id": "deepseek",
             "model": "deepseek-v4-pro", "enable": False,
             "modalities": ["text", "tool_use"],
             "custom_extra_body": {"max_tokens": 2048, "thinking": {"type": "disabled"}}},
        ],
        "provider_settings": {
            "enable": False, "streaming_response": False, "datetime_system_prompt": True,
            "web_search": False, "computer_use_runtime": "none",
            "computer_use_require_admin": True, "file_extract": {"enable": False},
            "proactive_capability": {"add_cron_tools": False},
            "show_tool_use_status": False, "show_tool_call_result": False,
            "display_reasoning_text": False, "default_image_caption_provider_id": "",
        },
        "agent_runner": {
            "runner_type": "local",
            "config": {
                "model": {"provider_id": "deepseek/flash", "fallback_provider_ids": [],
                          # AstrBot counts the initial attempt; 1 means no
                          # transport retry. Its normalizer clamps 0 to 1.
                          "request_max_retries": 1},
                "persona": {"persona_id": "qq_companion", "safety_mode": True,
                            "safety_mode_strategy": "system_prompt"},
                "compression": {"max_turns": 20, "trim_turns": 2,
                                "overflow_strategy": "truncate_by_turns"},
                "misc": {"max_steps": 3, "tool_call_timeout": 15,
                         "sanitize_context_by_modalities": True},
            },
        },
        "admins_id": [admin_id], "wake_prefix": ["/"], "plugin_set": ["*"],
        "disable_metrics": True, "log_level": "INFO", "log_file_enable": False,
        "trace_log_enable": False, "t2i": False, "timezone": "Asia/Shanghai",
        "subagent_orchestrator": {"main_enable": False},
    }
    validate_policy(config, admin_id, platform_id, require_key=False)
    return config


def initialize(state: Path, sources: Path, admin_id: str, platform_id: str):
    if not re.fullmatch(r"[1-9][0-9]{4,14}", admin_id):
        raise ValueError("Invalid administrator QQ ID")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", platform_id):
        raise ValueError("Invalid platform ID")
    if state.is_symlink() or (state / "data").exists() or (state / "secrets").exists():
        raise FileExistsError("State already contains data or secrets; init never overwrites")
    lock = json.loads((PROJECT / "config/plugins-lock.json").read_text())
    for plugin in lock["plugins"]:
        source = sources / plugin["id"]
        if not source.is_dir():
            source = sources / plugin["directory"]
        if source_tree_sha256(source) != plugin["source_tree_sha256"]:
            raise ValueError("Pinned plugin source is missing or has changed")
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(state, 0o700)
    onebot_token = secrets.token_urlsafe(48)
    dashboard_password = "Aa9" + secrets.token_urlsafe(27)
    for name, value in {
        "onebot-token": onebot_token,
        "dashboard-password": dashboard_password,
        "napcat-webui-token": secrets.token_urlsafe(48),
    }.items():
        write_private(state / "secrets" / name, value + "\n")
    config = build_config(admin_id, platform_id, onebot_token, dashboard_password)
    prepare_plugins(state, sources)
    data = state / "data"
    for plugin in ("phase1_guard", "phase2_policy"):
        shutil.copytree(PROJECT / "integrations" / plugin, data / "plugins" / plugin,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copyfile(PROJECT / "scripts/plugin_permissions.py",
                    data / "plugins/phase2_policy/plugin_permissions.py")
    write_private(data / "cmd_config.json", json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    write_private(data / "config/phase1_guard_config.json",
                  json.dumps({"send_timeout_seconds": 15.0}) + "\n")
    write_private(data / "config/phase2_policy_config.json",
                  json.dumps({"admin_id": admin_id, "platform_id": platform_id}) + "\n")
    prompt = "\n\n".join((PROJECT / "persona" / name).read_text()
                         for name in ("core_persona.md", "relationship.md"))
    write_private(data / "phase2/persona.md", prompt + "\n")
    # Plugin staging writes non-secret defaults; protect every writable config anyway.
    for path in (data / "config").glob("*.json"):
        os.chmod(path, 0o600)
    for directory in (data, data / "config", state / "secrets"):
        os.chmod(directory, 0o700)
    return {"status": "PREPARED", "state": str(state), "provider_enabled": False,
            "key_required": True, "secret_values_printed": False}


def inject_key(state: Path, secret_file: Path):
    raw = read_private(secret_file).strip()
    if not valid_key(raw):
        raise ValueError("Key has invalid length or format")
    config_path = state / "data/cmd_config.json"
    config = json.loads(read_private(config_path))
    policy = json.loads(read_private(state / "data/config/phase2_policy_config.json"))
    validate_policy(config, policy["admin_id"], policy["platform_id"], require_key=False)
    source = config["provider_sources"][0]
    source["key"] = [raw]
    source["enable"] = True
    config["provider_settings"]["enable"] = True
    for model in config["provider"]:
        if model["id"] == "deepseek/flash":
            model["enable"] = True
    validate_policy(config, policy["admin_id"], policy["platform_id"], require_key=True)
    write_private(config_path, json.dumps(config, ensure_ascii=False, indent=2) + "\n", replace=True)
    return {"status": "CONFIGURED", "provider_enabled": True, "key_values_printed": False,
            "restart_required": True, "api_call_tested": False}


def read_dashboard_key_config(state: Path):
    config = json.loads(read_private(state / "data/cmd_config.json"))
    policy = json.loads(read_private(state / "data/config/phase2_policy_config.json"))
    validate_policy(config, policy["admin_id"], policy["platform_id"], require_key=False)
    return config


def dashboard_key_status(state: Path):
    """Read only; disclose no key characters, length, hashes, or configuration."""
    config = read_dashboard_key_config(state)
    keys = config["provider_sources"][0].get("key", [])
    present = bool(keys)
    valid = isinstance(keys, list) and len(keys) == 1 and valid_key(keys[0])
    return {
        "status": "READY" if valid else "BLOCKED" if present else "WAITING",
        "key_present": present, "key_format_valid": bool(valid),
        "provider_enabled": config["provider_sources"][0].get("enable") is True,
        "flash_enabled": next(model.get("enable") is True for model in config["provider"]
                              if model["id"] == "deepseek/flash"),
        "key_values_printed": False, "api_call_tested": False,
    }


def accept_dashboard_key(state: Path):
    """Use only after AstrBot stops: retain the submitted key, then enable Flash."""
    config = read_dashboard_key_config(state)
    keys = config["provider_sources"][0].get("key", [])
    if not isinstance(keys, list) or len(keys) != 1 or not valid_key(keys[0]):
        raise ValueError("Dashboard must contain exactly one valid protected key")
    raw = keys[0]
    secrets_directory = state / "secrets"
    directory_fd = os.open(secrets_directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        directory_info = os.fstat(directory_fd)
        if directory_info.st_mode & 0o077:
            raise ValueError("Secret directory must have owner-only permissions")
    finally:
        os.close(directory_fd)
    target = secrets_directory / "deepseek-key"
    if target.exists() or target.is_symlink():
        if not secrets.compare_digest(read_private(target).strip(), raw):
            raise ValueError("Existing protected key differs from the dashboard key")
    else:
        write_private(target, raw + "\n",
                      owner=(directory_info.st_uid, directory_info.st_gid))
    # Reuse the existing validation and owner-preserving configuration writer.
    result = inject_key(state, target)
    result["dashboard_key_accepted"] = True
    result["protected_key_file_available"] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    init = subparsers.add_parser("init", help="Create fresh state; never overwrite existing data")
    init.add_argument("--state", required=True, type=Path)
    init.add_argument("--sources", required=True, type=Path)
    init.add_argument("--admin-id", required=True)
    init.add_argument("--platform-id", default="napcat")
    key = subparsers.add_parser("set-key", help="Inject protected key file while AstrBot is stopped")
    key.add_argument("--state", required=True, type=Path)
    key.add_argument("--secret-file", required=True, type=Path)
    status = subparsers.add_parser("dashboard-key-status", help="Read only; report safe key-presence booleans")
    status.add_argument("--state", required=True, type=Path)
    accept = subparsers.add_parser(
        "accept-dashboard-key", help="Accept a saved dashboard key while AstrBot is stopped")
    accept.add_argument("--state", required=True, type=Path)
    args = parser.parse_args()
    try:
        os.umask(0o077)
        if args.command == "init":
            result = initialize(args.state, args.sources, args.admin_id, args.platform_id)
        elif args.command == "set-key":
            result = inject_key(args.state, args.secret_file)
        elif args.command == "dashboard-key-status":
            result = dashboard_key_status(args.state)
        else:
            result = accept_dashboard_key(args.state)
    except Exception as exc:
        # JSON/parser/IO exceptions may quote private content or paths. Suppress all.
        print(json.dumps({"status": "ERROR", "error_type": type(exc).__name__,
                          "detail": "Configuration unchanged or staging incomplete; inspect locally without printing secrets"}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result["status"] == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
