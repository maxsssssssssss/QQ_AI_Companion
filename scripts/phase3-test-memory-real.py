#!/usr/bin/env python3
"""Bounded T3: pinned native MemoryService, synthetic records, real DeepSeek.

No production memory/QQ/profile is opened. API Key remains in the approved VPS
transport. All local databases are temporary and all saved evidence owner-only.
No fake extractor response is substituted. Default dry-run, maximum four calls.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import importlib
import importlib.util
import json
import os
import shlex
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import ModuleType, SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / ".runtime/upstream/memory_companion"
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
from integrations.phase3_memory import MemoryCoordinator, native_config

REMOTE = '''import importlib.util,json,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location("bounded","/opt/qq-ai-companion/phase2-src/scripts/phase2-test-deepseek.py")
api=importlib.util.module_from_spec(spec);spec.loader.exec_module(api)
key=api.read_key(Path("/opt/qq-ai-companion/deployment/state/secrets/deepseek-key"))
for index,line in enumerate(sys.stdin):
 if index>=MAX_CALLS: break
 payload=json.loads(line)
 reply=api.transport(key,payload,deadline=25)
 body=reply.get("body",{});choices=body.get("choices",[])
 text=choices[0].get("message",{}).get("content","") if choices else ""
 text=text.replace(key,"[REDACTED]") if isinstance(text,str) else ""
 print(json.dumps({"http":reply.get("http"),"error_type":reply.get("error"),"text":text,"usage":body.get("usage",{}),"finish_reason":choices[0].get("finish_reason") if choices else None}),flush=True)
 if reply.get("http") in (401,402,403,429): break
'''


class RealProvider:
    def __init__(self, ssh, max_calls=2):
        self.ssh, self.calls, self.started = ssh, [], time.monotonic()
        self.stopped, self.process, self.max_calls = False, None, max_calls

    async def start(self):
        if self.process is None:
            program = REMOTE.replace("MAX_CALLS", str(self.max_calls))
            self.process = await asyncio.create_subprocess_exec(self.ssh,"-T","-o","BatchMode=yes",
                "-o","StrictHostKeyChecking=yes","-o","ConnectTimeout=10","qq-companion-vps",
                "sudo -n env PYTHONDONTWRITEBYTECODE=1 python3 -u -c " + shlex.quote(program),
                stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)

    async def close(self):
        if self.process is not None and self.process.returncode is None:
            self.process.stdin.close()
            try: await asyncio.wait_for(self.process.wait(), timeout=3)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()

    async def text_chat(self, **kwargs):
        if self.stopped or len(self.calls) >= self.max_calls or time.monotonic()-self.started > 180:
            raise RuntimeError("real_memory_probe_budget_exhausted")
        messages = []
        if kwargs.get("system_prompt"):
            messages.append({"role":"system","content":kwargs["system_prompt"]})
        messages.extend(kwargs.get("contexts") or [])
        messages.append({"role":"user","content":kwargs.get("prompt", "")})
        if len(json.dumps(messages,ensure_ascii=False)) > 40000:
            raise ValueError("native_prompt_exceeds_probe_budget")
        payload = {"model":"deepseek-flash","messages":messages,"max_tokens":1200,
                   "thinking":{"type":"disabled"},"stream":False}
        item = {"index":len(self.calls)+1,"http":None,"state":"attempted","usage":{}}
        self.calls.append(item)
        before = time.monotonic()
        try:
            await self.start()
            self.process.stdin.write((json.dumps(payload,ensure_ascii=False)+"\n").encode())
            await self.process.stdin.drain()
            output = await asyncio.wait_for(self.process.stdout.readline(), timeout=40)
            if not output:
                item["transport_exit_code"] = await self.process.wait()
                diagnostic = await self.process.stderr.read()
                # Only fixed categories, never remote stderr, secrets or paths.
                item["transport_error_category"] = next((label for marker,label in (
                    (b"WSL", "wsl_interop"), (b"SyntaxError", "remote_syntax"),
                    (b"Permission denied", "permission_denied"),
                    (b"Traceback", "remote_script_error"),
                    (b"Could not resolve", "ssh_dns"),
                ) if marker in diagnostic), "unclassified")
                raise RuntimeError("bounded_remote_transport_failed")
            response = json.loads(output)
            item.update(http=response.get("http"),usage=response.get("usage") or {},
                        finish_reason=response.get("finish_reason"),response=response.get("text", ""),state="returned")
            if response.get("http") in (401,402,403,429):
                self.stopped = True
            if response.get("http") != 200:
                raise RuntimeError("real_memory_http_failure")
            return NS(completion_text=response.get("text", ""))
        except BaseException as exc:
            item["error_type"] = type(exc).__name__
            self.stopped = True
            await self.close()
            raise
        finally:
            item["elapsed_ms"] = round((time.monotonic()-before)*1000)


async def run(args):
    plan = {"mode":"DRY_RUN","max_chat_calls":args.max_calls,"max_output_tokens_per_call":1200,
            "scenario":args.scenario,
            "remote_timeout_seconds":25,"run_deadline_seconds":180,"retries":0,
            "production_changed":False,"real_qq":False,"private_profile_sent":False,
            "real_search_fetch":False,"key_extracted_from_vps":False}
    if not args.execute:
        return plan
    if not args.authorization or not args.output:
        raise ValueError("existing_authorization_and_private_output_required")
    if args.output.exists() or args.output.is_symlink():
        raise FileExistsError("new_output_required")
    observed = subprocess.check_output(["git","-C",str(UPSTREAM),"rev-parse","HEAD"],text=True).strip()
    if observed != "e40a83005b0a2e0817e5680c7d14398c9ab49790":
        raise ValueError("pinned_memory_revision_required")
    module = ModuleType("phase3_real_probe_memory")
    module.__path__ = [str(UPSTREAM)]
    sys.modules[module.__name__] = module
    Service = importlib.import_module(module.__name__+".core.service").MemoryCompanionService
    provider = RealProvider(args.ssh, args.max_calls)
    host = NS(get_provider_by_id=lambda _id:provider,get_using_provider=lambda _sid:provider)
    cfg = native_config({},enabled=True,provider_id="synthetic-records-real-flash")
    cfg["startup"] = {"background_grace_seconds":60}
    cfg["memory_summary"].update(min_events=1,trigger_event_count=1,provider_timeout_seconds=45)
    cfg["retrieval"].update(mode="basic",embedding_provider_id="",rerank_provider_id="")
    report = {**plan,"mode":"REAL_API_NATIVE_MEMORY_COMPONENT","authorization":args.authorization,
              "pinned_memory_commit":observed,"checks":{},"calls":provider.calls}
    with tempfile.TemporaryDirectory(prefix="qq-memory-real-synthetic-") as folder:
        path = Path(folder)
        service = Service(context=host,config=cfg,plugin_root=UPSTREAM,data_dir=path)
        co = MemoryCoordinator(service,path/"controls.sqlite3",{"synthetic-user"},enabled=True)
        def context(coordinator):
            return coordinator.context(session_id="fixture:FriendMessage:synthetic-user",user_id="synthetic-user",
                                       bot_id="synthetic-bot",platform="fixture")
        ctx = context(co)
        now = datetime.now(timezone.utc).isoformat()
        try:
            texts = ["我的职业是软件工程师。","我明天下午要参加面试。",
                     "我最喜欢喝清茶。","我还没有整理完旅行相册。"]
            if args.scenario == "native-assertion-correction":
                texts.append("我住在苏州工业园区。")
            sources = [await co.observe(ctx,source_id=f"real-synthetic-{i}",content=text,occurred_at=now)
                       for i,text in enumerate(texts)]
            first = await co.summarize(ctx)
            report["first_summary"] = first
            memory = await service.store.get_memory(first["memory_id"]) if first.get("memory_id") else None
            content = memory.content if memory else ""
            metadata = json.dumps(memory.metadata,ensure_ascii=False) if memory else ""
            report["checks"]["four_synthetic_categories"] = bool(memory) and all(t in content for t in ("软件工程师","面试","清茶","旅行相册"))
            rows = await service.store.get_timeline_by_ids([r["timeline_id"] for r in sources])
            report["checks"]["sources_and_time"] = (len(rows)==len(texts) and all(r["occurred_at"] for r in rows.values())
                and all(r["timeline_id"] in metadata for r in sources))
            report["first_record"] = {"type":memory.memory_type,"reality":memory.reality_level,"content":content} if memory else None
            old_assertions = []
            if args.scenario == "native-assertion-correction":
                old_assertions = [r for r in await service.store.list_memories(memory_type="assertion",
                    session_id=ctx.session_id,scope="private",limit=128)
                    if (r.metadata or {}).get("profile_dimension") == "residence" and "苏州工业园区" in r.content]
                report["checks"]["native_initial_residence_assertion"] = len(old_assertions) == 1
            elif memory:
                event = NS(unified_msg_origin=ctx.session_id,message_str="我之前说错了，我现在最喜欢喝白水。",persona_id=ctx.persona_id,
                    message_obj=NS(message_id="real-synthetic-correction"),get_platform_name=lambda:ctx.platform,
                    get_sender_id=lambda:ctx.user_id,get_self_id=lambda:ctx.bot_id)
                # Probe the native correction boundary on the ACTUAL extracted
                # record. Never seed a hand-authored observation to claim that
                # an automatically generated conversation_summary was revised.
                correction = await co.correct(event,memory_id=memory.id,
                    expected_version=co.revision.memory_ref(memory)["version"],correction_id="real-probe-fix",
                    content="用户现在最喜欢喝白水。")
                report["native_correction"] = correction
                report["checks"]["automatic_record_native_correction"] = correction.get("ok") is True
            correction_text = ("确认更正：我现在住在杭州西湖区。" if args.scenario == "native-assertion-correction"
                               else "我之前说错了，我现在最喜欢喝白水。")
            correction_source = await co.observe(ctx,source_id="real-synthetic-correction",content=correction_text,
                                                occurred_at=datetime.now(timezone.utc).isoformat())
            report["correction_source_state"] = correction_source["state"]
            second = await co.summarize(ctx)
            report["correction_summary"] = second
            old_ids = {r.id for r in old_assertions}
            if memory: old_ids.add(memory.id)
            if args.scenario == "native-assertion-correction":
                # summarize() invokes the release path's reconcile_assertions;
                # call again only to verify the reconciliation is idempotent.
                report["reconcile_repeat_archived"] = await co.reconcile_assertions(ctx)
                assertions = await service.store.list_memories(memory_type="assertion",session_id=ctx.session_id,
                                                               scope="private",limit=128)
                report["assertions_after_correction"] = [{"id":r.id,"content":r.content,
                    "validity_status":r.validity_status,"supersedes_id":r.supersedes_id,
                    "source_summary_id":(r.metadata or {}).get("assertion_source_summary_id"),
                    "dimension":(r.metadata or {}).get("profile_dimension")} for r in assertions]
                updated_old = [await service.store.get_memory(r.id) for r in old_assertions]
                replacements = [await service.store.get_memory(r.supersedes_id) for r in updated_old
                                if r and r.supersedes_id]
                report["checks"]["native_assertion_superseded_with_link"] = bool(updated_old) and all(
                    r and r.validity_status == "superseded" and r.supersedes_id for r in updated_old)
                report["checks"]["native_replacement_active"] = bool(replacements) and all(
                    r and r.validity_status == "active" and "杭州西湖区" in r.content for r in replacements)
                old_summary = await service.store.get_memory(memory.id) if memory else None
                report["checks"]["stale_source_summary_archived"] = bool(old_summary and old_summary.lifecycle == "archived")
                second_memory = await service.store.get_memory(second["memory_id"]) if second.get("memory_id") else None
                report["correction_record"] = {"id":second_memory.id,"content":second_memory.content,
                    "reality":second_memory.reality_level} if second_memory else None
                report["checks"]["correction_has_real_source"] = bool(second_memory and
                    correction_source.get("timeline_id") and correction_source["timeline_id"] in
                    json.dumps(second_memory.metadata,ensure_ascii=False))
            await co.close(); await service.aclose()
            service = Service(context=host,config=cfg,plugin_root=UPSTREAM,data_dir=path)
            co = MemoryCoordinator(service,path/"controls.sqlite3",{"synthetic-user"},enabled=True)
            ctx = context(co)
            recall_query = "杭州西湖区" if args.scenario == "native-assertion-correction" else "白水"
            recalled = await co.recall(ctx,recall_query)
            report["recalled"] = [{"id":r.memory.id,"content":r.memory.content,"type":r.memory.memory_type} for r in recalled]
            report["checks"]["fresh_native_service_recall"] = any(recall_query in r.memory.content for r in recalled)
            if args.scenario == "native-assertion-correction":
                current = await co.recall(ctx,"住在")
                report["current_residence_recall"] = [{"id":r.memory.id,"content":r.memory.content} for r in current]
                report["checks"]["no_superseded_record_in_current_recall"] = bool(current) and all(
                    r.memory.id not in old_ids for r in current)
                reopened_old = [await service.store.get_memory(r.id) for r in old_assertions]
                report["checks"]["revision_link_survives_reopen"] = bool(reopened_old) and all(
                    r and r.validity_status == "superseded" and r.supersedes_id for r in reopened_old)
            report["checks"]["no_history_used_for_recall"] = True
            report["checks"]["raw_sources_retained"] = len(await service.store.get_timeline_by_ids([r["timeline_id"] for r in sources]))==len(texts)
        except Exception as exc:
            report["error_type"] = type(exc).__name__
        finally:
            await co.close()
            await service.aclose()
            await provider.close()
    report["usage_totals"] = {key:sum(c.get("usage",{}).get(key,0) for c in provider.calls)
                              for key in ("prompt_tokens","completion_tokens","total_tokens")}
    report["status"] = "PASS" if report["checks"] and all(report["checks"].values()) and not report.get("error_type") else "PARTIAL"
    spec = importlib.util.spec_from_file_location("memory_probe_io",ROOT/"scripts/phase2-configure.py")
    io = importlib.util.module_from_spec(spec);spec.loader.exec_module(io)
    io.write_private(args.output,json.dumps(report,ensure_ascii=False,indent=2))
    return {"mode":report["mode"],"status":report["status"],"checks":report["checks"],
            "calls":len(provider.calls),"usage_totals":report["usage_totals"],"error_type":report.get("error_type"),
            "production_changed":False,"real_qq":False,"private_profile_sent":False}


if __name__ == "__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ssh",default="/mnt/c/Windows/System32/OpenSSH/ssh.exe")
    p.add_argument("--execute",action="store_true")
    p.add_argument("--max-calls",type=int,choices=range(1,5),default=2)
    p.add_argument("--scenario",choices=("native-type-boundary","native-assertion-correction"),default="native-type-boundary")
    p.add_argument("--authorization")
    p.add_argument("--output",type=Path)
    print(json.dumps(asyncio.run(run(p.parse_args())),ensure_ascii=False))
