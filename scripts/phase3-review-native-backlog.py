#!/usr/bin/env python3
"""Read-only native backlog consent manifest; never emits message content.

Use a private output path. This creates an unapproved review artifact, not
permission to send old messages to a model. Count/hash changes require review.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from integrations.phase3_memory.bridge import digest, MemoryCoordinator


def export_manifest(database, session_id):
    if ':FriendMessage:' not in session_id or not session_id.split(':')[-1]:
        raise ValueError('private_session_required')
    path=Path(database).resolve(strict=True)
    with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as db:
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        rows=db.execute("""SELECT * FROM timeline
            WHERE session_id=? AND scope='private' AND summarized_at=''
            ORDER BY COALESCE(julianday(NULLIF(occurred_at,'')),julianday(NULLIF(created_at,'')),0),created_at,id
            LIMIT 10001""",(session_id,)).fetchall()
        if len(rows)>10000: raise ValueError('backlog_review_limit')
        records=[{'timeline_id':r['id'],'fingerprint':MemoryCoordinator._backlog_fingerprint(dict(r))} for r in rows]
    manifest={'schema':'phase3.native-backlog.v1','session_id':session_id,'count':len(records),'records':records}
    return {**manifest,'manifest_hash':digest(manifest),'approved':False,'allow_model_use':False}


def save_private(manifest, output):
    path=Path(output)
    if not path.parent.is_dir(): raise ValueError('private_output_parent_required')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as f:
        json.dump(manifest,f,ensure_ascii=False,indent=2)
        f.write('\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True)
    parser.add_argument('--session',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    manifest=export_manifest(args.database,args.session)
    save_private(manifest,args.output)
    print(json.dumps({'status':'REVIEW_REQUIRED','count':manifest['count'],
        'manifest_hash':manifest['manifest_hash'],'approved':False,'content_exported':False}))


if __name__=='__main__': main()
