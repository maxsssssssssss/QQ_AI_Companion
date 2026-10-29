"""Public AstrBot hooks for durable duplicate rejection and text dispatch.

The pinned AstrBot after_message_sent hook also runs when delivery fails. This
plugin therefore awaits the public event.send API itself for its narrow offline,
non-streaming text profile, and clears that result to prevent a second dispatch.
"""

import asyncio
import math
import uuid

from .delivery import DeliveryReceipt, DeliveryService
from .turns import TurnCoordinator
from .bubbles import (Pacing, PROMPT, PROMPT_MARKER, semantic_plan, send_sequence, sha,
                      plain_qq_text, fallback_structure_protected, trim_boundary_newlines)

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star, StarTools

from .store import GuardStore
from . import recovery


class Phase1Guard(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.timeout = float(config.get("send_timeout_seconds", 2))
        if not math.isfinite(self.timeout) or not 0 < self.timeout <= 30:
            raise ValueError("send_timeout_seconds must be finite and in (0, 30]")
        self.store = GuardStore(StarTools.get_data_dir("phase1_guard") / "guard.sqlite3")
        self.phase3_enabled = config.get("phase3_enabled", False) is True
        self.phase3_proactive_enabled = config.get("phase3_proactive_enabled", False) is True
        self.multibubble_enabled = config.get("phase3_multibubble_enabled", True) is True
        self.casual_split_fallback_enabled = config.get("phase3_casual_split_fallback_enabled", False) is True
        self.plain_text_enabled = config.get("phase3_plain_text_enabled", False) is True
        self.empty_recovery_enabled = config.get("phase3_empty_recovery_enabled", False) is True
        self.pacing = Pacing(float(config.get('phase3_bubble_min_interval',2.0)),
                             float(config.get('phase3_bubble_max_interval',4.0)))
        self.stopping = False
        self._sending_tasks = set()
        self.phase3_admin_id = str(config.get("phase3_admin_id", ""))
        self.phase3_platform_id = str(config.get("phase3_platform_id", ""))
        self._delivery_observers = []
        self._input_components = {}
        self._input_snapshots = {}
        self._input_ready = {}
        self._input_media = {}
        self._media_provider_handles = {}
        self._media_download_handle = None
        self.delivery = None
        self.turns = None
        if self.phase3_enabled:
            if not self.phase3_admin_id.isdigit() or not self.phase3_platform_id:
                raise ValueError("Phase3 requires explicit private target identity")
            self.store.enable_phase3()
            self.store.enable_turn_diagnostics()
            self.store.recover_phase3_interrupted()
            self.store.enable_bubbles()
            self.store.recover_bubbles()
            if self.empty_recovery_enabled:
                recovery.initialize(self.store.db)
            self.delivery = DeliveryService(self.store, timeout=self.timeout)
            self.turns = TurnCoordinator(
                self.store, window=float(config.get("phase3_group_window", 0.8)),
                max_wait=float(config.get("phase3_group_max_wait", 3.0)),
                max_messages=int(config.get("phase3_group_max_messages", 8)))
            get_queue = getattr(context, 'get_event_queue', None)
            if callable(get_queue):
                from .media_download import install
                self._media_download_handle = install(get_queue(),
                    platform_id=self.phase3_platform_id, owner_id=self.phase3_admin_id,
                    on_admit=self.admit_input)
        recovered = self.store.recover_interrupted()
        logger.info("Phase1Guard initialized; interrupted attempts marked unknown: %s", recovered)

    def admit_input(self, event):
        """Claim in native queue order, before independent media preprocessing.

        This uses the same durable inbound ledger and turn coordinator. No
        waiting, secondary event queue or model work occurs at admission.
        """
        if event.get_extra('phase1_guard_key'):
            return True
        try:
            message_id = getattr(event.message_obj, 'message_id', None)
            if message_id is None or str(message_id) == '':
                raise ValueError('missing_message_id')
            key = (str(event.get_platform_id()), event.unified_msg_origin, str(message_id))
            raw = getattr(event.message_obj, 'raw_message', {})
            chain = raw.get('message', []) if isinstance(raw, dict) else []
            if not self.store.claim(*key, str(event.get_sender_id()), event.message_str, chain):
                event.stop_event()
                return False
            event.set_extra('phase1_guard_key', key)
            if self.phase3_enabled:
                turn, superseded = self.turns.observe(key, event.message_str,
                    groupable=not event.message_str.lstrip().startswith('/'))
                event.set_extra('phase3_turn_id', turn.turn_id)
                event.set_extra('phase3_source_message_ids', list(turn.source_ids))
                self._input_ready[key] = asyncio.get_running_loop().create_future()
                if superseded:
                    from astrbot.core.utils.active_event_registry import active_event_registry
                    active_event_registry.request_agent_stop_all(event.unified_msg_origin, exclude=event)
            return True
        except Exception as exc:
            logger.error('Phase1Guard admission failed closed: %s', type(exc).__name__)
            event.stop_event()
            return False

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE, priority=1000000)
    async def claim_inbound(self, event: AstrMessageEvent):
        if event.get_platform_name() != "aiocqhttp":
            return
        if self.phase3_enabled and (not event.is_private_chat()
                or str(event.get_sender_id()) != self.phase3_admin_id
                or str(event.get_platform_id()) != self.phase3_platform_id):
            event.stop_event()
            return
        if not self.admit_input(event):
            return
        key = event.get_extra('phase1_guard_key')
        if self.phase3_enabled:
            if (not event.is_private_chat() or str(event.get_sender_id()) != self.phase3_admin_id
                    or str(event.get_platform_id()) != self.phase3_platform_id):
                event.stop_event()
                return
            try:
                # Native preprocessing has already admitted/downloaded media.
                # Capture its bytes before any source event can be superseded.
                from .media import snapshot_components
                turn = self.turns.by_id[event.get_extra('phase3_turn_id')]
                snapshot = asyncio.create_task(snapshot_components(event.message_obj.message))
                self._input_snapshots[key] = snapshot
                try:
                    components, media_receipts = await snapshot
                except asyncio.CancelledError:
                    self.unavailable_source(key,event.message_str,'snapshot_cancelled')
                    raise
                except Exception as exc:
                    logger.warning('Phase3 optional media snapshot unavailable: %s',type(exc).__name__)
                    self.unavailable_source(key,event.message_str,'snapshot_unavailable')
                    components,media_receipts=self._input_components[key],self._input_media[key]
                self._input_components[key] = components
                self._input_media[key] = media_receipts
                if not self._input_ready[key].done():
                    self._input_ready[key].set_result(True)
                self.trace(event, 'inbound', {'source_count':len(turn.source_ids)})
                if media_receipts:
                    self.trace(event, 'media', {'receipts':media_receipts})
                valid_keys = {(t.platform,t.session,sid) for t in self.turns.by_id.values()
                    if t.phase in {'collecting','ready','generating'} for sid in t.source_ids}
                self._input_components = {k:v for k,v in self._input_components.items() if k in valid_keys}
                self._input_snapshots = {k:v for k,v in self._input_snapshots.items() if k in valid_keys or not v.done()}
                self._input_ready = {k:v for k,v in self._input_ready.items() if k in valid_keys or not v.done()}
                self._input_media = {k:v for k,v in self._input_media.items() if k in valid_keys}
                native_send = event.send
                event.set_extra("phase3_native_send", native_send)

                async def observed_send(message):
                    kind = event.get_extra("phase3_delivery_kind", "normal")
                    indices = event.get_extra("phase3_direct_indices", {})
                    index = indices.get(kind, 0)
                    indices[kind] = index + 1
                    event.set_extra("phase3_direct_indices", indices)
                    return await self.send_controlled(event, message, kind=kind, part_index=index)

                # Per-event adapter, never a process-global platform monkeypatch.
                # It also covers native error replies that bypass decoration.
                event.send = observed_send
            except Exception as exc:
                ready = self._input_ready.get(key)
                if ready is not None and not ready.done():
                    ready.set_result(False)
                event.stop_event()
                logger.error("Phase3Guard inbound failed closed: %s", type(exc).__name__)

    def unavailable_source(self,key,text,reason):
        from .media import unavailable_text
        self._input_components[key]=[Plain(text),Plain(unavailable_text(reason))]
        self._input_media[key]=[{'state':'unavailable','reason':reason}]
        ready=self._input_ready.get(key)
        if ready is not None and not ready.done():ready.set_result(False)

    @filter.on_decorating_result(priority=1000000)
    async def preserve_semantic_boundaries(self, event: AstrMessageEvent):
        """Consume the pinned parser's markers before native tag cleanup.

        Preserve boundaries as ordinary Plain components, not saved raw text.
        Every later safety/cleanup hook still sees and may alter/drop them;
        dispatch uses only the final surviving components.
        """
        if not self.phase3_enabled or not self.multibubble_enabled or not self.turn_is_current(event):
            return
        result = event.get_result()
        if result is None or not result.chain:
            return
        # Only consume model markers here. Automatic sentence boundaries are
        # chosen later from the final text that all native hooks have allowed.
        # Retain a boolean for source code/links/etc if cleanup removes syntax;
        # no original text or character-mapping metadata survives this hook.
        event.set_extra('phase3_fallback_source_protected',
            bool(event.get_extra('phase3_fallback_source_protected'))
            or fallback_structure_protected(result.get_plain_text()))
        parts = self.semantic_parts(result, event=event, allow_fallback=False)
        diagnostic=event.get_extra('phase3_segment_plan', {})
        source=diagnostic.get('source')
        event.set_extra('phase3_fallback_marker_protected',
            bool(event.get_extra('phase3_fallback_marker_protected'))
            or bool(diagnostic.get('raw_marker_present')))
        if source=='model_markers' and 1 < len(parts) <= 4:
            result.chain = [Plain(part.get_plain_text()) for part in parts]
            event.set_extra("phase3_semantic_boundaries", True)

    @filter.on_decorating_result(priority=-1000000)
    async def dispatch_text(self, event: AstrMessageEvent):
        key = event.get_extra("phase1_guard_key")
        result = event.get_result()
        if self.phase3_enabled:
            if result is None or not result.chain:
                return
            task=asyncio.current_task()
            self._sending_tasks.add(task)
            try:
                if not self.turn_is_current(event) or event.is_stopped():
                    return
                kind = event.get_extra("phase3_delivery_kind", "normal")
                if (event.get_extra("phase3_semantic_boundaries")
                        and 1 < len(result.chain) <= 4
                        and all(isinstance(part, Plain) for part in result.chain)):
                    parts = [result.derive([part]) for part in result.chain if part.text.strip()]
                    parts=[self.qq_plain_chain(part) for part in parts]
                    diagnostic=dict(event.get_extra('phase3_segment_plan',{}))
                    diagnostic.update(scope='final_allowed_text',planned_parts=len(parts))
                    event.set_extra('phase3_segment_plan',diagnostic)
                    self.trace(event,'cleaned_final',{'segment_plan':diagnostic})
                else:
                    parts = self.semantic_parts(result, event=event,
                        allow_fallback=self.casual_fallback_allowed(event),format_for_send=True)
                trimmed_newlines=0
                if kind!='progress':parts,trimmed_newlines=self.trim_bubble_edges(parts)
                self.trace(event,'cleaned_final',{'parts':len(parts),
                    'boundary_newlines_removed':trimmed_newlines,
                    'chars':sum(len(p.get_plain_text()) for p in parts),
                    'sha256':sha('\n'.join(p.get_plain_text() for p in parts))})
                async def send_part(index,delivery_id):
                    return await self.send_controlled(event,parts[index],kind=kind,
                        part_index=index,delivery_id=delivery_id,formatting_applied=True)
                if kind=='progress':
                    # Progress is a single separate bounded send, never a group.
                    await self.send_controlled(event,result,kind=kind)
                else:
                    delivered = await send_sequence(self.store,turn_id=event.get_extra('phase3_turn_id'),
                        kind=kind,texts=[p.get_plain_text() for p in parts],send_part=send_part,
                        is_current=lambda:self.turn_is_current(event) and not event.is_stopped(),pacing=self.pacing,
                        generation_hash=event.get_extra('phase3_generation_hash',''))
                    self.trace(event, 'cleaned_final', {'planned_parts':len(delivered.get('parts', [])),
                        'confirmed_parts':delivered.get('sent_count', 0),
                        'part_states':[p['state'] for p in delivered.get('parts', [])]})
                self.turns.complete(event.get_extra("phase3_turn_id"))
            finally:
                try:
                    self.update_delivery_history(event)
                finally:
                    # A projection/ledger exception must never leave the
                    # original result available for native automatic resend.
                    event.clear_result()
                    self._sending_tasks.discard(task)
            return
        if not key or result is None or not result.chain:
            return
        try:
            chain = [part.toDict() for part in result.chain]
            attempt = self.store.begin_outbound(key, result.get_plain_text(), chain)
        except Exception as exc:
            # Upstream logs hook exceptions and would continue sending, so a
            # failure to persist the attempt must explicitly clear its result.
            event.clear_result()
            event.stop_event()
            logger.error("Phase1Guard delivery observation failed closed: %s", type(exc).__name__)
            return
        cfg = self.context.get_config(event.unified_msg_origin)
        segmented = cfg.get("platform_settings", {}).get("segmented_reply", {}).get("enable", False)
        streaming = cfg.get("provider_settings", {}).get("streaming_response", False)
        if segmented or streaming or not all(isinstance(part, Plain) for part in result.chain):
            # Keep complex sends in the upstream path; a pre-send hook is not
            # evidence of delivery. No success is inferred from a later hook.
            self.store.finish(attempt, "unknown", "UnsupportedDeliveryProfile")
            logger.warning("Phase1Guard left complex delivery to upstream; status unknown")
            return
        if not result.get_plain_text().strip():
            # The native adapter returns without an API call for blank text.
            self.store.finish(attempt, "failed", "EmptyText")
            event.clear_result()
            return
        try:
            await asyncio.wait_for(event.send(result), timeout=self.timeout)
        except TimeoutError:
            # Timeout means the remote peer might have accepted the message.
            self.store.finish(attempt, "unknown", "TimeoutError")
            logger.error("Phase1Guard delivery status unknown: TimeoutError")
        except asyncio.CancelledError:
            self.store.finish(attempt, "unknown", "CancelledError")
            raise
        except Exception as exc:
            self.store.finish(attempt, "failed", type(exc).__name__)
            logger.error("Phase1Guard delivery failed: %s", type(exc).__name__)
        else:
            self.store.finish(attempt, "sent")
        finally:
            # Ownership of this result ends here, including timeout/failure.
            # A retry here could duplicate a delivery with a lost acknowledgement.
            event.clear_result()

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE, priority=-2000000)
    async def group_input(self, event: AstrMessageEvent):
        # Recorder and plugin inbound observers have seen each original message.
        # Only this final inbound hook changes the leader's model-facing text.
        if not self.phase3_enabled or not event.get_extra("phase1_guard_key"):
            return
        try:
            turn = await self.turns.wait(event.get_extra("phase1_guard_key"), claim_leadership=True)
            if turn is None:
                event.stop_event()
                return
            event.set_extra("phase3_source_message_ids", list(turn.source_ids))
            event.set_extra("phase3_original_text", event.message_str)
            event.message_str = "\n".join(turn.texts)
            platform, session, _ = event.get_extra('phase1_guard_key')
            grouped = []
            failures = []
            deadline=asyncio.get_running_loop().time()+8.0
            for source_id,source_text in zip(turn.source_ids,turn.texts):
                ready = self._input_ready.get((platform, session, source_id))
                if ready is not None:
                    try:
                        await asyncio.wait_for(asyncio.shield(ready),max(.001,deadline-asyncio.get_running_loop().time()))
                    except asyncio.CancelledError:
                        if ready.cancelled() and not asyncio.current_task().cancelling():
                            self.unavailable_source((platform,session,source_id),source_text,'preprocessing_cancelled')
                        else:
                            self.turns.cancel(turn.turn_id)
                            raise
                    except TimeoutError:
                        self.unavailable_source((platform,session,source_id),source_text,'preprocessing_timeout')
                grouped.extend(self._input_components.get((platform, session, source_id), []))
                failures.extend(x['reason'] for x in self._input_media.get((platform,session,source_id), [])
                                if x.get('state') == 'unavailable')
            if not self.turn_is_current(event):
                event.stop_event()
                return
            event.set_extra('phase3_media_failures', failures)
            if grouped:
                event.message_obj.message = grouped
            event.set_extra("phase3_grouped_text", event.message_str)
            self.trace(event, 'grouped', {'source_count':len(turn.source_ids),
                'component_types':[type(p).__name__ for p in event.message_obj.message]})
        except Exception as exc:
            event.stop_event()
            logger.error("Phase3Guard grouping failed closed: %s", type(exc).__name__)

    @filter.on_llm_request(priority=2000000)
    async def mark_generating(self, event: AstrMessageEvent, req):
        if self.phase3_enabled and event.get_extra("phase3_turn_id"):
            if (not event.is_private_chat() or str(event.get_sender_id()) != self.phase3_admin_id
                    or str(event.get_platform_id()) != self.phase3_platform_id):
                event.stop_event()
                return
            if not self.turns.generating(event.get_extra("phase3_turn_id")):
                event.stop_event()
                return
            self.trace(event, 'model_request', {'prompt_chars':len(str(getattr(req,'prompt',''))),
                'history_items':len(getattr(req,'contexts',[]) or [])})
            marker = PROMPT_MARKER
            prompt = str(getattr(req, "system_prompt", "") or "")
            if self.multibubble_enabled and marker not in prompt:
                req.system_prompt = prompt + "\n\n" + PROMPT
            if event.get_extra('phase3_media_failures'):
                from .media import unavailable_text
                req.system_prompt = str(req.system_prompt or '') + '\n' + '\n'.join(
                    unavailable_text(reason) for reason in dict.fromkeys(event.get_extra('phase3_media_failures')))

    @filter.on_agent_done(priority=-2000000)
    async def bind_delivery_history(self, event: AstrMessageEvent, run_context, response):
        """Use the original Agent message object, before its normal history save.

        The final response chain remains available for native decoration. The
        history projection starts unconfirmed, and becomes acknowledged text
        only after Guard sends; no conversation is cleared or replaced.
        """
        if not self.phase3_enabled or not event.get_extra('phase3_turn_id'):
            return
        token=event.get_extra('phase3_media_context_token')
        if token is not None:
            from .media_provider import reset_turn
            try:reset_turn(token)
            except ValueError:pass
            event.set_extra('phase3_media_context_token',None)
        messages=getattr(run_context,'messages',None)
        if not messages or messages[-1].role!='assistant' or getattr(messages[-1],'tool_calls',None):return
        message=messages[-1]
        content=message.content
        original=content if isinstance(content,str) else ''.join(getattr(p,'text','') for p in content or [])
        event.set_extra('phase3_generation_hash',event.get_extra('phase3_original_generation_hash') or sha(original))
        event.set_extra('phase3_history_message',message)
        from astrbot.core.agent.message import TextPart
        message.content=[TextPart(text='[本轮发送尚未确认，不得把生成内容当作用户已经收到。]')]

    @filter.on_llm_request(priority=-248999)
    async def retain_vision_history(self, event, req):
        # Immediately before the pinned Private 6.7.0 text-only compatibility
        # hook. Scope/capability are checked on this event's selected Provider.
        if not self.turn_is_current(event):return
        try:
            provider=await self.context.get_using_provider_async(event.unified_msg_origin)
            if 'image' not in (getattr(provider,'provider_config',{}).get('modalities') or []):return
            from .media_history import capture
            event.set_extra('phase3_vision_history',await capture(req.contexts))
        except Exception as exc:
            logger.warning('Phase3 optional image history unavailable: %s',type(exc).__name__)

    @filter.on_llm_request(priority=-249001)
    async def restore_vision_history(self, event, req):
        plan=event.get_extra('phase3_vision_history')
        event.set_extra('phase3_vision_history',None)
        if not plan or not self.turn_is_current(event):return
        from .media_history import restore
        result=restore(req.contexts,plan)
        self.trace(event,'media',{'history':result})

    @filter.on_agent_begin(priority=2000000)
    async def retain_generation_context(self, event, run_context):
        if self.turn_is_current(event):
            try:
                from .media import restore_current_images
                media = await restore_current_images(run_context.messages, event.message_obj.message)
                if media:
                    self.trace(event, 'media', {'restoration':media})
                from .media_provider import install, bind_turn
                provider = await self.context.get_using_provider_async(event.unified_msg_origin)
                if id(provider) not in self._media_provider_handles:
                    handle=install(provider)
                    if handle:self._media_provider_handles[id(provider)]=handle
                token=bind_turn(event.get_extra('phase3_turn_id'),
                    lambda record:self.trace(event,'media',{'fallback':record}))
                event.set_extra('phase3_media_context_token',token)
            except Exception as exc:
                # Optional image adaptation must not disable ordinary context
                # retention/recovery, including a Provider lookup failure.
                logger.warning('Phase3 optional media context unavailable: %s',type(exc).__name__)
        if self.empty_recovery_enabled and self.turn_is_current(event):
            event.set_extra('phase3_generation_context', run_context)

    @filter.on_agent_begin(priority=-3000000)
    async def trace_final_bubble_prompt(self, event, run_context):
        """Observe the built Agent system message, without storing its content."""
        if not self.turn_is_current(event):return
        try:
            pieces=[]
            for message in getattr(run_context, 'messages', []) or []:
                if getattr(message, 'role', None) != 'system':continue
                content=getattr(message, 'content', '')
                pieces.append(content if isinstance(content, str) else
                    ''.join(getattr(part, 'text', '') for part in content or []))
            system='\n'.join(pieces)
            self.trace(event, 'model_request', {'bubble_prompt':{
                'present':PROMPT in system, 'marker_count':system.count(PROMPT_MARKER),
                'prompt_sha256':sha(PROMPT), 'prompt_chars':len(PROMPT),
                'scope':'built_agent_system_messages'}})
        except Exception as exc:
            logger.warning('Phase3 optional bubble request diagnostics unavailable: %s',type(exc).__name__)

    @filter.on_llm_response(priority=3000000)
    async def recover_empty_final(self, event, response):
        # Read-only diagnostics before native cleanup. Keep only source
        # protection booleans, never text/offsets used to restore deleted data.
        event.set_extra('phase3_fallback_source_protected', False)
        event.set_extra('phase3_fallback_marker_protected', False)
        event.set_extra('phase3_semantic_boundaries', False)
        if response is not None:
            choices=getattr(getattr(response,'raw_completion',None),'choices',[]) or []
            reason=getattr(choices[0],'finish_reason',None) if choices else None
            self.trace(event, 'model_final', {'chars':len(response.completion_text or ''),
                'sha256':sha(response.completion_text or ''),
                'has_tools':bool(response.tools_call_name),
                'finish_reason':reason if reason in {'stop','length','tool_calls','content_filter'} else 'unavailable'})
            # Native cleanup can strip protected formatting later. Keep only
            # structural protection/counts, never another response body.
            if self.phase3_enabled and self.turn_is_current(event):
                try:
                    plan=semantic_plan(self.segment_parser(),response.completion_text or '',
                        enabled=self.multibubble_enabled, fallback_enabled=True)
                    diagnostic=self.segment_diagnostic(plan)
                    diagnostic['candidate_parts']=diagnostic.pop('planned_parts')
                    diagnostic.update(scope='raw_model_candidate',fallback_config_enabled=self.casual_split_fallback_enabled)
                    self.trace(event, 'model_final', {'segment_plan':diagnostic})
                    event.set_extra('phase3_fallback_source_protected',
                                    fallback_structure_protected(response.completion_text or ''))
                    event.set_extra('phase3_fallback_marker_protected',plan.raw_marker_present)
                except Exception as exc:
                    logger.warning('Phase3 optional bubble response diagnostics unavailable: %s',type(exc).__name__)
        # Before native cleanup: a deliberately dropped nonempty response must
        # never be resurrected. This only repairs an already-empty generation.
        if (not self.empty_recovery_enabled or not self.turn_is_current(event)
                or event.is_stopped() or response is None or response.role != 'assistant'
                or response.tools_call_name or (response.completion_text or '').strip()
                or event.get_extra('phase3_empty_recovery_handled')):
            return
        chain = getattr(getattr(response, 'result_chain', None), 'chain', []) or []
        if any(not isinstance(p, Plain) for p in chain):
            return
        context = event.get_extra('phase3_generation_context')
        messages = getattr(context, 'messages', None)
        if not messages or messages[-1].role != 'assistant' or messages[-1].tool_calls:
            return
        content = messages[-1].content
        original = content if isinstance(content, str) else ''.join(getattr(p, 'text', '') for p in content or [])
        if original.strip():
            return
        tid = event.get_extra('phase3_turn_id')
        if self.store.db.execute("SELECT 1 FROM deliveries WHERE turn_id=? AND kind IN ('normal','final')", (tid,)).fetchone():
            return
        event.set_extra('phase3_empty_recovery_handled', True)
        event.set_extra('phase3_original_generation_hash', sha(original))
        text, state = recovery.NOTICE, 'limited'
        if recovery.claim(self.store.db, tid):
            task = asyncio.current_task()
            self._sending_tasks.add(task)
            try:
                provider = await self.context.get_using_provider_async(event.unified_msg_origin)
                answer, usage = await recovery.continue_once(provider, messages, lambda: self.turn_is_current(event))
                state = 'recovered' if answer else 'failed'
                recovery.finish(self.store.db, tid, state, 'nonempty' if answer else 'invalid_final', usage)
                if answer:
                    text = answer
            except asyncio.CancelledError:
                recovery.finish(self.store.db, tid, 'cancelled', 'turn_cancelled')
                raise
            except Exception as exc:
                state = 'failed'
                recovery.finish(self.store.db, tid, state, 'timeout' if isinstance(exc, TimeoutError) else 'request_failed')
            finally:
                self._sending_tasks.discard(task)
        if not self.turn_is_current(event):
            return
        from astrbot.core.agent.message import TextPart
        response.completion_text = text
        messages[-1].content = [TextPart(text=text)]
        self.trace(event,'recovery',{'state':state,'chars':len(text)})
        # Continue the unchanged native cleanup, Guard delivery and history save.
        logger.warning('Phase3 empty final handled: %s; transport acceptance remains separate', state)

    def update_delivery_history(self,event):
        message=event.get_extra('phase3_history_message')
        if message is None:return
        rows=self.store.db.execute("SELECT text,state FROM deliveries WHERE turn_id=? AND kind IN ('normal','final') ORDER BY part_index",
            (event.get_extra('phase3_turn_id'),)).fetchall()
        sent=[r['text'] for r in rows if r['state']=='sent']
        text='\n'.join(sent)
        cancelled=self.store.db.execute("SELECT 1 FROM bubble_parts WHERE turn_id=? AND state!='sent' LIMIT 1",(event.get_extra('phase3_turn_id'),)).fetchone()
        if not sent:text='[本轮没有已确认送达的答复，不得声称用户看到了生成内容。]'
        elif cancelled:text+='\n[发送记录：其余气泡未确认送达，未重发；不要当作已经说过。]'
        from astrbot.core.agent.message import TextPart
        message.content=[TextPart(text=text)]
        # Private 6.7.0 defers the same native message with _no_save=True.
        # Guard owns this send and clears the native result, so its later
        # after-send callback cannot release that flag. Save only this durable
        # acknowledged projection (or fixed no-delivery notice), never raw text.
        message._no_save=False
        self.trace(event,'history',{'confirmed_parts':len(sent),'chars':len(text),
            'sha256':sha(text),'unconfirmed_tail':bool(cancelled)})

    def turn_is_current(self, event):
        return bool(self.phase3_enabled and self.turns and not self.stopping
                    and not event.get_extra("phase3_cancelled", False)
                    and not event.get_extra("agent_stop_requested", False)
                    and self.turns.is_current(event.get_extra("phase3_turn_id")))

    async def send_controlled(self, event, message_chain, *, kind="normal", part_index=0,
                              delivery_id=None, turn_id=None, formatting_applied=False):
        if not self.phase3_enabled or not self.delivery:
            raise RuntimeError("Phase3Disabled")
        if (not event.is_private_chat() or str(event.get_sender_id()) != self.phase3_admin_id
                or str(event.get_platform_id()) != self.phase3_platform_id):
            raise PermissionError("UnauthorizedDelivery")
        turn_id = turn_id or event.get_extra("phase3_turn_id")
        row = self.store.get_turn(turn_id)
        if not row or row['platform'] != self.phase3_platform_id or row['session'] != event.unified_msg_origin:
            raise PermissionError("WrongTurnDestination")
        if row['proactive']:
            raise PermissionError("UseProactiveDeliveryEntry")
        if not formatting_applied:message_chain=self.qq_plain_chain(message_chain)
        chain = [part.toDict() for part in message_chain.chain]
        text = "".join(getattr(part, "text", "") for part in message_chain.chain)
        supported = bool(message_chain.chain) and all(isinstance(part, Plain) for part in message_chain.chain)
        native = event.get_extra("phase3_native_send")
        if not callable(native):
            raise RuntimeError("NativeAdapterNotBound")

        async def sender():
            if not supported:
                return DeliveryReceipt(False, source="unsupported_media_disabled")
            await native(message_chain)
            # Pinned AiocqhttpMessageEvent.send awaits the API but returns None.
            return DeliveryReceipt(True, source="AstrBot4.29.1_event_send_ack")

        outcome = await self.delivery.send(turn_id=turn_id, text=text, chain=chain, sender=sender,
                                            kind=kind, part_index=part_index, delivery_id=delivery_id,
                                            is_current=lambda: self.turn_is_current(event) and not event.is_stopped())
        payload = {"platform": row["platform"], "session": row["session"], "turn_id": turn_id,
                   "source_message_ids": self.store.source_ids(turn_id), "text": text, "kind": kind,
                   "part_index": part_index, "delivery_id": outcome.delivery_id, "state": outcome.state}
        observed = event.get_extra("phase3_deliveries", [])
        if not any(item["delivery_id"] == outcome.delivery_id for item in observed):
            observed.append(payload)
            event.set_extra("phase3_deliveries", observed)
            await self._notify_delivery(payload)
        return outcome

    async def _send_proactive(self, *, platform, session, user_id, text, chain, sender,
                             turn_id=None, delivery_id=None, is_current=lambda: True,
                             part_index=0,finalize=True):
        """No forged inbound event. Caller supplies native adapter receipt callback.

        The lifecycle approval/pause gate belongs to the proactive coordinator;
        this boundary independently enforces the fixed private destination.
        """
        if not self.phase3_enabled or not self.delivery:
            raise RuntimeError("Phase3Disabled")
        expected = f"{self.phase3_platform_id}:FriendMessage:{self.phase3_admin_id}"
        if str(user_id) != self.phase3_admin_id or platform != self.phase3_platform_id or session != expected:
            raise PermissionError("UnauthorizedProactiveDestination")
        turn_id = turn_id or str(uuid.uuid4())
        if not self.store.get_turn(turn_id):
            self.store.create_turn(turn_id, platform, session, proactive=True)
        row = self.store.get_turn(turn_id)
        if not row['proactive'] or row['platform'] != platform or row['session'] != session:
            raise PermissionError("WrongProactiveTurn")
        outcome = await self.delivery.send(turn_id=turn_id, text=text, chain=chain, sender=sender,
                                           kind="proactive", part_index=part_index, delivery_id=delivery_id,
                                           is_current=is_current)
        if finalize:self.store.set_turn_state(turn_id, "done")
        return outcome

    def segment_parser(self):
        import importlib
        parser = None
        for metadata in self.context.get_all_stars():
            path = str(metadata.module_path)
            if path.endswith(".astrbot_plugin_private_companion.main") and metadata.activated:
                try:
                    parser = importlib.import_module(path.rsplit(".", 1)[0] + ".segmented_message")
                except Exception:
                    pass
                break
        return parser

    @staticmethod
    def segment_diagnostic(plan):
        return {key:getattr(plan,key) for key in ('source','reason','raw_marker_present',
            'raw_exact_marker_count','native_count','native_controlled')} | {'planned_parts':len(plan.texts)}

    def casual_fallback_allowed(self, event):
        return (event.get_extra('phase3_delivery_kind', 'normal') not in {'progress','final'}
                and not event.get_extra('phase3_search_executed')
                and not event.get_extra('phase3_fallback_source_protected')
                and not event.get_extra('phase3_fallback_marker_protected'))

    def semantic_parts(self, result, *, event=None, allow_fallback=True, format_for_send=False):
        """Native markers first; V4 fallback reads only the surviving body."""
        text = result.get_plain_text()
        if not all(isinstance(p, Plain) for p in result.chain):
            return [result]
        if event is not None:
            allow_fallback=(allow_fallback and self.casual_fallback_allowed(event)
                            and self.turn_is_current(event) and not event.is_stopped())
        plan=semantic_plan(self.segment_parser(),text,enabled=self.multibubble_enabled,
            fallback_enabled=self.casual_split_fallback_enabled,
            allow_fallback=allow_fallback)
        parts=[result.derive([Plain(part)]) for part in plan.texts]
        if format_for_send and plan.source!='smalltalk_fallback':
            parts=[self.qq_plain_chain(part) for part in parts]
        # A fallback plan already consists of exact, final permitted slices.
        # Do not run Markdown cleanup on each slice independently. Cosmetic
        # edge cleanup still runs once before transport, ledger and history.
        if event is not None:
            diagnostic=self.segment_diagnostic(plan)
            diagnostic.update(scope=('final_allowed_text' if format_for_send else 'before_native_decoration'),
                fallback_source_protected=bool(event.get_extra('phase3_fallback_source_protected')),
                fallback_marker_protected=bool(event.get_extra('phase3_fallback_marker_protected')))
            event.set_extra('phase3_segment_plan',diagnostic)
            self.trace(event,'cleaned_final',{'segment_plan':diagnostic})
        return parts

    @staticmethod
    def trim_bubble_edges(parts):
        """Finalize display text once, before ledger/transport/history agree on it."""
        cleaned=[];removed=0
        for part in parts:
            if len(part.chain)==1 and isinstance(part.chain[0],Plain):
                raw=part.chain[0].text
                text=trim_boundary_newlines(raw)
                removed+=len(raw)-len(text)
                if text!=raw:part=part.derive([Plain(text)])
            cleaned.append(part)
        return cleaned,removed

    def qq_plain_chain(self,result):
        if not self.plain_text_enabled or not all(isinstance(p,Plain) for p in result.chain):return result
        try:
            return result.derive([Plain(plain_qq_text(p.text)) for p in result.chain])
        except Exception:
            return result

    def trace(self, event, stage, values):
        if not self.phase3_enabled or not event.get_extra('phase3_turn_id'):
            return
        try:
            self.store.record_turn_stage(event.get_extra('phase3_turn_id'), stage, values)
        except Exception as exc:
            logger.warning('Phase3 optional turn diagnostics unavailable: %s',type(exc).__name__)

    def register_delivery_observer(self, callback):
        if callback not in self._delivery_observers:
            self._delivery_observers.append(callback)

    def unregister_delivery_observer(self, callback):
        if callback in self._delivery_observers:
            self._delivery_observers.remove(callback)

    async def _notify_delivery(self, payload):
        # Notification is observational. It cannot reclassify an acknowledged
        # send or trigger a retry. Observer implementations should only enqueue.
        for callback in tuple(self._delivery_observers):
            try:
                await asyncio.wait_for(callback(dict(payload)), timeout=1.0)
            except Exception as exc:
                logger.warning("Phase3Guard delivery observer failed: %s", type(exc).__name__)
        with self.store.db:
            self.store.db.execute("INSERT OR IGNORE INTO delivery_observed VALUES (?)", (payload["delivery_id"],))

    def session_busy(self, umo):
        if not self.turns:
            return False
        self.turns.prune()
        return any(turn.platform == self.phase3_platform_id and turn.session == umo
                   and turn.phase in {"collecting", "ready", "generating"}
                   for turn in self.turns.by_id.values())

    async def send_proactive(self, umo, text, *, delivery_id, is_current=lambda: True):
        if not self.phase3_proactive_enabled:
            raise PermissionError("ProactiveNotEnabled")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("EmptyProactiveText")
        expected = f"{self.phase3_platform_id}:FriendMessage:{self.phase3_admin_id}"
        if umo != expected:
            raise PermissionError("UnauthorizedProactiveDestination")
        platform = self.context.get_platform_inst(self.phase3_platform_id)
        if not platform or platform.meta().name != "aiocqhttp":
            raise PermissionError("WrongProactiveAdapter")
        turn_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "qq-companion-proactive:" + delivery_id))
        if not self.store.get_turn(turn_id):
            self.store.create_turn(turn_id,self.phase3_platform_id,umo,proactive=True)
        from astrbot.api.event import MessageEventResult
        parts=self.semantic_parts(MessageEventResult().message(text),format_for_send=True)
        parts,_=self.trim_bubble_edges(parts)
        def last_input():
            return self.store.db.execute('SELECT max(rowid) FROM inbound WHERE platform=? AND session=?',
                (self.phase3_platform_id,umo)).fetchone()[0]
        input_at_start=last_input()
        current=lambda: (not self.stopping and is_current() and not self.session_busy(umo)
                         and last_input()==input_at_start)
        async def send_part(index,part_id):
            message=parts[index]
            part_text=message.get_plain_text()
            async def sender():
                found=await self.context.send_message(umo,message)
                return DeliveryReceipt(found is True,source='AstrBot4.29.1_send_by_session_ack')
            result=await self._send_proactive(platform=self.phase3_platform_id,session=umo,
                user_id=self.phase3_admin_id,text=part_text,chain=[p.toDict() for p in message.chain],
                sender=sender,turn_id=turn_id,delivery_id=part_id,is_current=current,
                part_index=index,finalize=False)
            if not self.store.db.execute('SELECT 1 FROM delivery_observed WHERE delivery_id=?',(part_id,)).fetchone():
                await self._notify_delivery({'platform':self.phase3_platform_id,'session':umo,
                    'turn_id':turn_id,'source_message_ids':[],'text':part_text,'kind':'proactive',
                    'part_index':index,'delivery_id':part_id,'state':result.state})
            return result
        task=asyncio.current_task();self._sending_tasks.add(task)
        try:
            return await send_sequence(self.store,turn_id=turn_id,kind='proactive',
                texts=[p.get_plain_text() for p in parts],send_part=send_part,is_current=current,
                pacing=self.pacing,generation_hash=sha(text),delivery_prefix=delivery_id)
        finally:
            self.store.set_turn_state(turn_id,'done')
            self._sending_tasks.discard(task)

    async def terminate(self):
        self.stopping=True
        if self._media_download_handle:
            self._media_download_handle.restore()
        snapshots=[t for t in self._input_snapshots.values() if not t.done()]
        for task in snapshots:task.cancel()
        if snapshots:await asyncio.gather(*snapshots,return_exceptions=True)
        self._input_snapshots.clear()
        self._input_components.clear()
        for ready in self._input_ready.values():
            if not ready.done():ready.cancel()
        self._input_ready.clear()
        self._input_media.clear()
        pending=[t for t in self._sending_tasks if t is not asyncio.current_task() and not t.done()]
        for task in pending:task.cancel()
        if pending:await asyncio.gather(*pending,return_exceptions=True)
        for handle in self._media_provider_handles.values():handle.restore()
        self._media_provider_handles.clear()
        self.store.close()
