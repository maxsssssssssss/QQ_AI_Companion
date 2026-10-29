"""Joint native/real-model probe; imported only by the disposable test host.

Offer/pool/tick, prompt assembly, Agent, Guard, native history and preference
persistence are genuine. A local offline OneBot peer acknowledges messages;
none are QQ account deliveries. The synthetic backend is harness validation.
"""
import asyncio
import ast
import hashlib
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timedelta
import importlib
import importlib.util
import json
import re
from pathlib import Path
import sys
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
SESSION='offline:FriendMessage:10001'
PROVIDER_ID='joint-isolated/flash'
STAGES={
 'first':(
  ('open_empty','',False),
  ('controlled_explicit_preference','聊一点当前的音乐偏好：请自主选一种此刻偏爱的音乐，正文第一句以“我喜欢…音乐”或“我偏爱…音乐”起始，省略号处由你自主填写具体类别，必须包含“音乐”这个类别词，再说明自己的审美理由。音乐类别和理由由你决定，不预设答案，不假装实际听过、去过演出或有现实经历。这是你当下的审美选择，不是用户资料，也不必假称早已保存或过去说过。',False),
  ('cooldown_research','隔离公开研究机会：对章鱼皮肤是否能感光产生好奇。请实际Search找到一个研究或大学来源，随后Fetch其中一个完整原始URL，依据真实返回保存进度。受控研究题目备选是PubMed条目40511715（https://pubmed.ncbi.nlm.nih.gov/40511715/），此地址仅提供检索线索，不是已读取的事实；先Search核实来源，取得真实来源URL后再Fetch。也可选择搜索实际发现的大学PDF。总共仍最多3次Search/Fetch；标题页不算正文，失败后按剩余额度处理，不能编造成功。当前发送冷却，不要试图给用户发送或预告。',True)),
 'resume':(
  ('resume_research','这是重启后次日的正常联系机会。请检查待分享口袋中的实际研究，选一个未完成问题，读取其中已有来源核实后再决定是否分享或继续保存。不要自动重发旧稿，不把未分享内容说成已经讲过。',False),
  ('recall_preference','隔离后续联系机会：可从你已实际表达的自身偏好中，选一条形成一个新的想法，不重复原句；没有可召回偏好就如实不要假装有。不要编造游玩经历。',False)),
}

INTEREST_RESEARCH_STAGES={
 'first':(STAGES['first'][1],),
 'resume':(('interest_research',
  '本次只验证已批准的主动研究链路，不评价自然度：当前允许发送，先使用现有 PHASE3_INTEREST 协议表达一个关于章鱼皮肤感光的具体兴趣。本轮受控研究对象是 PubMed 40511715（https://pubmed.ncbi.nlm.nih.gov/40511715/），这是待核实线索，不是已经读取的事实。请实际 Search，用 site:pubmed.ncbi.nlm.nih.gov/40511715 检索确认该页的真实原始 URL，然后实际 Fetch 这个 PubMed 页面；本次核对该页的正文读取链，不转向 Wiley 或其他站点全文。取得可用正文后，依据实际来源形成独立结果批次，内容由你决定；没有可用正文就如实保存进度，不编造成功。兴趣表达和结果仍受现有 Guard 与各项额度保护，Search/Fetch 合计最多三次。',False),),
}


def stage_cases(spec):
    return (INTEREST_RESEARCH_STAGES if spec.get('scenario','full')=='interest-research' else STAGES)[spec['stage']]


