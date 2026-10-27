#!/usr/bin/env python3
"""Read aggregate Phase3 status. No login, writes, migration, or model requests.

Requires the unchanged phase2-status.py next to this script. Only fixed numeric,
boolean and enum fields are emitted; identities, content, credentials, Docker
command lines and raw exceptions never enter the output. Live SQLite uses mode=ro
(not immutable, which could miss WAL rows), with query_only and bounded queries.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date, datetime, timezone
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import time
from zoneinfo import ZoneInfo

spec=importlib.util.spec_from_file_location('phase2_status_reused',Path(__file__).with_name('phase2-status.py'))
phase2=importlib.util.module_from_spec(spec)
spec.loader.exec_module(phase2)
SHANGHAI=ZoneInfo('Asia/Shanghai')
DELIVERY_STATES=('pending','sent','failed','unknown')
DELIVERY_KINDS=('normal','progress','final','proactive')
VALIDITY=('active','superseded','expired','archived','deleted','quarantined')
GENERATION_STATES=('running','generated','empty','failed','cancelled','expired','interrupted','deferred','skipped')
SAFE_ERRORS={'unsafe_file_permissions','unexpected_file_owner','oversized_file',
    'docker_command_unavailable','docker_command_failed','oversized_docker_output',
    'unexpected_compose_output','unexpected_compose_service','invalid_database_file'}
phase2.POLICY_REASONS |= {'duplicate_phase3_runtime','invalid_phase3_runtime_config',
    'requested_phase3_runtime_unavailable','requested_phase3_plugin_unavailable',
    'missing_search_key','intermediate_buffer_enabled','unbounded_agent_steps',
    'ValueError','PermissionError','MemoryDenied','OSError','RuntimeError'}


def number(value, *, maximum=1e18):
    return value if type(value) in (int,float) and math.isfinite(value) and 0 <= value <= maximum else None


def boolean(value):
    return value if type(value) is bool else None


def iso_time(value):
    value=number(value,maximum=4102444800)
    if value is None or value < 946684800:
        return None
    return datetime.fromtimestamp(value,SHANGHAI).isoformat()


def parse_bytes(value):
    if not isinstance(value,str): return None
    match=re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)\s*(B|kB|KB|MB|GB|TB|KiB|MiB|GiB|TiB)',value.strip())
    if not match: return None
    scale={'B':1,'kB':1000,'KB':1000,'MB':1000**2,'GB':1000**3,'TB':1000**4,
           'KiB':1024,'MiB':1024**2,'GiB':1024**3,'TiB':1024**4}[match[2]]
    amount=number(float(match[1])*scale)
    return int(amount) if amount is not None else None


def filter_stats(raw):
    percent=raw.get('CPUPerc')
    cpu=float(percent[:-1]) if isinstance(percent,str) and re.fullmatch(r'[0-9]+(?:\.[0-9]+)?%',percent) else None
    memory=raw.get('MemUsage')
    memory=parse_bytes(memory.split('/',1)[0]) if isinstance(memory,str) else None
    pids=raw.get('PIDs')
    pids=int(pids) if isinstance(pids,str) and re.fullmatch(r'[0-9]{1,9}',pids) else None
    return {'cpu_percent':number(cpu,maximum=100000),'memory_used_bytes':memory,'pids':pids,
            'measurement':'docker_stats_no_stream','memory_semantics':'Docker Linux usage excluding inactive file cache'}


def container_usage(deployment):
    ids=phase2.command(['docker','compose','--project-directory',str(deployment),'-f',str(deployment/'compose.yml'),
                        'ps','--all','--quiet',*phase2.SERVICES]).split()
    if len(ids)>2 or any(not re.fullmatch(r'[0-9a-f]{12,64}',v) for v in ids):
        raise phase2.StatusError('unexpected_compose_output')
    result={name:{'status':'NOT_RUNNING'} for name in phase2.SERVICES}
    seen=set()
    for identifier in ids:
        service=json.loads(phase2.command(['docker','inspect','--format',
            '{{json (index .Config.Labels "com.docker.compose.service")}}',identifier]))
        if service not in phase2.SERVICES or service in seen:
            raise phase2.StatusError('unexpected_compose_service')
        seen.add(service)
        raw=phase2.command(['docker','stats','--no-stream','--format','{{json .}}',identifier]).strip()
        result[service]=filter_stats(json.loads(raw)) if raw else {'status':'NOT_RUNNING'}
    return result


def cpu_sample(path=Path('/proc/stat')):
    fields=path.read_text().splitlines()[0].split()
    if fields[0]!='cpu' or len(fields)<5: raise ValueError('invalid_cpu_sample')
    # guest fields are already included in user/nice; do not count them twice.
    values=[int(v) for v in fields[1:9]]
    return sum(values),values[3]+(values[4] if len(values)>4 else 0)


def cpu_percent(before,after):
    total,idle=after[0]-before[0],after[1]-before[1]
    return round(100*(total-idle)/total,2) if total>0 and 0<=idle<=total else None


def host_usage(deployment):
    first=cpu_sample();time.sleep(.2);second=cpu_sample()
    selected={}
    for line in Path('/proc/meminfo').read_text().splitlines():
        match=re.fullmatch(r'(MemTotal|MemAvailable|SwapTotal|SwapFree):\s*([0-9]+) kB',line)
        if match: selected[match[1]]=int(match[2])*1024
    total,available=selected['MemTotal'],selected['MemAvailable']
    disk=shutil.disk_usage(deployment)
    return {'logical_cpus':os.cpu_count(),'cpu_percent':cpu_percent(first,second),'cpu_sample_seconds':.2,
        'load_average':[round(v,3) for v in os.getloadavg()],
        'memory_total_bytes':total,'memory_available_bytes':available,'memory_used_bytes':total-available,
        'swap_total_bytes':selected.get('SwapTotal'),'swap_free_bytes':selected.get('SwapFree'),
        'disk_total_bytes':disk.total,'disk_used_bytes':disk.used,'disk_free_bytes':disk.free,
        'measurement':'current_host_usage_not_container_limits'}


def read_json(path):
    raw=json.loads(phase2.read_private(path))
    if not isinstance(raw,dict): raise ValueError('json_object_required')
    return raw


def configured_flags(data):
    runtime=read_json(data/'config/phase3_runtime_config.json')
    guard=read_json(data/'config/phase1_guard_config.json')
    search=read_json(data/'config/phase3_search_config.json')
    memory=read_json(data/'config/astrbot_plugin_memory_companion_config.json')
    private=read_json(data/'config/astrbot_plugin_private_companion_config.json')
    def nested(obj,group,key):
        return obj.get(key,obj.get(group,{}).get(key))
    return {'scope':'stored_configuration_not_live_runtime_introspection',
        'runtime':{key:boolean(runtime.get(key)) for key in ('enabled','persona','natural_chat','memory','search','proactive','background_web')},
        'guard':{key:boolean(guard.get(key)) for key in ('phase3_enabled','phase3_proactive_enabled')},
        'search_enabled':boolean(search.get('enabled')),
        'search_scope':search.get('scope','reviewed') if search.get('scope','reviewed') in ('reviewed','general_public') else 'invalid',
        'native_disk':{'memory_summary_enabled':boolean(memory.get('memory_summary',{}).get('enabled')),
                       'proactive_generation_enabled':boolean(nested(private,'proactive_generation_config','enable_llm_proactive_message')),
                       'proactive_max_daily':number(nested(private,'proactive_reach_config','max_daily_messages'),maximum=25)},
        'native_disk_note':'false/zero is expected; approved runtime activation is memory-only'}


@contextmanager
def readonly_db(path):
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
        raise phase2.StatusError('invalid_database_file')
    connection=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('PRAGMA trusted_schema=OFF')
        deadline=time.monotonic()+5
        connection.set_progress_handler(lambda:int(time.monotonic()>deadline),10000)
        yield connection
    finally: connection.close()


def has_table(db,name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone() is not None


def count(db,table,where='',params=()):
    # All table/where expressions below are source constants, never user input.
    return db.execute('SELECT count(*) FROM '+table+(' WHERE '+where if where else ''),params).fetchone()[0]


def guard_counts(path):
    with readonly_db(path) as db:
        result={'inbound_total':count(db,'inbound'),'legacy_outbound_total':count(db,'outbound'),
                'phase3_schema_present':has_table(db,'deliveries')}
        if result['phase3_schema_present']:
            result['deliveries']={kind:{state:count(db,'deliveries','kind=? AND state=?',(kind,state))
                for state in DELIVERY_STATES} for kind in DELIVERY_KINDS}
            result['turn_states']={state:count(db,'turns','state=?',(state,)) for state in
                ('collecting','ready','generating','done','superseded','cancelled','interrupted')}
        return result


def memory_counts(path):
    with readonly_db(path) as db:
        result={'timeline_total':count(db,'timeline'),
                'timeline_unsummarized':count(db,'timeline',"COALESCE(summarized_at,'')=''"),
                'memories_total':count(db,'memories')}
        for kind in ('conversation_summary','assertion'):
            result[kind]={'total':count(db,'memories','memory_type=?',(kind,)),
                'validity':{state:count(db,'memories','memory_type=? AND validity_status=?',(kind,state)) for state in VALIDITY}}
        if has_table(db,'summary_batches'):
            result['summary_batches_total']=count(db,'summary_batches')
        if has_table(db,'summary_batch_calls'):
            result['summary_model_calls_total']=count(db,'summary_batch_calls')
        return result


def search_counts(path,now):
    today=now.astimezone(SHANGHAI).date().isoformat()
    with readonly_db(path) as db:
        rows=db.execute('SELECT day,attempts FROM web_daily_attempts WHERE day<=? ORDER BY day DESC LIMIT 7',(today,)).fetchall()
        days=[]
        for day,attempts in rows:
            try: valid=isinstance(day,str) and date.fromisoformat(day).isoformat()==day
            except ValueError: valid=False
            if valid and type(attempts) is int and 0<=attempts<=1000000: days.append({'day':day,'attempts':attempts})
        return {'timezone':'Asia/Shanghai','today':today,
                'today_attempts':next((v['attempts'] for v in days if v['day']==today),0),'recent_days':days}


def proactive_status(data,now):
    cfg=read_json(data/'config/phase3_runtime_config.json')
    window=cfg.get('proactive_window',{})
    mode=window.get('mode','trial')
    mode=mode if mode in ('trial','continuous') else None
    start,end=window.get('window_start'),window.get('window_end')
    continuous=mode=='continuous' and number(start)==0 and number(end)==0
    quiet=window.get('quiet_hours',[23,9])
    quiet=quiet if isinstance(quiet,list) and len(quiet)==2 and all(type(v) is int and 0<=v<=23 for v in quiet) else None
    result={'requested':boolean(cfg.get('proactive')),'mode':mode,'timezone':'Asia/Shanghai',
        'window_start':iso_time(start),'window_end':iso_time(end),
        'has_scheduled_expiry':False if continuous else (True if mode=='trial' and iso_time(end) else None),
        'max_per_day':number(window.get('max_per_day'),maximum=12),
        'max_generations_per_day':number(window.get('max_generations_per_day'),maximum=96),
        'cooldown_seconds':number(window.get('cooldown'),maximum=604800),'quiet_hours':quiet,
        'state_scope':'derived_from_config_and_durable_ledger_not_live_plugin_flags'}
    stamp=now.timestamp();today=now.astimezone(SHANGHAI).date().isoformat()
    pause=0;pause_indefinite=False
    path=data/'plugin_data/phase3_runtime/proactive-controls.sqlite3'
    if path.exists():
        with readonly_db(path) as db:
            row=db.execute("SELECT value FROM proactive_controls WHERE name='pause_until'").fetchone()
            if row:
                try: pause=float(row[0])
                except (ValueError,TypeError): pause=0
                if number(pause,maximum=4102444800) is None: pause=0
            row=db.execute("SELECT value FROM proactive_controls WHERE name='pause_indefinite'").fetchone()
            pause_indefinite=bool(row and row[0] in ('1',1))
            result['attempts_today']={state:count(db,'proactive_attempts','day=? AND state=?',(today,state)) for state in (*DELIVERY_STATES,'cancelled')}
            result['generations_today']={state:count(db,'proactive_generations','day=? AND state=?',(today,state)) for state in GENERATION_STATES}
            columns={r[1] for r in db.execute('PRAGMA table_info(proactive_attempts)')}
            if {'sent_count','part_count'} <= columns:
                confirmed,planned=db.execute('SELECT COALESCE(sum(sent_count),0),COALESCE(sum(part_count),0) FROM proactive_attempts WHERE day=?',(today,)).fetchone()
                result['confirmed_bubbles_today']=number(confirmed)
                result['planned_bubbles_today']=number(planned)
    else: result['ledger']='NOT_INITIALIZED'
    result.update(paused=pause_indefinite or pause>stamp,pause_until=iso_time(pause),pause_indefinite=pause_indefinite)
    if result['requested'] is not True: state='DISABLED'
    elif mode is None or (mode=='continuous' and not continuous): state='UNCONFIGURED'
    elif mode=='trial' and (not result['window_start'] or not result['window_end'] or start>=end): state='UNCONFIGURED'
    elif mode=='trial' and stamp>=end: state='EXPIRED'
    elif mode=='trial' and stamp<start: state='WAITING'
    elif result['paused']: state='PAUSED'
    elif quiet is None: state='INVALID_QUIET_HOURS'
    else:
        hour=now.astimezone(SHANGHAI).hour;a,b=quiet
        state='QUIET_HOURS' if (a<b and a<=hour<b) or (a>b and (hour>=a or hour<b)) else 'IN_WINDOW'
    result['window_state']=state
    return result


def build_report(deployment,*,now=None):
    now=now or datetime.now(timezone.utc)
    data=deployment/'state/data'
    report={'status':'INSPECTED','observed_at':now.astimezone(SHANGHAI).isoformat(),
            'services_changed':False,'qq_login_requested':False,'sqlite_mode':'ro',
            'real_chat_acceptance':'NOT_INFERRED_FROM_STATUS'}
    def containers():
        value=phase2.inspect_containers(deployment)
        for item in value.values():
            if 'resources' in item:item['configured_limits']=item.pop('resources')
        return value
    checks={'host_usage':lambda:host_usage(deployment),'containers':containers,
        'container_actual_usage':lambda:container_usage(deployment),
        'astrbot_policy':lambda:phase2.filter_policy(read_json(data/'plugin_data/phase2_policy/readiness.json')),
        'configured_flags':lambda:configured_flags(data),
        'guard':lambda:guard_counts(data/'plugin_data/phase1_guard/guard.sqlite3'),
        'memory':lambda:memory_counts(data/'plugin_data/astrbot_plugin_memory_companion/memory_companion.db'),
        'search':lambda:search_counts(data/'plugin_data/phase3_search/budget.sqlite3',now),
        'proactive':lambda:proactive_status(data,now)}
    for name,check in checks.items():
        try: report[name]=check()
        except FileNotFoundError: report[name]={'status':'NOT_INITIALIZED'}
        except Exception as exc:
            code=str(exc) if isinstance(exc,phase2.StatusError) and str(exc) in SAFE_ERRORS else 'status_read_failed'
            report[name]={'status':'UNAVAILABLE','reason':code};report['status']='PARTIAL'
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment',type=Path,default=phase2.DEFAULT_DEPLOYMENT)
    result=build_report(parser.parse_args().deployment)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['status']=='INSPECTED' else 1)
