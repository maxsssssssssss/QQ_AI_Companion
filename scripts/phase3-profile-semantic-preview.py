#!/usr/bin/env python3
"""Local topic-based interview condensation; no model/provider/network calls.

Answers are grouped by meaning-bearing sections, not emitted one row per line.
Conservative exact/near duplicate consolidation preserves all source refs.
Every resulting statement still needs the owner's review and separate consent.
"""
import argparse
from collections import Counter,defaultdict
from difflib import SequenceMatcher
from html import escape
import hashlib
import json
import os
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
TOPICS=[('education',('学校','教育','学业','学习')),
        ('work',('实习','工作','求职','职业')),
        ('interests',('兴趣','爱好','游戏','音乐','动漫','阅读')),
        ('daily_preferences',('习惯','日常','生活方式','饮食')),
        ('values',('价值观','信念','原则'))]
LABELS={'address':'称呼候选','communication':'稳定交流偏好候选','boundaries':'交流边界候选',
 'identity':'基础背景','self_description':'自我描述（非诊断）','education':'教育与学习',
 'work':'工作与发展','interests':'长期兴趣','daily_preferences':'生活偏好','values':'价值观',
 'third_party_relationships':'家庭和朋友（他人经历）','host_family_history':'过往寄宿家庭经历（非 AI 共同经历）',
 'games':'游戏兴趣与体验','culture_interests':'阅读、音乐及其他兴趣','emotion_response':'情绪表现与回应偏好',
 'interaction_preferences':'交流参与偏好','proactive_preferences':'主动交互偏好（不等于启用授权）',
 'wishes':'愿望（非既定计划）',
 'background':'其他背景','temporary':'近期状态（待核实时效）','excluded':'待审或不导入'}


def normalize(text):
    return re.sub(r'[\s，。；：、,.!?！？:;\-*_`|]+','',text).casefold()


def deduplicate(parts):
    unique=[]
    for text,refs in parts:
        key=normalize(text)
        match=None
        for existing in unique:
            same_numbers=re.findall(r'\d+',existing['text'])==re.findall(r'\d+',text)
            same_negation=bool(re.search('不|未|没|否',existing['text']))==bool(re.search('不|未|没|否',text))
            if key==existing['key'] or (same_numbers and same_negation and SequenceMatcher(None,key,existing['key']).ratio()>=.94):
                match=existing;break
        if match: match['refs'].extend(r for r in refs if r not in match['refs'])
        else: unique.append({'text':text,'refs':list(refs),'key':key})
    return unique


def classify(section,sub,heading):
    if section in {'十','十四','十五','十六','十七','十八'}: return 'EXCLUDED','excluded'
    if section=='十三' or sub in {'1.4','3.4','4.2','5.1','7.7'}:return 'L3','temporary'
    if sub=='1.2':return 'L1','address'
    if sub in {'9.1','9.2'}:return 'L1','communication'
    if sub in {'9.4','9.5'}:return 'L1','boundaries'
    if sub=='1.3':return 'L2','education'
    if section=='一':return 'L2','identity'
    if section=='二':return 'L2','self_description'
    if section=='三':return 'L2','games'
    if section=='四':return 'L2','culture_interests'
    if section=='五':return 'L2','daily_preferences'
    if section=='六':return 'L2','third_party_relationships'
    if section=='七':return 'L2','host_family_history'
    if section=='八':return 'L2','emotion_response'
    if section=='九':return 'L2','interaction_preferences'
    if section=='十一':return 'L2','proactive_preferences'
    if section=='十二':return 'L2','wishes'
    for topic,words in TOPICS:
        if any(word in heading for word in words):return 'L2',topic
    return 'L2','background'


