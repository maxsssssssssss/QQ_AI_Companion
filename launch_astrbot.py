"""Headless entrypoint into the unmodified, pinned AstrBot lifecycle.

The WebUI is deliberately outside the Phase 1 offline workflow. No adapter,
pipeline, provider, plugin or database implementation is substituted here.
"""
import asyncio
import json
import os
from pathlib import Path
import signal
import sys

sys.path.insert(0, "/AstrBot")
import runtime_bootstrap

runtime_bootstrap.initialize_runtime_bootstrap()


async def main():
    # AstrBot imports external plugins as data.plugins.*; runtime root is a
    # supported location separate from its read-only source tree.
    sys.path.insert(0, os.environ["ASTRBOT_ROOT"])
    sys.path.insert(0, str(Path(__file__).parent / "scripts"))
    from configuration import validate_config
    validate_config(Path(os.environ["ASTRBOT_ROOT"]) / "data/cmd_config.json")
    from astrbot import __version__
    from astrbot.core import LogBroker, LogManager, db_helper, logger
    from astrbot.core.core_lifecycle import AstrBotCoreLifecycle

    root = Path(os.environ["ASTRBOT_ROOT"])
    for name in ("config", "plugins", "temp", "knowledge_base", "site-packages"):
        (root / "data" / name).mkdir(parents=True, exist_ok=True)
    broker = LogBroker()
    LogManager.set_queue_handler(logger, broker)
    core = AstrBotCoreLifecycle(broker, db_helper)
    await core.initialize()
    from plugin_permissions import apply_plugin_permissions
    permissions = await apply_plugin_permissions(core)
    # Public persona API: disable tools/skills for this text-only acceptance profile.
    persona_file = root / "persona.md"
    if not persona_file.exists():
        raise ValueError("Missing external persona file: ASTRBOT_ROOT/persona.md")
    prompt = persona_file.read_text()
    if not any(p.persona_id == "offline" for p in core.persona_mgr.personas):
        await core.persona_mgr.create_persona(
            "offline", prompt,
            tools=[], skills=[],
        )
    else:
        await core.persona_mgr.update_persona("offline", system_prompt=prompt, tools=[], skills=[])
    diagnostic = {
        "version": __version__, "pid": os.getpid(),
        "plugins": [{"name": p.name, "version": p.version,
                     "activated": p.activated, "instantiated": p.star_cls is not None,
                     "module": p.module_path}
                    for p in core.star_context.get_all_stars()],
        "providers": list(core.provider_manager.inst_map),
        "entrypoint": "AstrBotCoreLifecycle.initialize/start",
        "plugin_permissions": permissions,
    }
    (root / "startup.json").write_text(json.dumps(diagnostic, indent=2))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    core_task = asyncio.create_task(core.start())
    stop_task = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait((core_task, stop_task), return_when=asyncio.FIRST_COMPLETED)
        if core_task in done:
            await core_task
            raise RuntimeError("AstrBot lifecycle ended before shutdown was requested")
    finally:
        stop_task.cancel()
        await core.stop()
        core_task.cancel()
        await asyncio.gather(core_task, stop_task, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
