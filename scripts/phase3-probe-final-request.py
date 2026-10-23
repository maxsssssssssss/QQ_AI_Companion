#!/usr/bin/env python3
"""Capture one native AstrBot SDK request in a disposable, offline snapshot.

Never run against live data. The caller supplies SQLite backup copies, excludes
QQ login state, and uses Docker --network none. No core.start(), model response,
tool execution, or outbound QQ call occurs. Full payload/logs are private 0600.
The public-safe report contains only roles, counts, IDs, hashes and offsets.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
MARKER = '[PHASE3_PROFILE_EVIDENCE_V2]'
EMPTY_RECALL = '没有检索到足够相关的长期记忆；只依据当前用户消息回复。'


class Captured(BaseException):
    """Do not let the SDK/provider's ordinary Exception retry path consume it."""


def digest(value):
    raw = value if isinstance(value, bytes) else str(value).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def private_write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError('output_symlink_forbidden')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def text_parts(payload):
    for message_index, message in enumerate(payload.get('messages', [])):
        content = message.get('content', '')
        if isinstance(content, str):
            yield message_index, 0, message.get('role'), content
        elif isinstance(content, list):
            for part_index, part in enumerate(content):
                if isinstance(part, dict) and isinstance(part.get('text'), str):
                    yield message_index, part_index, message.get('role'), part['text']


def occurrences(payload, needle):
    result = []
    if not needle:
        return result
    for message_index, part_index, role, content in text_parts(payload):
        start = 0
        while (offset := content.find(needle, start)) >= 0:
            result.append({'message': message_index, 'part': part_index,
                           'role': role, 'offset': offset})
            start = offset + len(needle)
    return result


def evidence_records(system_prompt):
    """Read the existing runtime evidence contract, never infer new facts."""
    index = system_prompt.find(MARKER)
    if index < 0:
        return []
    start = system_prompt.find('\n{', index)
    if start < 0:
        return []
    payload, _ = json.JSONDecoder().raw_decode(system_prompt[start + 1:])
    return payload.get('evidence', [])


def request_evidence_records(req):
    """Inspect current temporary native evidence; support the older baseline.

    Historical user/tool messages are intentionally never searched for facts.
    This inspects request assembly and grants no permission to use a record.
    """
    surfaces = []
    for part in getattr(req, 'extra_user_content_parts', None) or []:
        body = getattr(part, 'text', '')
        if (isinstance(body, str) and '<MemoryCompanion-Context>' in body and '</MemoryCompanion-Context>' in body
                and MARKER in body):
            surfaces.append(body)
    if len(surfaces) > 1:
        raise ValueError('ambiguous_current_profile_context')
    return evidence_records(surfaces[0] if surfaces else str(req.system_prompt or ''))


def analyze(payload, *, records=(), core_records=()):
    def match(record, text):
        return {'id': record.get('id', ''), 'version': record.get('version'),
                'chars': len(text), 'sha256': digest(text),
                'occurrences': occurrences(payload, text),
                # JSON escapes quotes/newlines inside the V2 evidence block.
                'json_string_occurrences': occurrences(payload, json.dumps(text, ensure_ascii=False)[1:-1])}
    messages = []
    for index, message in enumerate(payload.get('messages', [])):
        serialized = json.dumps(message, ensure_ascii=False, sort_keys=True)
        messages.append({'index': index, 'role': message.get('role'),
            'chars': len(serialized), 'sha256': digest(serialized),
            'text_chars': sum(len(t) for i, _, _, t in text_parts(payload) if i == index),
            'tool_calls': len(message.get('tool_calls', [])),
            'image_parts': sum(p.get('type') == 'image_url' for p in message.get('content', [])
                               if isinstance(p, dict)) if isinstance(message.get('content'), list) else 0})
    return {'model': payload.get('model'), 'stream': payload.get('stream', False),
        'messages': messages, 'message_count': len(messages),
        'tools': [tool.get('function', {}).get('name') for tool in payload.get('tools', [])],
        'profile_marker_positions': occurrences(payload, MARKER),
        'native_empty_recall_positions': occurrences(payload, EMPTY_RECALL),
        'profile_evidence_matches': [match(r, r.get('text', '')) for r in records],
        'core_memory_matches': [match(r, r.get('text', '')) for r in core_records]}


