"""Bounded research/pending-delivery metadata in the existing proactive ledger.

This is neither a scheduler nor a memory fact store. Opportunities originate in
the native scheduler; calls are charged before I/O; findings remain unshared until
Guard confirms their delivery. Restoring this store never schedules a send.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlsplit
import uuid
from zoneinfo import ZoneInfo


TERMINAL_CALLS = frozenset({'returned', 'succeeded', 'failed', 'timeout', 'cancelled',
                           'empty', 'malformed', 'rejected', 'interrupted'})
KINDS = frozenset({'observation', 'opinion', 'hypothesis', 'external_fact', 'creative_work'})
STATES = frozenset({'unshared', 'researching', 'ready', 'shared', 'discarded', 'expired'})
FINDING_IDLE_SECONDS = 3 * 86400
FINDING_MAX_SECONDS = 30 * 86400
SECRET_KEYS = frozenset({'api_key', 'apikey', 'authorization', 'password', 'secret', 'token',
                         'access_token', 'cookie', 'set-cookie', 'reasoning_content', 'reasoning'})
USAGE_KEYS = frozenset({'prompt_tokens','completion_tokens','total_tokens','reasoning_tokens',
                        'input_tokens','output_tokens','prompt_cache_hit_tokens',
                        'prompt_cache_miss_tokens','cached_tokens'})


def _finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(name)
    return float(value)


def _string(value, limit, name, *, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not value and not empty):
        raise ValueError(name)
    return value


def _json(value, limit=65536):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(encoded.encode()) > limit:
        raise ValueError('autonomy_metadata_too_large')
    return encoded


def _scrub(value, *, depth=0):
    """Drop credentials/hidden reasoning; preserve the visible request/output."""
    if depth > 32:
        raise ValueError('audit_nesting_limit')
    if isinstance(value, dict):
        return {str(k): '[REDACTED]' if str(k).lower() in SECRET_KEYS
                else _scrub(v, depth=depth+1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(v, depth=depth+1) for v in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise ValueError('audit_json_payload_required')


def _sources(values):
    if not isinstance(values, list) or len(values) > 12:
        raise ValueError('invalid_finding_sources')
    result = []
    for source in values:
        if not isinstance(source, dict):
            raise ValueError('invalid_finding_source')
        url = _string(source.get('url'), 4096, 'invalid_source_url')
        parsed = urlsplit(url)
        # PublicPolicy remains the execution authority. Persistence additionally
        # excludes credentialed/fragment URLs and non-web references.
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError('invalid_source_url')
        item = {'url': url,
                'title': _string(source.get('title', ''), 500, 'invalid_source_title', empty=True),
                'observed_at': _finite(source.get('observed_at', source.get('fetched_at', 0)), 'invalid_source_time'),
                'verified': source.get('verified') is True}
        for key in ('attempt_id', 'content_sha256', 'quality', 'kind', 'verification'):
            if key in source:
                item[key] = _string(source[key], 200, 'invalid_source_metadata', empty=True)
        if item['observed_at'] < 0:
            raise ValueError('invalid_source_time')
        if 'expires_at' in source:
            item['expires_at'] = _finite(source['expires_at'], 'invalid_source_expiry')
            if item['expires_at'] < 0:
                raise ValueError('invalid_source_expiry')
        if 'revoked' in source:
            if type(source['revoked']) is not bool:raise ValueError('invalid_source_revocation')
            item['revoked']=source['revoked']
        result.append(item)
    return result


def _progress_signature(content, progress, sources):
    """A structural change check, not an LLM judgment of research usefulness.

    Receipt/time/title changes and reordering alone are not new research. The
    caller separately opts in only for a useful continuation; no-op saves still
    cannot extend TTL even if that caller incorrectly requests renewal.
    """
    return (' '.join(content.split()), ' '.join(progress.split()),
            sorted({(s['url'], s.get('content_sha256', '')) for s in sources}))


class AutonomyState:
    """Synchronous atomic reservations on ProactiveGate's SQLite connection.

    No method waits or performs network I/O. The caller must not enter with an
    unrelated uncommitted transaction: reservations must be durable before I/O.
    Existing production allowance tables are never cleared or reinterpreted.
    """
    def __init__(self, db, *, clock=time.time, audit_dir=None, model_limit=144,
                 tool_limit=288, tools_per_opportunity=3, exploration_seconds=600,
                 pocket_limit=64, retention_days=14, audit_capacity_bytes=64*1024*1024,
                 audit_entry_bytes=2*1024*1024):
        if not isinstance(db, sqlite3.Connection):
            raise TypeError('existing_proactive_connection_required')
        limits = ((model_limit, 1, 10000), (tool_limit, 1, 10000),
                  (tools_per_opportunity, 1, 3), (exploration_seconds, 1, 600),
                  (pocket_limit, 1, 256), (retention_days, 1, 14),
                  (audit_capacity_bytes, 4096, 1024*1024*1024),
                  (audit_entry_bytes, 1024, 16*1024*1024))
        if any(type(v) is not int or not lo <= v <= hi for v, lo, hi in limits):
            raise ValueError('invalid_autonomy_limits')
        if audit_entry_bytes > audit_capacity_bytes:
            raise ValueError('audit_entry_exceeds_capacity')
        self.db, self.clock = db, clock
        self.model_limit, self.tool_limit = model_limit, tool_limit
        self.tools_per_opportunity, self.exploration_seconds = tools_per_opportunity, exploration_seconds
        self.pocket_limit, self.retention_days = pocket_limit, retention_days
        self.audit_capacity_bytes, self.audit_entry_bytes = audit_capacity_bytes, audit_entry_bytes
        self.audit_dir = Path(audit_dir) if audit_dir is not None else None
        self._idle_connection()
        if self.audit_dir is not None:
            if self.audit_dir.is_symlink():
                raise ValueError('audit_directory_symlink')
            self.audit_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.audit_dir.chmod(0o700)
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS autonomy_opportunities(
              id TEXT PRIMARY KEY,candidate_id TEXT NOT NULL,session TEXT NOT NULL,
              created REAL NOT NULL,deadline REAL NOT NULL,input_version TEXT NOT NULL,
              state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS autonomy_evaluations(
              id TEXT PRIMARY KEY,opportunity_id TEXT NOT NULL,started REAL NOT NULL,
              deadline REAL NOT NULL,prompt_version TEXT NOT NULL,state TEXT NOT NULL,
              input_version TEXT NOT NULL DEFAULT '',
              raw_control TEXT NOT NULL DEFAULT '',parsed TEXT NOT NULL DEFAULT '{}',
              effective_state TEXT NOT NULL DEFAULT '',reason TEXT NOT NULL DEFAULT '',
              topic TEXT NOT NULL DEFAULT '',finished REAL,
              delivery_attempt TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS autonomy_calls(
              id TEXT PRIMARY KEY,evaluation_id TEXT NOT NULL,opportunity_id TEXT NOT NULL,
              day TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,
              started REAL NOT NULL,state TEXT NOT NULL,finished REAL,elapsed REAL,
              usage TEXT NOT NULL DEFAULT '{}',metadata TEXT NOT NULL DEFAULT '{}');
            CREATE INDEX IF NOT EXISTS autonomy_calls_day_kind ON autonomy_calls(day,kind);
            CREATE INDEX IF NOT EXISTS autonomy_calls_opportunity ON autonomy_calls(opportunity_id,kind);
            CREATE TABLE IF NOT EXISTS autonomy_pocket(
              id TEXT PRIMARY KEY,session TEXT NOT NULL,opportunity_id TEXT NOT NULL,
              question TEXT NOT NULL,content TEXT NOT NULL,kind TEXT NOT NULL,sources TEXT NOT NULL,
              progress TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,
              expires REAL NOT NULL,state TEXT NOT NULL,revision INTEGER NOT NULL,
              selected_eval TEXT NOT NULL DEFAULT '',selected_revision INTEGER,
              shared_attempt TEXT NOT NULL DEFAULT '',confirmed_sha256 TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS autonomy_supplements(
              exchange_id TEXT PRIMARY KEY,parent_attempt TEXT NOT NULL,parent_kind TEXT NOT NULL,
              attempt_id TEXT NOT NULL UNIQUE,created REAL NOT NULL,state TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS autonomy_supplements_parent ON autonomy_supplements(parent_attempt);
            CREATE TABLE IF NOT EXISTS autonomy_audits(
              id TEXT PRIMARY KEY,evaluation_id TEXT NOT NULL,kind TEXT NOT NULL,
              created REAL NOT NULL,path TEXT NOT NULL,sha256 TEXT NOT NULL,size INTEGER NOT NULL,
              complete INTEGER NOT NULL,state TEXT NOT NULL);
        ''')
        if 'input_version' not in {row[1] for row in self.db.execute('PRAGMA table_info(autonomy_evaluations)')}:
            self.db.execute("ALTER TABLE autonomy_evaluations ADD COLUMN input_version TEXT NOT NULL DEFAULT ''")
            self.db.commit()
        if 'delivery_attempt' not in {row[1] for row in self.db.execute('PRAGMA table_info(autonomy_evaluations)')}:
            self.db.execute("ALTER TABLE autonomy_evaluations ADD COLUMN delivery_attempt TEXT NOT NULL DEFAULT ''")
            self.db.commit()
        if 'last_progress' not in {row[1] for row in self.db.execute('PRAGMA table_info(autonomy_pocket)')}:
            # Legacy updated timestamps include arbitrary saves. Created is the
            # only conservative known progress timestamp; do not rejuvenate it.
            self.db.execute('ALTER TABLE autonomy_pocket ADD COLUMN last_progress REAL')
            self.db.execute('UPDATE autonomy_pocket SET last_progress=created')
            self.db.commit()
        with self.db:
            self.db.execute('UPDATE autonomy_pocket SET last_progress=created WHERE last_progress IS NULL')
            self.db.execute("UPDATE autonomy_calls SET state='interrupted',finished=? WHERE state='running'", (self.clock(),))
            self.db.execute("UPDATE autonomy_evaluations SET state='interrupted',effective_state='interrupted',reason='restart_requires_reevaluation',finished=? WHERE state='running'", (self.clock(),))
            self.db.execute("UPDATE autonomy_opportunities SET state='interrupted' WHERE state='running'")
            # Selection is advisory, never a durable authorization to send.
            self.db.execute("UPDATE autonomy_pocket SET selected_eval='',selected_revision=NULL WHERE selected_eval!=''")
            self.db.execute("UPDATE autonomy_supplements SET state='unknown' WHERE state='reserved'")
        self.prune_audits()
        self.prune_pocket()

    def _idle_connection(self):
        if self.db.in_transaction:
            raise RuntimeError('autonomy_requires_committed_connection')

    def _day(self, now=None):
        return datetime.fromtimestamp(self.clock() if now is None else now, ZoneInfo('Asia/Shanghai')).date().isoformat()

    def begin_opportunity(self, opportunity_id, *, evaluation_id, candidate_id, session,
                          deadline, input_version, prompt_version=''):
        opportunity_id = _string(opportunity_id, 500, 'invalid_opportunity_id')
        evaluation_id = _string(evaluation_id, 600, 'invalid_evaluation_id')
        candidate_id = _string(candidate_id, 500, 'invalid_candidate_id')
        session = _string(session, 300, 'invalid_session')
        version = _string(str(input_version), 200, 'invalid_input_version', empty=True)
        prompt_version = _string(prompt_version, 100, 'invalid_prompt_version', empty=True)
        deadline = _finite(deadline, 'invalid_deadline')
        now = self.clock()
        if deadline <= now:
            return {'allowed': False, 'reason': 'candidate_expired'}
        self._idle_connection()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            existing = self.db.execute('SELECT candidate_id,session,deadline FROM autonomy_opportunities WHERE id=?', (opportunity_id,)).fetchone()
            if existing and (existing[0] != candidate_id or existing[1] != session):
                raise ValueError('opportunity_identity_collision')
            if self.db.execute('SELECT 1 FROM autonomy_evaluations WHERE id=?', (evaluation_id,)).fetchone():
                self.db.rollback()
                return {'allowed': False, 'reason': 'evaluation_already_attempted'}
            # A reconsideration cannot extend the original candidate lifetime.
            candidate_deadline = min(deadline, existing[2]) if existing else deadline
            if candidate_deadline <= now:
                self.db.rollback()
                return {'allowed': False, 'reason': 'candidate_expired'}
            self.db.execute('INSERT OR IGNORE INTO autonomy_opportunities VALUES (?,?,?,?,?,?,?)',
                            (opportunity_id,candidate_id,session,now,candidate_deadline,version,'running'))
            self.db.execute("UPDATE autonomy_opportunities SET state='running' WHERE id=?", (opportunity_id,))
            run_deadline = min(candidate_deadline, now+self.exploration_seconds)
            self.db.execute('INSERT INTO autonomy_evaluations(id,opportunity_id,started,deadline,prompt_version,state,input_version) VALUES (?,?,?,?,?,?,?)',
                            (evaluation_id,opportunity_id,now,run_deadline,prompt_version,'running',version))
            self.db.commit()
            return {'allowed': True, 'reason': 'allowed', 'opportunity_id': opportunity_id,
                    'evaluation_id': evaluation_id, 'deadline': run_deadline}
        except BaseException:
            self.db.rollback()
            raise

    def evaluation(self, evaluation_id):
        row = self.db.execute('''SELECT e.id,e.opportunity_id,o.candidate_id,o.session,
            e.started,e.deadline,e.input_version,e.state,e.effective_state,e.reason,e.topic,e.delivery_attempt
            FROM autonomy_evaluations e JOIN autonomy_opportunities o ON o.id=e.opportunity_id WHERE e.id=?''', (evaluation_id,)).fetchone()
        keys = ('evaluation_id','opportunity_id','candidate_id','session','started','deadline',
                'input_version','state','effective_state','reason','topic','delivery_attempt')
        return dict(zip(keys,row)) if row else None

    def _reserve(self, evaluation_id, kind, request_id=None):
        _string(evaluation_id, 600, 'invalid_evaluation_id')
        request_id = request_id or str(uuid.uuid4())
        _string(request_id, 700, 'invalid_request_id')
        now = self.clock()
        self._idle_connection()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            current = self.evaluation(evaluation_id)
            reason = None
            if not current: reason = 'evaluation_missing'
            elif current['state'] != 'running': reason = 'evaluation_not_running'
            elif now >= current['deadline']: reason = 'exploration_deadline'
            elif self.db.execute('SELECT 1 FROM autonomy_calls WHERE id=?',(request_id,)).fetchone(): reason='call_already_attempted'
            elif kind == 'model':
                count = self.db.execute("SELECT count(*) FROM autonomy_calls WHERE day=? AND kind='model'",(self._day(now),)).fetchone()[0]
                if count >= self.model_limit: reason='model_request_budget'
            elif kind == 'calculator':
                count=self.db.execute("SELECT count(*) FROM autonomy_calls WHERE opportunity_id=? AND kind='calculator'",(current['opportunity_id'],)).fetchone()[0]
                if count>=4:reason='opportunity_calculator_budget'
            else:
                # A deferred re-evaluation may decide what to do later, but it
                # cannot refresh this opportunity's ten-minute exploration.
                created = self.db.execute('SELECT created FROM autonomy_opportunities WHERE id=?',
                    (current['opportunity_id'],)).fetchone()[0]
                count = self.db.execute("SELECT count(*) FROM autonomy_calls WHERE day=? AND kind IN ('search','fetch')",(self._day(now),)).fetchone()[0]
                per = self.db.execute("SELECT count(*) FROM autonomy_calls WHERE opportunity_id=? AND kind IN ('search','fetch')",(current['opportunity_id'],)).fetchone()[0]
                if now >= created+self.exploration_seconds: reason='opportunity_exploration_deadline'
                elif per >= self.tools_per_opportunity: reason='opportunity_tool_budget'
                elif count >= self.tool_limit: reason='proactive_tool_daily_budget'
            if reason:
                self.db.rollback()
                return {'allowed':False,'reason':reason}
            ordinal = self.db.execute('SELECT count(*) FROM autonomy_calls WHERE opportunity_id=?',(current['opportunity_id'],)).fetchone()[0]+1
            self.db.execute('INSERT INTO autonomy_calls(id,evaluation_id,opportunity_id,day,kind,ordinal,started,state) VALUES (?,?,?,?,?,?,?,?)',
                (request_id,evaluation_id,current['opportunity_id'],self._day(now),kind,ordinal,now,'running'))
            self.db.commit()
            return {'allowed':True,'reason':'allowed','id':request_id,'ordinal':ordinal}
        except BaseException:
            self.db.rollback()
            raise

    def reserve_model_request(self, evaluation_id, request_id=None):
        return self._reserve(evaluation_id, 'model', request_id)

    def reserve_tool_attempt(self, evaluation_id, kind, request_id=None):
        if kind not in {'search','fetch'}:
            raise ValueError('unsupported_proactive_tool')
        return self._reserve(evaluation_id, kind, request_id)

    def reserve_calculation(self, evaluation_id, request_id=None):
        """Four local calculations per durable opportunity, no web/model charge."""
        return self._reserve(evaluation_id,'calculator',request_id)

    def finish_call(self, request_id, state, *, usage=None, elapsed=0, metadata=None):
        if state not in TERMINAL_CALLS:
            raise ValueError('invalid_autonomy_call_state')
        elapsed = _finite(elapsed, 'invalid_call_duration')
        if elapsed < 0: raise ValueError('invalid_call_duration')
        usage = usage or {}
        if (not isinstance(usage,dict) or any(k not in USAGE_KEYS for k in usage)
                or any(type(v) is not int or v < 0 for v in usage.values())):
            raise ValueError('invalid_call_usage')
        metadata = _scrub(metadata or {})
        self._idle_connection()
        kind=self.db.execute('SELECT kind FROM autonomy_calls WHERE id=?',(request_id,)).fetchone()
        metadata_limit=256*1024 if kind and kind[0] in {'search','fetch'} else 65536
        with self.db:
            changed = self.db.execute("UPDATE autonomy_calls SET state=?,finished=?,elapsed=?,usage=?,metadata=? WHERE id=? AND state='running'",
                (state,self.clock(),elapsed,_json(usage),_json(metadata,metadata_limit),request_id)).rowcount
        return bool(changed)

    def tool_sources(self, opportunity_id):
        rows = self.db.execute("SELECT metadata FROM autonomy_calls WHERE opportunity_id=? AND kind IN ('search','fetch') AND state IN ('returned','succeeded') ORDER BY ordinal",(opportunity_id,)).fetchall()
        result = {}
        for row in rows:
            for source in json.loads(row[0]).get('sources',[]):
                if isinstance(source,dict) and isinstance(source.get('url'),str):
                    result[source['url']] = source
        return list(result.values())

    def update_decision(self, evaluation_id, *, raw_control, parsed, effective_state, reason, topic=''):
        _string(raw_control, 2000, 'invalid_raw_control', empty=True)
        _string(effective_state, 80, 'invalid_effective_state')
        _string(reason, 160, 'invalid_decision_reason', empty=True)
        _string(topic, 1000, 'invalid_selected_topic', empty=True)
        if not isinstance(parsed,dict):raise ValueError('parsed_decision_object_required')
        # Visible output lives only in bounded private audit snapshots, never
        # indefinitely in the compact decision ledger.
        parsed_json = _json(_scrub({k:v for k,v in parsed.items() if k in {
            'decision','delay_minutes','reason','selected_finding_ids','supplement'}}), 16384)
        self._idle_connection()
        with self.db:
            return bool(self.db.execute("UPDATE autonomy_evaluations SET raw_control=?,parsed=?,effective_state=?,reason=?,topic=?,state='complete',finished=? WHERE id=? AND state='running'",
                (raw_control,parsed_json,effective_state,reason,topic,self.clock(),evaluation_id)).rowcount)

    def evaluation_state(self,evaluation_id):
        return self.evaluation(evaluation_id)

    def update_delivery(self,evaluation_id,attempt_id,state,reason=''):
        """Attach a final Guard outcome without rewriting the model's decision.

        The caller supplies the existing Guard attempt identity and terminal
        result. Interest/progress sends do not finalize the evaluation. A
        completed delivery is immutable, including unknown and partial failure.
        """
        _string(evaluation_id,600,'invalid_evaluation_id')
        _string(attempt_id,700,'invalid_delivery_attempt')
        _string(reason,160,'invalid_delivery_reason',empty=True)
        if state not in {'sent','failed','unknown','cancelled'}:
            raise ValueError('invalid_delivery_state')
        self._idle_connection()
        with self.db:
            return bool(self.db.execute("""UPDATE autonomy_evaluations
                SET effective_state=?,reason=?,finished=?,delivery_attempt=?
                WHERE id=? AND state='complete' AND effective_state='ready_to_send'
                AND delivery_attempt=''""",
                (state,reason,self.clock(),attempt_id,evaluation_id)).rowcount)

    def finalize_evaluation(self,evaluation_id,state,*,reason=''):
        """Set an execution outcome while retaining any original control data."""
        _string(state,80,'invalid_evaluation_state')
        _string(reason,160,'invalid_evaluation_reason',empty=True)
        if state=='running':raise ValueError('terminal_evaluation_state_required')
        self._idle_connection()
        with self.db:
            return bool(self.db.execute("UPDATE autonomy_evaluations SET state='complete',effective_state=?,reason=?,finished=? WHERE id=? AND state='running'",
                (state,reason,self.clock(),evaluation_id)).rowcount)

    def save_finding(self, finding_id, *, session, question, content, kind, sources,
                     expires_at, opportunity_id, progress='', state='unshared', expected_revision=None,
                     research_progress=False):
        for value,limit,name in ((finding_id,500,'finding_id'),(session,300,'session'),
                                 (question,1000,'question'),(content,12000,'content'),
                                 (opportunity_id,500,'opportunity_id')):
            _string(value,limit,'invalid_'+name)
        _string(progress,3000,'invalid_progress',empty=True)
        if type(research_progress) is not bool:
            raise ValueError('invalid_research_progress')
        if kind not in KINDS or state not in {'unshared','researching','ready'}:
            raise ValueError('invalid_finding_kind_or_state')
        sources = _sources(sources)
        if kind == 'external_fact' and not sources and state != 'unshared':
            raise ValueError('external_fact_without_source_must_remain_unshared')
        expires = _finite(expires_at,'invalid_finding_expiry')
        now = self.clock()
        if not now < expires <= now+30*86400:
            raise ValueError('invalid_finding_expiry')
        self.prune_pocket()
        self._idle_connection()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            opportunity=self.db.execute('SELECT session FROM autonomy_opportunities WHERE id=?',(opportunity_id,)).fetchone()
            if not opportunity or opportunity[0]!=session:
                raise ValueError('finding_opportunity_scope_mismatch')
            old=self.db.execute('''SELECT session,revision,state,question,content,kind,sources,
                progress,created,updated,expires,last_progress FROM autonomy_pocket WHERE id=?''',(finding_id,)).fetchone()
            if old:
                if old[0]!=session or old[2] in {'discarded','shared','expired'}:
                    raise ValueError('finding_identity_or_terminal_state')
                if expected_revision is None or old[1]!=expected_revision:
                    raise ValueError('stale_finding_revision')
                previous_sources=json.loads(old[6])
                # A previously persisted receipt keeps its original observation
                # and expiry. Only a new actual receipt can refresh a source.
                receipts={(s['attempt_id'],s['url']):s for s in previous_sources if s.get('attempt_id')}
                for source in sources:
                    previous=receipts.get((source.get('attempt_id'),source['url']))
                    if previous:
                        source['observed_at']=previous['observed_at']
                        if 'expires_at' in previous:
                            source['expires_at']=min(previous['expires_at'],source.get('expires_at',previous['expires_at']))
                        if previous.get('revoked') is True:source['revoked']=True
                changed=(question,content,kind,_json(sources),progress,state)!=(
                    old[3],old[4],old[5],_json(previous_sources),old[7],old[2])
                new_signature=_progress_signature(content,progress,sources)
                old_signature=_progress_signature(old[4],old[7],previous_sources)
                meaningful=(new_signature[0]!=old_signature[0]
                    or bool(new_signature[1] and new_signature[1]!=old_signature[1])
                    or bool(set(new_signature[2])-set(old_signature[2])))
                renewed=bool(research_progress and meaningful)
                expires=min(expires,now+FINDING_IDLE_SECONDS,old[8]+FINDING_MAX_SECONDS,
                            expires if renewed else old[10])
                if not changed and expires==old[10]:
                    self.db.commit()
                    return {'saved':True,'id':finding_id,'revision':old[1],'state':old[2],
                            'reason':'unchanged','ttl_extended':False,'expires_at':old[10]}
                last_progress=now if renewed else old[11]
                self.db.execute('''UPDATE autonomy_pocket SET question=?,content=?,kind=?,sources=?,progress=?,updated=?,expires=?,state=?,revision=revision+1,selected_eval='',selected_revision=NULL,last_progress=? WHERE id=?''',
                    (question,content,kind,_json(sources),progress,now,expires,state,last_progress,finding_id))
                revision=old[1]+1
                ttl_extended=expires>old[10]
            else:
                count=self.db.execute("SELECT count(*) FROM autonomy_pocket WHERE session=? AND state IN ('unshared','researching','ready') AND expires>?",(session,now)).fetchone()[0]
                if count>=self.pocket_limit:
                    self.db.rollback();return {'saved':False,'reason':'pocket_capacity'}
                expires=min(expires,now+FINDING_IDLE_SECONDS)
                self.db.execute('''INSERT INTO autonomy_pocket(id,session,opportunity_id,question,content,kind,sources,progress,created,updated,expires,state,revision,last_progress) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?)''',
                    (finding_id,session,opportunity_id,question,content,kind,_json(sources),progress,now,now,expires,state,now))
                revision=1
                ttl_extended=False
            self.db.commit();return {'saved':True,'id':finding_id,'revision':revision,'state':state,
                                    'ttl_extended':ttl_extended,'expires_at':expires}
        except BaseException:
            self.db.rollback();raise

    def list_pocket(self, session, *, limit=12):
        if type(limit) is not int or not 1 <= limit <= 128:
            raise ValueError('invalid_pocket_page_limit')
        limit=min(limit,self.pocket_limit)
        self.prune_pocket()
        rows=self.db.execute('''SELECT id,question,content,kind,sources,progress,created,updated,expires,state,revision,last_progress
            FROM autonomy_pocket WHERE session=? AND state IN ('unshared','researching','ready') AND expires>? ORDER BY updated DESC,id LIMIT ?''',
            (session,self.clock(),limit)).fetchall()
        keys=('id','question','content','kind','sources','progress','created','updated','expires_at','state','revision','last_progress_at')
        result=[]
        for row in rows:
            item=dict(zip(keys,row));item['sources']=json.loads(item['sources']);item['requires_reevaluation']=True
            item['hard_expires_at']=item['created']+FINDING_MAX_SECONDS
            item['source_status']={s['url']:self.source_freshness(s) for s in item['sources']}
            # Retrieval age is not publication age or a claim's factual date.
            item['source_observed_age_seconds']={s['url']:max(0,self.clock()-s['observed_at'])
                if s['observed_at']>0 else None for s in item['sources']}
            item['evidence_status'] = ('unverified' if not item['sources']
                else 'retrieved_not_fact_verified') if item['kind']=='external_fact' else 'not_external_fact'
            result.append(item)
        return result

    def source_freshness(self, source):
        """Retrieval age is independent of draft TTL and factual publication age.

        Aging evidence remains usable with its actual date and scope. Only an
        explicit source expiry/revocation is an expiration, not three-day age.
        """
        if not isinstance(source,dict):return 'invalid'
        if source.get('revoked') is True:return 'revoked'
        try:
            observed=_finite(source.get('observed_at',source.get('fetched_at',0)),'invalid_source_time')
            expires=_finite(source['expires_at'],'invalid_source_expiry') if 'expires_at' in source else None
        except ValueError:return 'invalid'
        now=self.clock()
        if expires is not None and now>=expires:return 'expired'
        if observed<=0:return 'unknown'
        if observed>now:return 'future'
        return 'aging' if now>=observed+FINDING_IDLE_SECONDS else 'current'

    def source_is_current(self,source):
        """Freshness hint only; do not use this as retrieval/send authorization."""
        return self.source_freshness(source)=='current'

    def source_is_usable(self,source):
        """Time/lifecycle check only; callers must still verify receipt + scope."""
        return self.source_freshness(source) in {'current','aging','unknown'}

    def select_finding(self, finding_id, evaluation_id):
        current=self.evaluation(evaluation_id)
        if not current or current['state']!='running' or self.clock()>=current['deadline']:
            return False
        self._idle_connection()
        with self.db:
            return bool(self.db.execute("UPDATE autonomy_pocket SET selected_eval=?,selected_revision=revision WHERE id=? AND session=? AND state IN ('unshared','researching','ready') AND expires>?",
                (evaluation_id,finding_id,current['session'],self.clock())).rowcount)

    def mark_shared(self, finding_id, *, attempt_id, confirmed_text, evaluation_id,
                    whole_plan_confirmed=False):
        """Caller supplies Guard-confirmed prefix only, with current selection.

        This observer is not delivery authority. An empty prefix never marks a
        finding shared; partially sent claims remain conservatively unshared
        unless the entire selected content is present in the confirmed prefix.
        """
        _string(attempt_id,700,'invalid_attempt_id')
        _string(confirmed_text,65536,'invalid_confirmed_text',empty=True)
        if not confirmed_text:return False
        current=self.evaluation(evaluation_id)
        if not current:return False
        row=self.db.execute("SELECT content,kind,sources FROM autonomy_pocket WHERE id=? AND session=? AND selected_eval=? AND selected_revision=revision AND state IN ('unshared','researching','ready') AND expires>?",
            (finding_id,current['session'],evaluation_id,self.clock())).fetchone()
        if type(whole_plan_confirmed) is not bool:
            raise ValueError('invalid_delivery_confirmation')
        if not row or (not whole_plan_confirmed and row[0] not in confirmed_text):return False
        if row[1]=='external_fact' and not json.loads(row[2]):return False
        self._idle_connection()
        with self.db:
            return bool(self.db.execute("UPDATE autonomy_pocket SET state='shared',shared_attempt=?,confirmed_sha256=?,updated=? WHERE id=? AND selected_eval=? AND selected_revision=revision",
                (attempt_id,hashlib.sha256(confirmed_text.encode()).hexdigest(),self.clock(),finding_id,evaluation_id)).rowcount)

    def discard_finding(self,finding_id,*,session):
        self._idle_connection()
        with self.db:
            return bool(self.db.execute("UPDATE autonomy_pocket SET state='discarded',selected_eval='',selected_revision=NULL,updated=? WHERE id=? AND session=? AND state IN ('unshared','researching','ready')",
                (self.clock(),finding_id,session)).rowcount)

    def prune_pocket(self):
        """Expire research without reviving it; retain a bounded terminal tail."""
        self._idle_connection()
        now=self.clock()
        with self.db:
            # Bound legacy rows too, without replacing their source timestamps.
            self.db.execute("UPDATE autonomy_pocket SET expires=min(expires,created+?,last_progress+?) WHERE state IN ('unshared','researching','ready')",(FINDING_MAX_SECONDS,FINDING_IDLE_SECONDS))
            self.db.execute("UPDATE autonomy_pocket SET state='expired',selected_eval='',selected_revision=NULL,updated=? WHERE expires<=? AND state IN ('unshared','researching','ready')",(now,now))
            self.db.execute("DELETE FROM autonomy_pocket WHERE state IN ('expired','discarded','shared') AND updated<?",(now-self.retention_days*86400,))
            # New content can always replace old terminal research, never an
            # active unshared item. The active cap remains fail-closed.
            self.db.execute("DELETE FROM autonomy_pocket WHERE id IN (SELECT id FROM autonomy_pocket WHERE state IN ('expired','discarded','shared') ORDER BY updated DESC,id LIMIT -1 OFFSET ?)",(self.pocket_limit*3,))

    def supplement_eligibility(self, exchange_id, *, parent_attempt, parent_kind, attempt_id):
        for value in (exchange_id,parent_attempt,attempt_id):_string(value,700,'invalid_supplement_identity')
        if parent_kind not in {'passive','proactive','interest'}:
            return {'allowed':False,'reason':'supplement_cannot_parent_supplement'}
        if attempt_id==parent_attempt:
            raise ValueError('supplement_requires_independent_attempt')
        if self.db.execute('SELECT 1 FROM autonomy_supplements WHERE attempt_id=?',(parent_attempt,)).fetchone():
            return {'allowed':False,'reason':'supplement_cannot_parent_supplement'}
        if self.db.execute('SELECT 1 FROM autonomy_supplements WHERE exchange_id=? OR attempt_id=? OR parent_attempt=?',
                           (exchange_id,attempt_id,parent_attempt)).fetchone():
            return {'allowed':False,'reason':'supplement_already_attempted'}
        return {'allowed':True,'reason':'allowed','attempt_id':attempt_id}

    def reserve_supplement(self, exchange_id, *, parent_attempt, parent_kind, attempt_id):
        self._idle_connection()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            eligible=self.supplement_eligibility(exchange_id,parent_attempt=parent_attempt,
                parent_kind=parent_kind,attempt_id=attempt_id)
            if not eligible['allowed']:
                self.db.rollback();return eligible
            self.db.execute('INSERT INTO autonomy_supplements VALUES (?,?,?,?,?,?)',
                (exchange_id,parent_attempt,parent_kind,attempt_id,self.clock(),'reserved'))
            self.db.commit();return eligible
        except BaseException:
            self.db.rollback();raise

    def finish_supplement(self,attempt_id,state):
        if state not in {'sent','failed','unknown','cancelled'}:
            raise ValueError('invalid_supplement_state')
        self._idle_connection()
        with self.db:
            return bool(self.db.execute("UPDATE autonomy_supplements SET state=? WHERE attempt_id=? AND state='reserved'",(state,attempt_id)).rowcount)

    def write_audit(self,evaluation_id,kind,payload):
        if kind not in {'request','output','decision','delivery','tool'}:
            raise ValueError('invalid_audit_kind')
        if not self.evaluation(evaluation_id):
            raise ValueError('audit_evaluation_missing')
        if self.audit_dir is None:
            return {'complete':False,'reason':'audit_directory_unconfigured'}
        self.prune_audits()
        body=_json(_scrub(payload),16*1024*1024).encode()
        digest=hashlib.sha256(body).hexdigest();key=str(uuid.uuid4())
        if len(body)>self.audit_entry_bytes:
            self._idle_connection()
            with self.db:self.db.execute('INSERT INTO autonomy_audits VALUES (?,?,?,?,?,?,?,?,?)',
                (key,evaluation_id,kind,self.clock(),'',digest,len(body),0,'entry_capacity'))
            return {'id':key,'sha256':digest,'complete':False,'reason':'audit_entry_capacity'}
        self.prune_audits(required_bytes=len(body))
        name=key+'.json';path=self.audit_dir/name
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
        try:
            with os.fdopen(fd,'wb') as stream:
                stream.write(body);stream.flush();os.fsync(stream.fileno())
            self._idle_connection()
            with self.db:self.db.execute('INSERT INTO autonomy_audits VALUES (?,?,?,?,?,?,?,?,?)',
                (key,evaluation_id,kind,self.clock(),name,digest,len(body),1,'retained'))
        except BaseException:
            path.unlink(missing_ok=True);raise
        return {'id':key,'sha256':digest,'complete':True,'reason':'retained','ref':name}

    def prune_audits(self,required_bytes=0):
        if self.audit_dir is None:return
        self._idle_connection()
        rows=self.db.execute("SELECT id,path,size,created FROM autonomy_audits WHERE state='retained' ORDER BY created,id").fetchall()
        total=sum(r[2] for r in rows);deadline=self.clock()-self.retention_days*86400
        for key,name,size,created in rows:
            if created>=deadline and total+required_bytes<=self.audit_capacity_bytes:continue
            # Only paths created by this class are eligible; never unlink a
            # caller-provided path or follow a symbolic link outside the store.
            if name!=key+'.json' or '/' in name or '\\' in name:
                raise ValueError('invalid_private_audit_reference')
            path=self.audit_dir/name
            if path.is_symlink():raise ValueError('private_audit_symlink')
            path.unlink(missing_ok=True)
            with self.db:self.db.execute("UPDATE autonomy_audits SET state=?,path='',complete=0 WHERE id=?",
                ('retention_expired' if created<deadline else 'capacity_evicted',key))
            total-=size

    def summary(self,day=None):
        """Operational counts only. Never expose request, output or research text."""
        day=day or self._day()
        rows=self.db.execute('SELECT kind,state,count(*) FROM autonomy_calls WHERE day=? GROUP BY kind,state',(day,)).fetchall()
        calls={}
        for kind,state,count in rows:calls.setdefault(kind,{})[state]=count
        usage={}
        for (raw,) in self.db.execute('SELECT usage FROM autonomy_calls WHERE day=?',(day,)):
            for key,value in json.loads(raw).items():usage[key]=usage.get(key,0)+value
        return {'day':day,'calls':calls,'usage':usage,
                'limits':{'model_requests':self.model_limit,'tool_attempts':self.tool_limit,
                          'tools_per_opportunity':self.tools_per_opportunity,
                          'calculations_per_opportunity':4},
                'pending_findings':self.db.execute("SELECT count(*) FROM autonomy_pocket WHERE state IN ('unshared','researching','ready') AND expires>?",(self.clock(),)).fetchone()[0]}
