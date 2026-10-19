"""Validate the offline integration profile before importing AstrBot.

Only standard-library modules are used, so configuration errors can be diagnosed
before dependencies, containers, or the application have started.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


PLUGIN_IDS = {"message_recorder", "memory_companion", "private_companion"}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _fail(field: str, explanation: str):
    raise ValueError(f"{field}: {explanation}")


def _object(value, field):
    if not isinstance(value, dict):
        _fail(field, "must be a JSON object")
    return value


def _list(value, field, *, nonempty=True):
    if not isinstance(value, list) or (nonempty and not value):
        _fail(field, "must be a nonempty array" if nonempty else "must be an array")
    return value


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        _fail(field, "must be a nonempty string")
    return value


def _relative_path(value, field):
    text = _text(value, field)
    path = PurePosixPath(text)
    if path.is_absolute() or any(p in ("..", ".") for p in text.split("/")):
        _fail(field, "must be a project-relative path without '.' or '..'")
    if "\\" in text or ":" in text or "\x00" in text:
        _fail(field, "must use a safe relative POSIX path")
    return text


def local_timestamp(utc_iso: str, timezone_name: str = "Asia/Shanghai") -> str:
    """Convert an explicitly zoned ISO timestamp without using the host timezone."""
    try:
        instant = datetime.fromisoformat(utc_iso.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        _fail("timestamp", "must be an ISO timestamp with an explicit timezone")
    if instant.tzinfo is None or instant.utcoffset() is None:
        _fail("timestamp", "must include an explicit timezone")
    try:
        return instant.astimezone(ZoneInfo(timezone_name)).isoformat()
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        _fail("timezone", "must name an installed IANA timezone")


def validate_config(path: str | Path) -> dict:
    """Return the validated config (including sidecar metadata) or raise ValueError.