def sanitize_configs(state):
    changed = []
    def walk(value):
        if isinstance(value, list):
            return [walk(v) for v in value]
        if not isinstance(value, dict):
            return value
        result = {}
        for key, child in value.items():
            normalized = key.lower()
            if normalized in {'key', 'api_key', 'api_keys', 'token', 'access_token', 'password'} or normalized.endswith(('_api_key', '_secret', '_token')) or normalized == 'websearch_deepseek_key':
                result[key] = ['isolated-probe-dummy'] if isinstance(child, list) and child else ('isolated-probe-dummy' if child else child)
            elif normalized in {'proxy', 'http_proxy', 'https_proxy'}:
                result[key] = ''
            else:
                result[key] = walk(child)
        return result
    paths = [state / 'data/cmd_config.json', *sorted((state / 'data/config').glob('*.json'))]
    for path in paths:
        if path.is_symlink():
            raise ValueError('config_must_be_disposable_copy')
        value = walk(json.loads(path.read_text(encoding='utf-8-sig')))
        if path.name == 'cmd_config.json':
            value['platform'] = []
            value.setdefault('dashboard', {})['enable'] = False
            value['disable_metrics'] = True
            value['http_proxy'] = ''
        elif path.name == 'phase3_runtime_config.json':
            value['proactive'] = False
        elif path.name == 'astrbot_plugin_memory_companion_config.json':
            value.setdefault('memory_summary', {})['enabled'] = False
        private_write(path, value)
        changed.append(path.relative_to(state).as_posix())
    return changed


def validate_paths(args):
    state = args.state.resolve()
    marker = json.loads((state / 'probe-snapshot.json').read_text(encoding='utf-8-sig'))
    if marker.get('purpose') != 'isolated-final-request' or marker.get('disposable') is not True:
        raise ValueError('explicit_disposable_snapshot_required')
    if {name for _, name in socket.if_nameindex()} != {'lo'}:
        raise ValueError('docker_network_none_required')
    for path in (args.input, args.report, args.payload):
        if path and not path.resolve().is_relative_to(state):
            raise ValueError('input_and_private_outputs_must_be_inside_snapshot')
    if not (args.source / 'astrbot/core/astr_main_agent.py').is_file():
        raise ValueError('pinned_astrbot_source_required')
    return state, marker