def preview(source):
    doc=hashlib.sha256(source.encode()).hexdigest()
    blocks=[];section=sub=heading='';body=[]
    def flush():
        if body and section: blocks.append((section,sub,heading,list(body)))
        body.clear()
    for number,line in enumerate(source.splitlines(),1):
        main=re.match(r'^## ([一二三四五六七八九十]+)、(.*)',line)
        child=re.match(r'^### (\d+\.\d+)\s*(.*)',line)
        if main:
            flush();section,heading=main.groups();sub='';continue
        if child:
            flush();sub,heading=child.groups();continue
        if line.strip() and not line.startswith(('#','---','>')): body.append((number,line.strip()))
    flush()
    groups=defaultdict(list);source_units=0
    for sec,sub,heading,body in blocks:
        layer,topic=classify(sec,sub,heading)
        # Preserve paragraph/table assertions as one thematic answer block.
        # Do not infer missing answers or convert analyst conclusions to facts.
        sentences=[];refs=[]
        for number,text in body:
            if re.match(r'^\|(?:\s*:?-+:?\s*\|)+$',text):continue
            if text.startswith('|'):
                columns=[s.strip() for s in text.strip('|').split('|')]
                if columns[0] in {'项目','事项','游戏','标识'}:continue
                text='：'.join(columns)
            text=re.sub(r'^[\-•]\s*','',text).strip('* ')
            if not text:continue
            sentences.append(text)
            refs.append(f'sha256:{doc}#section={sub or sec}:line={number}')
            source_units+=1
        atoms=deduplicate([(s,[r]) for s,r in zip(sentences,refs)])
        if not atoms:continue
        text='；'.join(x['text'].rstrip('。；') for x in atoms)
        all_refs=[r for x in atoms for r in x['refs']]
        if layer=='L1' and len(text)>550: layer='L2'
        # Keep EXCLUDED subsections separately reviewable; merge L2 by topic.
        group_key=(layer,topic,(sub or sec) if layer in {'L3','EXCLUDED'} else '')
        groups[group_key].append((text,all_refs))
    entries=[];merged=0
    for (layer,topic,segment),parts in groups.items():
        values=deduplicate(parts);merged+=len(parts)-len(values)
        # Bound native-sized units without line splitting. Each chunk combines
        # complete topical answer blocks and retains every original span.
        chunks=[];texts=[];refs=[]
        for value in values:
            if texts and sum(map(len,texts))+len(value['text'])>2200:
                chunks.append((texts,refs));texts=[];refs=[]
            texts.append(value['text']);refs.extend(value['refs'])
        if texts:chunks.append((texts,refs))
        for index,(texts,refs) in enumerate(chunks):
            text=f'用户自述（{LABELS[topic]}）：'+'。'.join(texts)+'。'
            entries.append({'id':f'{layer.lower()}-{topic}-{segment or "topic"}-{index}'.replace('.','-'),
                'layer':layer,'topic':topic,'text':text,'source_refs':refs,
                'permission_scope':topic,'confidence_status':'pending_user_review','shared_experience':False,
                'temporal_status':'requires_date_and_expiry' if layer=='L3' else 'review_stability',
                'production_write_allowed':False,'model_use_allowed':False,'native_proactive_recall_allowed':False})
    return {'schema':'qq-companion.profile-semantic-preview.v1','source_document_sha256':doc,
        'reviewed':False,'mode':'PREVIEW_ONLY','entries':entries,
        'counts':dict(Counter(e['layer'] for e in entries)),
        'source_units':source_units,'thematic_answer_blocks':len(blocks),'duplicate_answer_blocks_merged':merged,
        'model_calls':0,'production_writes':0,'shared_experiences':0,
        'method':'section_topic_grouping_with_conservative_duplicate_consolidation',
        'limitations':['Owner must verify condensed meanings; no missing facts inferred.','L3 import is disabled.','No consent is inherited from interview answers.']}


def save_private(path,content):
    path=Path(path)
    if path.resolve().is_relative_to(ROOT):raise ValueError('private_preview_outside_repository_required')
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if path.parent.stat().st_mode&0o077:raise ValueError('owner_only_parent_required')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as f:f.write(content)


def render(report):
    body=[]
    for layer in ('L1','L2','L3','EXCLUDED'):
        rows=[]
        for e in report['entries']:
            if e['layer']==layer:
                rows.append('<article><h3>'+escape(e['id'])+' · '+escape(LABELS[e['topic']])+'</h3><p>'+escape(e['text'])+'</p><details><summary>原文来源与待审范围</summary><pre>'+escape('\n'.join(e['source_refs']))+'</pre></details></article>')
        body.append('<section><h2>'+layer+' · '+str(report['counts'].get(layer,0))+'</h2>'+''.join(rows)+'</section>')
    return '<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'"><title>主题合并后的私有资料预览</title><style>body{font:16px/1.7 system-ui;max-width:1000px;margin:30px auto;padding:20px}article{padding:16px;border:1px solid #ddd;margin:12px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}p{white-space:pre-wrap}</style><h1>主题合并后的私有资料预览</h1><p>所有条目仍待本人审核；未写入生产、未交给模型。本页无联网或提交功能。L1 保持精简；L2 合并同主题完整回答及保守去重；L3/EXCLUDED 单列不导入。自述不是诊断，愿望不是承诺，与他人的经历不是与 AI 的共同经历。</p>'+''.join(body)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();report=preview(args.source.read_text(encoding='utf-8-sig'))
    save_private(args.output,json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    save_private(args.output.with_suffix('.html'),render(report))
    print(json.dumps({k:report[k] for k in ('mode','counts','source_units','thematic_answer_blocks','duplicate_answer_blocks_merged','model_calls','production_writes')},ensure_ascii=False))
