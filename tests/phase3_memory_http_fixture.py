"""Deterministic HTTP peer for native memory/tool integration; no real model."""
import json
import re
import time
import uuid
from aiohttp import web
from offline_peers import MockLLM, KEY, append_event


def current_profile_evidence(body):
    """Read the last user's single native memory surface, never old answers.

    Fresh approved evidence must share MemoryCompanion's current temporary
    context. A second system surface or stale user/tool block cannot satisfy
    this fixture, even if it contains every expected synthetic fact.
    """
    marker = '[PHASE3_PROFILE_EVIDENCE_V2]'
    opening, closing = '<MemoryCompanion-Context>', '</MemoryCompanion-Context>'

    def text(message):
        content = message.get('content', '')
        if isinstance(content, list):
            content = '\n'.join(p.get('text', '') for p in content
                                if isinstance(p, dict) and p.get('type') == 'text')
        return content if isinstance(content, str) else ''

    messages = body.get('messages', [])
    assert all(marker not in text(m) for m in messages if m.get('role') == 'system'), \
        'profile evidence must not remain in system'
    latest = next((m for m in reversed(messages) if m.get('role') == 'user'), None)
    assert latest is not None, 'current user message absent'
    content = text(latest)
    assert content.count(opening) == content.count(closing) == 1, \
        'exactly one current native memory context required'
    start, end = content.index(opening) + len(opening), content.index(closing)
    assert start <= end, 'malformed current native memory context'
    context = content[start:end]
    assert content.count(marker) == context.count(marker) == 1, \
        'exactly one profile marker inside current native memory context required'
    tail = context.split(marker, 1)[1]
    start = tail.find('{')
    assert start >= 0, 'current profile payload absent'
    value, _ = json.JSONDecoder().raw_decode(tail[start:])
    assert isinstance(value, dict) and isinstance(value.get('evidence'), list)
    return value


