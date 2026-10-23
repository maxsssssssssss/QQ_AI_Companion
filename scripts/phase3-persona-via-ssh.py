#!/usr/bin/env python3
"""Real synthetic persona tests via verified SSH; the existing key stays on VPS.

No remote files are written. Uses the already installed Phase2 bounded transport.
Only generated synthetic outputs return over SSH into an owner-only local file.
Default dry-run; --execute records an existing explicit task authorization ID.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from integrations.phase3_runtime.persona import render_persona
spec = importlib.util.spec_from_file_location('persona_io', ROOT / 'scripts/phase2-configure.py')
io = importlib.util.module_from_spec(spec)
spec.loader.exec_module(io)

REMOTE = '''import importlib.util,json,time
from pathlib import Path
spec=importlib.util.spec_from_file_location("bounded", "/opt/qq-ai-companion/phase2-src/scripts/phase2-test-deepseek.py")
api=importlib.util.module_from_spec(spec);spec.loader.exec_module(api)
key=api.read_key(Path("/opt/qq-ai-companion/deployment/state/secrets/deepseek-key"))
suite=INPUT
results=[];start=time.monotonic()
for case in suite["cases"]:
 for version,prompt in suite["prompts"].items():
  if time.monotonic()-start >= 600: break
  payload={"model":"deepseek-flash","messages":[{"role":"system","content":prompt},{"role":"user","content":case["user"]}],"max_tokens":1024 if case["id"]=="V13" else 256,"thinking":{"type":"disabled"},"stream":False}
  before=time.monotonic();reply=api.transport(key,payload,deadline=min(20,600-(time.monotonic()-start)))
  body=reply.get("body",{});choices=body.get("choices",[])
  text=choices[0].get("message",{}).get("content","") if choices else ""
  # Defensive exact-secret redaction even though no prompt includes credentials.
  text=text.replace(key,"[REDACTED]") if isinstance(text,str) else ""
  results.append({"scenario":case["id"],"persona_version":version,"status":"PASS" if reply.get("http")==200 and text else "FAIL","http_status":reply.get("http"),"error_type":reply.get("error"),"elapsed_ms":round((time.monotonic()-before)*1000),"output":text,"finish_reason":choices[0].get("finish_reason") if choices else None,"usage":body.get("usage",{}),"human_rating":"NOT_RUN"})
  if reply.get("http") in [401,402,403,429]: break
 if results and results[-1]["http_status"] in [401,402,403,429]: break
print(json.dumps({"mode":"REAL_API","real_qq":False,"private_profile_sent":False,"production_changed":False,"results":results,"elapsed_ms":round((time.monotonic()-start)*1000)}))
'''


def run(args):
    suite = {'cases':json.loads((ROOT/'config/phase3-persona-scenarios.json').read_text())['cases'],
             'prompts':{'phase2_template':(ROOT/'persona/core_persona.md').read_text(),
                        'phase3_v01':render_persona((ROOT/'persona/phase3-v0.1.md').read_text(),'测试者')}}
    if args.phase3_only: suite['prompts'] = {'phase3_v01': suite['prompts']['phase3_v01']}
    plan = {'mode':'DRY_RUN','max_calls':len(suite['cases'])*len(suite['prompts']),'per_call_max_tokens':1024,'retries':0,'production_changed':False,
            'private_profile_sent':False,'key_extracted_from_vps':False}
    if not args.execute: return plan
    if not args.authorization or not args.output: raise ValueError('existing_task_authorization_and_private_output_required')
    if args.output.exists() or args.output.is_symlink(): raise FileExistsError('new_output_required')
    script = REMOTE.replace('INPUT', repr(suite))
    result = subprocess.run([args.ssh,'-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15',
        'qq-companion-vps','sudo -n env PYTHONDONTWRITEBYTECODE=1 python3 -'],input=script.encode(),
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=650)
    if result.returncode: raise RuntimeError('remote_persona_test_failed_no_stderr_dump')
    report = json.loads(result.stdout)
    report.update(authorization=args.authorization, persona_hashes={k:hashlib.sha256(v.encode()).hexdigest() for k,v in suite['prompts'].items()})
    # Worst-case peak USD bound (all input treated as cache miss), not a bill.
    prompt = sum(x['usage'].get('prompt_tokens',0) for x in report['results'])
    output = sum(x['usage'].get('completion_tokens',0) for x in report['results'])
    report['usage_totals']={'prompt_tokens':prompt,'completion_tokens':output}
    report['estimated_peak_cost_upper_usd']=round(prompt*0.30/1e6+output*1.20/1e6,6)
    report['pricing_source']='https://api-docs.deepseek.com/quick_start/pricing/'
    report['pricing_checked']='2026-10-21'
    io.write_private(args.output,json.dumps(report,ensure_ascii=False,indent=2))
    return {'mode':'REAL_API','calls':len(report['results']),'http_nonempty_pass':sum(x['status']=='PASS' for x in report['results']),
            'usage_totals':report['usage_totals'],'estimated_peak_cost_upper_usd':report['estimated_peak_cost_upper_usd'],
            'human_rating':'NOT_RUN','private_profile_sent':False,'production_changed':False,'real_qq':False}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ssh',default='/mnt/c/Windows/System32/OpenSSH/ssh.exe')
    p.add_argument('--execute',action='store_true')
    p.add_argument('--phase3-only',action='store_true')
    p.add_argument('--authorization')
    p.add_argument('--output',type=Path)
    print(json.dumps(run(p.parse_args()),ensure_ascii=False))
