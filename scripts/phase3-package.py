#!/usr/bin/env python3
"""Create a deterministic review bundle of explicit FIRST-PARTY Phase3 files.

No deployment, git write, upstream source, private state or credentials included.
Install only as an overlay on the stated Phase2 base after approval and backup.
"""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile

ROOT=Path(__file__).resolve().parents[1]
BASE='b6dfc82abbf388d93e0012fd91a55965e66fc4fe'


def files():
    selected=set()
    for area in ('integrations/phase1_guard','integrations/phase2_policy','integrations/phase3_runtime',
                 'integrations/phase3_memory','integrations/phase3_search','deploy/phase3'):
        selected.update(p for p in (ROOT/area).rglob('*') if p.is_file() and
                        (p.suffix in {'.py','.json','.md','.yaml','.yml'} or p.name.endswith('Dockerfile')))
    for pattern in ('scripts/phase3-*.py','scripts/test-phase3-*.py','scripts/test-phase3-*.sh',
                    'tests/*phase3*.py','config/phase3-*.json','docs/phase3-*.md'):
        selected.update(ROOT.glob(pattern))
    selected.update(ROOT/p for p in ('README.md','docs/known-issues.md','docs/persona-integration.md',
       'docs/private-profile-import.md','docs/natural-agent-validation.md','persona/phase3-v0.1.md',
       'persona/phase3-v0.2.md'))
    for p in selected:
        if not p.is_file() or p.is_symlink() or '__pycache__' in p.parts:
            raise ValueError('unsafe_or_missing_release_source')
        if p.stat().st_size>1024*1024: raise ValueError('unexpected_source_size')
    return sorted(selected)


def build(output):
    paths=files()
    contents={p.relative_to(ROOT).as_posix():p.read_bytes() for p in paths}
    manifest={'mode':'REVIEW_ONLY_NOT_DEPLOYED','required_base_commit':BASE,
        'includes_upstream_source':False,'includes_credentials':False,'production_migration_executed':False,
        'files':{name:hashlib.sha256(data).hexdigest() for name,data in contents.items()}}
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('xb') as raw, gzip.GzipFile(fileobj=raw,mode='wb',mtime=0,filename='') as gz, tarfile.open(fileobj=gz,mode='w') as tar:
        def add(name,data):
            item=tarfile.TarInfo(name);item.size=len(data);item.mode=0o644;item.mtime=0
            tar.addfile(item,io.BytesIO(data))
        for name,data in contents.items(): add(name,data)
        add('review-manifest.json',(json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode())
    return {'files':len(paths),'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'mode':manifest['mode']}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    print(json.dumps(build(p.parse_args().output)))
