#!/usr/bin/env python3
"""Bounded real Flash + synthetic native evidence; not QQ or human acceptance."""
import argparse,asyncio,importlib.util,json,os,shlex,sys,tempfile
from pathlib import Path
from types import SimpleNamespace as NS
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests'),str(ROOT/'.runtime/upstream/AstrBot')]

def module(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

async def run(output):
 if output.exists():raise ValueError('new_output_required')
 probe=module('existing_probe',ROOT/'scripts/phase3-test-profile-recall-real.py')
 budget=module('existing_budget',ROOT/'scripts/phase3-test-multibubble-real.py')
 total=budget.reserve(ROOT/'evidence/phase3/paid-acceptance-budget.sqlite3',output.name)
 result={'status':'FAIL','real_deepseek':True,'real_qq':False,'synthetic_only':True,'cumulative_reserved_cny':total,'calls':[]}
 with tempfile.TemporaryDirectory(prefix='qq-experience-real-') as temp:
  os.environ['ASTRBOT_ROOT']=temp;os.environ['ASTRBOT_DISABLE_METRICS']='1'
  import test_phase3_memory_native as native
  from integrations.phase3_memory import native_config,MemoryCoordinator
  from integrations.phase3_memory.profile_release import ProfileRelease
  from integrations.phase3_runtime.profile_recall import recall_profile
  from integrations.phase3_runtime.persona import render_persona
  from integrations.phase1_guard.bubbles import PROMPT
  from phase3_profile_fixture import manifest,approval,GAMES
  cfg=native_config({},enabled=False);cfg['private_companion_bridge']['accept_external_records']=True
  cfg['startup']={'background_grace_seconds':3600}
  def deny(*a,**kw):raise AssertionError('unexpected_background_model')
  service=native.NativeService(context=NS(get_provider_by_id=deny,get_using_provider=deny),config=cfg,plugin_root=native.UPSTREAM,data_dir=Path(temp)/'native')
  co=MemoryCoordinator(service,Path(temp)/'controls.sqlite3',{'synthetic-user'},enabled=True)
  ctx=co.context(session_id='fixture:FriendMessage:synthetic-user',user_id='synthetic-user',bot_id='synthetic-bot',platform='fixture')
  m=manifest(ctx);await ProfileRelease(service,ctx,Path(temp)/'import/ledger.sqlite3').apply(m,approval(m),apply=True)
  process=None
  try:
   process=await asyncio.create_subprocess_exec('/mnt/c/Windows/System32/OpenSSH/ssh.exe','-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10','qq-companion-vps','sudo -n env PYTHONDONTWRITEBYTECODE=1 python3 -u -c '+shlex.quote(probe.REMOTE),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
   persona=render_persona((ROOT/'persona/phase3-v0.1.md').read_text(),'测试者')
   cases=[('games','我平时玩哪些游戏？只根据已保存资料回答，不要上网。',GAMES),
    ('favorite','我最喜欢什么游戏？',('星露谷物语',)),
    ('longest','我玩得最久的游戏是什么？',()),
    ('recent','我昨天玩了什么游戏？',()),
    ('food','你记得我的口味吗？喜欢和不喜欢吃什么？',('南瓜汤','芹菜')),
    ('cross','我的游戏和阅读资料有哪些？',(*GAMES,'海底两万里')),
    ('greeting','卡了三天的合成 bug 终于解决了。',())]
   for name,q,wanted in cases:
    event=NS(unified_msg_origin=ctx.session_id,message_str=q,persona_id=ctx.persona_id,message_obj=NS(message_id='real-'+name),get_platform_name=lambda:ctx.platform,get_sender_id=lambda:ctx.user_id,get_self_id=lambda:ctx.bot_id)
    evidence=await recall_profile(service,ctx,event,query=q)
    system=persona+'\n'+PROMPT+(('\n'+evidence.render()) if evidence else '')
    messages=[{'role':'system','content':system},{'role':'user','content':'先前问过我的兴趣资料。'},{'role':'assistant','content':'刚才没有找到资料。'},{'role':'user','content':q}]
    payload={'model':'deepseek-flash','messages':messages,'max_tokens':640,'thinking':{'type':'disabled'},'stream':False}
    row={'case':name,'question':q,'state':'attempted','recall':evidence.diagnostic() if evidence else None};result['calls'].append(row)
    process.stdin.write((json.dumps(payload,ensure_ascii=False)+'\n').encode());await process.stdin.drain()
    response=json.loads(await asyncio.wait_for(process.stdout.readline(),25))
    row.update(response,state='returned');text=response.get('message',{}).get('content') or ''
    row['checks']={'http_ok':response.get('http')==200,'nonempty':bool(text),'expected_facts':all(w in text for w in wanted)}
    if name in {'longest','recent'}:
     row['checks']['unsupported_relation_not_invented']=any(term in text for term in ('没','不确定','不清楚','不知道','无法','不能','未'))
    if response.get('http') in (401,402,403,429):break
   result['status']='PASS' if len(result['calls'])==len(cases) and all(all(r.get('checks',{}).values()) and r.get('checks') for r in result['calls']) else 'FAIL'
  except Exception as exc:result['error_type']=type(exc).__name__
  finally:
   if process:
    if process.stdin:process.stdin.close()
    try:await asyncio.wait_for(process.wait(),5)
    except TimeoutError:process.kill();await process.wait()
   await co.close();await service.aclose()
 result['usage']={k:sum(r.get('usage',{}).get(k,0) for r in result['calls']) for k in ('prompt_tokens','completion_tokens')}
 result['estimate_cny']=(result['usage']['prompt_tokens']*2+result['usage']['completion_tokens']*8)/1e6
 result['account_bill']=False;result['human_naturalness']='NOT_TESTED'
 output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
 return result

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);a=p.parse_args()
 r=asyncio.run(run(a.output));print(json.dumps({k:r[k] for k in ('status','usage','estimate_cny','cumulative_reserved_cny')}));raise SystemExit(r['status']!='PASS')
