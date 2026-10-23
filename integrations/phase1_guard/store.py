"""Small durable deduplication and delivery observation store for Phase 1.

This is deliberately not a retry queue. A claimed message stays claimed after
an interrupted run, because repeating an unacknowledged send can duplicate it.
"""

from datetime import datetime
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo


def now_shanghai():
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="milliseconds")


class GuardStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS inbound (
                platform TEXT NOT NULL,
                session TEXT NOT NULL,
                message_id TEXT NOT NULL,
                sender_id TEXT NOT NULL,
                text TEXT NOT NULL,
                chain_json TEXT NOT NULL,
                received_at TEXT NOT NULL,
                duplicate_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(platform, session, message_id)
            );
            CREATE TABLE IF NOT EXISTS outbound (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                session TEXT NOT NULL,
                message_id TEXT NOT NULL,
                text TEXT NOT NULL,
                chain_json TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('pending','sent','failed','unknown')),
                error_type TEXT,
                created_at TEXT NOT NULL,
                finished_at TEXT,
                FOREIGN KEY(platform, session, message_id)
                    REFERENCES inbound(platform, session, message_id)
            );
        """)

    def recover_interrupted(self):
        """Mark unconfirmed attempts unknown once, during plugin startup."""
        with self.db:
            count = self.db.execute(
                "UPDATE outbound SET state='unknown', error_type='Interrupted', "
                "finished_at=? WHERE state='pending'", (now_shanghai(),)
            ).rowcount
        return count

    def claim(self, platform, session, message_id, sender_id, text, chain):
        """Atomically claim an inbound identity before downstream handlers."""
        if not all(isinstance(value, str) and value for value in (platform, session, message_id)):
            raise ValueError("A nonempty platform, session and message_id are required")
        with self.db:
            inserted = self.db.execute(
                "INSERT OR IGNORE INTO inbound "
                "(platform,session,message_id,sender_id,text,chain_json,received_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (platform, session, message_id, str(sender_id), text,
                 json.dumps(chain, ensure_ascii=False), now_shanghai()),
            ).rowcount
            if not inserted:
                self.db.execute(
                    "UPDATE inbound SET duplicate_count=duplicate_count+1 "
                    "WHERE platform=? AND session=? AND message_id=?",
                    (platform, session, message_id),
                )
        return bool(inserted)

    def begin_outbound(self, key, text, chain):
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO outbound "
                "(platform,session,message_id,text,chain_json,state,created_at) "
                "VALUES (?,?,?,?,?,'pending',?)",
                (*key, text, json.dumps(chain, ensure_ascii=False), now_shanghai()),
            )
        return cursor.lastrowid

    def finish(self, attempt_id, state, error_type=None):
        if state not in {"sent", "failed", "unknown"}:
            raise ValueError("Delivery result must be sent, failed or unknown")
        with self.db:
            changed = self.db.execute(
                "UPDATE outbound SET state=?, error_type=?, finished_at=? "
                "WHERE id=? AND state='pending'",
                (state, error_type, now_shanghai(), attempt_id),
            ).rowcount
            if changed != 1:
                raise ValueError("Only an existing pending delivery can be finalized")

    def snapshot(self):
        return {
            "inbound": [dict(row) for row in self.db.execute(
                "SELECT * FROM inbound ORDER BY platform,session,message_id")],
            "outbound": [dict(row) for row in self.db.execute(
                "SELECT * FROM outbound ORDER BY id")],
        }

    def enable_phase3(self):
        """Additive, opt-in migration. Legacy tables/queries remain unchanged.

        A consistent pre-migration SQLite backup is made once; rollback deploys
        the old Guard (which ignores these tables) without deleting new data.
        """
        exists = self.db.execute("SELECT 1 FROM sqlite_master WHERE name='guard_schema'").fetchone()
        if exists:
            version = self.db.execute("SELECT version FROM guard_schema").fetchone()
            if not version or version[0] != 3:
                raise ValueError("UnsupportedGuardSchema")
            return
        backup = self.path.with_name(self.path.name + ".pre-phase3.sqlite3")
        if backup.exists():
            raise ValueError("MigrationBackupAlreadyExists")
        import os
        fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        destination = sqlite3.connect(backup)
        try:
            self.db.backup(destination)
            if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("MigrationBackupIntegrity")
        finally:
            destination.close()
        try:
            self.db.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE guard_schema (version INTEGER NOT NULL);
                INSERT INTO guard_schema VALUES (3);
                CREATE TABLE turns (
                    turn_id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL, session TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN
                        ('collecting','ready','generating','done','superseded','cancelled','interrupted')),
                    proactive INTEGER NOT NULL DEFAULT 0,
                    predecessor TEXT REFERENCES turns(turn_id),
                    created_at TEXT NOT NULL, finished_at TEXT
                );
                CREATE TABLE turn_sources (
                    turn_id TEXT NOT NULL REFERENCES turns(turn_id),
                    ordinal INTEGER NOT NULL,
                    platform TEXT NOT NULL, session TEXT NOT NULL, message_id TEXT NOT NULL,
                    PRIMARY KEY(turn_id,platform,session,message_id),
                    UNIQUE(turn_id,ordinal),
                    FOREIGN KEY(platform,session,message_id) REFERENCES inbound(platform,session,message_id)
                );
                CREATE TABLE deliveries (
                    delivery_id TEXT PRIMARY KEY,
                    turn_id TEXT NOT NULL REFERENCES turns(turn_id),
                    kind TEXT NOT NULL CHECK(kind IN ('normal','progress','final','proactive')),
                    part_index INTEGER NOT NULL,
                    text TEXT NOT NULL, chain_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('pending','sent','failed','unknown')),
                    error_type TEXT, platform_message_id TEXT, receipt_source TEXT,
                    created_at TEXT NOT NULL, finished_at TEXT,
                    UNIQUE(turn_id,kind,part_index)
                );
                CREATE TABLE delivery_observed (delivery_id TEXT PRIMARY KEY REFERENCES deliveries(delivery_id));
                CREATE TABLE progress_claims (
                    turn_id TEXT PRIMARY KEY REFERENCES turns(turn_id), claimed_at TEXT NOT NULL
                );
                COMMIT;
            """)
        except BaseException:
            if self.db.in_transaction:
                self.db.rollback()
            raise

    def enable_turn_diagnostics(self):
        columns = {r[1] for r in self.db.execute('PRAGMA table_info(turns)')}
        if 'diagnostics_json' not in columns:
            with self.db:
                self.db.execute("ALTER TABLE turns ADD COLUMN diagnostics_json TEXT NOT NULL DEFAULT '{}'")

    def record_turn_stage(self, turn_id, stage, values):
        """Optional bounded metadata on the existing turn; never message text."""
        allowed = {'inbound', 'grouped', 'model_request', 'profile_recall',
                   'model_final', 'cleaned_final', 'recovery', 'history', 'media'}
        if stage not in allowed:
            raise ValueError('invalid_diagnostic_stage')
        raw = json.dumps(values, ensure_ascii=False)
        if len(raw) > 16000:
            raise ValueError('diagnostic_size_limit')
        with self.db:
            row = self.db.execute('SELECT diagnostics_json FROM turns WHERE turn_id=?', (turn_id,)).fetchone()
            if not row:
                return
            payload = json.loads(row[0])
            payload[stage] = {**payload.get(stage, {}), 'at':now_shanghai(), **values}
            self.db.execute('UPDATE turns SET diagnostics_json=? WHERE turn_id=?',
                            (json.dumps(payload, ensure_ascii=False), turn_id))

    def recover_phase3_interrupted(self):
        with self.db:
            count = self.db.execute(
                "UPDATE deliveries SET state='unknown',error_type='Interrupted',finished_at=? "
                "WHERE state='pending'", (now_shanghai(),)).rowcount
            self.db.execute("UPDATE turns SET state='interrupted',finished_at=? "
                            "WHERE state IN ('collecting','ready','generating')", (now_shanghai(),))
        return count

    def enable_bubbles(self):
        """Add only accounting tables; existing chats/delivery schema remain intact."""
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='bubble_plans'").fetchone():
            return
        import os
        backup=self.path.with_name(self.path.name+'.pre-multibubble.sqlite3')
        fd=os.open(backup,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
        with sqlite3.connect(backup) as target:
            self.db.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise ValueError('BubbleMigrationBackupIntegrity')
        try:
            self.db.executescript('''BEGIN IMMEDIATE;
                CREATE TABLE bubble_plans (
                    turn_id TEXT NOT NULL REFERENCES turns(turn_id), kind TEXT NOT NULL,
                    generation_sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(turn_id,kind));
                CREATE TABLE bubble_parts (
                    turn_id TEXT NOT NULL, kind TEXT NOT NULL, part_index INTEGER NOT NULL,
                    delivery_id TEXT NOT NULL UNIQUE, content_sha256 TEXT NOT NULL,
                    chars INTEGER NOT NULL, state TEXT NOT NULL
                        CHECK(state IN ('planned','sent','failed','unknown','cancelled')),
                    transport_attempts INTEGER NOT NULL DEFAULT 0 CHECK(transport_attempts IN (0,1)),
                    started_at TEXT, finished_at TEXT, reason TEXT,
                    PRIMARY KEY(turn_id,kind,part_index),
                    FOREIGN KEY(turn_id,kind) REFERENCES bubble_plans(turn_id,kind));
                COMMIT;''')
        except BaseException:
            if self.db.in_transaction:self.db.rollback()
            raise

    def plan_bubbles(self,turn_id,kind,texts,ids,generation_hash):
        from .bubbles import sha
        with self.db:
            exists=self.db.execute('SELECT generation_sha256 FROM bubble_plans WHERE turn_id=? AND kind=?',(turn_id,kind)).fetchone()
            expected=[(i,ids[i],sha(text),len(text)) for i,text in enumerate(texts)]
            if exists:
                old=[tuple(r) for r in self.db.execute('SELECT part_index,delivery_id,content_sha256,chars FROM bubble_parts WHERE turn_id=? AND kind=? ORDER BY part_index',(turn_id,kind))]
                if exists[0]!=generation_hash or old!=expected:raise ValueError('BubblePlanIdentityCollision')
                return False
            self.db.execute('INSERT INTO bubble_plans VALUES (?,?,?,?)',(turn_id,kind,generation_hash,now_shanghai()))
            self.db.executemany('INSERT INTO bubble_parts(turn_id,kind,part_index,delivery_id,content_sha256,chars,state) VALUES (?,?,?,?,?,?,\'planned\')',[(turn_id,kind,*row) for row in expected])
        return True

    def mark_bubble_transport(self,delivery_id):
        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE name='bubble_parts'").fetchone():return
        with self.db:
            self.db.execute('UPDATE bubble_parts SET transport_attempts=1,started_at=? WHERE delivery_id=? AND state=\'planned\'',(now_shanghai(),delivery_id))

    def finish_bubble(self,turn_id,kind,index,state):
        if state not in {'sent','failed','unknown'}:raise ValueError('InvalidBubbleResult')
        with self.db:self.db.execute('UPDATE bubble_parts SET state=?,finished_at=? WHERE turn_id=? AND kind=? AND part_index=? AND state=\'planned\'',(state,now_shanghai(),turn_id,kind,index))

    def cancel_remaining_bubbles(self,turn_id,kind,reason):
        rows=self.db.execute("SELECT * FROM bubble_parts WHERE turn_id=? AND kind=? AND state='planned'",(turn_id,kind)).fetchall()
        with self.db:
            for row in rows:
                actual=self.get_delivery(row['delivery_id'])
                state=(actual['state'] if actual and actual['state'] in {'sent','failed','unknown'} else
                       'unknown' if actual or row['transport_attempts'] else 'cancelled')
                self.db.execute('UPDATE bubble_parts SET state=?,finished_at=?,reason=? WHERE delivery_id=?',(state,now_shanghai(),reason,row['delivery_id']))

    def recover_bubbles(self):
        for row in self.db.execute("SELECT DISTINCT turn_id,kind FROM bubble_parts WHERE state='planned'").fetchall():
            self.cancel_remaining_bubbles(row[0],row[1],'process_interrupted_no_replay')

    def bubble_result(self,turn_id,kind):
        rows=[dict(r) for r in self.db.execute('SELECT * FROM bubble_parts WHERE turn_id=? AND kind=? ORDER BY part_index',(turn_id,kind))]
        states=[r['state'] for r in rows]
        state=('sent' if states and all(v=='sent' for v in states) else 'unknown' if any(v in {'unknown','planned'} for v in states) else 'failed' if 'failed' in states else 'cancelled')
        delivered=[]
        for row in rows:
            actual=self.get_delivery(row['delivery_id'])
            if actual and actual['state']=='sent':delivered.append(actual['text'])
        return {'state':state,'delivery_id':rows[0]['delivery_id'] if rows else None,
                'sent_count':len(delivered),'part_count':len(rows),
                'delivered_text':'\n'.join(delivered),'parts':[{k:r[k] for k in ('part_index','delivery_id','state','transport_attempts','finished_at')} for r in rows]}

    def create_turn(self, turn_id, platform, session, source_ids=(), *, proactive=False, predecessor=None):
        if not all(isinstance(x, str) and x for x in (turn_id, platform, session)):
            raise ValueError("InvalidTurnIdentity")
        if proactive and source_ids:
            raise ValueError("ProactiveMustNotForgeInbound")
        if not proactive and not source_ids:
            raise ValueError("ReactiveTurnNeedsSource")
        with self.db:
            self.db.execute("INSERT INTO turns(turn_id,platform,session,state,proactive,predecessor,created_at,finished_at) VALUES (?,?,?,?,?,?,?,NULL)",
                            (turn_id, platform, session, "ready" if proactive else "collecting",
                             int(proactive), predecessor, now_shanghai()))
            for ordinal, message_id in enumerate(source_ids):
                self.db.execute("INSERT INTO turn_sources VALUES (?,?,?,?,?)",
                                (turn_id, ordinal, platform, session, message_id))

    def attach_source(self, turn_id, key):
        turn = self.get_turn(turn_id)
        if not turn or (turn['platform'], turn['session']) != key[:2] or turn['proactive']:
            raise ValueError("CrossSessionSource")
        with self.db:
            ordinal = self.db.execute("SELECT count(*) FROM turn_sources WHERE turn_id=?", (turn_id,)).fetchone()[0]
            self.db.execute("INSERT INTO turn_sources VALUES (?,?,?,?,?)", (turn_id, ordinal, *key))

    def get_turn(self, turn_id):
        row = self.db.execute("SELECT * FROM turns WHERE turn_id=?", (turn_id,)).fetchone()
        return dict(row) if row else None

    def source_ids(self, turn_id):
        return [r[0] for r in self.db.execute(
            "SELECT message_id FROM turn_sources WHERE turn_id=? ORDER BY ordinal", (turn_id,))]

    def set_turn_state(self, turn_id, state):
        if state not in {'collecting','ready','generating','done','superseded','cancelled','interrupted'}:
            raise ValueError("InvalidTurnState")
        with self.db:
            changed = self.db.execute("UPDATE turns SET state=?,finished_at=? WHERE turn_id=?",
                                     (state, now_shanghai() if state in {'done','superseded','cancelled','interrupted'} else None,
                                      turn_id)).rowcount
            if changed != 1:
                raise ValueError("UnknownTurn")

    def claim_progress(self, turn_id):
        with self.db:
            row = self.get_turn(turn_id)
            if not row or row['state'] in {'done','superseded','cancelled','interrupted'}:
                return False
            return bool(self.db.execute("INSERT OR IGNORE INTO progress_claims VALUES (?,?)",
                                        (turn_id, now_shanghai())).rowcount)

    def begin_delivery(self, delivery_id, turn_id, kind, part_index, text, chain):
        serialized = json.dumps(chain, ensure_ascii=False)
        with self.db:
            created = bool(self.db.execute(
                "INSERT OR IGNORE INTO deliveries "
                "(delivery_id,turn_id,kind,part_index,text,chain_json,state,created_at) "
                "VALUES (?,?,?,?,?,?,'pending',?)",
                (delivery_id,turn_id,kind,part_index,text,serialized,now_shanghai())).rowcount)
            row = self.get_delivery(delivery_id)
            if not row or (row['turn_id'], row['kind'], row['part_index'], row['text'], row['chain_json']) != (
                    turn_id, kind, part_index, text, serialized):
                raise ValueError("DeliveryIdentityCollision")
        return row, created

    def get_delivery(self, delivery_id):
        row = self.db.execute("SELECT * FROM deliveries WHERE delivery_id=?", (delivery_id,)).fetchone()
        return dict(row) if row else None

    def finish_delivery(self, delivery_id, state, error_type=None, *, platform_message_id=None, receipt_source=None):
        if state not in {'sent','failed','unknown'}:
            raise ValueError("InvalidDeliveryState")
        if state == 'sent' and not receipt_source:
            raise ValueError("ConfirmedReceiptSourceRequired")
        with self.db:
            count = self.db.execute(
                "UPDATE deliveries SET state=?,error_type=?,platform_message_id=?,receipt_source=?,finished_at=? "
                "WHERE delivery_id=? AND state='pending'",
                (state,error_type,platform_message_id,receipt_source,now_shanghai(),delivery_id)).rowcount
            if count != 1:
                raise ValueError("OnlyPendingDeliveryCanFinish")

    def phase3_snapshot(self):
        return {table: [dict(r) for r in self.db.execute(f"SELECT * FROM {table}")]
                for table in ('turns','turn_sources','deliveries','progress_claims')}

    def close(self):
        self.db.close()