async def native_probe(args, descriptor, report):
    import httpx
    from astrbot.api.message_components import Plain
    from astrbot.api.platform import AstrBotMessage, MessageMember, PlatformMetadata, MessageType
    from astrbot.core import LogBroker, db_helper
    from astrbot.core.core_lifecycle import AstrBotCoreLifecycle
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
    from astrbot.core.pipeline.context import PipelineContext, call_event_hook
    from astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal import InternalAgentSubStage
    from astrbot.core.astr_main_agent import build_main_agent, _provider_supports_modality
    from astrbot.core.star.star_handler import EventType
    from astrbot.core.utils.image_input import prepare_request_images
    from astrbot.core.utils.media_utils import normalize_model_image_max_size
    from plugin_permissions import apply_plugin_permissions
    from integrations.phase3_runtime.persona import install_persona

    core = AstrBotCoreLifecycle(LogBroker(), db_helper)
    reset_coro = None
    capture = {'phase': 'initialize', 'count': 0, 'records': [], 'core_records': []}

    def intercept(request):
        capture['count'] += 1
        report['captured_http_requests'] = capture['count']
        report['captured_phase'] = capture['phase']
        if capture['count'] != 1:
            raise Captured('request_limit_exceeded')
        payload = json.loads(request.content)
        report.update(analyze(payload, records=capture['records'], core_records=capture['core_records']))
        report['payload_sha256'] = digest(request.content)
        report['payload_bytes'] = len(request.content)
        if args.payload:
            private_write(args.payload, payload)
        report['status'] = 'CAPTURED' if capture['phase'] == 'main_runner_step' else 'PRE_MAIN_MODEL_REQUEST'
        # Persist before raising, even if an upstream hook catches BaseException.
        private_write(args.report, report)
        raise Captured('final_request_captured')

    class NoQQ:
        async def call_action(self, *a, **kw):
            report['qq_send_attempts'] += 1
            raise Captured('qq_send_forbidden')
        def __getattr__(self, name):
            return self.call_action

    try:
        await core.initialize()
        platform_types = [p.meta().name for p in core.platform_manager.get_insts()]
        if any(name != 'webchat' for name in platform_types):
            raise RuntimeError('external_platform_instances_must_be_absent')
        # Fixed core.initialize unconditionally creates a local queue-only
        # WebChat adapter even with platform=[]. Stop it before request setup.
        await core.platform_manager.terminate()
        report['native_builtin_webchat_created_then_stopped'] = bool(platform_types)
        for provider in core.provider_manager.provider_insts:
            client = getattr(provider, 'client', None)
            if client is None or not hasattr(client, '_client'):
                raise RuntimeError('unsupported_provider_transport')
            await client._client.aclose()
            client._client = httpx.AsyncClient(transport=httpx.MockTransport(intercept), trust_env=False)
            client.api_key = 'isolated-probe-dummy'
            # No retry changes: Captured leaves the ordinary retry path.
        await apply_plugin_permissions(core)
        runtime = next(p.star_cls for p in core.star_context.get_all_stars()
                       if p.activated and str(p.module_path).endswith('.phase3_runtime.main'))
        owner, platform = str(descriptor['owner_id']), str(descriptor['platform_id'])
        prompt = await runtime.prepare(owner, platform)
        tools = runtime.memory_tool_names()
        if runtime.settings.get('search'):
            tools += ['web_search_deepseek', 'deepseek_fetch_web_page']
        await install_persona(core.persona_mgr, prompt, tools=tools)
        # Foreground hooks require ready; activate() starts paid summary and
        # proactive workers. This isolated probe intentionally starts neither.
        runtime.ready = True
        report['foreground_only_activation'] = True

        text = descriptor['text']
        message = AstrBotMessage()
        message.type = MessageType.FRIEND_MESSAGE
        message.message_id = 'isolated-probe:' + str(descriptor['message_id']) + ':' + uuid.uuid4().hex[:12]
        message.self_id = str(descriptor['self_id'])
        message.sender = MessageMember(owner, 'Probe')
        message.message = [Plain(text)]
        message.message_str = text
        message.session_id = owner
        message.raw_message = {'message': [p.toDict() for p in message.message]}
        event = AiocqhttpMessageEvent(text, message, PlatformMetadata('aiocqhttp', 'isolated-probe', platform, support_streaming_message=False), owner, NoQQ())
        event.is_wake = True
        event.is_at_or_wake_command = True
        await runtime.guard.claim_inbound(event)
        await runtime.observe_original(event)
        await runtime.guard.group_input(event)
        if event.is_stopped():
            raise RuntimeError('probe_inbound_stopped')
        report['turn_id'] = event.get_extra('phase3_turn_id')
        report['source_message_id_changed_only_on_copy'] = True
        umo = event.unified_msg_origin

        if descriptor.get('synthetic_fixture'):
            if descriptor['synthetic_fixture'] != 'approved-profile-with-old-failures':
                raise ValueError('unknown_synthetic_fixture')
            from phase3_profile_fixture import manifest, approval, failed_tool_history
            from integrations.phase3_memory.profile_release import ProfileRelease
            from integrations.phase3_memory.core_profile import promote_l1
            ctx = runtime.memory.context_for_session(umo)
            m = manifest(ctx)
            m['entries'].append({'id': 'core_fixture', 'layer': 'L1',
                'text': '用户自述（测试核心）：希望清楚区分已有资料和未知信息。',
                'permission_scope': 'core', 'confidence_status': 'user_confirmed',
                'shared_experience': False, 'source_refs': ['synthetic-profile:core']})
            release = ProfileRelease(runtime.memory.service, ctx, args.state / 'private/synthetic-import.sqlite3')
            result = await release.apply(m, approval(m), apply=True)
            if result['state'] != 'APPLIED':
                raise RuntimeError('synthetic_fixture_import_failed')
            await promote_l1(release, m, approval(m), apply=True)
            await core.conversation_manager.new_conversation(umo, persona_id='qq_companion', content=failed_tool_history())
            report['synthetic_fixture_imported'] = True

        manager = core.conversation_manager
        cid = await manager.get_curr_conversation_id(umo)
        conv = await manager.get_conversation(umo, cid) if cid else None
        history = json.loads(conv.history) if conv and isinstance(conv.history, str) else (conv.history if conv else [])
        report['snapshot_history_items'] = len(history)
        report['snapshot_history_sha256'] = digest(json.dumps(history, ensure_ascii=False, sort_keys=True))
        if descriptor.get('trim_matching_last_pair'):
            if len(history) < 2 or history[-2].get('role') != 'user' or history[-1].get('role') != 'assistant' or history[-1].get('tool_calls'):
                raise ValueError('last_failed_pair_not_plain_user_assistant')
            content = history[-2].get('content')
            values = [content] if isinstance(content, str) else [p.get('text') for p in content if isinstance(p, dict) and p.get('type') == 'text']
            if text not in values:
                raise ValueError('latest_user_does_not_exactly_match_replay')
            await manager.update_conversation(umo, conversation_id=cid, history=history[:-2])
            report['copied_history_tail_removed'] = [{'role': h['role'], 'sha256': digest(json.dumps(h, ensure_ascii=False, sort_keys=True))} for h in history[-2:]]
        config = core.star_context.get_config(umo=umo)
        stage = InternalAgentSubStage()
        await stage.initialize(PipelineContext(config, core.plugin_manager, 'default', db_helper))
        build_config = replace(stage.main_agent_cfg, provider_wake_prefix='', streaming_response=stage.streaming_response)
        capture['phase'] = 'native_build_and_hooks'
        prepared = {}
        built = await build_main_agent(event=event, plugin_context=core.star_context, config=build_config, apply_reset=False, prepared_images=prepared)
        if capture['count']:
            raise Captured('unexpected_request_during_build')
        if built is None:
            raise RuntimeError('native_build_rejected')
        reset_coro = built.reset_coro
        req = built.provider_request
        stopped = await call_event_hook(event, EventType.OnLLMRequestEvent, req)
        if capture['count']:
            raise Captured('unexpected_request_during_hook')
        if stopped:
            raise RuntimeError('native_request_hook_stopped')
        capture['records'] = request_evidence_records(req)
        ctx = runtime.memory.context_for_session(umo)
        core_records = await runtime.memory.service.core_memories_for_context(ctx)
        capture['core_records'] = [{'id': r.id, 'version': getattr(r, 'version', None), 'text': r.content} for r in core_records]
        report['profile_prefetch'] = event.get_extra('phase3_profile_recall')
        report['provider_id'] = built.provider.provider_config.get('id')
        report['provider_model'] = built.provider.get_model()
        report['tools_after_request_hooks'] = req.func_tool.names() if req.func_tool else []
        options = build_config.provider_settings.get('image_compress_options', {})
        await prepare_request_images(req, event, max_size=normalize_model_image_max_size(options.get('max_size') if isinstance(options, dict) else None), prepared=prepared, supports_image=_provider_supports_modality(built.provider, 'image'))
        capture['phase'] = 'native_reset'
        if reset_coro:
            coro, reset_coro = reset_coro, None
            await coro
        capture['phase'] = 'main_runner_step'
        async for _ in built.agent_runner.step():
            if capture['count']:
                break
        if capture['count'] != 1:
            raise RuntimeError('no_http_request_captured')
    except Captured:
        if not capture['count']:
            raise RuntimeError('blocked_before_http_capture') from None
    finally:
        if reset_coro:
            reset_coro.close()
        try:
            await asyncio.wait_for(core.stop(), 20)
        except Exception as exc:
            report['cleanup_error_type'] = type(exc).__name__


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--payload', type=Path)
    parser.add_argument('--timeout', type=int, default=180)
    args = parser.parse_args()
    args.state, marker = validate_paths(args)
    descriptor = json.loads(args.input.read_text(encoding='utf-8-sig'))
    if descriptor.get('synthetic_fixture') and marker.get('synthetic_only') is not True:
        raise ValueError('synthetic_fixture_forbidden_for_real_snapshot')
    report = {'schema': 'qq-companion.final-request-probe.v1', 'status': 'NOT_CAPTURED',
        'network': 'none', 'core_start_called': False, 'qq_send_attempts': 0,
        'captured_http_requests': 0, 'paid_api_calls': 0, 'tools_executed': 0,
        'production_changed': False,
        'deviations': ['disposable SQLite backup state', 'dummy credentials', 'no platform instances',
                       'no background activation', 'new inbound ID only on copy',
                       'direct native main-agent assembly, not EventBus replay']}
    args.report.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_write(args.report, report)
    log_path = args.report.with_name(args.report.stem + '-native.log')
    log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    os.fchmod(log_fd, 0o600)
    with os.fdopen(log_fd, 'w') as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        try:
            report['sanitized_config_files'] = sanitize_configs(args.state)
            os.environ['ASTRBOT_ROOT'] = str(args.state)
            os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
            sys.path[:0] = [str(args.source), str(args.state), str(ROOT), str(ROOT / 'scripts'), str(ROOT / 'tests')]
            import runtime_bootstrap
            runtime_bootstrap.initialize_runtime_bootstrap()
            asyncio.run(asyncio.wait_for(native_probe(args, descriptor, report), args.timeout))
        except Exception as exc:
            report['status'] = 'ERROR'
            report['error_type'] = type(exc).__name__
            import traceback
            traceback.print_exc()
        finally:
            private_write(args.report, report)
    print(json.dumps({'status': report['status'], 'captured_http_requests': report['captured_http_requests'],
                      'qq_send_attempts': report['qq_send_attempts'], 'paid_api_calls': 0}))
    return 0 if report['status'] == 'CAPTURED' else 1


if __name__ == '__main__':
    raise SystemExit(main())
