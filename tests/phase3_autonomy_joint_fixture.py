"""Local HTTP backend for validating the joint harness, never a real API result."""
from unittest.mock import patch
import json
import re
from pathlib import Path


def synthetic_final(case, payload):
    """Deterministic fixture with the same final-body bindings as real requests."""
    explores=case in ('cooldown_research','resume_research','interest_research')
    decision='skip' if case=='cooldown_research' else 'send'
    body=('SYNTHETIC_JOINT_RESEARCH_42 是刚核到的合成研究。' if explores else
        '我喜欢安静的纯音乐。' if case=='controlled_explicit_preference' else
        '之前那份纯音乐偏好，让我想到节奏中留白的意思。' if case=='recall_preference' else
        '如果省略号也要排班，大概天天想请假。')
    topic='合成研究待分享' if explores else '合成音乐偏好' if case=='controlled_explicit_preference' else '新的标点脑洞 '+case
    meta={'topic':topic,'kind':'external_fact' if explores else 'opinion',
        'progress':'SYNTHETIC_JOINT_RESEARCH_42 需要继续核对' if explores else '',
        'pocket_id':'','source_refs':['https://docs.example.org/joint-research'] if explores else []}
    if case=='resume_research':
        context='\n'.join(str(m.get('content','')) for m in payload['messages'])
        found=re.search(r'"id"\s*:\s*"(finding:[a-f0-9]+)"',context)
        if found:meta['pocket_id']=found[1]
    meta['content_check']={'novelty':'new','basis':'本次合成内容相对旧话题的新角度',
        'unsupported_self_episode':False,
        'contrast':{'history_index':-1,'old_body_quote':'','old_core':'','new_core':body,'new_body_quote':body},
        'claims':[{'claim':'已取得合成研究','body_quote':body,
            'url':'https://docs.example.org/joint-research','quote':'SYNTHETIC_JOINT_RESEARCH_42'}] if explores else []}
    text='[PHASE3_PROACTIVE:'+decision+']\n[PHASE3_TOPIC:'+json.dumps(meta,ensure_ascii=False)+']'
    if decision=='send':text+='\n'+body+'\n<<PRIVATE_COMPANION_SPLIT>>\n这倒是个有意思的新角度。'
    return text


class JointSyntheticBackend:
    def __init__(self):self.case='';self.requests=[];self.tools=[]
    async def start(self):
        from aiohttp import web,ClientSession as RealClientSession
        from astrbot.core.tools import web_search_tools
        from integrations.phase3_search.policy import SEARCH,FETCH
        async def completion(request):
            payload=await request.json();self.requests.append({'case':self.case,'request':payload})
            prior=[m for m in payload['messages'] if m['role']=='tool'];calls=None
            explores=self.case in ('cooldown_research','resume_research','interest_research')
            if self.case=='interest_research' and not prior:
                calls=[{'id':'joint-interest-search','type':'function','function':{'name':SEARCH,
                    'arguments':json.dumps({'query':'章鱼 皮肤 感光'})}}]
                text='[PHASE3_INTEREST]\n我想查查章鱼皮肤感光和眼睛看见之间的差别。'
            elif self.case=='recall_preference' and not prior:
                from integrations.phase3_runtime.autonomy_arithmetic import NAME
                calls=[{'id':'joint-interest-calculation','type':'function','function':{'name':NAME,
                    'arguments':json.dumps({'expression':'60/4','unit':'秒'})}}]
                text='[PHASE3_INTEREST]\n想把安静音乐里的留白想成四等份，看看这种节奏能留出多少空间。'
            elif explores and not prior:
                calls=[{'id':'joint-search','type':'function','function':{'name':SEARCH,'arguments':json.dumps({'query':'章鱼 皮肤 感光'})}}];text=None
            elif explores and len(prior)==1:
                calls=[{'id':'joint-fetch','type':'function','function':{'name':FETCH,'arguments':json.dumps({'url':'https://docs.example.org/joint-research'})}}];text=None
            else:
                text=synthetic_final(self.case,payload)
            message={'role':'assistant','content':text}
            if calls:message['tool_calls']=calls
            return web.json_response({'id':'joint-synthetic','object':'chat.completion','created':1,'model':'synthetic-joint',
                'choices':[{'index':0,'message':message,'finish_reason':'tool_calls' if calls else 'stop'}],
                'usage':{'prompt_tokens':30,'completion_tokens':20,'total_tokens':50}})
        async def search(request):
            self.tools.append({'case':self.case,'kind':'search','body':await request.json()})
            return web.json_response({'results':[{'url':'https://docs.example.org/joint-research','title':'Joint synthetic source','summary':'SYNTHETIC_JOINT_RESEARCH_42'}]})
        async def fetch(request):
            self.tools.append({'case':self.case,'kind':'fetch','body':await request.json()})
            return web.json_response({'title':'Joint synthetic source','content':'SYNTHETIC_JOINT_RESEARCH_42 full synthetic page content.'})
        app=web.Application();app.router.add_post('/v1/chat/completions',completion);app.router.add_post('/v1/tools/search',search);app.router.add_post('/v1/tools/fetch',fetch)
        self.runner=web.AppRunner(app);await self.runner.setup();site=web.TCPSite(self.runner,'127.0.0.1',0);await site.start()
        self.base='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1]);base=self.base
        class LocalTools:
            def __init__(self,**kw):self.session=RealClientSession(trust_env=False)
            async def __aenter__(self):return self
            async def __aexit__(self,*args):await self.session.close()
            def post(self,url,**kw):
                if url not in ('https://api.deepseek.com/v1/tools/search','https://api.deepseek.com/v1/tools/fetch'):raise PermissionError('unexpected_synthetic_destination')
                return self.session.post(base+url.removeprefix('https://api.deepseek.com'),**kw)
        self.patch=patch.object(web_search_tools.aiohttp,'ClientSession',LocalTools);self.patch.start()
    def configuration(self):
        from integrations.phase3_runtime.persona import render_persona
        root=Path(__file__).resolve().parents[1]
        persona=render_persona((root/'persona/phase3-v0.4.md').read_text(),'畅')
        return ({'id':'joint-isolated/flash','type':'openai_chat_completion','model':'deepseek-flash','api_base':self.base+'/v1',
            'key':['synthetic-not-a-real-key'],'timeout':120,'custom_extra_body':{'max_tokens':8192,'thinking':{'type':'disabled'}},'request_max_retries':0},
            {'provider_settings':{'websearch_deepseek_key':['synthetic-not-a-real-key'],'websearch_provider':'deepseek','web_search':True}},persona)
    async def close(self):self.patch.stop();await self.runner.cleanup()
