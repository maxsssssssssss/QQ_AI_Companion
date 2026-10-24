#!/usr/bin/env python3
"""Sequential Flash comparisons through verified SSH; default is a plan.

off-high: four two-turn scenarios (16 requests, original experiment).
off-low: one two-turn scenario (4 requests, separate evidence).

Read the live native persona/config only. The Key stays on the VPS. No QQ,
memory access, deployment, service restart, retries, or production file writes.
Raw responses (including original content) are saved in Git-ignored local evidence.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    (1, "普通日常", ["小鲸，我刚起床，今天真的不想动。", "哈哈，主要是床太舒服了，我再赖五分钟。"]),
    (2, "疲惫与安慰", ["今天工作好烦啊，感觉一直在处理乱七八糟的事情。", "其实也没什么大事，就是想跟你吐槽一下。"]),
    (3, "游戏与调侃", ["小鲸，我刚打完瓦回来，今天枪法简直跟梦游一样。", "你别光安慰我啊，想笑就笑。"]),
    (4, "兴趣与独立意见", ["我最近又有点想玩无人深空了，感觉还是喜欢那种到处乱逛的游戏。", "如果是你，你会更喜欢开飞船探索星系，还是去找那些奇怪的外星生物？"]),
]
LOW_CASES = [
    (1, "准时下班、炸鸡与生活偏好", [
        "小鲸，我今天终于准时下班了。本来想自己做顿饭，\n结果一回家就懒得动，直接点了炸鸡（笑）。",
        "你可别开始劝我健康饮食啊哈哈。\n不过认真问你，如果是你，你更喜欢每天按计划生活，\n还是偶尔随心所欲一点？我想听你自己的想法。",
    ]),
]

# Reuses the project's read_key/NoRedirect/safe_usage helpers. The old transport
# caps requests at 55/60s; this isolated worker instead has a 120s hard deadline.
WORKER = r'''
import importlib.util,json,socket,ssl,sys,urllib.request,urllib.error
spec=importlib.util.spec_from_file_location('bounded','/opt/qq-ai-companion/phase2-src/scripts/phase2-test-deepseek.py')
api=importlib.util.module_from_spec(spec);spec.loader.exec_module(api)
try:
    request=json.load(sys.stdin)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),api.NoRedirect(),urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    req=urllib.request.Request(request['endpoint'],data=json.dumps(request['payload'],ensure_ascii=False).encode(),
        headers={'Authorization':'Bearer '+request['key'],'Content-Type':'application/json'},method='POST')
    with opener.open(req,timeout=120) as response:
        data=response.read(1048577)
        result={'http':response.status,'body':json.loads(data)} if len(data)<=1048576 else {'http':response.status,'error':'response_too_large'}
except urllib.error.HTTPError as exc: result={'http':int(exc.code),'error':'http_error'}
except (TimeoutError,socket.timeout): result={'http':None,'error':'timeout'}
except urllib.error.URLError as exc:
    result={'http':None,'error':'timeout' if isinstance(exc.reason,(TimeoutError,socket.timeout)) else 'network_or_tls'}
except Exception: result={'http':None,'error':'transport_error'}
print(json.dumps(result))
'''

REMOTE = r'''
import copy,hashlib,importlib.util,json,platform,sqlite3,subprocess,sys,time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

root=Path('/opt/qq-ai-companion'); data=root/'deployment/state/data'
def stamp(): return datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
def sha(raw): return hashlib.sha256(raw).hexdigest()
def load(path): return json.loads(path.read_text(encoding='utf-8-sig'))
def emit(kind,**values): print(json.dumps({'kind':kind,**values},ensure_ascii=False),flush=True)
def snapshot():
    paths=['cmd_config.json','config/phase3_runtime_config.json','plugins/phase2_policy/policy.py',
           'plugins/phase3_runtime/persona.py','phase3/persona-v0.1.md']
    hashes={name:sha((data/name).read_bytes()) for name in paths}
    dep=root/'deployment'
    prefix=['docker','compose','--project-directory',str(dep),'-f',str(dep/'compose.yml')]
    states={}
    for service in ('astrbot','napcat'):
        ids=subprocess.check_output(prefix+['ps','--all','--quiet',service],stderr=subprocess.DEVNULL,text=True,timeout=10).split()
        if len(ids)!=1: raise ValueError('single_service_required')
        fmt='{"id":{{json .Id}},"started_at":{{json .State.StartedAt}},"restarts":{{json .RestartCount}},"running":{{json .State.Running}}}'
        states[service]=json.loads(subprocess.check_output(['docker','inspect','--format',fmt,ids[0]],stderr=subprocess.DEVNULL,text=True,timeout=10))
    return {'config_hashes':hashes,'containers':states}

try:
    before=snapshot()
    config=load(data/'cmd_config.json')
    model_id=config['agent_runner']['config']['model']['provider_id']
    model=next(p for p in config['provider'] if p['id']==model_id)
    source=next(p for p in config['provider_sources'] if p['id']==model['provider_source_id'])
    effective={**source,**model}
    if effective['model']!='deepseek-flash' or effective['type']!='openai_chat_completion': raise ValueError('unexpected_active_model')
    if effective['api_base'] not in ('https://api.deepseek.com','https://api.deepseek.com/v1'): raise ValueError('official_endpoint_required')
    if not model.get('enable') or not source.get('enable'): raise ValueError('active_provider_required')
    extra=copy.deepcopy(effective.get('custom_extra_body',{}))
    if extra.get('thinking')!={'type':'disabled'} or extra.get('max_tokens')!=8192: raise ValueError('production_override_changed')
    if set(extra)-{'thinking','max_tokens','reasoning_effort','temperature','top_p','presence_penalty','frequency_penalty','seed'}:
        raise ValueError('unreviewed_extra_parameters')
    if effective.get('timeout')!=120 or config['provider_settings'].get('streaming_response') is not False: raise ValueError('production_transport_changed')
    if config['agent_runner']['config']['persona']['persona_id']!='qq_companion': raise ValueError('unexpected_active_persona')
    with closing(sqlite3.connect((data/'data_v4.db').as_uri()+'?mode=ro',uri=True)) as db:
        rows=db.execute('SELECT system_prompt FROM personas WHERE persona_id=?',('qq_companion',)).fetchall()
    if len(rows)!=1 or not rows[0][0]: raise ValueError('one_complete_native_persona_required')
    persona=rows[0][0]
    runtime=load(data/'config/phase3_runtime_config.json')
    template=(data/'phase3/persona-v0.1.md').read_text()
    rendered=template.replace('{{preferred_address}}',runtime['preferred_address']).strip()
    if persona!=rendered or not persona.startswith('[QQ_COMPANION_PERSONA_V0_2]'): raise ValueError('native_persona_mismatch')
    if EXPECTED_PERSONA_SHA and sha(persona.encode())!=EXPECTED_PERSONA_SHA: raise ValueError('persona_changed_since_original_experiment')
    endpoint=effective['api_base'].rstrip('/')+'/chat/completions'
    common={} if EXPERIMENT=='off-low' else {k:v for k,v in extra.items() if k not in ('thinking','reasoning_effort')}
    common.update(model=effective['model'],max_tokens=8192,stream=False)
    params={'off':{**common,'thinking':{'type':'disabled'}},
            ON_MODE:{**common,'thinking':{'type':'enabled'},'reasoning_effort':THINKING_EFFORT}}
    if sum(len(users)*2 for _,_,users in CASES)!=EXPECTED_REQUESTS: raise ValueError('request_count_mismatch')
    spec=importlib.util.spec_from_file_location('bounded',root/'phase2-src/scripts/phase2-test-deepseek.py')
    api=importlib.util.module_from_spec(spec);spec.loader.exec_module(api)
    key=api.read_key(root/'deployment/state/secrets/deepseek-key')
    if effective.get('key')!=[key]: raise ValueError('protected_key_not_current_provider_key')
    metadata={'observed_at':stamp(),'experiment':EXPERIMENT,'expected_requests':EXPECTED_REQUESTS,
        'protected_key_matches_provider':True,'persona_matches_previous_experiment':bool(EXPECTED_PERSONA_SHA),
        'model':effective['model'],'provider_id':model_id,'provider_type':effective['type'],
        'endpoint':endpoint,'production_extra_body':extra,'effective_timeout':effective['timeout'],
        'request_parameters':params,'timeout_seconds':120,'hard_deadline_seconds':120,'retries':0,
        'stream':False,'transport':'Python stdlib urllib HTTPS; project read_key/NoRedirect/safe_usage',
        'python_version':platform.python_version(),'persona_source':'native personas.qq_companion.system_prompt',
        'persona_full_sha256':sha(persona.encode()),'persona_chars':len(persona),'persona_exact_template_match':True,
        'persona_version':'0.2','memory_injected':False,'initial_history':[],
        'persona_prompt':persona,'cases':CASES,'before_snapshot':before,
        'api_requests_only_not_qq_latency':True,'first_visible_token_measured':False}
    emit('metadata',**metadata)
    if EXECUTE:
        sequence=0; halted=False
        for group,label,users in CASES:
            for mode in (['off',ON_MODE] if group%2 else [ON_MODE,'off']):
                history=[{'role':'system','content':persona}]
                for turn,user in enumerate(users,1):
                    history.append({'role':'user','content':user})
                    payload={**params[mode],'messages':copy.deepcopy(history)}
                    encoded=json.dumps(payload,ensure_ascii=False).encode()
                    if len(encoded)>32000: raise ValueError('input_budget_exceeded_no_additional_request')
                    if sequence>=EXPECTED_REQUESTS: raise ValueError('request_count_limit')
                    sequence+=1; started=stamp()
                    emit('request_start',sequence=sequence,group=group,mode=mode,turn=turn,started_at=started,
                         payload_sha256=sha(encoded),history_sha256=sha(json.dumps(history,ensure_ascii=False).encode()))
                    tick=time.monotonic()
                    try:
                        response=subprocess.run([sys.executable,'-c',WORKER],input=json.dumps({'key':key,'endpoint':endpoint,'payload':payload}),
                            text=True,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=120,check=False)
                        reply=json.loads(response.stdout) if response.returncode==0 else {'http':None,'error':'worker_failed'}
                    except subprocess.TimeoutExpired: reply={'http':None,'error':'hard_timeout'}
                    elapsed=time.monotonic()-tick; completed=stamp()
                    body=reply.get('body',{}); choices=body.get('choices',[])
                    choice=choices[0] if choices else {}; message=choice.get('message',{})
                    content=message.get('content')
                    usage=api.safe_usage(body)
                    raw=json.dumps(body,ensure_ascii=False); redacted=key in raw
                    if redacted:
                        body=json.loads(raw.replace(key,'[REDACTED]')); content=body.get('choices',[{}])[0].get('message',{}).get('content')
                    row={'sequence':sequence,'group':group,'group_label':label,'mode':mode,'turn':turn,'user':user,
                        'started_at':started,'completed_at':completed,'elapsed_seconds':elapsed,
                        'http':reply.get('http'),'api_error':reply.get('error'),'timeout':reply.get('error') in ('timeout','hard_timeout'),
                        'finish_reason':choice.get('finish_reason'),'output':content,'usage':usage,
                        'reasoning_tokens':usage.get('reasoning_tokens'),'reasoning_content_present':bool(message.get('reasoning_content')),
                        'reasoning_chars':len(message.get('reasoning_content') or ''),'response_model':body.get('model'),
                        'response_body':body,'request_parameters':params[mode],
                        'request_messages':copy.deepcopy(history),'payload_sha256':sha(encoded),'credential_redaction':redacted,
                        'status':'OK' if reply.get('http')==200 and isinstance(content,str) and content.strip() else 'FAILED'}
                    emit('result',**row)
                    # Preserve each mode's exact first content, including empty content;
                    # never substitute an answer or leak the other mode's reply.
                    history.append({'role':'assistant','content':content if isinstance(content,str) else '', 'reasoning_content':''})
                    if reply.get('http') in (401,402,403,429): halted=True; break
                if halted: break
            if halted: break
    after=snapshot()
    emit('postflight',production_config_unchanged=before['config_hashes']==after['config_hashes'],
         containers_unchanged=before['containers']==after['containers'],after_snapshot=after,
         memory_database_opened=False,qq_messages=0,production_writes=False,service_restarts=False)
except Exception as exc:
    emit('fatal',code=str(exc) if isinstance(exc,ValueError) else type(exc).__name__)
    raise SystemExit(1)
'''


def reserve(run_id, requests=16):
    # Existing shared ledger; no new database and no resetting the original ¥10.
    path = ROOT / 'evidence/phase3/paid-acceptance-budget.sqlite3'
    amount = round(2.2 * requests / 16, 2)
    with sqlite3.connect(path.resolve().as_uri() + '?mode=rw', uri=True) as db:
        db.execute('BEGIN IMMEDIATE')
        total = db.execute('SELECT SUM(cny) FROM reservations').fetchone()[0]
        if total is None or total + amount > 9:
            raise ValueError('shared_budget_near_limit')
        db.execute('INSERT INTO reservations VALUES (?,?,?)',
                   (run_id, amount, datetime.now(timezone.utc).isoformat()))
    return {'previous_reserved_cny': total, 'this_reserved_cny': amount,
            'cumulative_reserved_cny': round(total + amount, 2), 'original_limit_cny': 10,
            'worst_case_peak_cny': requests * (32000 * 2 + 8192 * 8) / 1e6,
            'reservation_not_bill': True}


def run(args):
    experiment = args.experiment
    cases = LOW_CASES if experiment == 'off-low' else CASES
    effort, on_mode = ('low', 'low') if experiment == 'off-low' else ('high', 'on')
    requests = sum(len(users) * 2 for _, _, users in cases)
    plan = {'experiment':experiment,'requests':requests,'sequential':True,'timeout':120,'max_tokens':8192,'retries':0,
            'reasoning_effort':effort,'orders':[f'off,{on_mode}' if group%2 else f'{on_mode},off' for group,_,_ in cases],'stream':False}
    if not (args.inspect or args.execute):
        return {'status':'PLAN', **plan}
    expected_persona_sha = None
    if experiment == 'off-low':
        previous = ROOT/'evidence/phase3/thinking-ab-20261024/raw.jsonl'
        metadata = next(item for item in map(json.loads,previous.read_text().splitlines()) if item['kind']=='metadata')
        expected_persona_sha = metadata['persona_full_sha256']
    default_dir = 'thinking-off-low-20261024' if experiment == 'off-low' else 'thinking-ab-20261024'
    output = (args.output_dir or ROOT/'evidence/phase3'/default_dir).resolve()
    if not output.is_relative_to((ROOT / 'evidence').resolve()):
        raise ValueError('private_ignored_evidence_directory_required')
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = output / ('raw.jsonl' if args.execute else 'preflight.jsonl')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as evidence:
        if args.execute:
            budget = reserve(output.name, requests)
            evidence.write(json.dumps({'kind':'budget', **budget})+'\n'); evidence.flush()
        values = {'CASES':cases,'WORKER':WORKER,'EXECUTE':args.execute,'EXPERIMENT':experiment,
                  'ON_MODE':on_mode,'THINKING_EFFORT':effort,'EXPECTED_REQUESTS':requests,
                  'EXPECTED_PERSONA_SHA':expected_persona_sha}
        program = ''.join(name+'='+repr(value)+'\n' for name,value in values.items())+REMOTE
        proc = subprocess.Popen([args.ssh,'-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
            '-o','ConnectTimeout=15','-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3',
            'qq-companion-vps','sudo -n env PYTHONDONTWRITEBYTECODE=1 python3 -u -'],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
        proc.stdin.write(program); proc.stdin.close()
        count = successful = 0
        for line in proc.stdout:
            item = json.loads(line)
            evidence.write(json.dumps(item,ensure_ascii=False)+'\n'); evidence.flush(); os.fsync(evidence.fileno())
            if item['kind']=='metadata':
                print(json.dumps({k:item[k] for k in ('kind','model','production_extra_body','effective_timeout','persona_version','persona_chars','persona_full_sha256','request_parameters')}),flush=True)
            elif item['kind']=='result':
                count += 1
                successful += item['status'] == 'OK'
                print(json.dumps({k:item[k] for k in ('sequence','group','mode','turn','status','elapsed_seconds','usage','finish_reason')}),flush=True)
            elif item['kind'] in ('fatal','postflight'):
                print(json.dumps({k:v for k,v in item.items() if k!='after_snapshot'}),flush=True)
        code = proc.wait(timeout=15)
    if code: raise RuntimeError('remote_experiment_failed_see_private_evidence')
    status = ('PREFLIGHT_OK' if not args.execute else 'PARTIAL' if count < requests
              else 'COMPLETED' if successful == requests else 'COMPLETED_WITH_ERRORS')
    return {'status':status,'completed_requests':count,'successful_requests':successful,
            'evidence':str(path), **plan}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    group = p.add_mutually_exclusive_group()
    group.add_argument('--inspect', action='store_true')
    group.add_argument('--execute', action='store_true')
    p.add_argument('--experiment', choices=('off-high','off-low'), default='off-high')
    p.add_argument('--output-dir', type=Path)
    p.add_argument('--ssh', default='/mnt/c/Windows/System32/OpenSSH/ssh.exe')
    print(json.dumps(run(p.parse_args()),ensure_ascii=False))