This validator deliberately accepts only the Phase 1 offline profile: loopback
services and explicit fixture tokens. It does not enable real QQ or a real LLM.
"""
    try:
        # AstrBot persists cmd_config.json with UTF-8 BOM after initialization.
        config = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail("config", f"cannot read a valid UTF-8 JSON configuration ({type(exc).__name__})")
    _object(config, "config")
    # AstrBot rewrites its own schema and removes unknown top-level fields.
    # Our metadata is maintained independently by the harness, never inferred.
    if "phase1" not in config:
        sidecar = Path(path).parent.parent / "phase1.json"
        try:
            metadata = json.loads(sidecar.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            _fail("phase1", f"missing from config; cannot read required phase1.json sidecar ({type(exc).__name__})")
        config["phase1"] = _object(metadata, "phase1 sidecar")
    if config.get("timezone") != "Asia/Shanghai":
        _fail("timezone", "Phase 1 requires Asia/Shanghai")
    local_timestamp("2026-10-19T00:00:00Z", config["timezone"])
    dashboard = _object(config.get("dashboard"), "dashboard")
    if dashboard.get("enable") is not False:
        _fail("dashboard.enable", "must be false in the offline profile")

    phase = _object(config.get("phase1"), "phase1")
    if phase.get("mode") != "offline":
        _fail("phase1.mode", "must be offline")
    self_id = phase.get("mock_self_id")
    if type(self_id) is not int or self_id <= 0:
        _fail("phase1.mock_self_id", "must be a positive synthetic integer identity")
    paths = _object(phase.get("paths"), "phase1.paths")
    for name in ("data_dir", "persona_dir", "plugin_lock"):
        _relative_path(paths.get(name), f"phase1.paths.{name}")
    plugins = _list(phase.get("plugins"), "phase1.plugins")
    for index, plugin in enumerate(plugins):
        if not isinstance(plugin, str) or plugin not in PLUGIN_IDS:
            _fail(f"phase1.plugins[{index}]", "must name an audited Phase 1 plugin candidate")
    if len(plugins) != len(set(plugins)):
        _fail("phase1.plugins", "must not contain duplicate candidates")
    plugin_set = _list(config.get("plugin_set"), "plugin_set", nonempty=False)
    for index, plugin in enumerate(plugin_set):
        _text(plugin, f"plugin_set[{index}]")

    platforms = _list(config.get("platform"), "platform")
    platform_ids = set()
    active = 0
    for index, platform in enumerate(platforms):
        field = f"platform[{index}]"
        _object(platform, field)
        platform_id = _text(platform.get("id"), f"{field}.id")
        if platform_id in platform_ids:
            _fail(f"{field}.id", "must be unique")
        platform_ids.add(platform_id)
        if platform.get("type") != "aiocqhttp":
            _fail(f"{field}.type", "must be the OneBot v11 aiocqhttp adapter")
        if type(platform.get("enable")) is not bool:
            _fail(f"{field}.enable", "must be a boolean")
        active += platform["enable"]
        host = platform.get("ws_reverse_host")
        if not isinstance(host, str) or host not in LOOPBACK_HOSTS:
            _fail(f"{field}.ws_reverse_host", "must bind only to loopback")
        port = platform.get("ws_reverse_port")
        if type(port) is not int or not 1024 <= port <= 65535:
            _fail(f"{field}.ws_reverse_port", "must be an integer from 1024 through 65535")
        token = _text(platform.get("ws_reverse_token"), f"{field}.ws_reverse_token")
        if not token.startswith("offline-fixture-"):
            _fail(f"{field}.ws_reverse_token", "must use an explicit offline fixture token")
    if active != 1:
        _fail("platform", "must enable exactly one simulated OneBot endpoint")

    settings = _object(config.get("platform_settings"), "platform_settings")
    if settings.get("enable_id_white_list") is not True:
        _fail("platform_settings.enable_id_white_list", "must be true")
    if settings.get("unique_session") is not True:
        _fail("platform_settings.unique_session", "must be true for session isolation")
    whitelist = _list(settings.get("id_whitelist"), "platform_settings.id_whitelist")
    for index, identity in enumerate(whitelist):
        field = f"platform_settings.id_whitelist[{index}]"
        identity = _text(identity, field)
        parts = identity.split(":")
        is_group = identity.isascii() and identity.isdecimal() and int(identity) > 0
        is_private = (len(parts) == 3 and parts[0] in platform_ids
                      and parts[1] == "FriendMessage" and parts[2].isascii()
                      and parts[2].isdecimal() and int(parts[2]) > 0)
        if not is_group and not is_private:
            _fail(field, "must be an explicit synthetic group ID or platform:FriendMessage:user ID")
    for name in ("wl_ignore_admin_on_group", "wl_ignore_admin_on_friend"):
        if settings.get(name) is not False:
            _fail(f"platform_settings.{name}", "must be false; administrators cannot bypass the allowlist")
    admins = _list(config.get("admins_id"), "admins_id", nonempty=False)
    admin_fixture = phase.get("admin_fixture_enabled", False)
    if type(admin_fixture) is not bool:
        _fail("phase1.admin_fixture_enabled", "must be a boolean when present")
    if admins and not admin_fixture:
        _fail("admins_id", "must be empty unless phase1.admin_fixture_enabled is true")
    allowed_private_users = {entry.rsplit(":", 1)[1] for entry in whitelist if ":FriendMessage:" in entry}
    for index, admin in enumerate(admins):
        if (type(admin) not in (int, str) or not str(admin).isascii()
                or not str(admin).isdecimal() or str(admin) not in allowed_private_users):
            _fail(f"admins_id[{index}]", "synthetic administrators must be a subset of the private-message allowlist")

    source_ids = set()
    for index, source in enumerate(_list(config.get("provider_sources"), "provider_sources")):
        field = f"provider_sources[{index}]"
        _object(source, field)
        source_id = _text(source.get("id"), f"{field}.id")
        if source_id in source_ids:
            _fail(f"{field}.id", "must be unique")
        source_ids.add(source_id)
        if source.get("type") != "openai_chat_completion":
            _fail(f"{field}.type", "must use the OpenAI-compatible mock provider")
        if source.get("enable") is not True:
            _fail(f"{field}.enable", "must be true")
        api_base = _text(source.get("api_base"), f"{field}.api_base")
        try:
            url = urlsplit(api_base)
            port = url.port
        except ValueError:
            _fail(f"{field}.api_base", "must be a valid loopback HTTP endpoint")
        if (url.scheme != "http" or url.hostname not in LOOPBACK_HOSTS
                or port is None or not 1024 <= port <= 65535
                or url.username or url.password or url.query or url.fragment
                or url.path.rstrip("/") != "/v1"):
            _fail(f"{field}.api_base", "must be http://loopback:port/v1 without credentials")
        if source.get("proxy", "") != "":
            _fail(f"{field}.proxy", "must be empty for offline loopback traffic")
        for key_index, key in enumerate(_list(source.get("key"), f"{field}.key")):
            if not isinstance(key, str) or not key.startswith("offline-fixture-"):
                _fail(f"{field}.key[{key_index}]", "must use an explicit offline fixture key")

    provider_ids = set()
    for index, provider in enumerate(_list(config.get("provider"), "provider")):
        field = f"provider[{index}]"
        _object(provider, field)
        provider_id = _text(provider.get("id"), f"{field}.id")
        if provider_id in provider_ids:
            _fail(f"{field}.id", "must be unique")
        provider_ids.add(provider_id)
        source_id = provider.get("provider_source_id")
        if not isinstance(source_id, str) or source_id not in source_ids:
            _fail(f"{field}.provider_source_id", "must reference a configured mock provider source")
        _text(provider.get("model"), f"{field}.model")
        if provider.get("enable") is not True:
            _fail(f"{field}.enable", "must be true")
    runner = _object(config.get("agent_runner"), "agent_runner")
    runner_config = _object(runner.get("config"), "agent_runner.config")
    model = _object(runner_config.get("model"), "agent_runner.config.model")
    provider_id = model.get("provider_id")
    if not isinstance(provider_id, str) or provider_id not in provider_ids:
        _fail("agent_runner.config.model.provider_id", "must reference a configured model")
    return config


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Validate the offline Phase 1 profile")
    parser.add_argument("path", nargs="?", default="config/offline.json")
    args = parser.parse_args()
    try:
        validate_config(args.path)
    except ValueError as exc:
        parser.exit(1, f"Configuration error: {exc}\n")
    print("PASS: offline configuration is valid")
