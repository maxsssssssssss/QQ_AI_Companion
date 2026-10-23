"""Durable daily attempt ceiling; no credentials, queries or personal content."""
from datetime import datetime,timezone,date
from pathlib import Path
import re
import sqlite3
import uuid
from zoneinfo import ZoneInfo


class DailyBudget:
    def __init__(self, path, *, limit=60, day=None):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("invalid daily search budget")
        self.limit = limit
        self.day = day or (lambda: datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat())
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.path.chmod(0o600)
        self.db.execute("CREATE TABLE IF NOT EXISTS web_daily_attempts(day TEXT PRIMARY KEY, attempts INTEGER NOT NULL CHECK(attempts>=0))")
        self.db.execute('CREATE TABLE IF NOT EXISTS web_adopted_attempts(source TEXT PRIMARY KEY,day TEXT,count INTEGER)')
        self.db.execute('''CREATE TABLE IF NOT EXISTS web_tool_attempts(
            id TEXT PRIMARY KEY,turn_id TEXT,ordinal INTEGER,kind TEXT,
            progress_delivery_id TEXT,progress_state TEXT,started_at TEXT,finished_at TEXT,state TEXT)''')
        self.db.execute("UPDATE web_tool_attempts SET state='unknown_interrupted',finished_at=? WHERE state='running'",(datetime.now(timezone.utc).isoformat(),))
        self.db.commit()

    def reserve(self):
        today = self.day()
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO web_daily_attempts VALUES (?,0)",(today,))
            return bool(self.db.execute("UPDATE web_daily_attempts SET attempts=attempts+1 WHERE day=? AND attempts<?",(today,self.limit)).rowcount)

    def adopt(self,source,day,count):
        """Count previously authorized same-day probes exactly once; no reset."""
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',source) or date.fromisoformat(day).isoformat()!=day or type(count) is not int or not 1<=count<=60:
            raise ValueError('invalid_prior_attempts')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute('SELECT day,count FROM web_adopted_attempts WHERE source=?',(source,)).fetchone()
            if row:
                if tuple(row)!=(day,count):raise ValueError('prior_attempt_identity_collision')
                self.db.commit();return False
            self.db.execute('INSERT INTO web_adopted_attempts VALUES (?,?,?)',(source,day,count))
            self.db.execute('INSERT INTO web_daily_attempts VALUES (?,?) ON CONFLICT(day) DO UPDATE SET attempts=attempts+excluded.attempts',(day,count))
            self.db.commit();return True
        except BaseException:self.db.rollback();raise

    def tool_started(self,turn_id,ordinal,kind,progress_id,progress_state):
        if kind not in {'search','fetch'} or progress_state not in {'sent','failed','unknown','previously_claimed','cancelled_before_send','not_attempted'}:
            raise ValueError('invalid_web_audit_state')
        key=str(uuid.uuid4())
        with self.db:self.db.execute('INSERT INTO web_tool_attempts VALUES (?,?,?,?,?,?,?,NULL,?)',
            (key,turn_id,ordinal,kind,progress_id,progress_state,datetime.now(timezone.utc).isoformat(),'running'))
        return key

    def tool_finished(self,key,state):
        if state not in {'returned','failed','timeout','cancelled'}:raise ValueError('invalid_web_audit_state')
        with self.db:self.db.execute("UPDATE web_tool_attempts SET state=?,finished_at=? WHERE id=? AND state='running'",(state,datetime.now(timezone.utc).isoformat(),key))

    def close(self): self.db.close()
