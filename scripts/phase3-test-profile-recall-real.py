#!/usr/bin/env python3
"""T3: real Flash chooses native local tools over synthetic imported records.

No production profile, history or QQ transport. Key stays on the approved VPS.
This small bounded tool loop tests model choice, not the full AstrBot lifecycle.
"""
import argparse,asyncio,importlib,importlib.util,json,os,shlex,sys,tempfile,time
from pathlib import Path
from types import SimpleNamespace as NS

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests'),str(ROOT/'.runtime/upstream/AstrBot')]
REMOTE='''import json,sys,importlib.util
from pathlib import Path
s=importlib.util.spec_from_file_location('bounded','/opt/qq-ai-companion/phase2-src/scripts/phase2-test-deepseek.py');a=importlib.util.module_from_spec(s);s.loader.exec_module(a)
key=a.read_key(Path('/opt/qq-ai-companion/deployment/state/secrets/deepseek-key'))
for i,line in enumerate(sys.stdin):
 if i>=7:break
 r=a.transport(key,json.loads(line),deadline=15);b=r.get('body',{});c=b.get('choices',[])
 out={'http':r.get('http'),'error':r.get('error'),'message':c[0].get('message',{}) if c else {},'finish_reason':c[0].get('finish_reason') if c else None,'usage':b.get('usage',{})}
 print(json.dumps(out,ensure_ascii=False).replace(key,'[REDACTED]'),flush=True)
 if r.get('http') in (401,402,403,429):break
'''


