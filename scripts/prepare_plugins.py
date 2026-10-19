#!/usr/bin/env python3
"""Stage pinned, unchanged plugins and a restrictive offline evaluation profile.

Source checkouts are external inputs, never vendored. This command does not
fetch, modify or publish third-party source. Run in the isolated test container.
"""
from __future__ import annotations

import argparse
import copy
import json
import hashlib
from pathlib import Path
import shutil

PROJECT = Path(__file__).resolve().parents[1]


def schema_defaults(schema: dict) -> dict:
    result = {}
    for key, item in schema.items():
        if item.get("type") == "object":
            result[key] = schema_defaults(item.get("items", {}))
        elif "default" in item:
            result[key] = copy.deepcopy(item["default"])
    return result


def set_leaf(config: dict, name: str, value) -> None:
    """Keep grouped and legacy flat configuration aliases consistent."""
    for key in tuple(config):
        if isinstance(config[key], dict):
            set_leaf(config[key], name, value)
        elif key == name:
            config[key] = value


def private_profile(config: dict) -> dict:
    # The upstream schema contains hundreds of optional capabilities. Disable
    # every enable* switch, then explicitly retain protective checks and basic
    # synthetic local profiles. The scheduler can still initialize, but has no
    # proactive target, quota, enabled external action, or network namespace.
    retained = {
        "enable_store_control_tag_sanitization", "enable_outbound_secret_redaction",
        "enable_daily_token_soft_limit", "enable_framework_error_leak_guard",
        "enable_group_privacy_guard", "enable_group_third_party_portrait_guard",
        "enable_group_member_safety", "enable_group_reality_promise_guard",
        "enable_local_photo_load_guard", "enable_creative_work_read_guard",
        "enable_auto_user_profile_creation",
    }
    def walk(obj):
        for key, value in obj.items():
            if isinstance(value, dict):
                walk(value)
            elif isinstance(value, bool) and (key.startswith("enable") or key.endswith("_enabled")):
                obj[key] = key in retained
    walk(config)
    for name, value in {
        "target_user_ids": [], "target_group_ids": [], "group_whitelist_ids": [],
        "default_enable_configured_targets": False, "default_proactive_enabled": False,
        "default_proactive_daily_limit": 0, "owner_exclusive_proactive_limit": 0,
        "proactive_intensity_preset": "off", "require_private_opt_in": True,
        "enabled_proactive_actions": "", "storage_backend": "json",
        "allow_insomnia_night_message": False,
        "allow_generate_photo_on_reaction_turns": False,
        "inject_passive_states": False, "include_schedule_in_messages": False,
        "intercept_astrbot_group_context": False,
        "forward_message_image_vision": False, "worldbook_auto_import": False,
        "worldbook_self_registration": False, "worldbook_auto_pending_observations": False,
    }.items():
        set_leaf(config, name, value)
    # Some legacy runtime switches are accepted but absent from the schema.
    config["enable_daily_review"] = False
    return config


def plugin_config(plugin_id: str, source: Path) -> dict:
    config = schema_defaults(json.loads((source / "_conf_schema.json").read_text()))
    if plugin_id == "message_recorder":
        config.update(enable_commands=True, save_media_files=False,
                      save_raw_message=False, save_message_chain=True,
                      cleanup_interval_hours=0, retention_days=0, max_records=0)
    elif plugin_id == "memory_companion":
        for group, overrides in {
            "memory_summary": {"enabled": False},
            "startup": {"background_grace_seconds": 0},
            "portrait": {"daily_dispatch_enabled": False},
            "retrieval": {"embedding_enabled": False, "mode": "basic", "embedding_index_pending": False},
            "retrieval_advanced": {"embedding_backfill_enabled": False},
            "source_semantic": {"enabled": False, "history_backfill_enabled": False},
            "private_companion_bridge": {"enabled": False, "accept_external_records": False},
            "conversation_memory_advanced": {"cross_window_recent_continuity_enabled": False,
                "cross_window_group_to_private_enabled": False, "cross_window_private_to_group_enabled": False},
            "visibility": {"allow_self_timeline_everywhere": False, "allow_group_public_in_private": False,
                           "enable_acl_rules": True},
            "maintenance": {"auto_schedule_enabled": False, "sleep_backup_enabled": False},
        }.items():
            config.setdefault(group, {}).update(overrides)
    elif plugin_id == "private_companion":
        private_profile(config)
    return config


def source_tree_sha256(source: Path) -> str:
    """Digest exact source paths/content without relying on git in the image."""
    digest = hashlib.sha256()
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if ".git" in relative.parts or "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            kind, payload = b"link", str(path.readlink()).encode()
        elif path.is_file():
            kind, payload = b"file", path.read_bytes()
        else:
            continue
        digest.update(relative.as_posix().encode() + b"\0" + kind + b"\0")
        digest.update(hashlib.sha256(payload).digest())
    return digest.hexdigest()


def prepare_plugins(state_dir: str | Path, sources_dir: str | Path) -> list[dict]:
    state, sources = Path(state_dir), Path(sources_dir)
    manifest = json.loads((PROJECT / "config/plugins-lock.json").read_text())
    result = []
    for plugin in manifest["plugins"]:
        source = sources / plugin["id"]
        if not source.is_dir():
            source = sources / plugin["directory"]
        observed_hash = source_tree_sha256(source)
        if observed_hash != plugin["source_tree_sha256"]:
            raise RuntimeError(f"{plugin['id']} source integrity mismatch: {observed_hash}")
        actual = plugin["commit"]
        destination = state / "data/plugins" / plugin["directory"]
        if destination.exists():
            raise FileExistsError(f"Refusing to overwrite existing plugin: {destination}")
        shutil.copytree(source, destination, symlinks=True,
                        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
        config_path = state / "data/config" / f"{plugin['directory']}_config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        if config_path.exists():
            raise FileExistsError(f"Refusing to overwrite existing plugin configuration: {config_path}")
        config_path.write_text(json.dumps(plugin_config(plugin["id"], source), ensure_ascii=False, indent=2) + "\n")
        result.append({"id": plugin["id"], "commit": actual, "metadata_version": plugin["metadata_version"],
                       "source_unchanged": True, "configuration": str(config_path)})
    (state / "plugin-preparation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--sources", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare_plugins(args.state, args.sources), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
