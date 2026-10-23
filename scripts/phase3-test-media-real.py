#!/usr/bin/env python3
"""At most three real Flash requests with generated pixels, never QQ media.

Default dry run. Shares the existing acceptance budget and verified SSH/key
transport. This is a Provider protocol probe, not full Agent or real QQ proof.
"""
import argparse
import asyncio
import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import time

ROOT=Path(__file__).resolve().parents[1]


def load(name, filename):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/filename)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def synthetic_images():
    from PIL import Image,ImageDraw,ImageFont
    results=[]
    for kind in ('small_text','first','second'):
        picture=Image.new('RGB',(1800,1000) if kind=='small_text' else (420,280),'white')
        draw=ImageDraw.Draw(picture)
        if kind=='small_text':
            font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf',22)
            draw.text((950,700),'BLUE-731',font=font,fill='black')
        elif kind=='first':
            draw.ellipse((145,75,275,205),fill='red')
        else:
            draw.rectangle((60,85,160,185),fill='blue')
            draw.rectangle((250,85,350,185),fill='blue')
        output=io.BytesIO();picture.save(output,format='PNG');data=output.getvalue()
        results.append({'url':'data:image/png;base64,'+base64.b64encode(data).decode(),
            'metadata':{'fixture':kind,'format':'PNG','width':picture.width,'height':picture.height,
                        'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}})
    return results


def parse_json(text):
    text=text.strip()
    if text.startswith('```'):
        text=text.split('\n',1)[-1].rsplit('```',1)[0]
    try:return json.loads(text)
    except (ValueError,TypeError):return None


async def run(args):
    plan={'mode':'DRY_RUN','max_http_calls':3,'retries':0,'real_qq':False,'private_media_sent':False,
          'production_changed':False,'key_extracted':False,'human_rating':'NOT_TESTED'}
    if not args.execute:return plan
    if not args.output or args.output.exists():raise ValueError('new_output_required')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    transport=load('media_existing_transport','phase3-test-profile-recall-real.py')
    budget=load('media_existing_budget','phase3-test-multibubble-real.py')
    images=synthetic_images()
    reserved=budget.reserve(ROOT/'evidence/phase3/paid-acceptance-budget.sqlite3',args.output.name)
    report={**plan,'mode':'REAL_FLASH_SYNTHETIC_IMAGES','cumulative_reserved_cny':reserved,
            'model_requested':'deepseek-flash','images':[i['metadata'] for i in images],
            'calls':[],'checks':{},'account_bill':False}
    # Reuse the existing bounded, redacting VPS transport, with an even smaller
    # remote limit. The private key and API credential never leave their host.
    remote=transport.REMOTE.replace('if i>=7:break','if i>=3:break').replace(
        "out={'http':r.get('http')", "out={'model':b.get('model'),'http':r.get('http')")
    process=None;started=time.monotonic()
    try:
        process=await asyncio.create_subprocess_exec(args.ssh,'-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
            '-o','ConnectTimeout=10','qq-companion-vps','sudo -n env PYTHONDONTWRITEBYTECODE=1 python3 -u -c '+shlex.quote(remote),
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        async def ask(case,messages):
            if len(report['calls'])>=3 or time.monotonic()-started>100:raise TimeoutError('probe_limit')
            payload={'model':'deepseek-flash','messages':messages,'max_tokens':128,
                     'thinking':{'type':'disabled'},'stream':False}
            packed=json.dumps(payload,ensure_ascii=False)
            if len(packed)>2*1024*1024:raise ValueError('synthetic_payload_bound')
            row={'case':case,'state':'attempted'};report['calls'].append(row)
            before=time.monotonic();process.stdin.write((packed+'\n').encode());await process.stdin.drain()
            line=await asyncio.wait_for(process.stdout.readline(),25)
            if not line:raise RuntimeError('remote_transport_unavailable')
            row.update(json.loads(line),state='returned',elapsed_ms=round((time.monotonic()-before)*1000))
            if row.get('http')!=200:raise RuntimeError('model_http_failure')
            return row.get('message',{}).get('content') or ''
        def image(index):return {'type':'image_url','image_url':{'url':images[index]['url'],'detail':'high'}}
        screenshot=await ask('small_text',[{'role':'user','content':[
            {'type':'text','text':'请读出图中的唯一英文数字代号，仅输出代号。'},image(0)]}])
        report['checks']['small_text']=screenshot.strip()=='BLUE-731'
        pair=[{'role':'user','content':[{'type':'text','text':'按图片输入顺序，请用JSON回答first_color、first_count、second_color、second_count，颜色用英文red或blue，数量用数字。'},image(1),image(2)]}]
        reply=await ask('two_image_order',pair)
        report['checks']['two_image_order']=parse_json(reply)=={'first_color':'red','first_count':1,'second_color':'blue','second_count':2}
        pair.extend([{'role':'assistant','content':reply},{'role':'user','content':'上一轮第二张图的背景是什么颜色？只答一个英文颜色单词。'}])
        followup=await ask('history_followup',pair)
        report['checks']['history_followup']=followup.strip().lower()=='white'
        report['checks']['completed_outputs']=all(r.get('finish_reason')=='stop' and
            bool(r.get('message',{}).get('content')) for r in report['calls'])
        report['status']='PASS' if len(report['calls'])==3 and all(report['checks'].values()) else 'FAIL'
    except Exception as exc:
        report.update(status='FAIL',error_type=type(exc).__name__)
    finally:
        if process and process.returncode is None:
            process.stdin.close()
            try:await asyncio.wait_for(process.wait(),3)
            except TimeoutError:process.kill();await process.wait()
        inputs=sum(r.get('usage',{}).get('prompt_tokens',0) for r in report['calls'])
        outputs=sum(r.get('usage',{}).get('completion_tokens',0) for r in report['calls'])
        report.update(usage={'input':inputs,'output':outputs},peak_uncached_estimate_cny=round((inputs*2+outputs*8)/1000000,6),
            usage_incomplete=any(r.get('state')!='returned' or not r.get('usage') for r in report['calls']))
        fd=os.open(args.output,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
    return {key:report[key] for key in ('status','checks','usage','peak_uncached_estimate_cny','cumulative_reserved_cny')}|{'calls':len(report['calls'])}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--execute',action='store_true')
    parser.add_argument('--output',type=Path);parser.add_argument('--ssh',default='/mnt/c/Windows/System32/OpenSSH/ssh.exe')
    result=asyncio.run(run(parser.parse_args()));print(json.dumps(result,ensure_ascii=False))
    raise SystemExit(1 if result.get('status')=='FAIL' else 0)
