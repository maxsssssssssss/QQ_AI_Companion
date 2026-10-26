"""Fixture-only probe of the COMPLETE loaded Private _tick_user send chain.

Called only by phase3_launch after loopback validation, in disposable /state.
Never import this module in production plugins or invoke it against a VPS.
"""
from datetime import datetime, timedelta, timezone
from copy import deepcopy
import importlib
import os
import re
import sys
import time
from contextlib import ExitStack
from unittest.mock import patch


async def run_probe(core,runtime):
    # Each case resets only this disposable plugin's synthetic native data.
    # All three exercise the actual fixed admission/tick/generator/settlement.
    cases={}
    for decision in ('defer','skip','send'):
        cases[decision]=await _run_case(core,runtime,decision)
    result=cases['send']
    result['decision_cases']={key:{k:value[k] for k in
        ('pass','candidate_accepted','metrics','new_deliveries','generation_states','decision_metadata')}
        for key,value in cases.items()}
    from test_phase3_proactive_plan_native import run_probe as run_plan_probe
    result['cold_plan_recovery']=await run_plan_probe(core,runtime)
    result['pass']=all(value['pass'] for value in cases.values()) and result['cold_plan_recovery']['pass']
    if os.environ.get('PHASE3_PROACTIVE_TUNING_PROBE') == '1':
        tuned = {}
        for decision in ('defer', 'skip', 'send'):
            tuned[decision] = await _run_case(core, runtime, decision, tuning=True)
        result['tuning_cases'] = tuned
        result['pass'] = result['pass'] and all(value['pass'] for value in tuned.values())
    return result


