#!/usr/bin/env python3
"""Bounded synthetic A/B via verified SSH; key never leaves the VPS.

Default dry run. Outputs are synthetic model samples, never production chat.
No QQ transport, state changes, automatic retry or model-based quality rating.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import html
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import types

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from integrations.phase1_guard.bubbles import OLD_PROMPT,PROMPT,semantic_text
from integrations.phase3_runtime.persona import render_persona

CASES=[
 ('daily','刚吃完饭，味道挺好。'),
 ('good_news','卡了三天的 bug 终于解决了。'),
 ('tease','我信誓旦旦说今晚早睡，结果写代码一抬头又十二点了。'),
 ('quiet','今天有点累，想安静待会儿，不想回答问题。'),
 ('technical','请用 Python 写一个 SQLite 事务函数，完整保留代码块，并用一个两行小表格说明 commit 和 rollback 的区别。'),
 ('explicit_two','请用两个简短气泡回应：先打个招呼，再说一句鼓励的话。'),
 ('search_final','这是合成工具结果，不需要联网：官方公告说测试服务今天 22:00 维护，预计 10 分钟。请基于这条结果回答维护何时结束，不能增加公告外的事实。')]

REMOTE=r'''import importlib.util,json,time
from pathlib import Path
spec=importlib.util.spec_from_file_location('bounded','/opt/qq-ai-companion/phase2-src/scripts/phase2-test-deepseek.py')
api=importlib.util.module_from_spec(spec);spec.loader.exec_module(api)
key=api.read_key(Path('/opt/qq-ai-companion/deployment/state/secrets/deepseek-key'))
suite=INPUT
rows=[];started=time.monotonic();stop=False
for case,user in suite['cases']:
 for version,prompt in suite['prompts'].items():
  if stop or time.monotonic()-started>330:break
  payload={'model':'deepseek-flash','messages':[{'role':'system','content':prompt},{'role':'user','content':user}],
   'max_tokens':768 if case=='technical' else 320,'thinking':{'type':'disabled'},'stream':False}
  before=time.monotonic();reply=api.transport(key,payload,deadline=min(25,340-(before-started)))
  body=reply.get('body',{});choices=body.get('choices',[])
  text=choices[0].get('message',{}).get('content','') if choices else ''
  text=text.replace(key,'[REDACTED]') if isinstance(text,str) else ''
  rows.append({'case':case,'variant':version,'http':reply.get('http'),'error_type':reply.get('error'),
   'output':text,'finish_reason':choices[0].get('finish_reason') if choices else None,'usage':body.get('usage',{}),
   'model':body.get('model'),'elapsed_ms':round((time.monotonic()-before)*1000)})
  stop=reply.get('http') in (401,402,403,429)
print(json.dumps({'results':rows,'elapsed_ms':round((time.monotonic()-started)*1000)}))
'''

def reserve(path,run_id):
    """Conservative shared acceptance reserve, durable before any API attempt."""
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS reservations(id TEXT PRIMARY KEY,cny REAL,created TEXT)')
        db.execute('BEGIN IMMEDIATE')
        # Earlier authorized R1/real-search probes were <0.10 CNY in recorded
        # usage. Keep 1 CNY reserved when adopting this cumulative test ledger.
        db.execute('INSERT OR IGNORE INTO reservations VALUES (?,?,?)',('prior_phase3_acceptance_reserve',1.0,datetime.now(timezone.utc).isoformat()))
        total=db.execute('SELECT sum(cny) FROM reservations').fetchone()[0]
        if total+.30>9:raise ValueError('acceptance_budget_stop_near_ten_cny')
        db.execute('INSERT INTO reservations VALUES (?,?,?)',(run_id,.30,datetime.now(timezone.utc).isoformat()))
        return round(total+.30,2)

def run(args):
    cases=[c for c in CASES if not args.cases or c[0] in args.cases]
    plan={'calls_max':len(cases)*2,'retries':0,'timeout_seconds':25,'reserved_cny':.30,'real_qq':False,
          'synthetic_only':True,'production_changed':False,'human_rating':'NOT_TESTED'}
    if not args.execute:return {'mode':'DRY_RUN',**plan}
    if not args.output or args.output.exists():raise ValueError('new_output_required')
    package=types.ModuleType('real_bubble_private');package.__path__=[str(ROOT/'.runtime/upstream/private_companion')]
    sys.modules[package.__name__]=package
    spec=importlib.util.spec_from_file_location('real_bubble_private.segmented_message',ROOT/'.runtime/upstream/private_companion/segmented_message.py')
    parser=importlib.util.module_from_spec(spec);sys.modules[spec.name]=parser;spec.loader.exec_module(parser)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    reserve_total=reserve(ROOT/'evidence/phase3/paid-acceptance-budget.sqlite3',args.output.name)
    persona=render_persona((ROOT/'persona/phase3-v0.1.md').read_text(),'测试者')
    suite={'cases':cases,'prompts':{'previous':persona+'\n\n'+OLD_PROMPT,'candidate':persona+'\n\n'+PROMPT}}
    p=subprocess.run([args.ssh,'-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10',
        'qq-companion-vps','sudo -n env PYTHONDONTWRITEBYTECODE=1 python3 -'],
        input=REMOTE.replace('INPUT',repr(suite)).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=380)
    if p.returncode:raise RuntimeError('real_probe_ssh_failed_no_raw_stderr')
    result=json.loads(p.stdout)
    # Persist paid raw synthetic evidence before any optional local analysis.
    raw_path=args.output.with_suffix('.raw.json')
    fd=os.open(raw_path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    for row in result['results']:
        row['bubbles']=semantic_text(parser,row['output']) if row['output'] else []
        row['protocol_checks']={'no_visible_marker':all('PRIVATE_COMPANION_SPLIT' not in p for p in row['bubbles']),
            'bounded_bubbles':1<=len(row['bubbles'])<=4,'not_truncated':row['finish_reason']=='stop'}
        row['human_rating']='NOT_TESTED'
    prompt=sum(r['usage'].get('prompt_tokens',0) for r in result['results'])
    output=sum(r['usage'].get('completion_tokens',0) for r in result['results'])
    result.update(**plan,mode='REAL_DEEPSEEK_SYNTHETIC',observed_at=datetime.now(timezone.utc).isoformat(),
        prompt_hashes={k:hashlib.sha256(v.encode()).hexdigest() for k,v in suite['prompts'].items()},
        pricing_source='https://api-docs.deepseek.com/zh-cn/quick_start/pricing/',pricing_checked='2026-10-22',
        peak_uncached_estimate_cny=round((prompt*2+output*8)/1e6,6),not_account_bill=True,
        cumulative_conservative_reserved_cny=reserve_total,usage_totals={'input':prompt,'output':output})
    fd=os.open(args.output,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    sections=['<!doctype html><meta charset="utf-8"><title>多气泡真实模型样本</title><style>body{font:17px system-ui;max-width:1000px;margin:35px auto;background:#f3f5f3}section{background:white;padding:22px;margin:20px 0}.bubble{white-space:pre-wrap;background:#e5f2eb;padding:12px;border-radius:15px;margin:8px 0}small{color:#52605a}</style><h1>DeepSeek 多气泡前后样本</h1><p>全部为合成场景。气泡数不等于自然度评分。请评价像不像普通 QQ 好友、是否刻意拆句、模板腔及人格一致性。</p>']
    for case,user in cases:
        sections.append('<section><h2>'+html.escape(user)+'</h2>')
        for row in [r for r in result['results'] if r['case']==case]:
            sections.append('<h3>'+('既有提示词' if row['variant']=='previous' else '候选提示词')+'</h3>')
            sections.extend('<div class="bubble">'+html.escape(p)+'</div>' for p in row['bubbles'])
            sections.append('<small>HTTP '+str(row['http'])+'；'+str(len(row['bubbles']))+' 个气泡；自然度待本人评价</small>')
        sections.append('</section>')
    args.output.with_suffix('.html').write_text('\n'.join(sections))
    return {**plan,'actual_calls':len(result['results']),'http_200':sum(r['http']==200 for r in result['results']),
        'peak_uncached_estimate_cny':result['peak_uncached_estimate_cny'],'cumulative_reserved_cny':reserve_total,
        'protocol_pass':sum(all(r['protocol_checks'].values()) for r in result['results'])}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--execute',action='store_true')
    p.add_argument('--output',type=Path);p.add_argument('--ssh',default='/mnt/c/Windows/System32/OpenSSH/ssh.exe')
    p.add_argument('--cases',nargs='+',choices=[c[0] for c in CASES])
    print(json.dumps(run(p.parse_args()),ensure_ascii=False))
