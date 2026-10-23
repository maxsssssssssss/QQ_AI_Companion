#!/usr/bin/env python3
"""Read-only, content-free production acceptance evidence (not an automatic PASS).

Only counts, hashes, fixed enums and timestamps leave the host. Raw messages,
identities, URLs, model arguments, credentials and exception text are omitted.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path

spec=importlib.util.spec_from_file_location('phase3_status',Path(__file__).with_name('phase3-status.py'))
status=importlib.util.module_from_spec(spec);spec.loader.exec_module(status)
KINDS={'normal','progress','final','proactive'}
STATES={'pending','sent','failed','unknown'}
TOOLS={'web_search','fetch_url','web_search_tavily','web_search_bocha','web_search_deepseek','web_fetch',
       'memory_companion_navigate','memory_companion_sources','phase3_event','phase3_memory_control'}


def digest(value): return hashlib.sha256(str(value).encode()).hexdigest()


def stamp(value):
    try:
        parsed=datetime.fromisoformat(value)
        return parsed.isoformat() if parsed.year in range(2020,2101) else None
    except (ValueError,TypeError):return None


def chain_types(raw):
    try:chain=json.loads(raw)
    except (TypeError,ValueError):return {'unrecognized':1}
    result={}
    for item in chain if isinstance(chain,list) else []:
        kind=item.get('type') if isinstance(item,dict) else None
        kind=kind if kind in {'Plain','Image','text','image','Reply','At','record','video'} else 'other'
        result[kind]=result.get(kind,0)+1
    return result


def diagnostic_projection(raw):
    """Only a fixed shape leaves the existing private turn ledger."""
    try: rows=json.loads(raw)
    except (ValueError,TypeError):return {}
    if not isinstance(rows,dict):return {}
    result={}
    enums={'found','partial','not_found','unavailable','complete_approved_topics','unknown',
           'stop','length','tool_calls','content_filter','recovered','failed','limited','cancelled','published'}
    for stage in ('inbound','grouped','model_request','profile_recall','model_final','cleaned_final','recovery','history','media'):
        row=rows.get(stage)
        if not isinstance(row,dict):continue
        safe={'at':stamp(row.get('at'))}
        for key in ('source_count','prompt_chars','history_items','chars','parts','pages','elapsed_ms','confirmed_parts','unconfirmed_tail',
                    'native_ref_count','native_context_chars'):
            if key in row:safe[key]=status.number(row[key],maximum=100000000)
        for key in ('status','coverage','finish_reason','state','publication_status'):
            if key in row:safe[key]=row[key] if isinstance(row[key],str) and row[key] in enums else 'unavailable'
        for key in ('has_tools','native_published','native_snapshot_present','statistics_deferred'):
            if type(row.get(key)) is bool:safe[key]=row[key]
        if isinstance(row.get('sha256'),str) and __import__('re').fullmatch('[a-f0-9]{64}',row['sha256']):
            safe['sha256']=row['sha256']
        refs=row.get('memory_refs')
        if isinstance(refs,list):
            safe['memory_refs']=[{'id_sha256':digest(r.get('id')),'version_sha256':digest(r.get('version')),
                'chars':status.number(r.get('chars'))} for r in refs[:128] if isinstance(r,dict)]
        result[stage]=safe
    return result


def guard_evidence(path,since):
    with status.readonly_db(path) as db:
        db.row_factory=__import__('sqlite3').Row
        turns=db.execute('SELECT * FROM turns WHERE julianday(created_at)>=julianday(?) ORDER BY created_at LIMIT 100',(since,)).fetchall()
        result=[]
        for turn in turns:
            sources=db.execute('''SELECT i.* FROM turn_sources s JOIN inbound i
                ON i.platform=s.platform AND i.session=s.session AND i.message_id=s.message_id
                WHERE s.turn_id=? ORDER BY s.ordinal''',(turn['turn_id'],)).fetchall()
            sent=db.execute('SELECT * FROM deliveries WHERE turn_id=? ORDER BY created_at,part_index',(turn['turn_id'],)).fetchall()
            result.append({'turn_hash':digest(turn['turn_id']),
                'state':turn['state'] if turn['state'] in {'collecting','ready','generating','done','superseded','cancelled','interrupted'} else 'unknown',
                'proactive':bool(turn['proactive']),'created_at':stamp(turn['created_at']),'finished_at':stamp(turn['finished_at']),
                'source_count':len(sources),'sources':[{'content_sha256':digest(row['text']),
                    'chars':len(row['text']),'received_at':stamp(row['received_at']),'components':chain_types(row['chain_json'])} for row in sources],
                'deliveries':[{'delivery_hash':digest(row['delivery_id']),'kind':row['kind'] if row['kind'] in KINDS else 'unknown',
                    'part_index':row['part_index'],'state':row['state'] if row['state'] in STATES else 'unknown',
                    'content_sha256':digest(row['text']),'chars':len(row['text']),
                    'receipt_confirmed':row['state']=='sent' and row['receipt_source'] in {'AstrBot4.29.1_event_send_ack','AstrBot4.29.1_send_by_session_ack'},
                    'remote_message_id_present':bool(row['platform_message_id']),
                    'created_at':stamp(row['created_at']),'finished_at':stamp(row['finished_at'])} for row in sent]})
            result[-1]['bubbles']=bubble_evidence(db,turn['turn_id'])
            if 'diagnostics_json' in turn.keys():
                result[-1]['stages']=diagnostic_projection(turn['diagnostics_json'])
        return result


def bubble_evidence(db,turn_id):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='bubble_parts'").fetchone():
        return {'status':'NOT_INITIALIZED'}
    def hash_only(value):
        value=str(value or '')
        return value if len(value)==64 and all(c in '0123456789abcdef' for c in value) else None
    rows=db.execute('''SELECT p.*,g.generation_sha256 FROM bubble_parts p
        JOIN bubble_plans g ON g.turn_id=p.turn_id AND g.kind=p.kind
        WHERE p.turn_id=? ORDER BY p.kind,p.part_index''',(turn_id,)).fetchall()
    return [{'kind':r['kind'] if r['kind'] in KINDS else 'unknown',
             'part_index':r['part_index'],'delivery_hash':digest(r['delivery_id']),
             'generation_sha256':hash_only(r['generation_sha256']),
             'content_sha256':hash_only(r['content_sha256']),'chars':r['chars'],
             'state':r['state'] if r['state'] in {'planned','sent','failed','unknown','cancelled'} else 'unknown',
             'adapter_attempts':r['transport_attempts'],
             'started_at':stamp(r['started_at']),'finished_at':stamp(r['finished_at'])} for r in rows]


def model_evidence(path,since):
    epoch=datetime.fromisoformat(since).timestamp()
    with status.readonly_db(path) as db:
        row=db.execute('''SELECT count(*),COALESCE(sum(token_input_other),0),COALESCE(sum(token_input_cached),0),COALESCE(sum(token_output),0)
            FROM provider_stats WHERE start_time>=?''',(epoch,)).fetchone()
        runs,uncached,cached,output=map(int,row)
        return {'internal_agent_recorded_runs':runs,'http_request_count':None,
            'input_uncached':uncached,'input_cached':cached,'output':output,
            'peak_uncached_estimate_cny':round(((uncached+cached)*2+output*8)/1e6,6),
            'scope':'one_stats_row_per_agent_run_may_include_multiple_model_steps;memory_and_progress_separate','account_bill':False}


def recovery_evidence(path,since):
    """Separate SDK continuation usage; never count it as QQ delivery success."""
    with status.readonly_db(path) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='response_recoveries'").fetchone():
            return {'status':'NOT_INITIALIZED','attempts':0}
        db.row_factory=__import__('sqlite3').Row
        rows=db.execute('SELECT * FROM response_recoveries WHERE julianday(created_at)>=julianday(?) ORDER BY created_at',(since,)).fetchall()
        return {'attempts':len(rows),'input_tokens':sum(r['input_tokens'] for r in rows),
            'output_tokens':sum(r['output_tokens'] for r in rows),
            'scope':'empty_final_continuation_only;not_delivery_confirmation;unknown_usage_not_zero_cost',
            'rows':[{'turn_hash':digest(r['turn_id']),'state':r['state'] if r['state'] in {'pending','recovered','failed','cancelled','unknown'} else 'unknown',
                     'created_at':stamp(r['created_at']),'finished_at':stamp(r['finished_at'])} for r in rows]}


def memory_usage(path,since):
    raw=json.loads(path.read_text())
    epoch=datetime.fromisoformat(since).timestamp()
    rows=[row for row in raw.get('recent',[]) if isinstance(row,dict) and
          type(row.get('ts')) in (int,float) and row['ts']>=epoch]
    def total(name):return sum(v for row in rows if type(v:=row.get(name)) is int and v>=0)
    return {'recorded_calls':len(rows),'reported_tokens':total('reported_tokens'),
        'estimated_tokens':total('estimated_tokens'),'input':total('prompt_tokens'),'output':total('completion_tokens'),
        'successful_calls':sum(row.get('success') is True for row in rows),
        'peak_uncached_estimate_cny':round((total('prompt_tokens')*2+total('completion_tokens')*8)/1e6,6),
        'account_bill':False,'recent_record_limit':240}


def search_evidence(budget_path,guard_path,since):
    """Correlate durable acknowledgements and tools without exporting inputs.

    'returned' means the native adapter returned; its body may still contain an
    HTTP error. Do not turn a timing audit into a claim of successful retrieval.
    """
    with status.readonly_db(budget_path) as web, status.readonly_db(guard_path) as guard:
        web.row_factory=guard.row_factory=__import__('sqlite3').Row
        rows=web.execute('''SELECT * FROM web_tool_attempts
            WHERE julianday(started_at)>=julianday(?) ORDER BY started_at LIMIT 100''',(since,)).fetchall()
        result=[]
        for row in rows:
            progress=guard.execute('SELECT * FROM deliveries WHERE delivery_id=? AND turn_id=? AND kind=\'progress\'',
                (row['progress_delivery_id'],row['turn_id'])).fetchone()
            finals=guard.execute('SELECT * FROM deliveries WHERE turn_id=? AND kind=\'final\' ORDER BY part_index',
                (row['turn_id'],)).fetchall()
            def before(left,right):
                try:
                    a,b=datetime.fromisoformat(left),datetime.fromisoformat(right)
                    return a.tzinfo is not None and b.tzinfo is not None and a<=b
                except (TypeError,ValueError):return False
            confirmed=bool(progress and progress['state']=='sent' and progress['receipt_source'] in
                {'AstrBot4.29.1_event_send_ack','AstrBot4.29.1_send_by_session_ack'})
            result.append({'turn_hash':digest(row['turn_id']),'attempt_hash':digest(row['id']),
                'ordinal':row['ordinal'],'kind':row['kind'] if row['kind'] in {'search','fetch'} else 'unknown',
                'state':row['state'] if row['state'] in {'running','returned','failed','timeout','cancelled','unknown_interrupted'} else 'unknown',
                'progress_delivery_hash':digest(row['progress_delivery_id']) if row['progress_delivery_id'] else None,
                'progress_confirmed':confirmed,'progress_before_tool':confirmed and before(progress['finished_at'],row['started_at']),
                'started_at':stamp(row['started_at']),'finished_at':stamp(row['finished_at']),
                'final_parts':len(finals),'final_sent_parts':sum(r['state']=='sent' for r in finals),
                'all_finals_created_after_tool':bool(finals) and all(before(row['finished_at'],r['created_at']) for r in finals),
                'returned_is_not_http_success':True})
        return result


def report(data,since):
    dt=datetime.fromisoformat(since)
    if dt.tzinfo is None:raise ValueError('timezone_required')
    result={'observed_at':datetime.now(timezone.utc).isoformat(),'since':dt.isoformat(),
        'raw_content_exported':False,'human_naturalness':'PENDING_OWNER_EVALUATION','database_writes':False}
    for name,fn in {'turns':lambda:guard_evidence(data/'plugin_data/phase1_guard/guard.sqlite3',since),
                    'empty_recovery':lambda:recovery_evidence(data/'plugin_data/phase1_guard/guard.sqlite3',since),
                    'model_usage':lambda:model_evidence(data/'data_v4.db',since),
                    'memory_usage':lambda:memory_usage(data/'plugin_data/astrbot_plugin_memory_companion/memory_companion_token_usage.json',since),
                    'search_attempts':lambda:search_evidence(data/'plugin_data/phase3_search/budget.sqlite3',data/'plugin_data/phase1_guard/guard.sqlite3',since)}.items():
        try:result[name]=fn()
        except FileNotFoundError:result[name]={'status':'NOT_INITIALIZED'}
        except Exception:result[name]={'status':'UNAVAILABLE','reason':'audit_read_failed'}
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,default=Path('/opt/qq-ai-companion/deployment/state/data'))
    parser.add_argument('--since',required=True)
    print(json.dumps(report(**vars(parser.parse_args())),ensure_ascii=False,indent=2))
