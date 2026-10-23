#!/usr/bin/env python3
"""Create a private static side-by-side review; no requests, model, or approval."""
import argparse
from html import escape
import importlib.util
import json
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('private_io',ROOT/'scripts/phase2-configure.py')
io=importlib.util.module_from_spec(spec);spec.loader.exec_module(io)


def render(baseline,current):
    cases=json.loads((ROOT/'config/phase3-persona-scenarios.json').read_text())['cases']
    old={r['scenario']:r for r in baseline['results'] if r['persona_version']=='phase2_template'}
    new={r['scenario']:r for r in current['results'] if r['persona_version']=='phase3_v01'}
    sections=[]
    for case in cases:
        a,b=old.get(case['id'],{}),new.get(case['id'],{})
        sections.append('<details><summary>'+escape(case['id']+' · '+case['category']+' · '+case['user'])+'</summary><div class="pair"><section><h3>Phase 2</h3><pre>'+escape(a.get('output','未执行'))+'</pre></section><section><h3>V0.1 修订后</h3><pre>'+escape(b.get('output','未执行'))+'</pre></section></div></details>')
    return '''<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"><title>人格样本本人审阅</title><style>body{max-width:1200px;margin:32px auto;padding:20px;background:#faf9f6;color:#222;font:16px/1.65 system-ui}summary{padding:12px;cursor:pointer;border-top:1px solid #ddd}.pair{display:grid;grid-template-columns:1fr 1fr;gap:24px}pre{white-space:pre-wrap;font:inherit}section{background:white;padding:20px}@media(max-width:650px){.pair{grid-template-columns:1fr}}</style><h1>真实 DeepSeek：20 组人格样本</h1><p>全部输入为合成场景，没有发送访谈、真实聊天或账号资料。接口成功不等于自然度合格。本页无网络、提交、保存或自动批准能力。</p><p>请按场景编号指出喜欢/不喜欢的回答，重点看：自然、不反复追问、亲近但有主见、不虚构共同过去、不假称工具成功。技术长答新版给了更高输出上限；旧版截断不能用作人格优劣证据。公开任务没有规定量化评分门槛，不擅自替本人打分。</p>'''+''.join(sections)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline',type=Path,required=True);p.add_argument('--current',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.output.resolve().is_relative_to(ROOT.resolve()): raise ValueError('private_review_must_be_outside_repository')
    baseline=json.loads(io.read_private(a.baseline));current=json.loads(io.read_private(a.current))
    io.write_private(a.output,render(baseline,current));print('PRIVATE_REVIEW_CREATED; HUMAN_RATING=NOT_RUN')
