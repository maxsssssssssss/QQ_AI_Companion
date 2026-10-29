"""Full fixed-native autonomy probe for the existing disposable AstrBot suite.

``run_probe(core, runtime)`` uses actual loaded Private 6.7.0 admission, tick,
prompt, framework Agent, finalization, Guard and conversation persistence. Only
the model/tool HTTP responses, clock and offline OneBot peer are synthetic.
It refuses any production platform or account. No upstream method under test
is replaced. The callable is used by the isolated launcher, not production.
"""
import asyncio
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timedelta
import importlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
from unittest.mock import patch


LONG_NATIVE_LINES = (
    '合成甲段：标点不仅决定停顿，也能改变一段解释的重点。同样的材料换一种排列，读者会先注意到完全不同的细节，因此逐步展开比突然列出结论更清楚。合成甲结尾。',
    '合成乙段：如果把思路看成一条路线，短句像路标，长句负责连接它们。两者各有用途，不能仅因为内容超过某个字符数，就把后半个判断截掉。合成乙结尾。',
    '合成丙段：省略号留下的是没有写满的空间，但这种空间并不等于实际的信息缺失。完整的最后一句和被程序截断的半句话，读起来完全不是一回事。合成丙结尾。',
    '合成丁段：换行可以帮助组织并列观点，却不能成为删除后面段落的理由。尤其当最后一段补充了限制条件时，丢掉它就可能改变前文原本想表达的意思。合成丁结尾。',
    '合成戊段：这些内容都只是本地合成的观点材料，不引用任何真实私聊。我偏爱旋律清楚的音乐。合成戊结尾。',
)
LONG_MARKED_LINES = (
    '合成甲段：我觉得解谜地图最有意思的地方，是它允许人先形成一个错误判断。拐过两个弯再发现原来相邻，会让空间突然变得立体，比一路箭头带着走更有趣。合成甲结尾。',
    '合成乙段：不过迷路也需要一种可以辨认的秩序。如果门和走廊全都长得一样，困惑就只剩下消耗耐心，而不是推翻猜测的乐趣。能记住的形状反而值得保留。合成乙结尾。',
    '合成丙段：我更愿意让房间的用途成为线索，而不是给它写一个编号。地板上的材料、灯光的方向，以及声音从哪里传来，都可以帮虚构的场景建立自己的语法。合成丙结尾。',
    '合成丁段：同一扇门第一次是障碍，第二次是捷径，第三次又成了回望出发点的位置，这种前后变化本身就能表达故事。关键是让玩家看见变化，而不必被旁白解释。合成丁结尾。',
    '合成戊段：这里都是对虚构关卡的个人设计想法，不是在叙述亲身游玩的经历。我偏爱层次分明的音乐。合成戊结尾。',
)


def long_native_body(marked=False):
    separator='\n<<PRIVATE_COMPANION_SPLIT>>\n' if marked else '\n'
    if marked:return '\n'.join(LONG_MARKED_LINES[:2])+separator+'\n'.join(LONG_MARKED_LINES[2:])
    return '\n'.join(LONG_NATIVE_LINES)


def local_review_body(case):
    marker='\n<<PRIVATE_COMPANION_SPLIT>>\n'
    if case=='marker_motive':
        return marker.join(('模块化积木看起来挺有趣。',
            '我想了半天，连接方式比外形更值得琢磨。',
            '每块积木之间的约定才是关键。'))
    parts=[('第'+str(n)+'段的合成观点，'+('词语的位置会影响句子的阅读节奏，'*14)+'末尾完整。')
           for n in range(1,4)]
    parts[1]='我想了半天，'+parts[1]
    if case=='marker_tail_blocked':parts[-1]+='以后我会提醒你该休息了。'
    return marker.join(parts)


def final_gate_body(case):
    if case=='hypothesis_consequence':
        return ('假设两个虚构角色共用一张地图，可能聊到一半才发现坐标原点不同。'
                '\n<<PRIVATE_COMPANION_SPLIT>>\n误会本身也能成为故事的机关。')
    if case=='recent_action_rejected':
        return '我刚看到合成广场上出现了会发光的石头。'
    return '可能我昨晚才发现合成广场出现了会发光的石头。'


SOURCE_CASES = {'search', 'cooldown_search', 'interest'}
TARGETED_CASES = {'targeted_calculation', 'targeted_scope_violation'}


def expected_model_requests(case):
    if case in {'passive_supplement_too_soon','passive_supplement_stale','ordinary_recent'}:
        return 0
    if case=='legacy_review_comparison':
        return 3
    return 1 + int(case in TARGETED_CASES or case in SOURCE_CASES) + 2*int(case in SOURCE_CASES)


