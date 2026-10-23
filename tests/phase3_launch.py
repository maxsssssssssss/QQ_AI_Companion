"""T2-only headless lifecycle. Loopback fixture configuration is mandatory.

Uses the same Phase3Runtime.prepare method as production Phase2Policy. The
production endpoint validator is tested separately, never given a test bypass.
"""
import asyncio
import json
import os
from pathlib import Path
import signal
import sys

SOURCE = Path(os.environ["PHASE3_ASTRBOT_SOURCE"])
ROOT = Path(__file__).resolve().parents[1]
STATE = Path(os.environ["ASTRBOT_ROOT"])
sys.path[:0] = [str(SOURCE), str(STATE), str(ROOT / "scripts")]
import runtime_bootstrap
runtime_bootstrap.initialize_runtime_bootstrap()


async def main():
    from configuration import validate_config
    validate_config(STATE / "data/cmd_config.json")
    from astrbot.core import LogBroker, LogManager, db_helper, logger
    from astrbot.core.core_lifecycle import AstrBotCoreLifecycle
    from plugin_permissions import apply_plugin_permissions
    from integrations.phase3_runtime.persona import install_persona
    broker = LogBroker()
    LogManager.set_queue_handler(logger, broker)
    core = AstrBotCoreLifecycle(broker, db_helper)
    await core.initialize()
    await apply_plugin_permissions(core)
    candidates = [p.star_cls for p in core.star_context.get_all_stars()
                  if str(p.module_path).endswith(".phase3_runtime.main") and p.activated]
    if len(candidates) != 1 or candidates[0] is None:
        raise RuntimeError("Phase3Runtime not loaded")
    runtime = candidates[0]
    prompt = await runtime.prepare("10001", "offline")
    await install_persona(core.persona_mgr, prompt, tools=runtime.memory_tool_names())
    await runtime.activate()
    if (STATE / 'clear-context.flag').exists():
        await core.conversation_manager.new_conversation('offline:FriendMessage:10001', persona_id='qq_companion')
        (STATE / 'clear-context.flag').unlink()
    (STATE / "startup.json").write_text(json.dumps({"pid": os.getpid(), "phase3_ready": runtime.ready,
        "plugins": [{"module": p.module_path, "version": p.version, "active": p.activated,
                     "instantiated": p.star_cls is not None} for p in core.star_context.get_all_stars()]}))
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    task = asyncio.create_task(core.start())
    async def fixture_probes():
        while not stop.is_set():
            flag=STATE/'profile-probe.flag'
            if flag.exists():
                flag.unlink()
                try:
                    from phase3_profile_fixture import manifest, approval
                    from integrations.phase3_memory.profile_release import ProfileRelease
                    ctx=runtime.memory.context_for_session('offline:FriendMessage:10001')
                    m=manifest(ctx)
                    # The Windows-backed /state fixture cannot enforce POSIX
                    # 0700. Keep the import ledger in this container's tmpfs.
                    result=await ProfileRelease(runtime.memory.service,ctx,Path('/tmp/phase3-synthetic-profile/ledger.sqlite3')).apply(m,approval(m),apply=True)
                except Exception as exc:
                    import traceback
                    result={'state':'FAILED','error_type':type(exc).__name__,'trace':traceback.format_exc()}
                (STATE/'profile-probe.json').write_text(json.dumps(result))
            flag=STATE/'profile-history-probe.flag'
            if flag.exists():
                flag.unlink()
                try:
                    from phase3_profile_fixture import failed_tool_history
                    umo='offline:FriendMessage:10001'
                    previous=await core.conversation_manager.get_curr_conversation_id(umo)
                    history=failed_tool_history()
                    current=await core.conversation_manager.new_conversation(
                        umo,persona_id='qq_companion',content=history,
                        title='Synthetic profile evidence with sixteen old failed rows')
                    preserved=bool(previous and await core.conversation_manager.get_conversation(umo,previous))
                    result={'state':'APPLIED','history_items':len(history),
                        'previous_conversation_retained':preserved,'new_conversation':current!=previous}
                except Exception as exc:
                    result={'state':'FAILED','error_type':type(exc).__name__}
                (STATE/'profile-history-probe.json').write_text(json.dumps(result))
            flag=STATE/'proactive-probe.flag'
            if flag.exists():
                flag.unlink()
                try:
                    from phase3_proactive_probe import run_probe
                    result=await asyncio.wait_for(run_probe(core,runtime),120)
                except Exception as exc:
                    import traceback
                    result={'pass':False,'error_type':type(exc).__name__,'trace':traceback.format_exc()}
                (STATE/'proactive-probe.json').write_text(json.dumps(result,ensure_ascii=False))
            await asyncio.sleep(.2)
    probe_task=asyncio.create_task(fixture_probes())
    stop_task = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait((task, stop_task), return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            await task
            raise RuntimeError("AstrBot stopped before test teardown")
    finally:
        probe_task.cancel()
        stop_task.cancel()
        await core.stop()
        task.cancel()
        await asyncio.gather(task, stop_task, probe_task, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