async def run(args):
    if not args.execute:return {'mode':'DRY_RUN','max_http_calls':7,'synthetic_only':True,'real_qq':False}
    if not args.output or args.output.exists():raise ValueError('new_output_required')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    os.environ['ASTRBOT_DISABLE_METRICS']='1'
    with tempfile.TemporaryDirectory(prefix='qq-profile-real-') as temp:
        path=Path(temp);os.environ['ASTRBOT_ROOT']=str(path/'astrbot')
        import test_phase3_memory_native as native
        from integrations.phase3_memory import native_config,MemoryCoordinator
        from integrations.phase3_memory.profile_release import ProfileRelease
        from integrations.phase3_runtime.persona import render_persona
        from integrations.phase3_runtime.recall import GUIDANCE, available_topics
        from phase3_profile_fixture import manifest,approval,GAMES
        importlib.import_module(native.PACKAGE+'.main')
        from astrbot.core.star.register.star_handler import llm_tools
        from astrbot.core.agent.tool import ToolSet
        tools=ToolSet(tools=[t for t in llm_tools.func_list if t.name in ('memory_companion_navigate','memory_companion_sources')]).openai_schema()
        assert len(tools)==2
        def deny(*args,**kwargs):raise AssertionError('unexpected_summary_or_embedding_model_call')
        cfg=native_config({},enabled=False);cfg['private_companion_bridge']['accept_external_records']=True
        cfg['startup']={'background_grace_seconds':3600}
        service=native.NativeService(context=NS(get_provider_by_id=deny,get_using_provider=deny),config=cfg,plugin_root=native.UPSTREAM,data_dir=path/'native')
        co=MemoryCoordinator(service,path/'controls.sqlite3',{'synthetic-user'},enabled=True)
        ctx=co.context(session_id='fixture:FriendMessage:synthetic-user',user_id='synthetic-user',bot_id='synthetic-bot',platform='fixture')
        m=manifest(ctx);await ProfileRelease(service,ctx,path/'import'/'ledger.sqlite3').apply(m,approval(m),apply=True)
        s=importlib.util.spec_from_file_location('reserve',ROOT/'scripts/phase3-test-multibubble-real.py');budget=importlib.util.module_from_spec(s);s.loader.exec_module(budget)
        total=budget.reserve(ROOT/'evidence/phase3/paid-acceptance-budget.sqlite3',args.output.name)
        report={'mode':'REAL_FLASH_NATIVE_MEMORY_SYNTHETIC','real_qq':False,'private_profile_sent':False,'production_changed':False,'key_extracted':False,
            'cumulative_reserved_cny':total,'calls':[],'cases':[],'max_http_calls':7,'retries':0,'human_rating':'NOT_TESTED'}
        process=None;start=time.monotonic()
        try:
            process=await asyncio.create_subprocess_exec(args.ssh,'-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10','qq-companion-vps',
                'sudo -n env PYTHONDONTWRITEBYTECODE=1 python3 -u -c '+shlex.quote(REMOTE),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            persona=render_persona((ROOT/'persona/phase3-v0.1.md').read_text(),'测试者')
            for index,(question,wanted) in enumerate([
                ('我平时玩哪些游戏？只根据你记得的资料回答，不要上网。',GAMES),
                ('你还记得我喜欢和不喜欢吃什么吗？只按已有资料回答。',('南瓜汤','芹菜')),
                ('刚忙完，来打个招呼。',()),
            ]):
                messages=[{'role':'system','content':persona+'\n'+GUIDANCE},
                    {'role':'user','content':'先问过我的游戏兴趣。'},
                    {'role':'assistant','content':'这轮我没查到具体游戏资料。'},
                    {'role':'user','content':question}]
                event=NS(unified_msg_origin=ctx.session_id,message_str=question,persona_id=ctx.persona_id,
                    message_obj=NS(message_id=f'real-synthetic-recall-{index}'),get_platform_name=lambda:ctx.platform,
                    get_sender_id=lambda:ctx.user_id,get_self_id=lambda:ctx.bot_id)
                topics=await available_topics(service,ctx,event)
                if topics:messages[0]['content']+='\n本轮可检索的部分已确认资料主题：'+'；'.join(topics)
                case={'id':index,'question':question,'tool_calls':[],'final':'','checks':{}};report['cases'].append(case)
                for step in range(3):
                    if len(report['calls'])>=7 or time.monotonic()-start>150:raise TimeoutError('probe_budget')
                    payload={'model':'deepseek-flash','messages':messages,'tools':tools,'tool_choice':'auto','max_tokens':512,'thinking':{'type':'disabled'},'stream':False}
                    assert len(json.dumps(payload,ensure_ascii=False))<30000
                    item={'case':index,'step':step,'state':'attempted'};report['calls'].append(item)
                    process.stdin.write((json.dumps(payload,ensure_ascii=False)+'\n').encode());await process.stdin.drain()
                    line=await asyncio.wait_for(process.stdout.readline(),25)
                    if not line:raise RuntimeError('remote_transport_unavailable')
                    reply=json.loads(line);item.update(reply,state='returned')
                    if reply['http']!=200:raise RuntimeError('real_model_http_failure')
                    msg=reply['message'];messages.append({k:v for k,v in msg.items() if k in ('role','content','tool_calls')})
                    calls=msg.get('tool_calls') or []
                    if not calls:
                        case['final']=msg.get('content') or '';break
                    if len(calls)!=1:raise ValueError('probe_requires_one_native_tool_step')
                    call=calls[0];fn=call['function'];params=json.loads(fn['arguments'])
                    operation={'memory_companion_navigate':'navigate','memory_companion_sources':'sources'}.get(fn['name'])
                    if operation is None:raise ValueError('unapproved_probe_tool')
                    result=await service.query_for_model(event,operation,**params)
                    body=result.get('result',result)
                    case['tool_calls'].append({'name':fn['name'],'args':params,'result':result})
                    messages.append({'role':'tool','tool_call_id':call['id'],'content':json.dumps(result,ensure_ascii=False)})
                case['checks']={'nonempty':bool(case['final']),
                    'facts_or_no_needless_tool':all(g in case['final'] for g in wanted) if wanted else not case['tool_calls'],
                    'native_evidence_received':any(t['name']=='memory_companion_navigate' and (t['result'].get('result',t['result'])).get('evidence') for t in case['tool_calls']) if wanted else True}
            report['status']='PASS' if all(all(c['checks'].values()) for c in report['cases']) else 'FAIL'
        except Exception as exc:
            report.update(status='FAIL',error_type=type(exc).__name__)
            if process and process.returncode is not None:
                diagnostic=await process.stderr.read()
                report['transport_exit_code']=process.returncode
                report['transport_error_category']=next((label for marker,label in (
                    (b'Permission denied','authentication'),(b'SyntaxError','remote_syntax'),
                    (b'Connection reset','ssh_reset'),(b'WSL','wsl_interop'),
                    (b'Traceback','remote_script'),(b'Cannot assign requested address','ssh_socket'))
                    if marker in diagnostic),'unclassified')
        finally:
            if process and process.returncode is None:
                process.stdin.close()
                try:await asyncio.wait_for(process.wait(),3)
                except TimeoutError:process.kill();await process.wait()
            await co.close();await service.aclose()
            inputs=sum(r.get('usage',{}).get('prompt_tokens',0) for r in report['calls']);outputs=sum(r.get('usage',{}).get('completion_tokens',0) for r in report['calls'])
            report.update(usage={'input':inputs,'output':outputs},peak_uncached_estimate_cny=round((inputs*2+outputs*8)/1000000,6),
                usage_incomplete=any(r.get('state')!='returned' or not r.get('usage') for r in report['calls']),account_bill=False)
            fd=os.open(args.output,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,'w') as f:json.dump(report,f,ensure_ascii=False,indent=2)
        return {k:report[k] for k in ('status','usage','peak_uncached_estimate_cny','cumulative_reserved_cny')}|{'calls':len(report['calls']),'cases':[{k:c[k] for k in ('id','checks')} for c in report['cases']]}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--execute',action='store_true');p.add_argument('--output',type=Path)
    p.add_argument('--ssh',default='/mnt/c/Windows/System32/OpenSSH/ssh.exe')
    print(json.dumps(asyncio.run(run(p.parse_args())),ensure_ascii=False))