class MemoryMockLLM(MockLLM):
    async def complete(self, request):
        if request.headers.get('Authorization') != f'Bearer {KEY}':
            return web.json_response({'error': {'message': 'fixture authentication failed'}}, status=401)
        body = await request.json()
        messages = body.get('messages', [])
        latest_body = next((m.get('content', '') for m in reversed(messages) if m.get('role') == 'user'), '')
        latest = (next((str(p.get('text','')) for p in latest_body if p.get('type')=='text'), '')
                  if isinstance(latest_body, list) else str(latest_body))
        rows = []
        for line in latest.splitlines():
            try: value = json.loads(line)
            except ValueError: continue
            if isinstance(value, dict) and 'event_id' in value and 'event_type' in value:
                rows.append(value)
        if rows:
            facts = [{'fact': r['content'], 'refs': [r['event_id']],
                'evidence': [{'ref': r['event_id'], 'quote': r['content']}]} for r in rows if r['event_type'] == 'user_message']
            payload = {'outcome':'memory', 'summary':' '.join(r['content'] for r in rows),
                'canonical_summary':' '.join(r['content'] for r in rows), 'importance':0.7,
                'summary_refs':[r['event_id'] for r in rows], 'key_facts':facts,
                'assertions':[{'subject':r['speaker_id'], 'predicate':'residence', 'value':m.group(0),
                    'polarity':'positive', 'durability':'stable', 'refs':[r['event_id']]}
                    for r in rows if r['event_type']=='user_message'
                    for m in re.finditer(r'(?:现在)?住在[^。！？]+', r['content'])]}
            return self.response(body, {'role':'assistant', 'content':json.dumps(payload,ensure_ascii=False)}, 'summary')
        if '图像载荷核验' in latest:
            import base64,io
            from PIL import Image
            parts=[p for p in latest_body if p.get('type')=='image_url']
            assert len(parts)==2
            for part in parts:
                url=part['image_url']['url'];assert url.startswith('data:')
                image=Image.open(io.BytesIO(base64.b64decode(url.split(',',1)[1])))
                assert image.getpixel((0,0))[:3]==(0,0,255)
            return self.response(body,{'role':'assistant','content':'两张合成图都是蓝色。'},'image_payload')
        if latest.startswith('含图历史空正文核验'):
            assert any(p.get('type')=='image_url' for m in messages
                if isinstance(m.get('content'),list) for p in m['content'])
            if messages[-1].get('role')=='system':
                return self.response(body,{'role':'assistant','content':'上一轮两张图都是蓝色。'},'image_history_recovery')
            return self.response(body,{'role':'assistant','content':'','reasoning_content':'SYNTHETIC_PRIVATE_REASONING'},'image_history_empty')
        if latest.startswith('隔离多气泡实际装饰链'):
            return self.response(body, {'role':'assistant',
                'content':'**合成问候。**\n<<PRIVATE_COMPANION_SPLIT>>\n合成鼓励。'}, 'semantic_bubbles')
        if latest.startswith('隔离空正文工具回复'):
            if messages[-1].get('role') == 'system':
                assert any(m.get('role')=='tool' for m in messages)
                assert 'tools' not in body
                return self.response(body, {'role':'assistant','content':'合成资料补答成功。'}, 'empty_recovery')
            if messages[-1].get('role') != 'tool':
                return self.tool(body, 'memory_companion_sources', {'action':'search','terms':['清茶'],'limit':2}, 'empty_recovery_tool')
            return self.response(body, {'role':'assistant','content':'','reasoning_content':'SYNTHETIC_PRIVATE_REASONING'}, 'empty_final')
        if latest.startswith('本轮资料正文核验'):
            from phase3_profile_fixture import GAMES
            evidence = current_profile_evidence(body)
            assert evidence['status'] == 'found'
            assert evidence['coverage'] == 'complete_approved_topics'
            records = evidence['evidence']
            assert records and all(r.get('id') and r.get('version') and r.get('text') for r in records)
            actual_body = '\n'.join(r['text'] for r in records)
            games=[g for g in GAMES if g in actual_body]
            assert tuple(games) == GAMES, 'current profile body lacks expected synthetic facts'
            return self.response(body,{'role':'assistant','content':'资料里提到'+ '、'.join(games)+'。'},'profile_prefetch_final')
        if latest.startswith('隔离导入资料核验'):
            if messages[-1].get('role')!='tool':
                assert '[PHASE3_MEMORY_RECALL_V1]' in json.dumps(messages,ensure_ascii=False)
                return self.tool(body,'memory_companion_navigate',{'action':'search','query':'游戏','limit':6},'profile_navigate')
            result=json.loads(messages[-1]['content']);result=result.get('result',result)
            evidence='\n'.join(r.get('content','') for r in result.get('evidence',[]))
            games=[g for g in ('星露谷物语','戴森球计划','传送门2') if g in evidence]
            return self.response(body,{'role':'assistant','content':'资料里提到你玩'+ '、'.join(games)+'。'},'profile_final')
        if 'PHASE3_PROACTIVE_NATIVE_PROBE' in json.dumps(messages, ensure_ascii=False) and not latest.startswith('PHASE3_PROACTIVE_NATIVE_PROBE'):
            serialized=json.dumps(messages,ensure_ascii=False)
            if 'PHASE3_PROACTIVE_DECISION_DEFER' in serialized:
                return self.response(body, {'role':'assistant','content':'[PHASE3_PROACTIVE:defer:60]'}, 'proactive_defer')
            if 'PHASE3_PROACTIVE_DECISION_SKIP' in serialized:
                return self.response(body, {'role':'assistant','content':'[PHASE3_PROACTIVE:skip]'}, 'proactive_skip')
            return self.response(body, {'role':'assistant', 'content':'[PHASE3_PROACTIVE:send]\n想起你说的合成面试了。\n<<PRIVATE_COMPANION_SPLIT>>\n结果出来了吗？'}, 'proactive')
        if latest.startswith('合成事件') and messages[-1].get('role') != 'tool':
            if '完成' in latest:
                args = dict(state='completed', expected_revision=2)
            elif '改期' in latest:
                args = dict(state='awaiting_result', expected_revision=1, due_at='2030-10-23T15:00:00+08:00')
            else:
                args = dict(state='scheduled', expected_revision=0, due_at='2030-10-22T15:00:00+08:00')
            args.update(action='update', event_id='fixture-interview', quote=latest)
            return self.response(body, {'role':'assistant','content':None,'tool_calls':[{
                'id':'call_'+uuid.uuid4().hex[:12], 'type':'function',
                'function':{'name':'phase3_event','arguments':json.dumps(args,ensure_ascii=False)}}]}, 'event_tool')
        if latest.startswith('隔离记忆核验'):
            # Reply only from actual memory injection, not our stored requests.
            text = '\n'.join(re.findall(r'<MemoryCompanion-Context>(.*?)</MemoryCompanion-Context>',
                json.dumps(messages,ensure_ascii=False), flags=re.S))
            if messages[-1].get('role') != 'tool':
                return self.tool(body, 'memory_companion_navigate', {'action':'search', 'query':'住在 清茶', 'cue':'住在', 'limit':5}, 'recall_tool')
            tool_messages = [m for m in messages if m.get('role') == 'tool']
            # Only actual evidence is an oracle input. Echoed query terms and
            # error descriptions cannot prove the requested fact was found.
            for message in tool_messages:
                result = json.loads(message.get('content', '{}'))
                result = result.get('result', result)
                text += '\n'.join(str(row.get('content','')) for row in result.get('evidence', []))
                text += '\n'.join(str(row.get('excerpt','')) for row in result.get('sources', []))
            if len(tool_messages) == 1 and '清茶' not in text:
                return self.tool(body, 'memory_companion_sources', {'action':'search','terms':['清茶'],'limit':5}, 'recall_sources')
            reply = '目前住在杭州，喜欢清茶。' if '杭州' in text and '清茶' in text else '没有相关长期记忆。'
            return self.response(body, {'role':'assistant','content':reply}, 'recall')
        if latest.startswith(('停止使用关于紫藤27', '别再提关于紫藤27')):
            if messages[-1].get('role') != 'tool':
                return self.tool(body, 'phase3_memory_control', {'action':'search','query':'紫藤27'}, 'control_search')
            result = json.loads(messages[-1].get('content','{}'))
            last_user = max(i for i,m in enumerate(messages) if m.get('role')=='user')
            results = [json.loads(m.get('content','{}')) for m in messages[last_user+1:] if m.get('role')=='tool']
            search = next((r for r in results if r.get('sources')), {})
            revoked = {r.get('source_ref') for r in results if r.get('source_state')=='revoked'}
            pending = sorted([r for r in search.get('sources',[]) if r['source_ref'] not in revoked],
                             key=lambda r:r.get('speaker_role')!='user')
            if pending and result.get('ok'):
                source = pending[0]
                return self.tool(body, 'phase3_memory_control', {'action':'no_mention' if latest.startswith('别再提') else 'stop_use','query':'紫藤27','quote':latest,
                    'source_ref':source['source_ref'],'expected_version':source['source_version']}, 'control_withdraw')
            return self.response(body, {'role':'assistant','content':'已停止使用该来源的派生记忆；原始档案保留。' if result.get('ok') else '该来源未成功停用。'}, 'control_final')
        if latest.startswith('普通召回隔离检查'):
            if messages[-1].get('role') != 'tool':
                return self.tool(body, 'memory_companion_sources', {'action':'search','terms':['保密标签'],'limit':5}, 'privacy_recall')
            result = json.loads(messages[-1].get('content','{}'))
            result = result.get('result',result)
            evidence = '\n'.join(r.get('excerpt','') for r in result.get('sources',[]))
            injected = '\n'.join(re.findall(r'<MemoryCompanion-Context>(.*?)</MemoryCompanion-Context>',
                json.dumps(messages,ensure_ascii=False), flags=re.S))
            return self.response(body, {'role':'assistant','content':'隐私资料仍被召回。' if '紫藤27' in evidence+injected else '没有可用的保密标签记忆。'}, 'privacy_recall_final')
        return self.response(body, {'role':'assistant','content':'MOCK_REPLY: '+latest}, 'chat')

    def tool(self, body, name, args, kind):
        return self.response(body, {'role':'assistant','content':None,'tool_calls':[{
            'id':'call_'+uuid.uuid4().hex[:12],'type':'function',
            'function':{'name':name,'arguments':json.dumps(args,ensure_ascii=False)}}]}, kind)

    def response(self, body, message, kind):
        response = {'id':'chatcmpl-'+uuid.uuid4().hex,'object':'chat.completion','created':int(time.time()),
            'model':'phase1-mock','choices':[{'index':0,'message':message,
                'finish_reason':'tool_calls' if message.get('tool_calls') else 'stop'}],
            'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}}
        result = {'request':body,'status':200,'response':response,'kind':kind}
        self.requests.append(result)
        append_event(self.evidence,'llm',result)
        return web.json_response(response)
