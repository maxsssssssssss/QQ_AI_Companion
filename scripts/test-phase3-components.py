#!/usr/bin/env python3
"""Run each native test module in its own synthetic AstrBot root and save counts.

Use the pinned Phase3 virtualenv. This runner never contacts QQ or real models.
Local HTTP peers in native tests may require the normal sandbox approval.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
PATTERNS=['test_phase3_runtime.py','test_phase3_staging.py','test_phase3_delivery.py','test_phase3_turns.py',
 'test_phase3_delivery_native.py','test_phase3_memory_native.py','test_phase3_proactive_native.py',
 'test_phase3_search.py','test_phase3_search_native.py','test_phase3_search_policy_integration.py',
 'test_phase3_search_release_audit.py','test_phase3_memory_summary_provider.py','test_phase3_status.py',
 'test_phase3_native_authorize.py','test_phase3_multibubble.py','test_phase3_profile_release.py',
 'test_phase3_install_overlay.py','test_phase3_qq_audit.py','test_phase3_multibubble_native.py',
 'test_phase3_empty_response_native.py','test_phase3_profile_recall_native.py',
 'test_phase3_profile_evidence_native.py','test_phase3_media_native.py',
 'test_phase3_media_download_native.py','test_phase3_summary_consistency_native.py',
 'test_phase3_ingress_native.py','test_phase3_profile_acl_failure_native.py',
 'test_phase3_experience_release.py','test_phase3_profile_http_fixture.py','test_phase3_profile_render.py',
 'test_phase3_profile_publication_native.py','test_phase3_context_compression.py',
 'test_phase3_context_fallback.py','test_phase3_proactive_continuous.py',
 'test_phase3_proactive_pending.py','test_phase3_bubbles_v3_release.py','test_phase3_search_pending.py',
 'test_phase3_bubbles_history_audit.py']

def child(pattern, output):
    sys.path[:0]=[str(ROOT),str(ROOT/'scripts'),str(ROOT/'.runtime/upstream/AstrBot')]
    suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern=pattern)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    status='PASS' if result.wasSuccessful() and result.testsRun and not result.skipped else 'FAIL'
    payload={'module':pattern,'status':status,'tests':result.testsRun,'failures':len(result.failures),
             'errors':len(result.errors),'skipped':len(result.skipped),'real_qq':False,'real_deepseek':False}
    output.write_text(json.dumps(payload,indent=2)+'\n')
    return status=='PASS'

def run(output, patterns=None):
    output.mkdir(parents=True,exist_ok=False)
    results=[]
    for pattern in patterns or PATTERNS:
        name=Path(pattern).stem
        with tempfile.TemporaryDirectory(prefix='qq-phase3-'+name+'-') as state:
            env=dict(os.environ,ASTRBOT_ROOT=state,ASTRBOT_DISABLE_METRICS='1',PYTHONDONTWRITEBYTECODE='1',
                     PYTHONPATH=str(ROOT/'.runtime/upstream/AstrBot')+':'+str(ROOT))
            log=output/(name+'.log');report=output/(name+'.json')
            with log.open('w') as f:
                try:
                    result=subprocess.run([sys.executable,__file__,'--child',pattern,'--output',str(report)],
                                          env=env,cwd=state,stdout=f,stderr=subprocess.STDOUT,timeout=180)
                    record=json.loads(report.read_text()) if report.exists() else {'module':pattern,'status':'FAIL','error':'missing_result'}
                    if result.returncode: record['status']='FAIL'
                except subprocess.TimeoutExpired:
                    record={'module':pattern,'status':'FAIL','error':'deadline'}
            results.append(record)
            print(json.dumps(record),flush=True)
    summary={'status':'PASS' if all(x['status']=='PASS' for x in results) else 'FAIL','modules':results,
             'tests':sum(x.get('tests',0) for x in results),'real_qq':False,'real_deepseek':False}
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    return summary['status']=='PASS'

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--child',choices=PATTERNS)
    p.add_argument('--only',nargs='+',choices=PATTERNS,help='Rerun only changed or previously blocked modules')
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    raise SystemExit(0 if (child(a.child,a.output) if a.child else run(a.output.resolve(),a.only)) else 1)
