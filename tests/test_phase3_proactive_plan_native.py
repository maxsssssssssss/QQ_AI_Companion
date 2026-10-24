"""Full loaded Private 6.7.0 cold-plan probe, called only by isolated T2 harness.

No fixture replaces initialize config, schedule, tick, should_send, native Agent,
or Guard delivery. The clock and model/OneBot HTTP peers are synthetic. Native
maintenance unrelated to proactive scheduling is suppressed in this probe.
"""
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime
import importlib
import random
import sys
from unittest.mock import patch
from zoneinfo import ZoneInfo


async def run_probe(core, runtime):
    if runtime.platform_id != 'offline' or runtime.admin_id != '10001':
        raise PermissionError('synthetic_offline_runtime_required')
    results = {}
    for decision in ('send', 'skip', 'defer'):
        results[decision] = await _case(core, runtime, decision)
    return {'pass': all(case['pass'] for case in results.values()),
            'level': 'T2_full_AstrBot_synthetic_clock_HTTP_model_OneBot',
            'cases': results, 'real_qq': False, 'real_deepseek': False}


async def _case(core, runtime, decision):
    from astrbot.api.message_components import Plain
    from astrbot.api.platform import AstrBotMessage, MessageMember, MessageType
    from astrbot.api.star import StarTools
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
    from integrations.phase3_runtime.proactive import (
        ProactiveGate, NativeProactiveBridge, native_proactive_config, authorize_native_target)

    private_meta = runtime._star('astrbot_plugin_private_companion')
    private = private_meta.star_cls
    package = private_meta.module_path.rsplit('.', 1)[0]
    bootstrap = importlib.import_module(package + '.plugin_bootstrap')
    message = importlib.import_module(package + '.proactive_message')
    original_data, original_config = deepcopy(private.data), private.config
    original_guard_enabled = runtime.guard.phase3_proactive_enabled
    random_state = random.getstate()
    # Start during the authorized daytime; advance to actual native due times.
    now = [datetime(2026, 10, 24, 10, 0, tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()]
    umo = 'offline:FriendMessage:10001'
    gate_path = StarTools.get_data_dir('phase3_runtime') / ('cold-plan-' + decision + '.sqlite3')
    gate = ProactiveGate(gate_path, allowed_session=umo, enabled=True, mode='continuous',
        max_per_day=5, max_generations_per_day=5, cooldown=10800, quiet_hours=(0, 9), clock=lambda: now[0])
    bridge = None
    checks = {}
    trace = {'schedule': 0, 'should_send': 0, 'tick_user': 0, 'model': 0}
    decisions = []
    config = native_proactive_config(dict(original_config), '10001', enabled=True,
                                    policy={'mode': 'continuous'})
    # Synthetic-only provider; no credential is read or copied from production.
    config['proactive_generation_config']['LLM_PROVIDER_ID'] = 'mock/phase1'
    config['LLM_PROVIDER_ID'] = 'mock/phase1'
    config['basic_config']['plugin_specific_persona_id'] = 'qq_companion'
    config['plugin_specific_persona_id'] = 'qq_companion'
    # Empty old conversation state, as after a fresh-start cleanup. No candidate
    # or future time is seeded: the native schedule entry must create both.
    private.data['users'] = {}
    private.data['proactive_candidate_pool'] = []
    private.data['proactive_runtime'] = {}
    with ExitStack() as stack:
        for name, module in tuple(sys.modules.items()):
            if name.startswith(package + '.'):
                if hasattr(module, '_now_ts'):
                    stack.enter_context(patch.object(module, '_now_ts', lambda: now[0]))
                if hasattr(module, '_today_key'):
                    stack.enter_context(patch.object(module, '_today_key',
                        lambda: datetime.fromtimestamp(now[0], ZoneInfo('Asia/Shanghai')).date().isoformat()))
        stack.enter_context(patch.object(private, '_environment_now',
            lambda: datetime.fromtimestamp(now[0], ZoneInfo('Asia/Shanghai'))))
        stack.enter_context(patch.object(private, '_scheduler_maintenance_tasks', lambda: ()))
        original_framework = private._run_framework_agent_text
        async def select_synthetic_http_response(**kwargs):
            trace['model'] += 1
            # This selector is understood only by the loopback MockLLM. All
            # native prompt construction, Agent and provider HTTP remain real.
            kwargs['prompt'] += '\nPHASE3_PROACTIVE_NATIVE_PROBE PHASE3_PROACTIVE_DECISION_' + decision.upper()
            return await original_framework(**kwargs)
        stack.enter_context(patch.object(private, '_run_framework_agent_text', select_synthetic_http_response))
        original_schedule, original_should_send, original_tick = (
            private._schedule_next_proactive, private._should_send, private._tick_user)
        def observe_schedule(*args, **kwargs):
            trace['schedule'] += 1
            return original_schedule(*args, **kwargs)
        def observe_should_send(*args, **kwargs):
            trace['should_send'] += 1
            return original_should_send(*args, **kwargs)
        async def observe_tick(*args, **kwargs):
            trace['tick_user'] += 1
            return await original_tick(*args, **kwargs)
        stack.enter_context(patch.object(private, '_schedule_next_proactive', observe_schedule))
        stack.enter_context(patch.object(private, '_should_send', observe_should_send))
        stack.enter_context(patch.object(private, '_tick_user', observe_tick))
        try:
            private.config = config
            bootstrap.initialize_plugin_config(private, config)
            msg = AstrBotMessage(); msg.type = MessageType.FRIEND_MESSAGE
            msg.message_id = 'cold-plan-source-' + decision; msg.self_id = '90000'
            msg.sender = MessageMember('10001', 'Synthetic')
            msg.message = [Plain('合成身份绑定')]; msg.message_str = '合成身份绑定'
            msg.session_id = '10001'; msg.raw_message = {}
            platform = core.star_context.get_platform_inst('offline')
            event = AiocqhttpMessageEvent(msg.message_str, msg, platform.meta(), '10001', platform.bot)
            native_id = private._private_user_id_for_event(event, '10001')
            user = private._get_user(native_id)
            private._stamp_private_event_identity(user, event, '10001')
            private._note_private_user_umo(native_id, user, umo)
            user['umo'] = umo
            for key in ('last_private_seen', 'last_activity_at', 'last_user_message_at',
                        'last_reply_at', 'last_private_activity_at', 'last_private_reply_at'):
                user[key] = 0
            authorize_native_target(private, umo, approved=True, daily_limit=5)
            private._prime_enabled_user_schedules()
            checks['native_cold_start_disabled_empty'] = (
                private.max_daily_messages == 0 and not private.enable_llm_proactive_message
                and not user.get('next_proactive_at') and not user.get('proactive_impulses'))
            bridge = NativeProactiveBridge(private, runtime.guard, gate, message._ProactiveSendOutcome)
            original_record = bridge._record_decision
            def observe_decision(user_value, native, value):
                context = native.get('context') if isinstance(native, dict) else None
                previous = context.get('phase3_decision', {}) if isinstance(context, dict) else {}
                record = {'decision': value.get('decision'), 'delay_minutes': value.get('delay_minutes'),
                    'context_type_before': type(context).__name__, 'now': now[0],
                    'native_expire_at': native.get('expire_at'), 'decision_expires_at': value.get('expires_at'),
                    'previous_expires_at': previous.get('expires_at')}
                result = original_record(user_value, native, value)
                context = native.get('context')
                record['context_type_after'] = type(context).__name__
                record['recorded'] = deepcopy(context.get('phase3_decision', {})) if isinstance(context, dict) else {}
                decisions.append(record)
                return result
            bridge._record_decision = observe_decision
            bridge.attach(); bridge.activate_native_config()
            runtime.guard.phase3_proactive_enabled = True
            # Gate closures must not initialize candidates or invoke the model.
            gate.pause()
            await private._run_scheduler_cycle(immediate=True)
            checks['paused_does_not_plan_or_model'] = not user.get('next_proactive_at') and trace['model'] == 0
            gate.pause(0); gate.set_control('busy_until', now[0] + 3600)
            await private._run_scheduler_cycle(immediate=True)
            checks['busy_does_not_plan_or_model'] = not user.get('next_proactive_at') and trace['model'] == 0
            gate.set_control('busy_until', 0)
            now[0] -= 3 * 3600
            await private._run_scheduler_cycle(immediate=True)
            checks['outside_hours_does_not_plan_or_model'] = not user.get('next_proactive_at') and trace['model'] == 0
            now[0] += 3 * 3600
            random.seed(17)
            await private._run_scheduler_cycle(immediate=True)
            first = private._planned_proactive_impulse(user)
            checks['native_created_empty_plan'] = bool(
                first and user.get('next_proactive_at', 0) > now[0] and trace['schedule'] > 0)
            checks['planning_does_not_consume_evaluation'] = trace['model'] == 0 and not gate.db.execute(
                'SELECT 1 FROM proactive_generations').fetchone()
            if not first:
                return {'pass': False, 'checks': checks, 'trace': trace,
                    'diagnostic': deepcopy(private.data.get('proactive_runtime', {}))}
            first_id = first['id']
            # Exercise recovery with the actual generated candidate rather than
            # constructing a hand-picked plan accepted only by the test.
            now[0] = max(now[0], first['expire_at'] + 1)
            if datetime.fromtimestamp(now[0], ZoneInfo('Asia/Shanghai')).hour < 9:
                now[0] = datetime.fromtimestamp(now[0], ZoneInfo('Asia/Shanghai')).replace(hour=10).timestamp()
            random.seed(23)
            await private._run_scheduler_cycle(immediate=True)
            current = private._planned_proactive_impulse(user)
            checks['expired_plan_replaced_without_model'] = bool(
                current and current['id'] != first_id and user.get('next_proactive_at', 0) > now[0]
                and trace['model'] == 0)
            if not current:
                return {'pass': False, 'checks': checks, 'trace': trace,
                    'diagnostic': deepcopy(private.data.get('proactive_runtime', {}))}
            # Defer needs sufficient remaining native lifespan. Preserve the
            # normal expiry; a decision which cannot fit must become skip.
            before = runtime.guard.store.db.execute(
                "SELECT count(*) FROM deliveries WHERE kind='proactive'").fetchone()[0]
            preflights = []
            for _ in range(6):
                now[0] = max(now[0] + 1, float(user.get('next_proactive_at') or 0) + 1)
                preflights.append(list(private._should_send(user)))
                await private._run_scheduler_cycle(immediate=True)
                if trace['model']:
                    break
            rows = runtime.guard.store.db.execute(
                "SELECT state FROM deliveries WHERE kind='proactive' ORDER BY rowid").fetchall()[before:]
            generations = [r[0] for r in gate.db.execute('SELECT state FROM proactive_generations')]
            checks['actual_native_tick_should_send_model'] = bool(
                trace['tick_user'] and trace['should_send'] and trace['model'] == 1
                and bridge.metrics['generation_attempts'] == 1)
            # Native random opportunities can be shorter than a 60-minute
            # model defer. The bridge must then retire it, never extend TTL.
            observed = decisions[0] if decisions else {}
            defer_fits = bool(observed and observed['now'] + observed['delay_minutes'] * 60
                < observed['decision_expires_at'])
            expected = ('generated' if decision == 'send' else 'skipped'
                        if decision == 'skip' or not defer_fits else 'deferred')
            checks['model_decision_once'] = generations == [expected]
            if decision != 'send':
                checks['native_decision_metadata_retained'] = bool(decisions and all(
                    record['recorded'].get('decision') == ('defer' if decision == 'defer' and defer_fits else 'skip')
                    for record in decisions))
                checks['decision_never_extends_native_expiry'] = bool(decisions and all(
                    0 < record['recorded'].get('expires_at', 0) <= record['native_expire_at']
                    for record in decisions))
            checks['guard_delivery_matches_decision'] = (
                len(rows) == 2 and all(r[0] == 'sent' for r in rows)) if decision == 'send' else not rows
            delivery_metadata = {}
            if decision == 'send':
                settled = next((item for item in private._cleanup_proactive_impulses(user)
                                if item.get('id') == current['id']), {})
                delivery_metadata = (settled.get('context') or {}).get('phase3_delivery', {})
                checks['native_delivery_matches_guard'] = delivery_metadata == {
                    'state': 'sent', 'sent_count': 2, 'part_count': 2}
            next_before = user.get('next_proactive_at')
            await private._run_scheduler_cycle(immediate=True)
            checks['same_clock_does_not_repeat_generation'] = trace['model'] == 1
            if decision == 'defer' and defer_fits:
                checks['defer_deadline_preserved'] = user.get('next_proactive_at') == next_before
            elif decision == 'defer':
                checks['defer_beyond_expiry_is_skipped'] = generations == ['skipped']
            prior_model_count = trace['model']
            generation_metrics = dict(bridge.metrics)
            bridge.detach(); gate.close()
            gate = ProactiveGate(gate_path, allowed_session=umo, enabled=True, mode='continuous',
                max_per_day=5, max_generations_per_day=5, cooldown=10800,
                quiet_hours=(0, 9), clock=lambda: now[0])
            bridge = NativeProactiveBridge(private, runtime.guard, gate, message._ProactiveSendOutcome)
            bridge.attach(); bridge.activate_native_config()
            await private._run_scheduler_cycle(immediate=True)
            checks['bridge_restart_no_replay'] = trace['model'] == prior_model_count
            return {'pass': all(checks.values()), 'checks': checks, 'trace': trace,
                'preflights': preflights, 'generation_states': generations,
                'decision_observations': decisions,
                'native_delivery_metadata': delivery_metadata,
                'new_delivery_states': [r[0] for r in rows], 'metrics': generation_metrics,
                'restart_metrics': dict(bridge.metrics),
                'maintenance': deepcopy(private.data.get('proactive_runtime', {}).get('phase3_plan_maintenance', {}))}
        finally:
            if bridge is not None:
                bridge.detach()
            gate.close()
            runtime.guard.phase3_proactive_enabled = original_guard_enabled
            private.config = original_config
            bootstrap.initialize_plugin_config(private, original_config)
            private.data.clear(); private.data.update(original_data)
            random.setstate(random_state)