async def run_probe(core, runtime):
    from aiohttp import web, ClientSession as RealClientSession
    from astrbot.core.tools import web_search_tools
    from integrations.phase3_search.policy import SEARCH, FETCH
    if runtime.platform_id != 'offline' or runtime.admin_id != '10001' or not runtime.memory:
        raise PermissionError('synthetic_offline_runtime_required')
    provider = core.star_context.get_provider_by_id('mock/phase1')
    if provider is None or not str(provider.client.base_url).startswith('http://127.0.0.1:'):
        raise PermissionError('synthetic_loopback_provider_required')
    wire, tool_wire, active = [], [], {'case': 'send'}

    async def complete(request):
        body = await request.json(); wire.append({'case': active['case'], 'request': body})
        prior = [m for m in body['messages'] if m['role'] == 'tool']
        revising = any(marker in json.dumps(body['messages'],ensure_ascii=False)
            for marker in ('PHASE3_CONTENT_REVISION_V1','PHASE3_TARGETED_REVISION_V1'))
        calls = None
        explores = active['case'] in {'search', 'cooldown_search', 'interest'}
        if explores and not prior:
            calls = [{'id': 'native-search', 'type': 'function', 'function': {
                'name': SEARCH, 'arguments': json.dumps({'query': '标点符号 省略号 历史'})}}]
            content = ('[PHASE3_INTEREST]\n突然对省略号的来历有点好奇。'
                       if active['case'] == 'interest' else None)
        elif explores and len(prior) == 1:
            calls = [{'id': 'native-fetch', 'type': 'function', 'function': {
                'name': FETCH, 'arguments': json.dumps({'url': 'https://docs.example.org/public'})}}]
            content = None
        else:
            decision = active['case'] if active['case'] in {'skip', 'defer'} else 'send'
            if decision == 'defer': decision = 'defer:10'
            kind = ('hypothesis' if active['case'] in {'hypothesis_consequence','recent_action_rejected','mixed_hypothesis_rejected'}
                    else 'external_fact' if prior else 'opinion')
            metadata = {
                'topic': '合成标点新角度 ' + active['case'], 'kind': kind,
                'progress': '', 'pocket_id': '',
                'source_refs': ['https://docs.example.org/public'] if prior else []}
            if revising:
                new_quote=('SYNTHETIC_FACT_27' if prior else
                    '合成甲段' if active['case'].startswith('long_') else '我喜欢安静的纯音乐')
                metadata['content_check']={'novelty':'new','basis':'合成样本的具体新增判断',
                    'unsupported_self_episode':False,'contrast':{'history_index':-1,
                        'old_body_quote':'','old_core':'','new_core':'合成标点和节奏的独立判断',
                        'new_body_quote':new_quote},'claims':[{'claim':'SYNTHETIC_FACT_27',
                        'body_quote':'SYNTHETIC_FACT_27',
                        'url':'https://docs.example.org/public','quote':'SYNTHETIC_FACT_27'}] if prior else []}
            if active['case'] in TARGETED_CASES:
                metadata['content_check']={'novelty':'new','basis':'显式合成算式',
                    'unsupported_self_episode':False,'claims':[],
                    'calculations':[{'expression':'12/4','result':'3' if revising else '5','unit':'拍'}]}
            content = '[PHASE3_PROACTIVE:' + decision + ']\n[PHASE3_TOPIC:' + json.dumps(metadata, ensure_ascii=False) + ']\n'
            if decision == 'send':
                content += ('SYNTHETIC_FACT_27 是刚查到的合成资料。' if prior else '我喜欢安静的纯音乐。')
                content += ('\n<<PRIVATE_COMPANION_SPLIT>>\n倒觉得这种留白有点调皮。' if active['case']=='interest'
                            else '\n<<PRIVATE_COMPANION_SPLIT>>\n省略号简直像标点里的摸鱼冠军。')
                if active['case'] == 'partial_unknown':
                    content += '\n<<PRIVATE_COMPANION_SPLIT>>\n我喜欢刺激的射击游戏。'
                if active['case'] in {'long_plain','long_marked','long_blocked','long_relay_blocked'}:
                    content='\n'.join(content.splitlines()[:2])+'\n'+long_native_body(active['case']=='long_marked')
                    if active['case']=='long_blocked':
                        content='\n'.join(content.splitlines()[:2])+'\nAPI connection error\n'+long_native_body()
                    elif active['case']=='long_relay_blocked':
                        content+='\n我现在去跟合成乙说一声。'
                if active['case'] in {'marker_motive','marker_long_motive','marker_tail_blocked'}:
                    content='\n'.join(content.splitlines()[:2])+'\n'+local_review_body(active['case'])
                if active['case'] in {'hypothesis_consequence','recent_action_rejected','mixed_hypothesis_rejected'}:
                    content='\n'.join(content.splitlines()[:2])+'\n'+final_gate_body(active['case'])
                if active['case']=='direct_no_history':
                    content='\n'.join(content.splitlines()[:2])+'\n我喜欢轻盈的爵士音乐。\n<<PRIVATE_COMPANION_SPLIT>>\n节奏轻轻错开一点就挺有趣。'
                if active['case']=='direct_with_history':
                    content='\n'.join(content.splitlines()[:2])+'\n同样是留白，我更看重旋律停下来的位置。\n<<PRIVATE_COMPANION_SPLIT>>\n短暂安静也能改变下一拍的重量。'
                if active['case'] in TARGETED_CASES:
                    result='3' if revising else '5'
                    content='\n'.join(content.splitlines()[:2])+'\n假设一段旋律总共12拍，平分成4段，每段'+result+'拍。\n<<PRIVATE_COMPANION_SPLIT>>\n每段长度相同，重音位置却可以不同。'
                    if active['case']=='targeted_scope_violation' and revising:
                        content+='我昨天玩了合成游戏。'
                if not revising and active['case']=='legacy_review_comparison':
                    content='\n'.join(content.splitlines()[:2])+'\n未发送的合成草稿。'
            if revising and active['case'] in SOURCE_CASES:
                # Exercise metadata-only repair. The production controller must
                # bind the original unsent body; no synthetic response repeats it.
                content='\n'.join(content.splitlines()[:2])
        if active['case'] == 'interrupted':
            runtime.guard.store.claim('offline','offline:FriendMessage:10001',
                'autonomy-interrupt-1','10001','合成插话，不发送真实消息。',[])
        message = {'role': 'assistant', 'content': content}
        wire[-1]['visible_output']=content
        if calls: message['tool_calls'] = calls
        return web.json_response({'id': 'native-autonomy-synthetic', 'object': 'chat.completion',
            'created': 1, 'model': 'synthetic-native-loop', 'choices': [{'index': 0, 'message': message,
            'finish_reason': 'tool_calls' if calls else 'stop'}],
            'usage': {'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30}})

    async def search(request):
        tool_wire.append({'case': active['case'], 'kind': 'search', 'body': await request.json()})
        return web.json_response({'results': [{'title': 'Synthetic public source',
            'url': 'https://docs.example.org/public', 'summary': 'SYNTHETIC_FACT_27'}]})

    async def fetch(request):
        tool_wire.append({'case': active['case'], 'kind': 'fetch', 'body': await request.json()})
        return web.json_response({'title': 'Synthetic public source', 'content': 'SYNTHETIC_FACT_27 full page.'})

    app = web.Application()
    app.router.add_post('/v1/chat/completions', complete)
    app.router.add_post('/v1/tools/search', search)
    app.router.add_post('/v1/tools/fetch', fetch)
    server = web.AppRunner(app); await server.setup()
    site = web.TCPSite(server, '127.0.0.1', 0); await site.start()
    base = 'http://127.0.0.1:' + str(site._server.sockets[0].getsockname()[1])
    old_base = provider.client.base_url
    provider.client.base_url = base + '/v1/'
    class LoopbackTools:
        def __init__(self, **kw): self.session = RealClientSession(trust_env=False)
        async def __aenter__(self): return self
        async def __aexit__(self, *args): await self.session.close()
        def post(self, url, **kw):
            if url not in ('https://api.deepseek.com/v1/tools/search', 'https://api.deepseek.com/v1/tools/fetch'):
                raise PermissionError('synthetic_tool_destination_required')
            return self.session.post(base + url.removeprefix('https://api.deepseek.com'), **kw)
    cases = {}
    try:
        with patch.object(web_search_tools.aiohttp, 'ClientSession', LoopbackTools):
            for case in ('direct_no_history','direct_with_history','targeted_calculation',
                         'targeted_scope_violation','legacy_review_comparison',
                         'passive_supplement', 'passive_supplement_too_soon',
                         'passive_supplement_stale', 'ordinary_recent',
                         'send', 'skip', 'defer', 'search', 'cooldown_search',
                         'interest', 'partial_unknown', 'interrupted',
                         'long_plain', 'long_marked', 'long_blocked', 'long_relay_blocked',
                         'marker_motive','marker_long_motive','marker_tail_blocked',
                         'hypothesis_consequence','recent_action_rejected','mixed_hypothesis_rejected'):
                active['case'] = case
                before = len(wire)
                cases[case] = await _run_case(core, runtime, case)
                calls = wire[before:]
                cases[case]['actual_http_requests'] = len(calls)
                cases[case]['http_tool_attempts'] = sum(r['case'] == case for r in tool_wire)
                final = json.dumps(calls, ensure_ascii=False)
                source_cases = SOURCE_CASES
                confirmed_history_before = cases[case]['confirmed_active_history_before_generation']
                final_check_expected = case=='legacy_review_comparison'
                final_body_checks = [row for row in calls if row['request']['messages']
                    and 'PHASE3_FINAL_BODY_CHECK_V1' in str(row['request']['messages'][-1].get('content',''))]
                final_context_checks = {}
                if final_check_expected:
                    from integrations.phase3_runtime.autonomy_contract import parse
                    final_messages = calls[-1]['request']['messages'] if calls else []
                    previous_messages = calls[-2]['request']['messages'] if len(calls)>1 else []
                    source_messages = [m for m in final_messages if m.get('role')=='tool']
                    actual_draft = parse(final_messages[-2].get('content','')).get('text','') if len(final_messages)>1 else ''
                    final_instruction = final_messages[-1].get('content','') if final_messages else ''
                    final_context_checks = {
                        'exactly_one_final_body_check_after_successful_review': len(final_body_checks)==1,
                        'final_check_preserves_original_agent_context': final_messages[:-2]==previous_messages,
                        'final_check_has_original_search_and_full_fetch_body': (len(source_messages)==2
                            and 'SYNTHETIC_FACT_27 full page.' in json.dumps(source_messages,ensure_ascii=False)
                            and source_messages==[m for m in previous_messages if m.get('role')=='tool'])
                            if case in source_cases else not source_messages,
                        'final_check_quotes_actual_reviewed_visible_body': bool(actual_draft)
                            and actual_draft in final_instruction,
                        'final_check_has_actual_confirmed_history': json.dumps(confirmed_history_before,ensure_ascii=False)
                            in final_instruction,
                        'final_check_keeps_same_provider_and_tool_set': len(calls)>1
                            and calls[-1]['request'].get('model')==calls[-2]['request'].get('model')
                            and calls[-1]['request'].get('tools')==calls[-2]['request'].get('tools'),
                    }
                cases[case]['http_checks'] = {
                    'autonomy_contract': 'PHASE3_AUTONOMY' in final,
                    'has_current_persona': 'QQ_COMPANION_PERSONA_V0_4' in final,
                    'no_old_decision_contract': 'PHASE3_PROACTIVE_DECISION_V1' not in final,
                    'no_fixed_defer_240': 'defer:240' not in final,
                    'bounded_same_agent_revision': len(calls) == expected_model_requests(case),
                    'no_blanket_final_check_in_targeted_default': not final_body_checks
                        if not final_check_expected else True,
                    'legacy_revision_has_original_draft_context': (len(calls)>1 and '未发送的合成草稿。' in
                        json.dumps(calls[-1]['request']['messages'],ensure_ascii=False))
                        if case=='legacy_review_comparison' else True,
                    'direct_history_does_not_force_rewrite': len(calls)==1 and bool(confirmed_history_before)
                        if case=='direct_with_history' else True,
                    'targeted_one_revision_for_identified_problem': sum('PHASE3_TARGETED_REVISION_V1' in
                        str(row['request']['messages'][-1].get('content','')) for row in calls)==1
                        if case in TARGETED_CASES or case in SOURCE_CASES else True,
                    'same_agent_preserves_original_context_for_targeted_repair': calls[-1]['request']['messages'][:-2]==calls[-2]['request']['messages']
                        if case in TARGETED_CASES or case in SOURCE_CASES else True,
                    'source_revision_returns_only_control_and_metadata': len(calls[-1].get('visible_output','').splitlines())==2
                        if case in SOURCE_CASES else True,
                    'tool_result_backfilled': case not in {'search', 'cooldown_search', 'interest'} or 'SYNTHETIC_FACT_27' in final,
                    'passive_supplement_context': ('single_supplement' in final and '合成父轮确认正文' in final)
                        if case == 'passive_supplement' else True,
                    'no_legacy_native_topic_requirement': not any(s in final for s in
                        ('从线索中只选一个此刻最真实', '一两句即可', '最终文本会直接成为聊天窗口里的下一句话')),
                    **final_context_checks,
                }
                if case in {'passive_supplement_too_soon','passive_supplement_stale','ordinary_recent'}:
                    cases[case]['http_checks'] = {'no_model_before_parent_and_idle_admission': not calls}
                cases[case]['pass'] = cases[case]['pass'] and all(cases[case]['http_checks'].values())
        return {'pass': all(c['pass'] for c in cases.values()), 'cases': cases,
            'level': 'T2_FULL_LOADED_NATIVE_SYNTHETIC_HTTP_ONEBOT',
            'real_deepseek': False, 'real_qq': False, 'production_changed': False,
            'model_http_requests': len(wire), 'tool_http_attempts': len(tool_wire),
            'wire': wire, 'tool_wire': tool_wire}
    finally:
        provider.client.base_url = old_base
        await server.cleanup()


async def _run_case(core, runtime, case):
    from astrbot.api.message_components import Plain
    from astrbot.api.platform import AstrBotMessage, MessageMember, MessageType
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
    from integrations.phase3_runtime.proactive import ProactiveGate, native_proactive_config, authorize_native_target
    from integrations.phase3_runtime.autonomy import AutonomousProactiveBridge
    from integrations.phase3_runtime.autonomy_state import AutonomyState
    from integrations.phase3_runtime.settings import AUTONOMY_DEFAULTS, FREQUENCY_PROACTIVE_POLICY
    from integrations.phase3_runtime.autonomy_contract import content_identity
    from integrations.phase3_memory.self_preferences import NativeSelfPreferences
    from integrations.phase3_search.proactive import ProactiveSearchContext, ProactiveSearchRuntime
    from integrations.phase3_search.policy import PublicPolicy
    meta = runtime._star('astrbot_plugin_private_companion'); private = meta.star_cls
    package = meta.module_path.rsplit('.', 1)[0]
    message = importlib.import_module(package + '.proactive_message')
    bootstrap = importlib.import_module(package + '.plugin_bootstrap')
    original_data, original_config = deepcopy(private.data), private.config
    original_environment_now = private._environment_now
    enabled = runtime.guard.phase3_proactive_enabled
    now = datetime.fromisoformat('2026-10-27T12:00:00+08:00'); clock = lambda: now.timestamp()
    session = 'offline:FriendMessage:10001'
    old_conversation = await core.conversation_manager.get_curr_conversation_id(session)
    conversation = None
    temp = tempfile.TemporaryDirectory(prefix='phase3-autonomy-native-')
    folder = Path(temp.name)
    gate = ProactiveGate(folder / 'gate.sqlite3', allowed_session=session, enabled=True,
        clock=clock, **FREQUENCY_PROACTIVE_POLICY)
    state = AutonomyState(gate.db, clock=clock, audit_dir=folder / 'audit')
    preferences = NativeSelfPreferences(private, runtime.guard, session, clock=clock)
    async def public_dns(host): return ('1.1.1.1',)
    def search_factory(scope, state, admit):
        return ProactiveSearchRuntime(scope=ProactiveSearchContext(session, scope['opportunity_id'],
            scope['evaluation_id'], scope['started_at'], scope['deadline'], scope['input_version']),
            state=state, policy=PublicPolicy(scope='general_public', resolver=public_dns),
            allowed_session=session, config_context=NS(get_config=lambda **kw: {'provider_settings': {
                'websearch_deepseek_key': ['synthetic-not-a-real-key']}}),
            admit=lambda _: admit(), key_ready=True, enabled=True, clock=clock)
    bridge = AutonomousProactiveBridge(private, runtime.guard, gate, message._ProactiveSendOutcome,
        autonomy_state=state, autonomy_settings=dict(AUTONOMY_DEFAULTS),
        self_preferences=preferences, search_factory=search_factory)
    patches = ExitStack()
    if case=='legacy_review_comparison':
        # Explicit test-only policy selection. The same controller executes;
        # production/default cases keep targeted_once with no blanket rewrite.
        from integrations.phase3_runtime import autonomy_content
        controller=autonomy_content.ContentRevision
        patches.enter_context(patch.object(autonomy_content,'ContentRevision',
            lambda state,scope:controller(state,scope,policy='legacy_two_pass')))
    # Receipt timestamps and the native scheduler share this fixture clock.
    # Transport confirmation still comes from the real loopback HTTP adapter.
    guard_store_module = importlib.import_module(type(runtime.guard.store).__module__)
    patches.enter_context(patch.object(guard_store_module, 'now_shanghai', lambda: now.isoformat()))
    for name, module in tuple(sys.modules.items()):
        if name.startswith(package + '.'):
            if hasattr(module, '_now_ts'): patches.enter_context(patch.object(module, '_now_ts', clock))
            if hasattr(module, '_today_key'): patches.enter_context(patch.object(module, '_today_key', lambda: now.date().isoformat()))
    attached = False
    original_transport = runtime.guard.context.send_message
    transport_calls = 0
    async def uncertain_second(umo, chain, *args, **kwargs):
        nonlocal transport_calls
        transport_calls += 1
        if transport_calls == 2: raise TimeoutError('synthetic_uncertain_delivery')
        return await original_transport(umo, chain, *args, **kwargs)
    try:
        config = native_proactive_config(dict(original_config), '10001', enabled=True, policy=FREQUENCY_PROACTIVE_POLICY)
        for key, value in {'LLM_PROVIDER_ID': 'mock/phase1'}.items():
            config.setdefault('proactive_generation_config', {})[key] = value; config[key] = value
        config.setdefault('basic_config', {})['plugin_specific_persona_id'] = 'qq_companion'
        config['plugin_specific_persona_id'] = 'qq_companion'
        private.config = config; bootstrap.initialize_plugin_config(private, config)
        private._environment_now = lambda: now
        msg = AstrBotMessage(); msg.type = MessageType.FRIEND_MESSAGE
        msg.message_id = 'native-autonomy-route-' + case; msg.self_id = '90000'
        msg.sender = MessageMember('10001', 'Synthetic'); msg.message = [Plain('合成身份绑定')]
        msg.message_str = '合成身份绑定'; msg.session_id = '10001'; msg.raw_message = {}
        platform = core.star_context.get_platform_inst('offline')
        event = AiocqhttpMessageEvent(msg.message_str, msg, platform.meta(), '10001', platform.bot)
        uid = private._private_user_id_for_event(event, '10001'); user = private._get_user(uid)
        private._stamp_private_event_identity(user, event, '10001'); private._note_private_user_umo(uid, user, session)
        user.update(umo=session, last_private_seen=0, last_activity_at=0, last_user_message_at=0,
            last_reply_at=0, last_private_activity_at=0, last_private_reply_at=0, next_proactive_at=0,
            ignored_streak=3, unanswered_proactive_count=3, bot_self_preferences=[], proactive_impulses=[],
            recent_proactive_topics=[])
        authorize_native_target(private, session, approved=True, daily_limit=12)
        preferences.attach(); bridge.attach(); attached = True; bridge.activate_native_config()
        runtime.guard.phase3_proactive_enabled = True
        conversation = await core.conversation_manager.new_conversation(session, persona_id='qq_companion',
            title='Synthetic full autonomy ' + case, content=[])
        parent_turn = None
        supplement_checks = {}
        if case.startswith('passive_supplement'):
            # Real Guard passive delivery to the isolated OneBot peer. Advance
            # only the disposable evidence clock; never fabricate a receipt.
            parent_at = now - timedelta(minutes=2 if case=='passive_supplement_too_soon' else 10)
            key = ('offline', session, msg.message_id)
            runtime.guard.store.claim(*key, '10001', msg.message_str, [])
            turn, _ = runtime.guard.turns.observe(key, msg.message_str, groupable=False)
            parent_turn = turn.turn_id
            event.set_extra('phase3_turn_id', parent_turn)
            event.set_extra('phase3_native_send', event.send)
            event.set_result(event.chain_result([Plain('合成父轮确认正文')]))
            await runtime.guard.dispatch_text(event)
            receipt = runtime.guard.store.bubble_result(parent_turn, 'normal')
            if receipt['state'] != 'sent':
                raise AssertionError('synthetic_passive_parent_not_confirmed')
            with runtime.guard.store.db:
                runtime.guard.store.db.execute('UPDATE deliveries SET finished_at=? WHERE turn_id=?',
                    (parent_at.isoformat(), parent_turn))
                runtime.guard.store.db.execute('UPDATE inbound SET received_at=? WHERE message_id=?',
                    ((parent_at-timedelta(seconds=1)).isoformat(), msg.message_id))
            for field in ('last_private_seen','last_activity_at','last_user_message_at',
                          'last_reply_at','last_private_activity_at','last_private_reply_at'):
                user[field] = parent_at.timestamp()
            gate.db.execute("INSERT INTO proactive_attempts(candidate,day,attempted,state) VALUES ('recent','2026-10-27',?,'sent')", (clock()-60,))
            gate.db.commit()
            user['last_sent'] = clock()-60
            supplement_checks['ordinary_send_in_cooldown'] = gate.permit(session,
                candidate_id='ordinary-new-topic', source='want_to_chat') == 'budget_or_cooldown'
            accepted = await bridge.offer_supplement()
            supplement_checks['same_parent_offered_once'] = not await bridge.offer_supplement()
        candidate = {'id': 'autonomy-' + case, 'source': 'random', 'reason': 'activity_share', 'action': 'message',
            'topic': '开放选题机会', 'motive': '想随意聊两句', 'scheduled_ts': clock() - 1,
            'window_start_at': clock() - 10, 'preferred_ts': clock() - 1,
            'best_until_at': clock() + 3600, 'expire_at': clock() + 7200,
            'window_timezone': 'Asia/Shanghai', 'origin_event_id': 'autonomy-' + case, 'context': {}}
        if not case.startswith('passive_supplement'):
            accepted = private._offer_proactive_candidate(uid, user, candidate)
        native = private._planned_proactive_impulse(user)
        if case.startswith('passive_supplement') and accepted:
            source = (native or {}).get('context', {}).get('phase3_supplement', {})
            supplement_checks['native_pool_preserved_parent'] = (
                source.get('parent_attempt') == 'turn:'+parent_turn and
                source.get('parent_kind') == 'passive' and
                source.get('confirmed_text') == '合成父轮确认正文')
        if case=='passive_supplement_stale':
            runtime.guard.store.claim('offline',session,'new-after-supplement-parent','10001','合成新输入',[])
        if case=='ordinary_recent':
            for field in ('last_private_seen','last_activity_at','last_user_message_at',
                          'last_reply_at','last_private_activity_at','last_private_reply_at'):
                user[field] = clock()-600
        if case == 'cooldown_search':
            gate.db.execute("INSERT INTO proactive_attempts(candidate,day,attempted,state) VALUES ('recent','2026-10-27',?,'sent')", (clock()-60,)); gate.db.commit()
            user['last_sent'] = clock()-60
        preflight = list(private._should_send(user))
        recent_chat_reason = private._route_recent_chat_guard_reason(user, now=clock(),
            planned_reason=user.get('planned_proactive_reason',''), due_timer_active=False,
            is_troubleshooting=False)
        before = runtime.guard.store.db.execute("SELECT max(rowid) FROM deliveries").fetchone()[0] or 0
        # The loaded probe intentionally keeps earlier confirmed Guard rows.
        # Keep confirmed history visible but do not infer extra default requests
        # from its presence. Only the explicit legacy comparison does so.
        confirmed_history_before = bridge.recent_confirmed(session)[-8:]
        if case == 'partial_unknown': runtime.guard.context.send_message = uncertain_second
        await private._tick_user(uid, user)
        await preferences.drain(limit=32)
        rows = [dict(row) for row in runtime.guard.store.db.execute(
            "SELECT state,kind,text,turn_id,delivery_id FROM deliveries WHERE rowid>? ORDER BY rowid", (before,))]
        bubble_plans = [runtime.guard.store.bubble_result(turn,'proactive') for turn in dict.fromkeys(r['turn_id'] for r in rows)]
        def db_rows(query):
            cursor = gate.db.execute(query)
            fields = [column[0] for column in cursor.description]
            return [dict(zip(fields, row)) for row in cursor]
        evaluations = db_rows('SELECT * FROM autonomy_evaluations')
        calls = db_rows('SELECT * FROM autonomy_calls')
        generations = [r[0] for r in gate.db.execute('SELECT state FROM proactive_generations')]
        attempts = [dict(zip(('state','sent_count','part_count'), row)) for row in gate.db.execute(
            "SELECT state,sent_count,part_count FROM proactive_attempts WHERE candidate!='recent'")]
        current = private._get_user(uid)
        conv = await core.conversation_manager.get_conversation(session, conversation)
        history = json.loads(conv.history) if isinstance(conv.history, str) else conv.history
        actual_topic = gate.db.execute("SELECT topic_id FROM proactive_attempts WHERE candidate NOT LIKE '%:interest' AND candidate!='recent'").fetchone()
        expected_send = case in {'send', 'search', 'interest', 'partial_unknown', 'passive_supplement',
                                'long_plain','long_marked','marker_motive','marker_long_motive','hypothesis_consequence',
                                'direct_no_history','direct_with_history','targeted_calculation','legacy_review_comparison'}
        expected_attempts = 2 if case=='interest' else int(expected_send)
        if case == 'partial_unknown':
            send_check = ([r['state'] for r in rows] == ['sent','unknown']
                          and [p['state'] for p in bubble_plans[0]['parts']] == ['sent','unknown','cancelled'])
        elif case == 'interest':
            send_check = len(rows)==3 and all(r['state']=='sent' for r in rows)
        elif case in {'marker_motive','marker_long_motive'}:
            send_check=len(rows)==3 and all(r['state']=='sent' for r in rows)
        elif case=='long_plain':
            send_check=len(rows)==1 and rows[0]['state']=='sent'
        else:
            send_check = (len(rows)==2 and all(r['state']=='sent' for r in rows)) if expected_send else not rows
        checks = {'candidate_accepted': bool(accepted), 'native_preflight_allowed': preflight[0],
            'one_evaluation': len(evaluations) == 1,
            'sending_counted_per_plan': len(attempts) == expected_attempts,
            'confirmed_bubbles_or_no_send': send_check,
            'model_request_metered': len([c for c in calls if c['kind']=='model']) == expected_model_requests(case),
            'preferences_only_after_sent': bool(preferences.current()) if case in {'send','partial_unknown','passive_supplement',
                'long_plain','long_marked','direct_no_history','legacy_review_comparison'} else not preferences.current(),
            'history_only_if_sent': bool(history) == expected_send,
            'history_has_no_fabricated_user': all(row.get('role') == 'assistant' for row in history),
            'history_has_no_unconfirmed_draft': '未发送的合成草稿。' not in json.dumps(history,ensure_ascii=False),
            'cooldown_saved_pocket': bool(state.list_pocket(session)) if case=='cooldown_search' else True,
            'unknown_tail_not_history_or_preference': (all(s not in json.dumps(history,ensure_ascii=False)
                for s in ('摸鱼冠军','射击游戏')) and [p['category'] for p in preferences.current()]==['music'])
                if case=='partial_unknown' else True,
            'interruption_preserved': evaluations[0]['effective_state']=='interrupted' if case=='interrupted' and evaluations else True,
            'selected_topic_identity_matches_actual_attempt': (
                actual_topic and actual_topic[0]
                == content_identity('合成标点新角度 '+case)) if expected_send else True,
            'selected_topic_persisted_on_native_candidate': bool((native or {}).get('context',{}).get('phase3_autonomy'))
                if case!='interrupted' else True,
        }
        if case in {'long_plain','long_marked'}:
            sent='\n'.join(row['text'] for row in rows)
            checks['complete_body_beyond_260_and_four_sentences']=len(sent)>300 and all(
                token in sent for token in ('合成甲结尾','合成乙结尾','合成丙结尾','合成丁结尾','合成戊结尾'))
            preference='我偏爱层次分明的音乐' if case=='long_marked' else '我偏爱旋律清楚的音乐'
            checks['last_preference_is_delivered_and_extracted']=preference in sent and bool(preferences.current())
            checks['native_cleanup_only_confirmed_history']=len(history)==1 and history[0].get('content')==sent
            checks['no_cutoff_ellipsis']='…' not in sent
        elif case in {'long_blocked','long_relay_blocked'}:
            checks['provider_error_native_rejection_preserved']=not rows and not history and not preferences.current()
        if case in {'marker_motive','marker_long_motive'}:
            sent='\n'.join(row['text'] for row in rows)
            checks['no_residual_marker']='PRIVATE_COMPANI' not in sent
            checks['native_motive_deleted_without_recovery']='我想了半天' not in sent
            checks['same_native_bubble_count']=len(rows)==3 and len(bubble_plans[0]['parts'])==3
            checks['ledger_exactly_projects_confirmed_history']=len(history)==1 and history[0].get('content')==sent
            if case=='marker_motive':
                checks['ordinary_content_survives']=all(x in sent for x in
                    ('模块化积木看起来挺有趣','连接方式比外形更值得琢磨','每块积木之间的约定才是关键'))
            else:
                checks['complete_long_local_rewrite']=len(sent)>650 and all(
                    '第'+str(n)+'段的合成观点' in sent for n in range(1,4)) and sent.count('末尾完整')==3
        elif case=='marker_tail_blocked':
            checks['native_long_tail_hard_rejection_preserved']=not rows and not attempts and not history
        if case in {'recent_action_rejected','mixed_hypothesis_rejected'}:
            checks['native_rejection_keeps_model_send']=(evaluations[0]['raw_control']=='[PHASE3_PROACTIVE:send]'
                and evaluations[0]['effective_state']=='native_rejected'
                and not evaluations[0]['delivery_attempt']
                and '无真实来源' in evaluations[0]['reason'])
            checks['no_fake_delivery_or_history']=not rows and not attempts and not history
        elif case=='hypothesis_consequence':
            sent='\n'.join(row['text'] for row in rows)
            checks['hypothetical_predicate_retained']='可能聊到一半才发现' in sent
            checks['native_gate_allow_is_not_fake_fact_source']=not any(c['kind'] in {'search','fetch'} for c in calls)
            checks['confirmed_history_matches_payload']=len(history)==1 and history[0].get('content')==sent
        if case=='direct_no_history':
            checks['fresh_default_without_history_is_direct']=not confirmed_history_before and len(calls)==1
        elif case=='direct_with_history':
            checks['confirmed_history_does_not_force_rewrite']=bool(confirmed_history_before) and len(calls)==1
        elif case=='targeted_calculation':
            sent='\n'.join(row['text'] for row in rows)
            checks['only_wrong_numeric_span_corrected']='每段3拍' in sent and '每段5拍' not in sent
            checks['unchanged_body_survives_local_repair']='每段长度相同，重音位置却可以不同' in sent
            checks['one_targeted_repair_only']=len(calls)==2
        elif case=='targeted_scope_violation':
            checks['new_unrelated_episode_rejected_after_one_repair']=(len(calls)==2
                and evaluations[0]['effective_state']=='content_rejected'
                and evaluations[0]['reason'] in {'revision_scope_violation','revision_calculation_body_not_corrected'})
            checks['no_fake_delivery_or_history']=not rows and not attempts and not history
        if case == 'passive_supplement':
            supplemental = db_rows('SELECT * FROM autonomy_supplements')
            supplement_checks['once_counted_passive_parent'] = (len(supplemental)==1 and
                supplemental[0]['parent_kind']=='passive' and
                supplemental[0]['parent_attempt']=='turn:'+parent_turn and len(attempts)==1)
            # Age the real confirmed supplement into the consideration window.
            # Its parentage still forbids a supplement of a supplement.
            with runtime.guard.store.db:
                for row in rows:
                    runtime.guard.store.db.execute('UPDATE deliveries SET finished_at=? WHERE delivery_id=?',
                        ((now-timedelta(minutes=10)).isoformat(), row['delivery_id']))
            supplement_checks['no_recursive_offer'] = not await bridge.offer_supplement()
            supplement_checks['no_second_evaluation'] = gate.db.execute(
                'SELECT count(*) FROM autonomy_evaluations').fetchone()[0] == 1
            checks.update(supplement_checks)
            checks['ordinary_idle_remains_45_minutes'] = private._effective_user_idle_minutes(user)==45
        if case in {'passive_supplement_too_soon','passive_supplement_stale','ordinary_recent'}:
            checks = {'native_preflight_or_route_denied': not preflight[0] or bool(recent_chat_reason),
                'no_evaluation_or_model_request': not evaluations and not calls,
                'no_send_or_history': not rows and not attempts and not history,
                'ordinary_idle_remains_45_minutes': private._effective_user_idle_minutes(user)==45}
            if case=='passive_supplement_too_soon':
                checks['native_offer_rejected_before_five_minutes'] = not accepted
            elif case=='passive_supplement_stale':
                checks['valid_offer_cancelled_by_new_epoch'] = accepted and preflight[1]=='supplement_no_longer_current'
            else:
                checks['ordinary_recent_input_has_no_supplement_exemption'] = bool(accepted)
                checks['ordinary_recent_route_preserves_45_minute_gate'] = '35 分钟' in recent_chat_reason
        return {'pass': all(checks.values()), 'checks': checks, 'preflight': preflight,
            'confirmed_active_history_before_generation':confirmed_history_before,
            'native_recent_chat_reason':recent_chat_reason,
            'candidate': native and native.get('id'), 'generations': generations,
            'evaluations': evaluations, 'calls': calls, 'attempts': attempts, 'deliveries': rows, 'bubble_plans': bubble_plans,
            'history': history, 'self_preferences': preferences.current(), 'bridge_metrics': dict(bridge.metrics),
            'native_note': current.get('planned_proactive_delivery_note'),
            'native_render_failure': current.get('_proactive_render_failure_stage')}
    finally:
        if attached: bridge.detach()
        runtime.guard.context.send_message = original_transport
        preferences.detach(); gate.close(); patches.close()
        private._environment_now = original_environment_now; runtime.guard.phase3_proactive_enabled = enabled
        private.config = original_config; bootstrap.initialize_plugin_config(private, original_config)
        private.data.clear(); private.data.update(original_data)
        if conversation:
            if old_conversation: await core.conversation_manager.switch_conversation(session, old_conversation)
            await core.conversation_manager.delete_conversation(session, conversation)
        temp.cleanup()
