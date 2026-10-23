#!/usr/bin/env python3
"""Owner-only offline HTML review of local interview. No import or networking.

Contains sensitive source text: use only outside the repository. Every item is
pending review; this file neither grants model use nor produces an import file.
"""
import argparse
from html import escape
import importlib.util
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('preview_source_map', ROOT/'scripts/phase3-profile-preview.py')
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


def render(source):
    report = preview.preview(source)
    lines = source.splitlines()
    items = []
    for layer in ('L1','L2','L3','EXCLUDED'):
        rows = []
        for unit in report['units']:
            if unit['layer'] != layer: continue
            rows.append('<tr><td>'+str(unit['source_line'])+'</td><td><pre>'+escape(lines[unit['source_line']-1])+'</pre></td><td>'+escape(unit['confidence'])+'<br>'+escape(unit['meaning'])+'<br>'+escape(unit['sensitivity'])+'</td></tr>')
        items.append('<details><summary>'+layer+' — '+str(report['counts'].get(layer,0))+' 个原文审阅单元</summary><table><tr><th>原始行号</th><th>原文（待逐条确认与精简）</th><th>候选分类</th></tr>'+''.join(rows)+'</table></details>')
    return '''<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"><title>个人资料：本地待审预览</title><style>body{font:16px/1.6 system-ui;max-width:1200px;margin:32px auto;padding:20px;color:#222;background:#faf9f6}summary{font-size:20px;padding:14px;cursor:pointer}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:10px;text-align:left;vertical-align:top}pre{white-space:pre-wrap;font:inherit;margin:0}td:last-child{font-size:12px;max-width:220px}code{word-break:break-all}</style><h1>个人资料：本地待审预览</h1><p>未导入生产，未发送给 DeepSeek，未用于公开搜索。本页没有联网、提交或保存操作。请通过原始行号指出需删除、修改、缩减或同意使用的条目；“确认内容”和“允许模型使用”应分别确认。</p><p>L1：少量稳定事实候选，尚未精简；L2：按需检索背景；L3：带时间的状态，需核对当前有效性；EXCLUDED：延期、未回答、推断及设计条目，不导入。下列统计是原文行级审阅单元，不是已确认的记忆事实。所有过去的外部经历均不标为与机器人共同经历。</p><p>来源SHA-256：<code>'''+report['source_sha256']+'</code></p>'+''.join(items)


def write(source, output):
    if output.resolve().is_relative_to(ROOT.resolve()): raise ValueError('private_review_must_be_outside_repository')
    output.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as f: f.write(render(source))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('source',type=Path)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();write(a.source.read_text(encoding='utf-8-sig'),a.output)
    print('PRIVATE_OFFLINE_PREVIEW_CREATED; model_calls=0; production_writes=0; consent=PENDING')
