#!/usr/bin/env python3
"""Prepare reviewable configuration in a NEW directory; never edit a live state.

Input must be a stopped, privately retained Phase2 snapshot. This tool is NOT a
deployment command, consent to migrate a database, or permission to start QQ.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
from integrations.phase3_runtime.settings import validate_settings, private_config
from integrations.phase3_runtime.persona import CURRENT_TEMPLATE
from integrations.phase3_memory.bridge import native_config
from integrations.phase3_runtime.proactive import native_proactive_config
from integrations.phase2_policy.policy import validate_policy
spec = importlib.util.spec_from_file_location("phase2_staging_helpers", ROOT / "scripts/phase2-configure.py")
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
context_spec = importlib.util.spec_from_file_location("phase3_context_config", ROOT / "scripts/phase3-context-config.py")
context_config = importlib.util.module_from_spec(context_spec)
context_spec.loader.exec_module(context_config)


def candidate(base, *, settings, admin_id, platform_id, search_key=None):
    settings = validate_settings(settings)
    validate_policy(base, admin_id, platform_id,
                    capabilities={"search": base.get("provider_settings", {}).get("web_search") is True})
    config = context_config.make_candidate(base)
    provider = config["provider_settings"]
    provider.update(web_search=settings.get("search", False), buffer_intermediate_messages=False,
                    display_reasoning_text=False, show_tool_use_status=False, show_tool_call_result=False)
    config["agent_runner"]["config"]["misc"].update(max_steps=10, tool_call_timeout=40)
    config["platform_settings"]["rate_limit"].update(time=60, count=40)
    for source in config["provider_sources"]:
        if source["id"] == "deepseek":
            source["timeout"] = 120
    for model in config["provider"]:
        if model["id"] == "deepseek/flash":
            model["custom_extra_body"]["max_tokens"] = 8192
            if "timeout" in model:
                model["timeout"] = 120
    if settings.get("search"):
        if not helpers.valid_key(search_key):
            raise ValueError("separate_search_key_required")
        provider.update(websearch_provider="deepseek", websearch_deepseek_key=[search_key], web_search_link=True)
    else:
        provider["websearch_deepseek_key"] = []
    validate_policy(config, admin_id, platform_id, capabilities={"search": settings.get("search", False)})
    return config


def stage(snapshot, output, settings, *, search_key_path=None):
    if output.exists() or output.is_symlink():
        raise FileExistsError("new_output_directory_required")
    if snapshot.resolve() == output.resolve() or snapshot.resolve() in output.resolve().parents:
        raise ValueError("output_must_be_outside_snapshot")
    settings = validate_settings(context_config.make_runtime_candidate(settings))
    base = json.loads(helpers.read_private(snapshot / "data/cmd_config.json"))
    identity = json.loads(helpers.read_private(snapshot / "data/config/phase2_policy_config.json"))
    key = helpers.read_private(search_key_path).strip() if search_key_path else None
    new = candidate(base, settings=settings, search_key=key, **identity)
    output.mkdir(mode=0o700, parents=True)
    def write(path, value):
        helpers.write_private(output / path, json.dumps(value, ensure_ascii=False, indent=2))
    write("data/cmd_config.json", new)
    write("data/config/phase2_policy_config.json", identity)
    write("data/config/phase3_runtime_config.json", settings)
    write("data/config/phase1_guard_config.json", {
        "send_timeout_seconds": 2, "phase3_enabled": settings.get("natural_chat", False),
        "phase3_proactive_enabled": settings.get("proactive", False),
        "phase3_multibubble_enabled": True,
        "phase3_casual_split_fallback_enabled": True,
        "phase3_bubble_min_interval": 1.0,"phase3_bubble_max_interval": 2.0,
        "phase3_admin_id": identity["admin_id"], "phase3_platform_id": identity["platform_id"],
        "phase3_group_window": .8, "phase3_group_max_wait": 3, "phase3_group_max_messages": 8})
    write("data/config/phase3_search_config.json", {
        "enabled": settings.get("search", False), **identity,
        "scope": "general_public", "public_terms": [], "fetch_hosts": [],
        "daily_attempt_limit": 60, "limits": {"max_calls": 3}})
    for name in ("private_companion", "memory_companion"):
        file = Path("data/config") / ("astrbot_plugin_" + name + "_config.json")
        old = json.loads(helpers.read_private(snapshot / file))
        updated = (native_proactive_config(private_config(old), identity["admin_id"], enabled=settings.get("proactive", False),
                                          policy=settings.get("proactive_window")) if name == "private_companion" else
                   native_config(old, enabled=settings.get("memory", False), provider_id="deepseek/flash"))
        if name == "memory_companion":
            # A stored enabled flag would let the upstream worker start before
            # Phase2Policy verifies the full release. Runtime activates in memory
            # only after validation and checks the old native timeline backlog.
            updated["memory_summary"]["enabled"] = False
        write(file, updated)
    # Only first-party sources. Original upstream plugins remain supplied via
    # their existing pinned private deployment; never repack them here.
    for name in ("phase1_guard", "phase2_policy", "phase3_runtime", "phase3_memory", "phase3_search"):
        shutil.copytree(ROOT / "integrations" / name, output / "data/plugins" / name,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copyfile(ROOT / "scripts/plugin_permissions.py", output / "data/plugins/phase2_policy/plugin_permissions.py")
    # Keep the established deployment slot for loader/installer compatibility;
    # the marker inside the template identifies the actual persona version.
    helpers.write_private(output / "data/phase3/persona-v0.1.md", (ROOT / "persona" / CURRENT_TEMPLATE).read_text())
    manifest = {"mode": "STAGED_NOT_DEPLOYED", "production_changes": False, "snapshot_database_copied": False,
                "profile_import": "PENDING_APPROVAL", "background_web": False,
                "native_memory_activation": "AFTER_POLICY_AND_AUTHORIZED_TIMELINE_CHECK" if settings.get("memory") else "DISABLED",
                "native_proactive_target_migration": "REQUIRES_SEPARATE_APPROVED_BACKUP_AND_CAPABILITY_UPDATE" if settings.get("proactive") else "DISABLED",
                "enabled_features": [name for name in ("persona", "natural_chat", "memory", "search", "proactive") if settings.get(name)],
                "source_config_sha256": hashlib.sha256((snapshot / "data/cmd_config.json").read_bytes()).hexdigest(),
                "required_next_step": "review configuration and plugin diffs; approve production backup/migration/restart; do not overwrite the whole data directory"}
    write("review-manifest.json", manifest)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--search-key-file", type=Path)
    args = parser.parse_args()
    print(json.dumps(stage(args.snapshot, args.output, json.loads(args.settings.read_text()), search_key_path=args.search_key_file), ensure_ascii=False))
