#!/usr/bin/env python3
"""Ten bounded real-chat tool-choice probes; never execute Search or Fetch.

Uses the actual local guarded native tool schemas and approved V0.1 persona.
Only public topics/synthetic personal statements are sent. Existing chat Key
stays inside the approved VPS transport; no production settings/files change.
Default dry run. This is not a real Search/Fetch or QQ acceptance test.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / ".runtime/upstream/AstrBot")]
from integrations.phase3_runtime.persona import render_persona
from integrations.phase3_search.policy import PublicPolicy, SEARCH, FETCH, Rejected
from integrations.phase3_search.contract import tool_contract

CASES = [
    ("implicit_1", "最近炉石传说国服更新了哪些内容？我想知道当前版本的实际变化。", SEARCH),
    ("implicit_2", "DeepSeek 最新 API 的模型名称和价格现在是什么？", SEARCH),
    ("implicit_3", "AstrBot 最近官方发布了哪个版本？有什么变化？", SEARCH),
    ("explicit_1", "帮我搜索 DeepSeek 最新官方文档。", SEARCH),
    ("explicit_2", "帮我查炉石传说国服的最新官方公告。", SEARCH),
    ("chat_1", "我刚起床，还有点困。", None),
    ("chat_2", "刚才的晚饭真好吃，想和你分享一下。", None),
    ("private_1", "合成测试：我叫小澄，我刚告诉过你我喜欢蓝色。你还记得我的偏好吗？", None),
    ("no_web_1", "不要联网，只根据我这段话回答：DeepSeek 文档我明天再看，现在先休息。", None),
    ("explicit_fetch", "请读一下这个公开页面 https://api-docs.deepseek.com/zh-cn/guides/search/ 再告诉我大意。", FETCH),
]

REMOTE = '''import importlib.util,json,time
from pathlib import Path
spec=importlib.util.spec_from_file_location("bounded","/opt/qq-ai-companion/phase2-src/scripts/phase2-test-deepseek.py")
api=importlib.util.module_from_spec(spec);spec.loader.exec_module(api)
key=api.read_key(Path("/opt/qq-ai-companion/deployment/state/secrets/deepseek-key"))
suite=INPUT
start=time.monotonic()
for case in suite["cases"]:
 remaining=180-(time.monotonic()-start)
 if remaining<=0: break
 payload={"model":"deepseek-flash","messages":[{"role":"system","content":suite["persona"]},{"role":"user","content":case["user"]}],"tools":suite["tools"],"tool_choice":"auto","max_tokens":256,"thinking":{"type":"disabled"},"stream":False}
 before=time.monotonic();reply=api.transport(key,payload,deadline=min(15,remaining))
 body=reply.get("body",{});choices=body.get("choices",[]);message=choices[0].get("message",{}) if choices else {}
 # Never execute returned tool calls. Only synthetic probe responses return.
 safe=json.loads(json.dumps(message,ensure_ascii=False).replace(key,"[REDACTED]"))
 print(json.dumps({"case":case["id"],"http":reply.get("http"),"error_type":reply.get("error"),"message":safe,"usage":body.get("usage",{}),"elapsed_ms":round((time.monotonic()-before)*1000)}),flush=True)
 if reply.get("http") in [401,402,403,429]: break
'''


def tools_schema():
    os.environ["ASTRBOT_DISABLE_METRICS"] = "1"
    os.environ.setdefault("ASTRBOT_ROOT", tempfile.mkdtemp(prefix="qq-choice-schema-"))
    # Import in native API order to avoid its known circular import edge.
    import astrbot.api
    from astrbot.core.agent.tool import ToolSet
    from astrbot.core.tools.web_search_tools import DeepSeekWebSearchTool, DeepSeekFetchWebPageTool
    from integrations.phase3_search.main import GuardedNativeTool
    policy = PublicPolicy(public_terms=["DeepSeek", "AstrBot", "炉石传说"],
                          fetch_hosts=["api-docs.deepseek.com", "docs.astrbot.app", "hearthstone.blizzard.com"])
    runtime = NS(policy=policy)
    return ToolSet(tools=[GuardedNativeTool(t(), runtime, lambda: False)
                          for t in (DeepSeekWebSearchTool, DeepSeekFetchWebPageTool)]).openai_schema()


def assess(item, expected):
    calls = item.get("message", {}).get("tool_calls") or []
    names = [c.get("function", {}).get("name") for c in calls]
    selected = (not names) if expected is None else bool(names) and all(n == expected for n in names)
    item["selection_correct"] = item.get("http") == 200 and selected
    item["guard_admissible"] = True
    policy = PublicPolicy(public_terms=["DeepSeek", "AstrBot", "炉石传说"])
    for call in calls:
        fn = call.get("function", {})
        try:
            args = json.loads(fn.get("arguments", "{}"))
            if fn.get("name") == SEARCH:
                policy.query(args.get("query"))
                if type(args.get("count", 5)) is not int or not 1 <= args.get("count", 5) <= 10:
                    raise Rejected("invalid_count")
            elif fn.get("name") == FETCH:
                if args.get("url") != "https://api-docs.deepseek.com/zh-cn/guides/search/":
                    raise Rejected("unexpected_url")
            else:
                raise Rejected("unapproved_tool")
        except Exception:
            item["guard_admissible"] = False
    return item


def run(args):
    plan = {"mode":"DRY_RUN", "max_chat_calls":10, "per_call_output_tokens":256,
            "per_call_timeout_seconds":15, "deadline_seconds":180, "retries":0,
            "search_calls":0, "fetch_calls":0, "real_qq":False, "production_changed":False,
            "private_profile_sent":False, "key_extracted_from_vps":False}
    if not args.execute:
        return plan
    if not args.authorization or not args.output:
        raise ValueError("existing_task_authorization_and_private_output_required")
    if args.output.exists() or args.output.is_symlink():
        raise FileExistsError("new_output_required")
    policy=PublicPolicy(public_terms=["DeepSeek","AstrBot","炉石传说"])
    suite = {"persona":render_persona((ROOT/"persona/phase3-v0.1.md").read_text(),"测试者")+"\n\n"+tool_contract(policy),
             "tools":tools_schema(), "cases":[{"id":i,"user":text} for i,text,_ in CASES]}
    command = [args.ssh,"-T","-o","BatchMode=yes","-o","StrictHostKeyChecking=yes","-o","ConnectTimeout=15",
               "qq-companion-vps","sudo -n env PYTHONDONTWRITEBYTECODE=1 python3 -"]
    try:
        result = subprocess.run(command,input=REMOTE.replace("INPUT",repr(suite)).encode(),
                                stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=200)
        output, interrupted = result.stdout, result.returncode != 0
    except subprocess.TimeoutExpired as exc:
        output, interrupted = exc.stdout or b"", True
    expected = {i:wanted for i,_,wanted in CASES}
    results = []
    for line in output.decode().splitlines():
        try:
            item=json.loads(line)
            if item.get("case") in expected:
                results.append(assess(item,expected[item["case"]]))
        except ValueError:
            continue
    report={**plan,"mode":"REAL_CHAT_TOOL_SELECTION_ONLY","results":results,
            "authorization":args.authorization,"interrupted":interrupted,
            "usage_totals":{key:sum(r.get("usage",{}).get(key,0) for r in results)
                            for key in ("prompt_tokens","completion_tokens","total_tokens")}}
    spec=importlib.util.spec_from_file_location("choice_private_io",ROOT/"scripts/phase2-configure.py")
    private_io=importlib.util.module_from_spec(spec);spec.loader.exec_module(private_io)
    private_io.write_private(args.output,json.dumps(report,ensure_ascii=False,indent=2))
    return {"mode":report["mode"],"responses":len(results),"interrupted":interrupted,
            "selection_correct":sum(r["selection_correct"] for r in results),
            "selected_and_admissible":sum(r["selection_correct"] and r["guard_admissible"] for r in results),
            "implicit_trials":sum(r["case"].startswith("implicit") for r in results),
            "implicit_successes":sum(r["case"].startswith("implicit") and r["selection_correct"] and r["guard_admissible"] for r in results),
            "usage_totals":report["usage_totals"],"search_calls":0,"fetch_calls":0,"real_qq":False}


if __name__ == "__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ssh",default="/mnt/c/Windows/System32/OpenSSH/ssh.exe")
    p.add_argument("--execute",action="store_true")
    p.add_argument("--authorization")
    p.add_argument("--output",type=Path)
    print(json.dumps(run(p.parse_args()),ensure_ascii=False))
