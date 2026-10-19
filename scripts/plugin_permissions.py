"""Configure plugin command ACLs through AstrBot's command-management API.

This changes supported runtime configuration, not third-party source or handler
implementations. The policy is applied before the event loop starts accepting
messages and is persisted by AstrBot's own preference store.
"""
from __future__ import annotations


def flatten_commands(commands):
    for command in commands:
        yield command
        yield from flatten_commands(command.get("sub_commands", []))


async def apply_plugin_permissions(core) -> dict:
    from astrbot.core.star.command_management import (
        list_commands,
        update_command_permission,
    )

    commands = list(flatten_commands(await list_commands()))
    recorder = [command for command in commands
                if "astrbot_plugin_message_recorder" in command.get("module_path", "")]
    for command in recorder:
        await update_command_permission(command["handler_full_name"], "admin")
    fresh = list(flatten_commands(await list_commands()))
    recorder_after = [command for command in fresh
                      if "astrbot_plugin_message_recorder" in command.get("module_path", "")]
    memory = [command for command in fresh
              if "astrbot_plugin_memory_companion" in command.get("module_path", "")]
    loaded_recorder = any("astrbot_plugin_message_recorder" in str(p.module_path)
                          for p in core.star_context.get_all_stars() if p.activated)
    if loaded_recorder and not recorder_after:
        raise RuntimeError("Recorder loaded without discoverable commands; cannot confirm command ACL")
    if any(command["permission"] != "admin" for command in recorder_after):
        raise RuntimeError("Recorder command permission configuration did not take effect")
    # MemoryCompanion explicitly declares administrator permissions on its
    # management subcommands. Report the effective descriptors for evidence.
    summarize = lambda command: {
        "handler_full_name": command["handler_full_name"],
        "command": command["effective_command"],
        "permission": command["permission"],
    }
    return {
        "mechanism": "AstrBot command_management.update_command_permission",
        "recorder_commands": [summarize(command) for command in recorder_after],
        "memory_commands": [summarize(command) for command in memory],
        "source_modified": False,
    }