async def _run_case(core,runtime,decision,*,tuning=False):
    import inspect
    from astrbot.api.message_components import Plain
    from astrbot.api.platform import AstrBotMessage, MessageMember, MessageType
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
    from integrations.phase3_runtime.proactive import (
        ProactiveGate,NativeProactiveBridge,native_proactive_config,authorize_native_target)
    from astrbot.api.star import StarTools
    if runtime.platform_id!='offline' or runtime.admin_id!='10001' or not runtime.memory:
        raise PermissionError('synthetic_offline_runtime_required')
    meta=runtime._star('astrbot_plugin_private_companion')
    private=meta.star_cls
    original_data=deepcopy(private.data)
    module=importlib.import_module(meta.module_path.rsplit('.',1)[0]+'.proactive_message')
    bootstrap=importlib.import_module(meta.module_path.rsplit('.',1)[0]+'.plugin_bootstrap')
    original_config=private.config
    original_environment_now=private._environment_now
    case_id=('tuning-' if tuning else '')+decision
    policy = None
    history_policy = None
    if tuning:
        from integrations.phase3_runtime.settings import tuned_proactive_candidate
        candidate = tuned_proactive_candidate(runtime.settings)
        policy = candidate['proactive_window']
        history_policy = candidate['proactive_history']
    config=native_proactive_config(dict(original_config),'10001',enabled=True,policy=policy)
    if tuning:
        for key,value in {'proactive_generation_history_limit':80,
                'proactive_history_context_mode':'compact','proactive_history_recent_raw_count':8,
                'proactive_history_max_chars':16000}.items():
            config.setdefault('proactive_generation_config',{})[key]=value
            config[key]=value
    # Explicit test-only clock/quiet override; never part of staged production.
    if not tuning:
        config['basic_config']['quiet_hours']=''
        config['quiet_hours']=''
    config['proactive_generation_config']['LLM_PROVIDER_ID']='mock/phase1'
    config['LLM_PROVIDER_ID']='mock/phase1'
    config['basic_config']['plugin_specific_persona_id']='qq_companion'
    config['plugin_specific_persona_id']='qq_companion'
    now=datetime.now(timezone.utc)
    due=now-timedelta(minutes=2)
    from zoneinfo import ZoneInfo
    quote='PHASE3_PROACTIVE_NATIVE_PROBE PHASE3_PROACTIVE_DECISION_'+decision.upper()+' 合成面试，'+due.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M')+'等待结果。'
    if tuning:
        # Native Memory deduplicates identical contents even when message/event
        # IDs differ. This is a separate synthetic event, not a replay of the
        # earlier legacy case, so its evidence must also carry that identity.
        quote += ' 本轮合成上下文扩展验收编号：'+case_id+'。'
    umo='offline:FriendMessage:10001'
    msg=AstrBotMessage();msg.type=MessageType.FRIEND_MESSAGE;msg.message_id='native-probe-source-'+case_id
    msg.self_id='90000';msg.sender=MessageMember('10001','Synthetic');msg.message=[Plain(quote)]
    msg.message_str=quote;msg.session_id='10001';msg.raw_message={}
    platform=core.star_context.get_platform_inst('offline')
    event=AiocqhttpMessageEvent(quote,msg,platform.meta(),'10001',platform.bot)
    ctx=runtime.memory.context(session_id=umo,user_id='10001',bot_id='90000',message_id=msg.message_id,message_text=quote)
    runtime.memory.remember_context(ctx)
    # Explicit synthetic inbound record. There is no forged production event.
    runtime.guard.store.claim('offline',umo,msg.message_id,'10001',quote,[p.toDict() for p in msg.message])
    await runtime.memory.observe(ctx,source_id=msg.message_id,content=quote,occurred_at=now.isoformat())
    event_id='native-probe-event-'+case_id
    await runtime.memory.user_event(ctx,event_id=event_id,state='awaiting_result',source_id=msg.message_id,
        original_text=quote,quote=quote,occurred_at=now.isoformat(),due_at=due.isoformat(),
        expires_at=(now+timedelta(hours=24 if tuning else 3)).isoformat())
    native_now=original_environment_now().replace(hour=12,minute=0,second=0,microsecond=0)
    clock=(lambda:native_now.timestamp()) if tuning else time.time
    gate_options=dict(policy) if tuning else dict(window_start=time.time()-1,
        window_end=time.time()+14400,cooldown=60,quiet_hours=(0,0))
    gate=ProactiveGate(StarTools.get_data_dir('phase3_runtime')/('native-probe-'+case_id+'.sqlite3'),
        allowed_session=umo,enabled=True,clock=clock,**gate_options)
    async def case_events(session):
        return [row for row in await runtime._proactive_events(session) if row['event_id']==event_id]
    bridge=NativeProactiveBridge(private,runtime.guard,gate,module._ProactiveSendOutcome,
        event_resolver=runtime._proactive_event,event_source=case_events,event_delivered=runtime._event_prompted,
        history_policy=history_policy)
    enabled=runtime.guard.phase3_proactive_enabled
    trace=[];traced={}
    def contains(value):return 'PRIVATE_COMPANION_SPLIT' in str(value)
    for name in ('_run_framework_agent_text','_generate_proactive_message_via_framework',
                 '_finalize_proactive_generated_text','_extract_reaction_expression_hidden_intent',
                 '_visible_text_without_tts_reading','_normalize_proactive_sentence_flow'):
        old=getattr(private,name,None)
        if not callable(old):continue
        traced[name]=old
        if inspect.iscoroutinefunction(old):
            async def wrapper(*a,_name=name,_old=old,**k):
                result=await _old(*a,**k)
                trace.append({'method':_name,'input_marker':contains(a),'output_marker':contains(result)})
                if _name=='_finalize_proactive_generated_text' and contains(result):
                    trace[-1]['synthetic_output']=result
                return result
        else:
            def wrapper(*a,_name=name,_old=old,**k):
                result=_old(*a,**k)
                trace.append({'method':_name,'input_marker':contains(a),'output_marker':contains(result)})
                return result
        setattr(private,name,wrapper)
    clock_patches=ExitStack()
    if tuning:
        package=meta.module_path.rsplit('.',1)[0]
        for name,native_module in tuple(sys.modules.items()):
            if name.startswith(package+'.'):
                if hasattr(native_module,'_now_ts'):
                    clock_patches.enter_context(patch.object(native_module,'_now_ts',clock))
                if hasattr(native_module,'_today_key'):
                    clock_patches.enter_context(patch.object(native_module,'_today_key',lambda:native_now.date().isoformat()))
    conversation_manager=core.conversation_manager
    old_conversation_id=await conversation_manager.get_curr_conversation_id(umo) if tuning else None
    synthetic_conversation_id=None
    bridge.attach()
    try:
        private.config=config
        bootstrap.initialize_plugin_config(private,config)
        # Explicit synthetic daytime clock exercises the real native reason
        # windows even when this disposable test runs at night. It does not
        # replace _should_send or any candidate/identity/budget policy method.
        private._environment_now=lambda: native_now
        native_id=private._private_user_id_for_event(event,'10001')
        user=private._get_user(native_id)
        private._stamp_private_event_identity(user,event,'10001')
        private._note_private_user_umo(native_id,user,umo)
        user['umo']=umo
        user.update(last_private_seen=0,last_activity_at=0,last_user_message_at=0,last_reply_at=0,
                    last_private_activity_at=0,last_private_reply_at=0,next_proactive_at=0)
        authorize_native_target(private,umo,approved=True,daily_limit=8 if tuning else 2)
        bridge.activate_native_config()
        if tuning:
            history=[{'role':'user' if index%2==0 else 'assistant',
                'content':f'PROCTX_{decision}_{index:03d} '+('甲'*300),
                'timestamp':clock()-(100-index)*60} for index in range(100)]
            synthetic_conversation_id=await conversation_manager.new_conversation(
                umo,persona_id='qq_companion',content=history,title='Synthetic proactive tuning '+decision)
            user['recent_proactive_topics']=[{'ts':clock()-25*3600,
                'signature':'tuning-expired','text':'PROTOPIC_EXPIRED'}]+[
                {'ts':clock()-23*3600+index*60,'signature':f'tuning-{index}',
                 'text':f'PROTOPIC_{decision}_{index:02d} '+('乙'*100)} for index in range(8)]
            topic_hint=private._format_recent_proactive_topics_hint(user)
            history_checks={'candidate_policy_loaded':policy==dict(mode='continuous',window_start=0,
                window_end=0,max_per_day=8,max_generations_per_day=48,cooldown=7200,quiet_hours=[23,9]),
                'history_policy_loaded':history_policy==dict(summary_limit=8,ordinary_retention_hours=24),
                'native_history_limits':(private._proactive_history_limit('generation')==80
                    and private.persona_setting('proactive_history_recent_raw_count',0)==8
                    and private.persona_setting('proactive_history_max_chars',0)==16000),
                'native_capability_daily_eight':user.get('proactive_daily_limit')==8,
                'native_runtime_daily_eight':private.max_daily_messages==8,
                'native_cooldown_120_minutes':private.min_interval_minutes==120,
                'native_quiet_hours':private.quiet_hours=='23:00-09:00',
                'eight_23_hour_topics_visible':len(re.findall(r'PROTOPIC_'+decision+r'_\d{2}',topic_hint))==8,
                'expired_25_hour_topic_removed':'PROTOPIC_EXPIRED' not in topic_hint,
                'dedup_window_preserved':private._proactive_dedup_window_minutes('sent',240)==240}
        runtime.guard.phase3_proactive_enabled=True
        accepted=await bridge.offer_due_event()
        impulse=private._planned_proactive_impulse(user)
        # The event offer's normal one-hour candidate window is intentionally
        # widened in this isolated fixture so a one-hour defer has a future.
        if impulse:
            impulse['expire_at']=clock()+10800
            user['planned_proactive_expire_at']=impulse['expire_at']
            # Native topic/motive excerpts are short. Put this fixture selector
            # at the start of the real motive rather than relying on an unseen
            # suffix of a long source quote.
            impulse['motive']='PHASE3_PROACTIVE_DECISION_'+decision.upper()
            user['planned_proactive_motive']=impulse['motive']
        diagnostic={'candidate_accepted':accepted,'native_user_enabled':private._user_enabled_for_proactive(native_id,user),
                    'native_daily_limit':private._effective_user_daily_limit(user),'synthetic_native_hour':12}
        from astrbot.api.event import MessageEventResult
        diagnostic['guard_parser_probe']={'parts':len(runtime.guard.semantic_parts(MessageEventResult().message('甲\n<<PRIVATE_COMPANION_SPLIT>>\n乙'))),
            'enabled':runtime.guard.multibubble_enabled,
            'private_metadata':[(str(p.module_path),p.activated) for p in runtime.guard.context.get_all_stars() if 'private_companion' in str(p.module_path)]}
        diagnostic['preflight']=list(private._should_send(user))
        before=runtime.guard.store.db.execute("SELECT count(*) FROM deliveries WHERE kind='proactive'").fetchone()[0]
        # This is the actual upstream 6.7.0 method, captured by bridge.attach.
        await private._tick_user(native_id,user)
        rows=runtime.guard.store.db.execute("SELECT state,kind,delivery_id FROM deliveries WHERE kind='proactive' ORDER BY rowid").fetchall()
        diagnostic.update(metrics=dict(bridge.metrics),new_deliveries=len(rows)-before,
            semantic_trace=trace,
            deliveries=[dict(row) for row in rows],event=runtime._proactive_event(umo,event_id),
            native_last_note=user.get('planned_proactive_delivery_note',''),native_render_failure=user.get('_proactive_render_failure_stage',''))
        diagnostic['generation_states']=[r[0] for r in gate.db.execute('SELECT state FROM proactive_generations')]
        if tuning:
            diagnostic['tuning_checks']=history_checks
            diagnostic['tuning_policy']=policy
            diagnostic['attempts']=[dict(zip(('state','sent_count','part_count'),row)) for row in
                gate.db.execute('SELECT state,sent_count,part_count FROM proactive_attempts')]
        current=private._get_user(native_id)
        live=next((item for item in private._cleanup_proactive_impulses(current)
                   if item.get('id')==(impulse or {}).get('id')), {})
        diagnostic['decision_metadata']=live.get('context',{}).get('phase3_decision',{})
        diagnostic['native_candidate_state']=live.get('state')
        if decision=='send':
            diagnostic['pass']=bool(accepted and len(rows)-before==2 and all(r['state']=='sent' for r in rows[-2:])
                                    and diagnostic['event']['prompted'] and bridge.metrics['generation_attempts']==1)
        else:
            expected='deferred' if decision=='defer' else 'skipped'
            diagnostic['pass']=bool(accepted and len(rows)==before and not diagnostic['event']['prompted']
                and bridge.metrics['generation_attempts']==1 and diagnostic['generation_states']==[expected]
                and diagnostic['decision_metadata'].get('decision')==decision
                and diagnostic['native_candidate_state']==('deferred' if decision=='defer' else 'blocked'))
        if tuning:
            diagnostic['pass']=diagnostic['pass'] and all(history_checks.values())
            if decision=='send':
                diagnostic['pass']=diagnostic['pass'] and diagnostic['attempts']==[
                    {'state':'sent','sent_count':2,'part_count':2}]
        return diagnostic
    finally:
        bridge.detach();gate.close()
        for name,original in traced.items():setattr(private,name,original)
        private._environment_now=original_environment_now
        runtime.guard.phase3_proactive_enabled=enabled
        private.config=original_config
        bootstrap.initialize_plugin_config(private,original_config)
        private.data.clear();private.data.update(original_data)
        clock_patches.close()
        if synthetic_conversation_id:
            if old_conversation_id:
                await conversation_manager.switch_conversation(umo,old_conversation_id)
            await conversation_manager.delete_conversation(umo,synthetic_conversation_id)
