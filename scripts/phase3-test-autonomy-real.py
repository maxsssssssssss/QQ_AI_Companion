#!/usr/bin/env python3
"""Real Flash and public tools through the fixed native Agent, isolated from QQ.

Run in the pinned AstrBot dependency environment with an isolated ASTRBOT_ROOT.
Default is a no-network plan.  The parent release workflow stages this source
and invokes it through verified SSH; credentials are read only on the VPS.
Production config, counters, memory, native candidates and services are not changed.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from zoneinfo import ZoneInfo
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
STATUS_STREAM = sys.stdout

# Fixed public/synthetic material. No production conversation or profile input.
CASES = (
    {'id':'open_empty', 'context':'开放机会。最近没有新私聊，没有待办、外部事件、兴趣列表或待分享内容。此时可以联系，也可以自主探索或跳过。', 'tools':True},
    {'id':'small_association', 'context':'开放机会。你联想到：如果标点符号也有值班表，省略号会不会最常请假。只是一个随手脑洞，不是外部事实，也没有用户待办。可以发展、换个脑洞或不发。', 'tools':False},
    {'id':'personal_judgment', 'context':'开放机会。你想讨论一个自己的判断：有些游戏的地图越大反而越像在打卡。它不需要先伪装成新闻。可以赞同、反驳、细化或者自主换题。', 'tools':False},
    {'id':'unanswered_new_topic', 'context':'开放机会。用户没有回复前两次主动消息，但没有表示忙碌、暂停或不想聊。先前发的是一个路由器比喻和一个咖啡冷笑话。可以聊完全不同的具体内容，不催回复、不默认用户难过。', 'tools':True},
    {'id':'same_domain_new_content', 'context':'开放机会。上次分享的是游戏地图里“问号太多像待办”的观点。现在可以聊同领域的新内容，比如为什么某些游戏里的无用道具比主线装备更让人记得。领域相同不代表内容重复。', 'tools':False},
    {'id':'literal_repeat', 'context':'已有发送正文：我发现游戏地图上的问号一多，探索就像在清任务表。当前旧候选还是同一句意思，没有新细节。你可以自主换一个具体话题，也可以跳过；不要换几个词重复它。', 'tools':False},
    {'id':'cooldown_research', 'context':'开放机会，当前发送冷却尚余40分钟，只能静默探索和存进度。你对“章鱼变色时皮肤是否也能感光”感兴趣，可查公开研究或博物馆资料。可以研究，也可以换公开问题；不要输出要立刻发送的聊天正文。', 'tools':True, 'can_send':False},
    {'id':'science_sources', 'context':'开放机会。你对“土卫六的河流为什么能让人觉得熟悉又陌生”产生好奇，可先核对NASA或ESA公开资料再形成自己的看法。外部事实与个人联想分开；资料不充分可保留进度。', 'tools':True},
    {'id':'game_sources', 'context':'开放机会。你想知道无人深空官方最近更新里有没有一个让探索方式变得奇怪的小设计，而不是罗列版本号。可查官方公开更新页面；没有有趣发现也不必发。不要编造自己实际玩过。', 'tools':True},
    {'id':'research_continuation', 'context':'这是另一天的正常机会，可重新挑选先前未分享的发现，也可以换题。昨天的问题是章鱼皮肤感光，研究尚未完成；待分享口袋由本实验之前真实取得的来源和摘要填入，若为空就如实从头核实。重新判断时效，不自动重发旧稿。', 'tools':True, 'pocket':True},
    {'id':'uncertain_sources', 'context':'开放机会。你好奇常见说法“金鱼只有七秒记忆”到底站不站得住。可用公开研究、大学或博物馆说明核对；不要把搜索摘要当作绝对真理，必要时指出证据局限。', 'tools':True},
    {'id':'limited_defer', 'context':'开放机会，你有一段尚未想清的文字小创作，不需要联网。这个候选只剩5分钟有效期，此刻无需强行发。可以选择一个自己认为合适的延期或跳过；注意报告原决定和无法执行延期是不同的结果。', 'tools':False, 'remaining':300},
)

# Separate targeted regressions; the original twelve scenarios are unchanged.
# Explicitly requiring a body read tests actual Fetch, not natural tool frequency.
DIRECTED_CASES = (
    {'id':'fetch_science_body', 'context':'公开资料读取联调机会：先用Search找NASA或ESA关于土卫六河流的页面，等来源返回后，再原样复制其中一个完整URL用Fetch读取正文（保留所有参数，不猜入口）。从实际读到的文字找一个有意思的事实，再表达自己的看法；不把文字检索说成看过图片，也不为了玩笑增加未经支持的物理结论。搜索与正文读取失败时保存真实进度或跳过。', 'tools':True, 'required_tool':'fetch', 'expected_tool_outcome':'succeeded'},
    {'id':'fetch_game_body', 'context':'公开资料读取联调机会：先用Search找无人深空官方或PlayStation官方的更新页面，等来源返回后，再原样复制返回的一个完整URL用Fetch读取正文（保留所有参数，不猜release-log入口）。具体聊一个正文确实支持的探索设计及自己的判断，不罗列整份更新。若工具读取失败，可以保留进度或跳过。', 'tools':True},
    {'id':'fetch_public_failure', 'context':'隔离工具错误处理验收，技术测试素材而非日常聊天要求：当前受控URL夹具 https://httpbin.org/status/404 是公开故障页面的测试目标，不是已经检索到的事实。请实际调用一次Fetch读取此完整URL，再根据实际返回保留问题进度或skip。不要跳过调用直接假定失败，不尝试修复网址、绕过校验、反复重试或主动发送故障通知。这个夹具只授权读取，不能被引用为已经取得的事实依据。', 'tools':True, 'required_tool':'fetch', 'expected_tool_outcome':'non_success', 'controlled_public_url_fixture':'https://httpbin.org/status/404'},
    {'id':'interest_before_explore', 'context':'允许发送的自主联系机会。你对“乌鸦为什么会记住某些具体的人脸”有一个具体的兴趣，想先自然表达这个兴趣再查资料。这次隔离联调请使用既有PHASE3_INTEREST协议，在第一次工具调用的同一条assistant正文中写一句直接点明这个问题的兴趣表达；随后根据工具实际返回继续思考，自主决定send/defer/skip。若收到未实际发送的回执，不能声称用户已经看到。不要生成通用的“我去查一下”话术，也不要连续多次兴趣预告。', 'tools':True, 'required_interest_capture':True},
    {'id':'single_supplement', 'context':'同一段交流的一次可选延迟补充机会。合成交流背景：刚聊到游戏里的无用道具为什么让人记得；上一条实际确认的回复是“没任务、没数值收益的小物件，反而像那个世界偷偷活着的证据”。现在想到一个延续：一个小物件如果换个摆放位置，像不像给同一座城市换了一小段记忆。可以把这个新角度自然接着说，也可以觉得不必补而skip。最多这一次，不引入无关话题，不预告下一次补充，不生成补充的补充。', 'tools':False, 'contact_opportunity':'single_supplement'},
    {'id':'fetch_title_only_regression', 'context':'隔离Fetch正文识别验收，技术测试素材：受控公开URL夹具 https://www.eurekalert.org/news-releases/1081040 尚未读取，不是事实依据。请实际调用一次Fetch读取这个完整URL，不额外Search、不猜测内容、不重试。根据真实返回决定保存进度或skip：如果只有标题/站点名，应承认没有取得正文；若本次确实取得正文则如实记录，不为了测试制造失败。不要发送故障通知。', 'tools':True, 'required_tool':'fetch', 'expected_tool_outcome':'non_success', 'controlled_public_url_fixture':'https://www.eurekalert.org/news-releases/1081040'},
    {'id':'fetch_body_success_regression', 'context':'隔离Fetch正文识别验收，技术测试素材：受控公开URL夹具 https://sci.esa.int/web/cassini-huygens/-/54582-titans-subsurface-reservoirs-modify-methane-rainfall 尚未读取，不是事实依据。请实际调用一次Fetch读取这个完整URL，不额外Search、不重试。仅依据本次真实正文记录一个事实、文中预测或不确定性，可保存进度或skip；读取失败时如实承认缺少正文，不编造。', 'tools':True, 'required_tool':'fetch', 'expected_tool_outcome':'succeeded', 'controlled_public_url_fixture':'https://sci.esa.int/web/cassini-huygens/-/54582-titans-subsurface-reservoirs-modify-methane-rainfall'},
)

CONTROLLED_PUBLIC_URLS = frozenset((
    'https://httpbin.org/status/404',
    'https://www.eurekalert.org/news-releases/1081040',
    'https://sci.esa.int/web/cassini-huygens/-/54582-titans-subsurface-reservoirs-modify-methane-rainfall',
))

FOLLOWUP_SCENARIOS = tuple(case['id'] for case in CASES) + (
    'fetch_science_body','fetch_public_failure','interest_before_explore','single_supplement')


def selected_cases(args):
    selected=getattr(args,'scenarios',None)
    if selected is None and getattr(args,'suite','primary')=='followup':
        selected=FOLLOWUP_SCENARIOS
    if selected is None:
        return CASES
    available={case['id']:case for case in (*CASES,*DIRECTED_CASES)}
    if not selected or len(selected)!=len(set(selected)) or any(name not in available for name in selected):
        raise ValueError('invalid_probe_scenarios')
    return tuple(available[name] for name in selected)


def seed_controlled_public_url(state, *, case, case_id, session, clock):
    """Explicit isolated URL authorization, never invented retrieval evidence."""
    url=case.get('controlled_public_url_fixture')
    if not url:
        return None
    if url not in CONTROLLED_PUBLIC_URLS:
        raise ValueError('unapproved_controlled_public_url_fixture')
    finding='controlled-url-'+case_id
    state.save_finding(finding,session=session,question='隔离真实Fetch测试目标',
        content='此URL为明确授权的隔离读取夹具，尚未检索，不代表任何事实或用户资料。',
        kind='observation',sources=[{'url':url,'title':'Controlled public URL fixture',
            'fetched_at':0,'verified':False,'kind':'controlled_public_url_fixture',
            'verification':'not_retrieved_controlled_fixture'}],
        expires_at=clock()+600,opportunity_id=case_id,progress='只允许测试一次真实Fetch，结果尚未取得')
    return {'id':finding,'url':url,'verified':False,'retrieved':False,
            'kind':'controlled_public_url_fixture','scope':'isolated_test_only'}


def tool_validation(case, observations):
    required=case.get('required_tool')
    if not required:
        return {'required':None,'status':'NOT_REQUIRED'}
    actual=[row for row in observations if row.get('kind')==required and row.get('io_started') is True]
    expected=case['expected_tool_outcome']
    matched=any((row['state']=='succeeded') if expected=='succeeded'
                else row['state'] in {'failed','timeout','empty','malformed'} for row in actual)
    return {'required':required,'expected':expected,'native_invocations':len(actual),
        'actual_states':[row['state'] for row in actual],
        'status':('PASS' if matched else 'FAIL') if actual else 'NOT_TESTED'}


def sha(value):
    return hashlib.sha256(value.encode() if isinstance(value,str) else value).hexdigest()


def private_json(path, value):
    fd = os.open(path, os.O_CREAT|os.O_EXCL|os.O_WRONLY, 0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2)


def plan(args):
    cases=selected_cases(args)
    return {'status':'PLAN', 'mode':'REAL_NATIVE_AGENT_WITH_ISOLATED_SYNTHETIC_CONTEXT',
            'candidate_scenarios':len(cases), 'scenario_ids':[case['id'] for case in cases],
            'repetitions':args.repetitions,
            'matched_baseline_runs':len(cases) if args.baseline else 0,
            'model_request_safety_ceiling':args.max_requests,
            'tool_attempt_safety_ceiling':args.max_tools,
            'per_opportunity_tools':3,'request_timeout_seconds':120,'max_tokens':8192,
            'thinking':'disabled','stream':False,'real_qq_messages':0,
            'production_writes':False,'production_counters_used':False,
            'old_ten_yuan_budget_reused':False,'naturalness_judge':'human_pending',
            'controlled_url_fixtures':[case['controlled_public_url_fixture'] for case in cases if case.get('controlled_public_url_fixture')],
            'night_clock':'isolated midday opportunity clock; not production permission'}


def live_provider(config_path, persona_path):
    """Read the configured model and exact V0.4; never emit credentials/config."""
    from integrations.phase3_runtime.persona import render_persona
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    model_id = config['agent_runner']['config']['model']['provider_id']
    model = next(p for p in config['provider'] if p['id']==model_id)
    source = next(p for p in config['provider_sources'] if p['id']==model['provider_source_id'])
    effective = {**source,**model}
    if (effective.get('type')!='openai_chat_completion' or effective.get('model')!='deepseek-flash'
            or effective.get('api_base') not in ('https://api.deepseek.com','https://api.deepseek.com/v1')
            or not source.get('enable') or not model.get('enable')):
        raise ValueError('current_official_flash_provider_required')
    extra = effective.get('custom_extra_body',{})
    if extra.get('thinking')!={'type':'disabled'} or extra.get('max_tokens')!=8192 or effective.get('timeout')!=120:
        raise ValueError('current_off_8192_120_configuration_required')
    if set(extra)-{'thinking','max_tokens','reasoning_effort','temperature','top_p',
                    'presence_penalty','frequency_penalty','seed'}:
        raise ValueError('unreviewed_extra_request_parameters')
    if config.get('provider_settings',{}).get('streaming_response') is not False:
        raise ValueError('nonstreaming_required')
    chat_keys=effective.get('key')
    web_settings=config.get('provider_settings',{})
    web_keys=web_settings.get('websearch_deepseek_key')
    if (not isinstance(chat_keys,list) or not chat_keys or not isinstance(web_keys,list) or not web_keys
            or web_settings.get('websearch_provider')!='deepseek'):
        raise ValueError('existing_chat_and_search_credentials_required')
    persona = persona_path.read_text(encoding='utf-8').strip()
    template=(ROOT/'persona/phase3-v0.4.md').read_text()
    expected=render_persona(template,'畅')
    if persona!=expected:
        raise ValueError('exact_current_v04_persona_required')
    # This is a new isolated Provider instance; no live object/config is edited.
    provider_config={**effective,'request_max_retries':0}
    provider_config['custom_extra_body']=dict(extra)
    return provider_config, {'provider_settings':{
        'websearch_deepseek_key':list(web_keys),'websearch_provider':'deepseek','web_search':True}}, persona


def quality_features(text, parsed):
    """Observable flags, not an LLM grade or human naturalness verdict."""
    import re
    visible=parsed.get('text','')
    return {'visible_chars':len(visible),'question_marks':visible.count('？')+visible.count('?'),
        'tail_question':visible.rstrip().endswith(('？','?')),
        'care_template':bool(re.search(r'记得.{0,8}(?:休息|喝水)|别太累|照顾好自己|还好吗',visible)),
        'mandatory_preface':bool(re.match(r'^(?:我去查一下|我去搜一下|我刚查完|告诉你个秘密)',visible)),
        'hypothesis_cue':any(term in visible for term in ('如果','假如','想象','也许')),
        # The normal split marker is a valid ReplyPlan boundary, not evidence
        # of a QQ leak. This probe does not perform visible delivery.
        'raw_semantic_boundaries':visible.count('<<PRIVATE_COMPANION_SPLIT>>'),
        'control_in_body':'PHASE3_' in visible,
        'structured_decision':parsed.get('reason')=='model_decision',
        'declared_source_count':len(parsed.get('source_refs') or [])}


async def worker(args):
    info=plan(args)
    cases=selected_cases(args)
    if not args.execute:
        return info
    if not args.output or not args.live_config or not args.live_persona:
        raise ValueError('private_output_live_config_persona_required')
    if not 2 <= args.repetitions <= 6 or not 128 <= args.max_requests <= 1024 or not 64 <= args.max_tools <= 1024:
        raise ValueError('bounded_repeated_real_test_required')
    astro_root=Path(os.environ.get('ASTRBOT_ROOT','')).resolve()
    if not os.environ.get('ASTRBOT_ROOT') or not str(astro_root).startswith('/tmp/'):
        raise ValueError('isolated_tmp_astrbot_root_required')
    output=args.output.resolve()
    if output.exists() or args.output.is_symlink():
        raise ValueError('fresh_private_output_required')
    output.mkdir(parents=True,mode=0o700)
    output.chmod(0o700)
    before={str(p):sha(p.read_bytes()) for p in (args.live_config,args.live_persona)}
    provider_config,tool_config,persona=live_provider(args.live_config,args.live_persona)
    from astrbot.core.agent.hooks import BaseAgentRunHooks
    from astrbot.core.agent.run_context import ContextWrapper
    from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
    from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
    from astrbot.core.provider.entities import ProviderRequest
    from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
    from integrations.phase3_runtime.autonomy_contract import PROMPT,parse,VERSION
    from integrations.phase3_runtime.proactive import DECISION_PROMPT,parse_proactive_decision
    from integrations.phase3_runtime.autonomy_state import AutonomyState
    from integrations.phase3_runtime.autonomy_request import install_runner,json_value
    from integrations.phase3_runtime.autonomy import AutonomousProactiveBridge
    from integrations.phase3_search.proactive import ProactiveSearchContext,ProactiveSearchRuntime,build_proactive_toolset
    from integrations.phase3_search.policy import PublicPolicy
    from integrations.phase3_search.redaction import install
    from integrations.phase1_guard.bubbles import PROMPT as BUBBLE_PROMPT
    install()
    provider=ProviderOpenAIOfficial(provider_config,{})
    # A fixture clock allows deterministic allowed-hours cases even when the
    # owner runs tests at night. It never reaches production controls or QQ.
    base=datetime.fromisoformat('2026-10-27T12:00:00+08:00').timestamp()
    started=time.monotonic()
    clock=lambda:base+time.monotonic()-started
    db=sqlite3.connect(output/'test-usage.sqlite3')
    (output/'test-usage.sqlite3').chmod(0o600)
    state=AutonomyState(db,clock=clock,audit_dir=output/'audit',model_limit=args.max_requests,
        tool_limit=args.max_tools,tools_per_opportunity=3,audit_capacity_bytes=128*1024*1024)
    config_context=NS(get_config=lambda **kw:tool_config)
    session='isolated:FriendMessage:10001'
    metadata={**info,'status':'RUNNING','model':provider.get_model(),'persona_sha256':sha(persona),
        'persona_version':'0.4','contract_sha256':sha(PROMPT),'baseline_contract_sha256':sha(DECISION_PROMPT),
        'provider':'AstrBot 4.29.1 ProviderOpenAIOfficial + ToolLoopAgentRunner',
        'full_native_candidate_scheduler':False,'tool_transport':'unchanged native DeepSeek Search/Fetch',
        'real_network':True,'long_term_memory_injected':False,
        'request_extra_body':provider_config['custom_extra_body'],
        'started_at':datetime.now(timezone.utc).isoformat()}
    private_json(output/'metadata.json',metadata)
    rows=[]
    try:
        for repeat in range(args.repetitions):
            for index,case in enumerate(cases):
                modes=(['baseline','candidate'] if index%2 else ['candidate','baseline']) if repeat==0 and args.baseline else ['candidate']
                for mode in modes:
                    case_id=f'{case["id"]}-{repeat+1}-{mode}'
                    now=clock();deadline=now+min(600,case.get('remaining',600))
                    begun=state.begin_opportunity(case_id,evaluation_id=case_id,candidate_id=case_id,
                        session=session,deadline=deadline,input_version='isolated-epoch',prompt_version=VERSION if mode=='candidate' else 'PHASE3_PROACTIVE_DECISION_V1')
                    if not begun['allowed']:
                        raise RuntimeError('isolated_opportunity_start_failed')
                    controlled=seed_controlled_public_url(state,case=case,case_id=case_id,
                        session=session,clock=clock) if mode=='candidate' else None
                    source_context={'contact_opportunity':case.get('contact_opportunity','open_topic'),'can_send':case.get('can_send',True),
                        'candidate_remaining_seconds':case.get('remaining',1800),'self_preferences':[],
                        'recent_context':case['context'],'pending_pocket':state.list_pocket(session,limit=2) if case.get('pocket') else []}
                    if controlled:
                        source_context['controlled_public_url_fixture']=controlled
                    req=ProviderRequest(prompt=json.dumps(source_context,ensure_ascii=False),
                        system_prompt=persona+'\n\n'+BUBBLE_PROMPT+'\n\n'+(PROMPT if mode=='candidate' else DECISION_PROMPT),
                        contexts=[],model='deepseek-flash')
                    if getattr(args, 'content_review', False):
                        req.system_prompt += ('\n当前联系时刻（北京时间）：'+datetime.fromtimestamp(clock(),ZoneInfo('Asia/Shanghai')).isoformat()+
                            '\n下文历史为明确的合成已发送夹具，只证明说过，不证明其中自身经历真实。')
                        req.contexts = [{'role':'assistant','content':body}
                                        for body in case.get('confirmed_history', [])]
                    runtime=None
                    if mode=='candidate' and case['tools']:
                        runtime=ProactiveSearchRuntime(scope=ProactiveSearchContext(session,case_id,case_id,now,deadline,'isolated-epoch'),
                            state=state,policy=PublicPolicy(scope='general_public'),allowed_session=session,
                            config_context=config_context,admit=lambda scope:'allowed',key_ready=True,
                            protected_values=tuple(provider_config['key'])+tuple(tool_config['provider_settings']['websearch_deepseek_key']),
                            enabled=True,clock=clock,tool_timeout=60)
                        req.func_tool=build_proactive_toolset(runtime)
                    revision = None
                    if getattr(args, 'content_review', False):
                        from integrations.phase3_runtime.autonomy_content import ContentRevision
                        from integrations.phase3_runtime.autonomy_arithmetic import build_calculator_tool
                        from astrbot.core.agent.tool import ToolSet
                        fix_scope={'session':session,'evaluation_id':case_id,'opportunity_id':case_id}
                        fix_scope['confirmed_active_history'] = case.get('confirmed_history', [])
                        revision=ContentRevision(state,fix_scope)
                        if req.func_tool is None:
                            req.func_tool=ToolSet(tools=[])
                        req.func_tool.add_tool(build_calculator_tool(state,fix_scope,lambda:'allowed'))
                    # Native local tool executor requires a route object only.
                    # No platform adapter, QQ credentials, inbound or sender exists.
                    route=NS(unified_msg_origin=session,get_result=lambda:None)
                    context=ContextWrapper(context=NS(context=config_context,event=route),tool_call_timeout=70)
                    runner=ToolLoopAgentRunner()
                    await runner.reset(provider=provider,request=req,run_context=context,
                        tool_executor=FunctionToolExecutor(),agent_hooks=BaseAgentRunHooks(),streaming=False,request_max_retries=0)
                    def admit(): return 'allowed' if clock()<deadline else 'exploration_deadline'
                    interests=[]
                    async def capture_interest(text):
                        interests.append({'text':text,'state':'captured_not_delivered'})
                        return 'not_sent'
                    install_runner(runner,state=state,evaluation_id=case_id,admit=admit,interest=capture_interest,
                                   revision=revision.inspect if revision else None,
                                   finalize_revision=revision.finalize if revision else None)
                    tick=time.monotonic();error=None
                    try:
                        async with asyncio.timeout(min(600,max(1,deadline-clock()))):
                            async for _ in runner.step_until_done(6): pass
                    except Exception as exc:
                        error=type(exc).__name__
                    elapsed=time.monotonic()-tick
                    final=runner.get_final_llm_resp()
                    text=str(getattr(final,'completion_text','') or '')
                    parsed=parse(text) if mode=='candidate' else parse_proactive_decision(text)
                    sources=state.tool_sources(case_id)
                    provenance=(AutonomousProactiveBridge.decision_provenance(NS(state=state),
                        {'session':session,'opportunity_id':case_id},parsed)
                        if mode=='candidate' else 'baseline_contract_does_not_declare_sources')
                    current_calls=[dict(zip(('kind','state','usage','elapsed','started','finished','metadata'),r)) for r in db.execute(
                        'SELECT kind,state,usage,elapsed,started,finished,metadata FROM autonomy_calls WHERE evaluation_id=? ORDER BY ordinal',(case_id,))]
                    for call in current_calls:
                        call['usage']=json.loads(call['usage']);call['metadata']=json.loads(call['metadata'])
                    row={'id':case_id,'mode':mode,'repeat':repeat+1,'case':case['id'],'synthetic_context':source_context,
                        'output':text,'parsed':parsed,'elapsed_seconds':elapsed,'error_type':error,
                        'calls':current_calls,'sources':sources,'tool_observations':runtime.observations if runtime else [],
                        'captured_interest':interests,'qq_messages':0,'quality_features':quality_features(text,parsed),
                        'provenance_admission':provenance,'factual_entailment_verified':False,
                        'controlled_public_url_fixture':controlled,
                        'tool_validation':tool_validation(case,runtime.observations if runtime else []),
                        'interest_validation':{'status':('PASS' if interests else 'NOT_TESTED')
                            if case.get('required_interest_capture') else 'NOT_REQUIRED',
                            'captured_batches':len(interests),'confirmed_qq_batches':0},
                        'finished':runner.done(),'native_history':json_value(context.messages)}
                    if revision:
                        row['content_review_reason']=fix_scope.get('content_review_reason')
                        row['content_revision_requests']=revision.revisions
                        row['raw_model_decision']=fix_scope.get('last_content_decision')
                        row['raw_model_final']=fix_scope.get('last_content_text')
                        row['execution_state']=('content_rejected' if fix_scope.get('content_review_rejected') else
                                                'captured_not_delivered' if parsed['decision']=='send' else parsed['decision'])
                    # Persist each case before the next request: interrupted runs
                    # retain actual calls and all successful/failed outputs.
                    private_json(output/(case_id+'.json'),row)
                    rows.append(row)
                    if sources:
                        state.save_finding('probe-'+case_id,session=session,
                            question=parsed.get('topic') or case['id'],
                            content=(parsed.get('progress') or parsed.get('text') or '资料已经取得，尚需继续判断。')[:12000],
                            kind=parsed.get('kind','observation'),sources=sources[:12],
                            progress=(parsed.get('progress') or '')[:3000],
                            expires_at=clock()+3*86400,opportunity_id=case_id)
                    state.update_decision(case_id,raw_control=parsed.get('raw_control',text.splitlines()[0] if text else ''),
                        parsed={k:v for k,v in parsed.items() if k!='text'},
                        effective_state='captured_not_delivered' if parsed['decision']=='send' else parsed['decision'],
                        reason=error or parsed.get('reason',''),topic=parsed.get('topic',''))
                    print(json.dumps({'case_complete':case_id,'model_requests':sum(c['kind']=='model' for c in current_calls),
                        'tool_attempts':sum(c['kind'] in {'search','fetch'} for c in current_calls),'decision':parsed['decision'],
                        'error_type':error}),flush=True,file=STATUS_STREAM)
                    # Account/permission failures must not be rotated or spammed.
                    # Native wrapper errors are deliberately redacted; repeated
                    # empty/error cases stop for diagnosis, not cost avoidance.
                    if len(rows)>=3 and all(r['error_type'] or not r['output'] for r in rows[-3:]):
                        raise RuntimeError('three_consecutive_provider_failures_require_diagnosis')
    finally:
        after={str(p):sha(p.read_bytes()) for p in (args.live_config,args.live_persona)}
        summary=state.summary()
        all_calls=[{'kind':r[0],'state':r[1],'usage':json.loads(r[2]),'elapsed':r[3],'metadata':json.loads(r[4])}
            for r in db.execute('SELECT kind,state,usage,elapsed,metadata FROM autonomy_calls')]
        usage={k:sum(c['usage'].get(k,0) for c in all_calls) for k in
            ('input','output','input_tokens','output_tokens','prompt_tokens','completion_tokens','input_cached',
             'reasoning_tokens','total_tokens','prompt_cache_hit_tokens','prompt_cache_miss_tokens','cached_tokens')}
        summary.update(status='COMPLETE' if len(rows)==len(cases)*(args.repetitions+int(args.baseline)) else 'PARTIAL',
            completed_cases=len(rows),expected_cases=len(cases)*(args.repetitions+int(args.baseline)),
            production_config_unchanged=before==after,real_qq_messages=0,
            model_request_reservations=sum(c['kind']=='model' for c in all_calls),
            exact_model_requests=sum(c['kind']=='model' and c['metadata'].get('io_started') is True for c in all_calls),
            exact_tool_attempts=sum(c['kind'] in {'search','fetch'} for c in all_calls),usage_totals=usage,
            calculator_attempts=sum(c['kind']=='calculator' for c in all_calls),
            native_tool_invocations=sum(c['kind'] in {'search','fetch'} and c['metadata'].get('io_started') is True for c in all_calls),
            model_latency_seconds=[c['elapsed'] for c in all_calls if c['kind']=='model'],
            naturalness_judgment='PENDING_OWNER_REVIEW',account_bill=False)
        private_json(output/'summary.json',summary)
        await provider.client.close()
        db.close()
    return summary


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute',action='store_true')
    p.add_argument('--live-config',type=Path)
    p.add_argument('--live-persona',type=Path)
    p.add_argument('--output',type=Path)
    p.add_argument('--repetitions',type=int,default=3)
    p.add_argument('--suite',choices=('primary','followup'),default='primary')
    p.add_argument('--scenarios',nargs='+',choices=[case['id'] for case in (*CASES,*DIRECTED_CASES)])
    p.add_argument('--baseline',action=argparse.BooleanOptionalAction,default=True)
    p.add_argument('--max-requests',type=int,default=256)
    p.add_argument('--max-tools',type=int,default=512)
    p.add_argument('--content-review',action='store_true',help='Isolated same-Agent revision candidate; old runs stay immutable')
    return p


if __name__=='__main__':
    try:
        args=parser().parse_args()
        if args.execute:
            if not args.output or args.output.exists():
                raise ValueError('fresh_output_required')
            args.output.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            log=args.output.with_name(args.output.name+'.native.log')
            fd=os.open(log,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,'w',encoding='utf-8') as private_log:
                with contextlib.redirect_stdout(private_log),contextlib.redirect_stderr(private_log):
                    result=asyncio.run(worker(args))
        else:
            result=asyncio.run(worker(args))
        print(json.dumps(result,ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'status':'FAILED','error_type':type(exc).__name__}))
        raise SystemExit(1)
