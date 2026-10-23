#!/usr/bin/env python3
"""Three bounded real Flash decisions using the deployed adapter's contract.

Synthetic candidates only. This tests the exact decision/persona/bubble prompt
and parser, not the full native scheduler or QQ delivery. Key remains on VPS.
The caller must combine it with the actual fixed native process probe (T2).
"""
import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from integrations.phase3_runtime.proactive import DECISION_PROMPT,parse_proactive_decision
from integrations.phase3_runtime.persona import render_persona
from integrations.phase1_guard.bubbles import PROMPT

CASES=(
    ('new_topic_after_unanswered','send',
     '合成候选：聊一个新的双人解谜思路。用户先前明确邀请你有想法就来聊；已确认的合成背景是用户喜欢双人解谜。'
     '现在北京时间15:00，今天尚未主动联系，距上次主动超过四小时。上次未回复的话题是工作进展，本次不再追问。'
     '你刚在已有对话中想到：两人各自描述半边机关图，再拼出通关方法。这个新想法有具体内容，无需查网。'
     '没有暂停、忙碌或关闭话题。按契约选择是否自然地分享，不要描述自己在现实中玩过。'),
    ('timing_can_wait','defer',
     '合成候选：分享一个已经聊过的拼图新点子。内容有依据、用户也愿意聊，但已知用户正在完成一项工作，'
     '预计约一小时后有空；没有新事实需要立刻传达。候选三小时后失效。现在不适合打断，稍后再考虑有价值。'
     '这里只检验决策契约；真实运行中的明确忙碌会在模型前被本地门控。'),
    ('withdrawn_topic','skip',
     '合成候选：追问一次旧面试结果。现有可靠上下文已明确记录用户回答过结果，并表示这个话题不要再提。'
     '该候选没有新进展，不需要延期等待，也不应该换句话重问。这里只检验决策契约；真实运行已回应或关闭话题'
     '的候选会在模型前被本地门控。'),
)


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value


async def run(args):
    plan={'max_http_calls':3,'retries':0,'synthetic_only':True,'real_qq':False,
          'production_changed':False,'full_native_scheduler':False,'human_naturalness':'NOT_TESTED'}
    if not args.execute:
        return {'status':'DRY_RUN',**plan}
    if not args.output or args.output.exists():
        raise ValueError('new_output_required')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    transport=load('proactive_existing_transport',ROOT/'scripts/phase3-test-profile-recall-real.py')
    budget=load('proactive_shared_budget',ROOT/'scripts/phase3-test-multibubble-real.py')
    reserved=budget.reserve(ROOT/'evidence/phase3/paid-acceptance-budget.sqlite3',args.output.name)
    # Same reviewed transport; narrow its hard request ceiling to three.
    remote=transport.REMOTE.replace('if i>=7:break','if i>=3:break')
    remote=remote.replace("'usage':b.get('usage',{})", "'usage':b.get('usage',{}),'model':b.get('model')")
    persona=render_persona((ROOT/'persona/phase3-v0.1.md').read_text(),'测试者')
    system=persona+'\n\n'+PROMPT+'\n\n'+DECISION_PROMPT
    report={'status':'FAIL',**plan,'real_deepseek':True,'calls':[],
            'contract_sha256':hashlib.sha256(DECISION_PROMPT.encode()).hexdigest(),
            'cumulative_reserved_cny':reserved,'account_bill':False}
    process=None
    try:
        process=await asyncio.create_subprocess_exec(args.ssh,'-T','-o','BatchMode=yes',
            '-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10','qq-companion-vps',
            'sudo -n env PYTHONDONTWRITEBYTECODE=1 python3 -u -c '+shlex.quote(remote),
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        for name,expected,context in CASES:
            row={'case':name,'expected_decision':expected,'synthetic_context':context,'state':'attempted'}
            report['calls'].append(row)
            payload={'model':'deepseek-flash','messages':[{'role':'system','content':system},
                     {'role':'user','content':context}], 'max_tokens':320,
                     'thinking':{'type':'disabled'},'stream':False}
            process.stdin.write((json.dumps(payload,ensure_ascii=False)+'\n').encode())
            await process.stdin.drain()
            line=await asyncio.wait_for(process.stdout.readline(),25)
            if not line:
                raise RuntimeError('bounded_remote_transport_closed')
            response=json.loads(line);row.update(response,state='returned')
            decision=parse_proactive_decision(response.get('message',{}).get('content') or '')
            row['parsed']=decision
            row['checks']={'http_ok':response.get('http')==200,
                'completed':response.get('finish_reason')=='stop',
                'expected_decision':decision['decision']==expected,
                'no_extra_tools':not response.get('message',{}).get('tool_calls'),
                'send_has_text_or_no_send_has_none':bool(decision['text'])==(expected=='send'),
                'no_visible_control':'PHASE3_PROACTIVE:' not in decision['text']}
            if response.get('http') in (401,402,403,429):
                break
        report['status']='PASS' if len(report['calls'])==3 and all(
            row.get('checks') and all(row['checks'].values()) for row in report['calls']) else 'FAIL'
    except Exception as exc:
        report['error_type']=type(exc).__name__
    finally:
        if process:
            if process.stdin:process.stdin.close()
            try:await asyncio.wait_for(process.wait(),5)
            except TimeoutError:process.kill();await process.wait()
        report['usage']={key:sum(row.get('usage',{}).get(key,0) for row in report['calls'])
                         for key in ('prompt_tokens','completion_tokens')}
        usage=report['usage']
        report['estimate_cny']=round((usage['prompt_tokens']*2+usage['completion_tokens']*8)/1e6,6)
        report['usage_incomplete']=any(row.get('state')!='returned' or not row.get('usage') for row in report['calls'])
        fd=os.open(args.output,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true');parser.add_argument('--output',type=Path)
    parser.add_argument('--ssh',default='/mnt/c/Windows/System32/OpenSSH/ssh.exe')
    result=asyncio.run(run(parser.parse_args()))
    print(json.dumps({key:result[key] for key in ('status','max_http_calls','usage','estimate_cny',
        'cumulative_reserved_cny') if key in result}))
    raise SystemExit(result['status']=='FAIL')