def load_real_helper():
    spec=importlib.util.spec_from_file_location('joint_real_safety',ROOT/'scripts/phase3-test-autonomy-real.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


class FixtureClock:
    def __init__(self, base):self.move(base)
    def move(self, value):self.base=float(value);self.anchor=time.monotonic()
    def __call__(self):return self.base+time.monotonic()-self.anchor
    def datetime(self):return datetime.fromtimestamp(self(),datetime.fromisoformat('2000-01-01T00:00:00+08:00').tzinfo)


def guard_receipt_clock_patch(store, clock):
    """Align only this disposable host's loaded Guard receipt clock.

    Native scheduling and Gate already use FixtureClock. Receipt timestamps
    must share that clock after the next-day restore; global time/monotonic and
    production Guard source are deliberately untouched.
    """
    module=sys.modules[type(store).__module__]
    return patch.object(module,'now_shanghai',lambda:clock.datetime().isoformat(timespec='milliseconds'))


def visible_tail_preserved(final_text,confirmed_text):
    def substantive(text):
        return re.sub(r'[^\w]+','',str(text).replace('<<PRIVATE_COMPANION_SPLIT>>',''))
    tail=substantive(final_text)[-40:]
    return not tail or tail in substantive(confirmed_text)


def actual_message_text(requests):
    """Decode actual contents, never search JSON escaping as model-visible text."""
    return '\n'.join(m.get('content','') if isinstance(m.get('content',''),str)
        else json.dumps(m.get('content'),ensure_ascii=False)
        for request in requests for m in request.get('messages',[]))


def confirmed_history_present(prior, requests):
    visible=''.join(actual_message_text(requests).split())
    # Native history may normalize whitespace; all substantive text must remain.
    return all(''.join(str(value).split()) in visible for value in prior)


def send_outcome_valid(decision, evaluation, cooldown, deliveries, pocket, calls):
    if not evaluation:return False
    if decision!='send':return True
    if not cooldown:return evaluation.get('effective_state')=='sent'
    return (evaluation.get('effective_state')=='ready_unshared' and not deliveries
        and bool(pocket) and any(call.get('kind')=='model' for call in calls)
        and sum(call.get('kind') in ('search','fetch') for call in calls)<=3)


def native_cleanup_replayer(source):
    """Execute only the three pinned pure methods; no plugin imports or I/O."""
    path=Path(source);raw=path.read_bytes();tree=ast.parse(raw)
    wanted={'_repair_proactive_reply_air_locally','_ensure_chat_sentence_punctuation',
        '_split_proactive_sentence_units'}
    methods=[node for node in ast.walk(tree) if isinstance(node,ast.FunctionDef) and node.name in wanted]
    if {node.name for node in methods}!=wanted or len(methods)!=3:
        raise ValueError('pinned_native_cleanup_methods_missing')
    namespace={'re':re};module=ast.Module(body=methods,type_ignores=[])
    exec(compile(ast.fix_missing_locations(module),str(path),'exec'),namespace)
    cls=type('PinnedPureCleanupReplay',(),{name:namespace[name] for name in wanted})
    return cls()._repair_proactive_reply_air_locally,hashlib.sha256(raw).hexdigest()


def native_cleanup_records_from_log(text):
    # Existing evidence logs are bounded. Truncated log strings cannot match the
    # actual model body or exact replay and therefore cannot excuse missing text.
    pattern=r'主动消息疑似回复空气,已用本地轻量规则修正: flags=([^\s]+) before=(.*?) after=([^\r\n]*)'
    return [{'flags':flags.split(','),'before':before,'after':after,'evidence':'native_log'}
        for flags,before,after in re.findall(pattern,re.sub(r'\x1b\[[0-9;]*m','',text))]


def native_cleanup_proof(final_text,confirmed_text,records,replay,source_sha=''):
    """Allow only recorded inverts_initiator repair exactly reproduced by native code.

    Body comparison separately normalizes presentation punctuation/whitespace;
    this proof never claims raw byte equality or excuses arbitrary tail removal.
    """
    norm=lambda value:re.sub(r'[^\w]+','',str(value).replace('<<PRIVATE_COMPANION_SPLIT>>',''))
    expected=str(final_text);verified=[]
    for record in records:
        before=record.get('before','');after=record.get('after','')
        if record.get('flags')!=['inverts_initiator'] or not before or before==after:continue
        if expected.count(before)!=1:continue
        if replay(before,['inverts_initiator'])!=after:continue
        expected=expected.replace(before,after,1)
        verified.append({'before':before,'after':after,'flags':['inverts_initiator'],
            'exact_native_replay':True,'evidence':record.get('evidence','native_call_observation')})
    allowed=bool(verified) and norm(expected)==norm(confirmed_text)
    return {'allowed_native_cleanup':allowed,'native_source_sha256':source_sha,
        'verified_repairs':verified,'comparison':'all substantive text after exact native local repair; presentation normalized',
        'unrelated_missing_body_not_allowed':not allowed}


def confirmed_music_preference(preferences, deliveries):
    """Require a newly extracted native expression linked to this Guard receipt."""
    confirmed={row['delivery_id']:row['text'] for row in deliveries if row['state']=='sent'}
    return any(pref.get('category')=='music' and pref.get('source')=='confirmed_visible_reply'
        and pref.get('confirmed_source',{}).get('delivery_id') in confirmed
        and pref.get('statement','')
        and pref['statement'] in confirmed[pref['confirmed_source']['delivery_id']]
        for pref in preferences)


def dict_rows(db,sql,params=()):
    cur=db.execute(sql,params);cols=[d[0] for d in cur.description]
    return [dict(zip(cols,row)) for row in cur]


async def run_joint_stage(core,runtime,spec):
    from astrbot.api.message_components import Plain
    from astrbot.api.platform import AstrBotMessage,MessageMember,MessageType
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
    from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
    from astrbot.core.provider.entities import ProviderType
    from integrations.phase3_runtime.proactive import ProactiveGate,native_proactive_config,authorize_native_target,resolve_native_target
    from integrations.phase3_runtime.autonomy import AutonomousProactiveBridge
    from integrations.phase3_runtime.autonomy_state import AutonomyState
    from integrations.phase3_runtime.settings import AUTONOMY_DEFAULTS,FREQUENCY_PROACTIVE_POLICY
    from integrations.phase3_memory.self_preferences import NativeSelfPreferences
    from integrations.phase3_search.proactive import ProactiveSearchContext,ProactiveSearchRuntime
    from integrations.phase3_search.policy import PublicPolicy
    from integrations.phase3_search.redaction import install
    if runtime.platform_id!='offline' or runtime.admin_id!='10001' or not runtime.memory:
        raise PermissionError('isolated_offline_runtime_required')
    if (spec['stage'] not in STAGES or spec['backend'] not in ('real','synthetic')
            or spec.get('scenario','full') not in ('full','interest-research')):
        raise ValueError('invalid_joint_stage')
    folder=Path(spec['state'])/'joint-autonomy';folder.mkdir(mode=0o700,exist_ok=True)
    helper=load_real_helper();backend=None
    before={}
    if spec['backend']=='real':
        paths=[Path(spec['live_config']),Path(spec['live_persona'])]
        before={str(p):helper.sha(p.read_bytes()) for p in paths}
        provider_cfg,tool_cfg,persona=helper.live_provider(*paths)
    else:
        from phase3_autonomy_joint_fixture import JointSyntheticBackend
        backend=JointSyntheticBackend();await backend.start()
        provider_cfg,tool_cfg,persona=backend.configuration()
    provider_cfg={**provider_cfg,'id':PROVIDER_ID,'request_max_retries':0}
    provider=ProviderOpenAIOfficial(provider_cfg,{})
    core.star_context.provider_manager.inst_map[PROVIDER_ID]=provider
    await core.star_context.provider_manager.set_provider(PROVIDER_ID,ProviderType.CHAT_COMPLETION,umo=SESSION)
    if await core.star_context.get_using_provider_async(SESSION) is not provider:
        raise RuntimeError('actual_native_provider_not_selected')
    install()
    meta=runtime._star('astrbot_plugin_private_companion');private=meta.star_cls
    package=meta.module_path.rsplit('.',1)[0]
    message=importlib.import_module(package+'.proactive_message')
    bootstrap=importlib.import_module(package+'.plugin_bootstrap')
    # Only wall-clock scheduling is driven deterministically. Native candidate
    # and tick implementations themselves are neither stubbed nor replaced.
    scheduler=getattr(private,'_task',None)
    if scheduler and not scheduler.done():
        scheduler.cancel();await asyncio.gather(scheduler,return_exceptions=True)
    now=datetime.fromisoformat('2026-10-28T12:00:00+08:00')
    if spec['stage']=='resume':now+=timedelta(days=1)
    clock=FixtureClock(now.timestamp())
    gate=ProactiveGate(folder/'gate.sqlite3',allowed_session=SESSION,enabled=True,
        clock=clock,**FREQUENCY_PROACTIVE_POLICY)
    state=AutonomyState(gate.db,clock=clock,audit_dir=folder/'audit',
        model_limit=spec['max_requests'],tool_limit=spec['max_tools'],tools_per_opportunity=3,
        audit_capacity_bytes=128*1024*1024)
    preferences=NativeSelfPreferences(private,runtime.guard,SESSION,clock=clock)
    async def synthetic_dns(host):return ('1.1.1.1',)
    def search_factory(scope,store,admit):
        return ProactiveSearchRuntime(scope=ProactiveSearchContext(SESSION,scope['opportunity_id'],
            scope['evaluation_id'],scope['started_at'],scope['deadline'],scope['input_version']),
            state=store,policy=PublicPolicy(scope='general_public',**({'resolver':synthetic_dns} if backend else {})),
            allowed_session=SESSION,config_context=NS(get_config=lambda **kw:tool_cfg),
            admit=lambda _:admit(),key_ready=True,enabled=True,clock=clock,tool_timeout=60,
            protected_values=tuple(provider_cfg['key'])+tuple(tool_cfg['provider_settings']['websearch_deepseek_key']))
    bridge=AutonomousProactiveBridge(private,runtime.guard,gate,message._ProactiveSendOutcome,
        autonomy_state=state,autonomy_settings=dict(AUTONOMY_DEFAULTS),self_preferences=preferences,
        search_factory=search_factory,generation_timeout=120,
        history_policy={'summary_limit':8,'ordinary_retention_hours':24})
    patches=ExitStack()
    patches.enter_context(guard_receipt_clock_patch(runtime.guard.store,clock))
    for name,module in tuple(sys.modules.items()):
        if name.startswith(package+'.'):
            if hasattr(module,'_now_ts'):patches.enter_context(patch.object(module,'_now_ts',clock))
            if hasattr(module,'_today_key'):patches.enter_context(patch.object(module,'_today_key',lambda:clock.datetime().date().isoformat()))
    original_config=private.config;original_environment=private._environment_now
    attached=False;cases=[];cleanup_records=[]
    original_repair=private._repair_proactive_reply_air_locally
    replay,cleanup_source_sha=native_cleanup_replayer(message.__file__)
    def observed_repair(text,flags):
        result=original_repair(text,flags)
        cleanup_records.append({'before':str(text),'after':result,'flags':list(flags),
            'evidence':'native_call_observation'})
        return result
    patches.enter_context(patch.object(private,'_repair_proactive_reply_air_locally',observed_repair))
    try:
        config=native_proactive_config(dict(original_config),'10001',enabled=True,policy=FREQUENCY_PROACTIVE_POLICY)
        config.setdefault('proactive_generation_config',{})['LLM_PROVIDER_ID']=PROVIDER_ID;config['LLM_PROVIDER_ID']=PROVIDER_ID
        config.setdefault('basic_config',{})['plugin_specific_persona_id']='qq_companion';config['plugin_specific_persona_id']='qq_companion'
        private.config=config;bootstrap.initialize_plugin_config(private,config);private._environment_now=clock.datetime
        if spec['stage']=='first':
            msg=AstrBotMessage();msg.type=MessageType.FRIEND_MESSAGE;msg.message_id='joint-route'
            msg.self_id='90000';msg.sender=MessageMember('10001','Synthetic');msg.message=[Plain('隔离身份绑定')]
            msg.message_str='隔离身份绑定';msg.session_id='10001';msg.raw_message={}
            platform=core.star_context.get_platform_inst('offline')
            event=AiocqhttpMessageEvent(msg.message_str,msg,platform.meta(),'10001',platform.bot)
            uid=private._private_user_id_for_event(event,'10001');user=private._get_user(uid)
            private._stamp_private_event_identity(user,event,'10001');private._note_private_user_umo(uid,user,SESSION)
            user.update(umo=SESSION,last_private_seen=0,last_activity_at=0,last_user_message_at=0,
                last_reply_at=0,last_private_activity_at=0,last_private_reply_at=0,next_proactive_at=0,
                ignored_streak=2,unanswered_proactive_count=2,bot_self_preferences=[],
                proactive_impulses=[],recent_proactive_topics=[])
            authorize_native_target(private,SESSION,approved=True,daily_limit=12)
            await core.conversation_manager.new_conversation(SESSION,persona_id='qq_companion',title='Isolated real joint probe',content=[])
        else:
            uid,user=resolve_native_target(private,SESSION)
        preferences.attach();bridge.attach();attached=True;bridge.activate_native_config();runtime.guard.phase3_proactive_enabled=True
        before_delivery=runtime.guard.store.db.execute('SELECT count(*) FROM deliveries').fetchone()[0]
        restored={'native_preferences':preferences.current(),'pocket':state.list_pocket(SESSION),
            'model_calls':gate.db.execute("SELECT count(*) FROM autonomy_calls WHERE kind='model'").fetchone()[0],
            'deliveries':before_delivery}
        await asyncio.sleep(.25)
        restored['no_auto_resend_after_attach']=runtime.guard.store.db.execute('SELECT count(*) FROM deliveries').fetchone()[0]==before_delivery
        for index,(case,brief,cooldown) in enumerate(stage_cases(spec)):
            if backend:backend.case=case
            if cooldown:
                last=gate.db.execute("SELECT MAX(attempted) FROM proactive_attempts WHERE sent_count>0").fetchone()[0]
                if last is None:
                    cases.append({'case':case,'status':'NOT_TESTED','reason':'no_real_isolated_confirmed_parent_for_cooldown'});continue
                clock.move(float(last)+60)
            else:clock.move(now.timestamp()+index*7200)
            candidate={'id':'joint-'+spec['stage']+'-'+case,'source':'random' if not brief else 'conversation',
                'reason':'activity_share' if not brief else 'conversation','action':'message','topic':{'open_empty':'开放选题机会','controlled_explicit_preference':'现在偏爱的音乐与审美理由',
                    'cooldown_research':'章鱼皮肤感光的公开研究问题',
                    'resume_research':'昨天未分享的研究来源继续核实',
                    'recall_preference':'已表达审美偏好如何形成新联想',
                    'interest_research':'先表达兴趣再核实章鱼皮肤感光来源'}[case],
                'motive':'可以自主选题，也可以跳过','scheduled_ts':clock()-1,'window_start_at':clock()-5,
                'preferred_ts':clock()-1,'best_until_at':clock()+1800,'expire_at':clock()+3600,
                'window_timezone':'Asia/Shanghai','origin_event_id':'joint-'+case,
                'context':{} if not brief else {'isolated_material':brief}}
            async with private._data_lock:
                # Drive the next scripted fixture opportunity through the native
                # pool without silently evaluating its automatically rescheduled
                # random candidate. Historical candidates and sends remain intact.
                replaced=deepcopy(private._planned_proactive_impulse(user))
                if replaced:
                    private._mark_planned_candidate_status(user,'cancelled',note='isolated_joint_next_scripted_opportunity')
                    private._clear_pending_proactive_plan(user)
                accepted=bool(private._offer_proactive_candidate(uid,user,candidate))
            native=deepcopy(private._planned_proactive_impulse(user))
            opportunity_id=str(private._planned_proactive_delivery_key(user) or '')
            preflight=list(private._should_send(user))
            prior_confirmed=[r[0] for r in runtime.guard.store.db.execute(
                "SELECT text FROM deliveries WHERE state='sent' ORDER BY rowid DESC LIMIT 8")]
            prior_preferences=preferences.current();prior_pocket=state.list_pocket(SESSION)
            firstrow=runtime.guard.store.db.execute('SELECT COALESCE(MAX(rowid),0) FROM deliveries').fetchone()[0]
            before_calls=gate.db.execute('SELECT count(*) FROM autonomy_calls').fetchone()[0]
            cleanup_start=len(cleanup_records)
            started=time.monotonic()
            await asyncio.wait_for(private._tick_user(uid,user),650)
            await preferences.drain(limit=128)
            private._save_data_sync(sections={'users','proactive_candidate_pool'})
            conversation=await core.conversation_manager.get_curr_conversation_id(SESSION)
            conv=await core.conversation_manager.get_conversation(SESSION,conversation)
            history=json.loads(conv.history) if isinstance(conv.history,str) else conv.history
            deliveries=[dict(r) for r in runtime.guard.store.db.execute('SELECT * FROM deliveries WHERE rowid>? ORDER BY rowid',(firstrow,))]
            evaluation=dict_rows(gate.db,'SELECT * FROM autonomy_evaluations WHERE opportunity_id=?',(opportunity_id,))
            calls=dict_rows(gate.db,'SELECT * FROM autonomy_calls WHERE evaluation_id IN (SELECT id FROM autonomy_evaluations WHERE opportunity_id=?) ORDER BY ordinal',(opportunity_id,))
            request_refs=dict_rows(gate.db,"SELECT path FROM autonomy_audits WHERE kind='request' AND evaluation_id IN (SELECT id FROM autonomy_evaluations WHERE opportunity_id=?) ORDER BY created",(opportunity_id,))
            request_payloads=[json.loads((folder/'audit'/r['path']).read_text()) for r in request_refs if r['path']]
            request_text=actual_message_text(request_payloads)
            output_refs=dict_rows(gate.db,"SELECT path FROM autonomy_audits WHERE kind='output' AND evaluation_id IN (SELECT id FROM autonomy_evaluations WHERE opportunity_id=?) ORDER BY created",(opportunity_id,))
            final_model={}
            from integrations.phase3_runtime.autonomy_contract import parse
            for ref in output_refs:
                output=json.loads((folder/'audit'/ref['path']).read_text())
                for choice in output.get('choices',[]):
                    parsed=parse(choice.get('content',''))
                    if parsed['reason']=='model_decision':final_model=parsed
            prefs=preferences.current();pocket=state.list_pocket(SESSION)
            pocket_rows=dict_rows(gate.db,'SELECT id,state,revision,selected_eval,updated,shared_attempt FROM autonomy_pocket WHERE session=?',(SESSION,))
            visible=''.join(str(m.get('content','')) for m in history if m.get('role')=='assistant')
            confirmed=[r for r in deliveries if r['state']=='sent']
            cleanup_proof=native_cleanup_proof(final_model.get('text',''),''.join(d['text'] for d in confirmed),
                cleanup_records[cleanup_start:],replay,cleanup_source_sha)
            checks={'native_candidate_accepted':accepted,'native_selected_exact_candidate':bool(native and native.get('origin_event_id')==candidate['origin_event_id']),
                'native_preflight_allowed':bool(preflight and preflight[0]),'evaluated_once':len(evaluation)==1,
                'actual_metered_model_request':any(c['kind']=='model' for c in calls),
                'valid_model_decision':bool(evaluation and json.loads(evaluation[0]['parsed']).get('reason')=='model_decision'),
                'native_send_decision_fully_delivered':send_outcome_valid(final_model.get('decision'),evaluation[0] if evaluation else {},cooldown,deliveries,pocket,calls),
                'model_visible_tail_preserved':(visible_tail_preserved(final_model.get('text',''),''.join(d['text'] for d in confirmed)) or cleanup_proof['allowed_native_cleanup'])
                    if evaluation and evaluation[0]['effective_state']=='sent' else True,
                'actual_selected_flash_in_request':bool(request_payloads) and all(p.get('model')=='deepseek-flash' for p in request_payloads),
                'history_has_no_fabricated_user':all(m.get('role')=='assistant' for m in history),
                'confirmed_only_history':all(r['text'] in visible for r in confirmed),
                'native_current_date_in_request':clock.datetime().date().isoformat() in request_text,
                'guard_confirmed_history_in_request':confirmed_history_present(prior_confirmed,request_payloads),
                'native_self_preferences_in_request':all(p['statement'] in request_text for p in prior_preferences),
                'durable_pocket_in_request':all(p['id'] in request_text for p in prior_pocket),
                'actual_preference_extracted':confirmed_music_preference(prefs,deliveries)
                    if case=='controlled_explicit_preference' else True,
                'recall_has_actual_native_preference':bool(prior_preferences) and all(
                    p['statement'] in request_text for p in prior_preferences) if case=='recall_preference' else True,
                'research_used_actual_search_fetch':all(any(c['kind']==kind for c in calls) for kind in (('search','fetch') if case in ('cooldown_research','interest_research') else ('fetch',)))
                    if case in ('cooldown_research','resume_research','interest_research') else True,
                'research_fetch_body_obtained':any(c['kind']=='fetch' and c['state']=='succeeded' for c in calls)
                    if case in ('cooldown_research','resume_research','interest_research') else True,
                'no_real_qq':True,'cooldown_no_send':not deliveries if cooldown else True,
                'cooldown_pocket_created':bool(pocket) if cooldown else True,
                'existing_pocket_reconsidered':any(p['id'] in {old['id'] for old in prior_pocket}
                    and (p['selected_eval']==opportunity_id or p['updated']>=now.timestamp()) for p in pocket_rows)
                    if case=='resume_research' else True}
            if (backend and case=='recall_preference') or case=='interest_research':
                # Explicitly exercise the formerly untested next-day interest
                # preface -> independent final batch with real native clocks.
                attempts=dict_rows(gate.db,'SELECT * FROM proactive_attempts WHERE candidate IN (?,?)',
                    (evaluation[0]['id'],evaluation[0]['id']+':interest')) if evaluation else []
                checks['next_day_interest_and_final_confirmed']=(len(attempts)==2
                    and all(a['state']=='sent' and a['sent_count']>0 for a in attempts))
                checks['receipts_share_fixture_date']=bool(confirmed) and all(
                    datetime.fromisoformat(d['finished_at']).date()==clock.datetime().date() for d in confirmed)
                supplements=dict_rows(gate.db,'SELECT * FROM autonomy_supplements WHERE attempt_id=?',
                    (evaluation[0]['id'],)) if evaluation else []
                checks['one_counted_nonrecursive_interest_supplement']=(len(supplements)==1
                    and supplements[0]['parent_kind']=='interest'
                    and supplements[0]['parent_attempt']==evaluation[0]['id']+':interest'
                    and supplements[0]['state']=='sent')
                interest=next((a for a in attempts if a['candidate'].endswith(':interest')),None)
                final=next((a for a in attempts if not a['candidate'].endswith(':interest')),None)
                tool_calls=[c for c in calls if c['kind'] in ('search','fetch')]
                if case=='interest_research':
                    checks['interest_then_tools_then_final']=bool(interest and final and tool_calls
                        and interest['finished']<=min(c['started'] for c in tool_calls)
                        and final['attempted']>=max(c['finished'] or c['started'] for c in tool_calls))
            cases.append({'case':case,'status':'PASS' if all(checks.values()) else 'PARTIAL','checks':checks,
                'preflight':preflight,'selected_candidate':native,'opportunity_id':opportunity_id,'replaced_pending_fixture_candidate':replaced,'evaluation':evaluation,'calls':calls,
                'elapsed_seconds':time.monotonic()-started,'deliveries':deliveries,'history':history,
                'self_preferences':prefs,'pocket':pocket,'persistent_pocket_rows':pocket_rows,'bridge_metrics':dict(bridge.metrics),
                'private_final_request_refs':[r['path'] for r in request_refs],
                'native_cleanup_proof':cleanup_proof,'native_cleanup_observations':cleanup_records[cleanup_start:],
                'prior_confirmed_texts':prior_confirmed,
                'final_model_body_chars':len(final_model.get('text','')),
                'guard_confirmed_body_chars':sum(len(d['text']) for d in confirmed),
                'preference_test_scope':'controlled_supported_native_expression_only; unconstrained r1 misses remain'
                    if case=='controlled_explicit_preference' else None,
                'prior_call_count':before_calls,'level':'T2_T3_JOINT_NATIVE_REAL_HTTP_OFFLINE_ONEBOT' if not backend else 'T2_JOINT_SYNTHETIC_HTTP_OFFLINE_ONEBOT'})
            helper.private_json(folder/('case-'+case+'.json'),cases[-1])
        await preferences.drain(limit=128);private._save_data_sync(sections={'users','proactive_candidate_pool'})
        result={'stage':spec['stage'],'backend':spec['backend'],'real_deepseek':backend is None,'real_qq':False,
            'production_changed':False,'native_scheduler_clock_driven':True,'guard_receipt_fixture_clock_aligned':True,'restored':restored,'cases':cases,
            'summary':state.summary(),'persistent_preferences':preferences.current(),'persistent_pocket':state.list_pocket(SESSION),
            'provider_model':provider.get_model(),'persona_sha256':helper.sha(persona),
            'native_tick_not_mocked':True,'candidate_pool_not_mocked':True,'guard_send_not_mocked':True}
        result['restart_checks']={'native_preferences_reloaded':bool(restored['native_preferences']),
            **({'unshared_pocket_reloaded':bool(restored['pocket'])} if spec.get('scenario','full')=='full' else {}),
            'previous_model_counter_reloaded':restored['model_calls']>0} if spec['stage']=='resume' else {}
        if before:result['live_inputs_unchanged']=all(helper.sha(Path(p).read_bytes())==v for p,v in before.items())
        result['pass']=all(c['status']=='PASS' for c in cases) and restored['no_auto_resend_after_attach'] and all(result['restart_checks'].values())
        return result
    finally:
        if attached:bridge.detach()
        preferences.detach();gate.close();patches.close()
        private.config=original_config;private._environment_now=original_environment
        bootstrap.initialize_plugin_config(private,original_config)
        core.star_context.provider_manager.inst_map.pop(PROVIDER_ID,None)
        await provider.client.close()
        if backend:await backend.close()
