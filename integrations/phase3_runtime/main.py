"""Bind one persona and native companion services to the existing policy/Guard."""
import asyncio
from datetime import datetime, timezone
from dataclasses import replace
import importlib
import json
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .persona import render_persona
from .settings import validate_settings
from .recall import append_guidance, available_topics
from .profile_recall import recall_profile
from .profile_publication import publish_profile
from .proactive import ProactiveGate, NativeProactiveBridge, parse_proactive_controls, resolve_native_target

MEMORY_TOOLS = {"phase3_event", "phase3_memory_control", "memory_companion_navigate", "memory_companion_sources"}


class Phase3Runtime(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.settings = validate_settings(config)
        self.enabled = config.get("enabled", False)
        self.ready = False
        self.prepared = False
        self.guard = self.memory = self.proactive = self.proactive_bridge = None
        self.persona_prompt = ""
        self.identities, self.tasks = {}, set()
        self.memory_wake = asyncio.Event()
        self.metrics = {"memory_observations": 0, "memory_errors": 0, "memory_queue_overflow": 0}

    def policy_capabilities(self):
        return {"search": self.settings.get("search", False)}

    @filter.on_agent_begin(priority=1800000)
    async def prepare_context_fallback(self, event, run_context):
        limit = self.settings.get("context_compression_fallback_turns", 0)
        if not limit or not self.ready or not self.guard or not self.guard.turn_is_current(event):
            return
        try:
            from .context_compression import install_for_run
            installed = install_for_run(event, run_context, limit)
            self.guard.trace(event, 'model_request',
                             {'context_compression': {'fallback_turns': limit, 'installed': installed}})
            if not installed:
                logger.warning('Phase3 context fallback unavailable; native token protection retained')
        except Exception as exc:
            # This optional limit must not block chat or alter the native token
            # safety check. Cancellation is intentionally not caught here.
            logger.warning('Phase3 context fallback unavailable: %s', type(exc).__name__)

    def memory_tool_names(self):
        return sorted(MEMORY_TOOLS) if self.prepared and self.memory else []

    def _star(self, directory):
        matches = [p for p in self.context.get_all_stars()
                   if str(p.module_path).endswith("." + directory + ".main") and p.activated and p.star_cls is not None]
        if len(matches) != 1:
            raise ValueError("required_phase3_component_missing")
        return matches[0]

    async def prepare(self, admin_id, platform_id):
        if self.prepared or self.ready or self.memory or self.proactive_bridge:
            raise ValueError("phase3_terminate_required_before_prepare")
        if not self.enabled or not self.settings.get("persona"):
            raise ValueError("phase3_persona_not_enabled")
        self.admin_id, self.platform_id = str(admin_id), str(platform_id)
        template = (Path(get_astrbot_data_path()) / "phase3/persona-v0.1.md").read_text()
        self.persona_prompt = render_persona(template, self.settings.get("preferred_address", "你"))
        if self.settings.get("natural_chat"):
            self.guard = self._star("phase1_guard").star_cls
            if not self.guard.phase3_enabled or self.guard.phase3_admin_id != self.admin_id or self.guard.phase3_platform_id != self.platform_id:
                raise ValueError("guard_scope_mismatch")
        if self.settings.get("search"):
            search = self._star("phase3_search").star_cls
            if not search.settings.get("enabled") or str(search.settings.get("admin_id")) != self.admin_id or str(search.settings.get("platform_id")) != self.platform_id:
                raise ValueError("search_scope_mismatch")
        if self.settings.get("memory"):
            bridge = importlib.import_module(__package__.rsplit(".", 1)[0] + ".phase3_memory.bridge")
            service = bridge.resolve_memory_service(self.context)
            self.memory = bridge.MemoryCoordinator(service, StarTools.get_data_dir("phase3_runtime") / "memory-controls.sqlite3", {self.admin_id}, enabled=True, runtime_managed=True)
            self.memory.bind_archive(self.guard.store)
            self.guard.register_delivery_observer(self.delivery_observed)
        if self.settings.get("proactive"):
            if not self.guard.phase3_proactive_enabled:
                raise ValueError("proactive_guard_not_enabled")
            private = self._star("astrbot_plugin_private_companion")
            if str(private.version) != "6.7.0":
                raise ValueError("private_version_mismatch")
            native_id, native_user = resolve_native_target(private.star_cls,
                f"{self.platform_id}:FriendMessage:{self.admin_id}")
            if (not private.star_cls._user_enabled_for_proactive(native_id, native_user)
                    or int(native_user.get("proactive_daily_limit", 0)) <= 0):
                raise PermissionError("native_target_migration_approval_required")
            module = importlib.import_module(private.module_path.rsplit(".", 1)[0] + ".proactive_message")
            self.proactive = ProactiveGate(StarTools.get_data_dir("phase3_runtime") / "proactive-controls.sqlite3",
                allowed_session=f"{self.platform_id}:FriendMessage:{self.admin_id}", enabled=False,
                **self.settings.get("proactive_window", {}))
            self.proactive_bridge = NativeProactiveBridge(private.star_cls, self.guard, self.proactive, module._ProactiveSendOutcome,
                history_policy=self.settings.get("proactive_history"),
                event_source=self._proactive_events, event_resolver=self._proactive_event,
                event_delivered=self._event_prompted)
        self.prepared = True
        return self.persona_prompt

    async def activate(self):
        if not self.prepared or self.ready:
            raise ValueError("phase3_not_prepared")
        try:
            if self.memory:
                approval_path = Path(get_astrbot_data_path()) / 'phase3/memory-backlog-approval.json'
                approval = json.loads(approval_path.read_text()) if approval_path.exists() else None
                await self.memory.activate_runtime(approved_backlog=approval)
            if self.proactive_bridge:
                self.proactive.enabled = True
                self.proactive_bridge.attach()
                self.proactive_bridge.activate_native_config()
        except BaseException:
            if self.memory:
                self.memory.disable_runtime()
            if self.proactive_bridge:
                self.proactive.enabled = False
                self.proactive_bridge.detach()
            raise
        self.ready = True
        if self.memory:
            task = asyncio.create_task(self._memory_worker())
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
            self.memory_wake.set()

    def _proactive_event(self, umo, event_id):
        ctx = self.memory.context_for_session(umo) if self.memory else None
        if ctx is None:
            return {"eligible": False, "state": "identity_unavailable", "revision": 0}
        return self.memory.proactive_event(ctx, event_id, now=datetime.now(timezone.utc).isoformat())

    async def _proactive_events(self, umo):
        ctx = self.memory.context_for_session(umo) if self.memory else None
        if ctx is None:
            return []
        results = []
        for row in self.memory.due_events(ctx, now=datetime.now(timezone.utc).isoformat()):
            if not self._proactive_event(umo, row["event_id"])["eligible"]:
                continue
            record = await self.memory.service.store.get_memory(row["memory_id"])
            if record and self.memory.revision.correction_owner(ctx, record):
                results.append({"event_id": row["event_id"], "revision": row["revision"],
                    "source_id": row["source_id"], "user_quote": record.content, "expires_at": row["expires_at"]})
        return results

    def _event_prompted(self, umo, event_id, expected_revision, receipt):
        ctx = self.memory.context_for_session(umo) if self.memory else None
        if ctx is None:
            return {"state": "identity_unavailable"}
        return self.memory.mark_event_prompted(ctx, event_id, expected_revision=expected_revision, delivery=receipt)

    def _authorized(self, event):
        return (self.ready and event.get_platform_name() == "aiocqhttp" and event.is_private_chat()
                and str(event.get_sender_id()) == self.admin_id and str(event.get_platform_id()) == self.platform_id)

    async def _reset_memory_context(self, event):
        if not event.get_extra('phase3_memory_context_reset'):
            await self.context.conversation_manager.new_conversation(event.unified_msg_origin,persona_id='qq_companion')
            event.set_extra('phase3_memory_context_reset',True)

    async def _memory_worker(self):
        # Guard retains the original content. Only durable source cursors live
        # in the memory helper; wakes coalesce instead of dropping observations.
        while self.ready:
            try:
                await asyncio.wait_for(self.memory_wake.wait(), timeout=30)
            except TimeoutError:
                pass
            self.memory_wake.clear()
            try:
                while self.ready:
                    count = await self.memory.drain_archive(self.guard.store, limit=32)
                    self.metrics["memory_observations"] += count
                    if not count:
                        break
                    await asyncio.sleep(0)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.metrics["memory_errors"] += 1
                logger.warning("Phase3 memory archive recovery deferred: %s", type(exc).__name__)

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE, priority=900000)
    async def observe_original(self, event: AstrMessageEvent):
        if not self._authorized(event) or not event.get_extra("phase1_guard_key"):
            return
        text = event.message_str.strip()
        if self.proactive:
            for action in parse_proactive_controls(text, now=self.proactive.clock(), window_end=self.proactive.end):
                self.proactive.set_control("last_control_source", event.message_obj.message_id)
                if action["kind"] == "pause":
                    self.proactive.pause(action["until"])
                elif action["kind"] == "busy":
                    self.proactive.set_control("busy_until", action["until"])
                elif action["kind"] == "block_last_topic":
                    topic = self.proactive._get("last_topic")
                    if topic:
                        self.proactive.block_topic(topic)
        if not self.memory:
            return
        # The authorized runtime has one fixed persona. Native Memory's event
        # resolver reads this host identity; its native tools must use the same
        # persona as background observations, including after process restart.
        event.persona_id = "qq_companion"
        ctx = self.memory.context(session_id=event.unified_msg_origin, user_id=event.get_sender_id(),
                                  bot_id=event.get_self_id(), message_id=event.message_obj.message_id,
                                  message_text=text)
        self.identities[event.unified_msg_origin] = ctx
        self.memory.remember_context(ctx)
        if text in {"确认更正", "确认这次身份变更", "取消这次身份变更"}:
            result = await self.memory.confirm_identity_change(ctx, self.guard.store,
                confirmation_id=str(event.message_obj.message_id), text=text)
            event.set_extra("phase3_identity_review", result["state"])
        elif self.memory.defer_identity_change(ctx, str(event.message_obj.message_id), event.message_str):
            event.set_extra("phase3_identity_review", "confirmation_required")
        if text in {"别记了", "不要再记录我", "停止使用我的记忆"}:
            # Restriction must take effect before the next model request; it is
            # not queued behind ordinary extraction and does not erase archives.
            await self.memory.pause(ctx)
            await self._reset_memory_context(event)
            event.set_extra('phase3_memory_paused',True)
            return
        self.memory_wake.set()

    async def delivery_observed(self, payload):
        if not self.ready or not self.memory or payload["state"] != "sent" or payload["kind"] == "progress":
            return
        ctx = self.identities.get(payload["session"]) or self.memory.context_for_session(payload["session"])
        if ctx is None:
            # The Guard receipt remains durable. A later authorized inbound
            # establishes identity and wakes recovery; never invent a bot ID.
            self.metrics["memory_errors"] += 1
            return
        self.memory_wake.set()

    @filter.llm_tool(name="phase3_event")
    async def phase3_event(self, event: AstrMessageEvent, action: str, event_id: str = "",
                           state: str = "", quote: str = "", due_at: str = "",
                           expected_revision: int = 0):
        """Read or update the user's real event lifecycle from current quoted evidence.

        First list existing events and reuse the same event_id/revision for changes.
        Never invent a completed activity, user response, due time or shared experience.
        Dates must be explicit YYYY-MM-DD HH:MM in the user's quote; otherwise
        leave due_at empty and ask a brief clarification. This is not a reminder.

        Args:
            action(string): list or update.
            event_id(string): Existing ID for an update, or a short unique new ID.
            state(string): scheduled, awaiting_result, responded, completed, cancelled or do_not_discuss.
            quote(string): Exact quote from the current user's original message.
            due_at(string): ISO timestamp with timezone, only when explicitly stated.
            expected_revision(number): Revision returned by list, zero for a new event.
        """
        if not self._authorized(event) or not self.memory:
            return json.dumps({"ok": False, "reason": "memory_not_authorized"})
        ctx = self.memory.context_for_session(event.unified_msg_origin)
        if ctx is None:
            return json.dumps({"ok": False, "reason": "identity_unavailable"})
        try:
            if action == "list":
                return json.dumps({"ok": True, "events": await self.memory.list_events(ctx)}, ensure_ascii=False)
            if action != "update":
                return json.dumps({"ok": False, "reason": "unsupported_action"})
            source_ids = event.get_extra("phase3_source_message_ids") or [str(event.message_obj.message_id)]
            source = next((row for sid in source_ids if (row := self.guard.store.db.execute(
                "SELECT * FROM inbound WHERE platform=? AND session=? AND sender_id=? AND message_id=?",
                (self.platform_id, event.unified_msg_origin, self.admin_id, str(sid))).fetchone())
                and quote and quote in row["text"]), None)
            if source is None:
                return json.dumps({"ok": False, "reason": "current_original_quote_required"})
            await self.memory.observe(ctx, source_id=source["message_id"], content=source["text"], occurred_at=source["received_at"])
            result = await self.memory.user_event(ctx, event_id=event_id, state=state, quote=quote,
                source_id=source["message_id"], original_text=source["text"], occurred_at=source["received_at"],
                due_at=due_at, expected_revision=expected_revision)
            return json.dumps({"ok": True, **result}, ensure_ascii=False)
        except ValueError as exc:
            return json.dumps({"ok": False, "reason": str(exc)}, ensure_ascii=False)

    @filter.llm_tool(name="phase3_memory_control")
    async def phase3_memory_control(self, event: AstrMessageEvent, action: str, query: str,
                                    quote: str = "", source_ref: str = "", expected_version: str = ""):
        """Find and stop using a specifically named derived memory, preserving the raw archive.

        Search first; do not act on an ambiguous target. Use source_ref and
        source_version returned in this turn, plus the user's exact current
        instruction. stop_use/delete_derived withdraw that native source and
        dependent memories; no_mention suppresses both automatic recall and
        proactive event selection using the same native source withdrawal.
        Repeat for each matching source the user explicitly named, including
        copied bot replies. If search is incomplete or the budget ends, state
        the partial result; never promise a topic was fully forgotten.
        Native withdrawal affects the entire source and its derived facts.
        Before mutating, the current user must explicitly confirm 整条来源;
        explain this impact when requesting that confirmation.
        Physical deletion of the original Guard/Recorder archive is unavailable.

        Args:
            action(string): search, stop_use, delete_derived or no_mention.
            query(string): Short literal identifying the specific information, never the complete profile.
            quote(string): Exact current user instruction naming the information and requested operation.
            source_ref(string): timeline source_ref returned by search.
            expected_version(string): Exact source_version returned by search.
        """
        if not self._authorized(event) or not self.memory:
            return json.dumps({"ok": False, "reason": "memory_not_authorized"})
        try:
            result = await self.memory.control_memory(event, action=action, query=query,
                quote=quote, source_ref=source_ref, expected_version=expected_version)
            if result.get('ok') and action in {'stop_use','delete_derived','no_mention'}:
                await self._reset_memory_context(event)
                result.update(context_reset=True,original_conversation_retained=True,
                              impact='whole_source_and_dependent_memories')
            return json.dumps(result, ensure_ascii=False)
        except ValueError as exc:
            return json.dumps({"ok": False, "reason": str(exc)}, ensure_ascii=False)

    @filter.on_llm_request(priority=1000000)
    async def expose_core(self, event: AstrMessageEvent, req):
        if req.func_tool and (not self._authorized(event) or not self.memory):
            from astrbot.core.agent.tool import ToolSet
            req.func_tool = ToolSet(tools=[t for t in req.func_tool.tools if t.name not in MEMORY_TOOLS])
        if self._authorized(event):
            # This excludes the subsequently injected private memories/profile.
            event.set_extra("phase3_core_persona", self.persona_prompt)
            if event.get_extra('phase3_memory_paused'):
                req.system_prompt += "\n记忆采集和召回已停用，并已开启新对话上下文；原始档案及旧对话保留。可简短如实告知，不能说已彻底删除原始数据。"
            if event.get_extra("phase3_identity_review") == "confirmation_required":
                req.system_prompt += "\n本轮重大身份变更尚未写入长期记忆。请只做一次简短确认，告知可回复‘确认这次身份变更’，不要声称已经更正。"
            elif event.get_extra("phase3_identity_review") == "clarification_required":
                req.system_prompt += "\n身份变更待确认条目不唯一。请让用户用‘确认更正：具体资料’明确要修改的内容，不要猜测。"
            if self.memory:
                ctx = self.memory.context_for_session(event.unified_msg_origin)
                if ctx:
                    try:
                        await asyncio.wait_for(self.memory.reconcile_assertions(ctx), 1.0)
                    except Exception as exc:
                        # Derived-state maintenance cannot prevent ordinary
                        # chat. Native retrieval still revalidates every row.
                        logger.warning('Phase3 assertion maintenance deferred: %s', type(exc).__name__)

    @filter.on_llm_request(priority=-800000)
    async def guide_memory_recall(self, event: AstrMessageEvent, req):
        if (not self._authorized(event) or not self.memory
                or not self.settings.get('memory_recall_guidance', False)
                or event.get_extra('phase3_memory_paused')):
            return
        ctx = self.memory.context_for_session(event.unified_msg_origin)
        if ctx and self.memory.service._scope_feature_enabled(ctx, 'recall'):
            tools_available = append_guidance(req)
            if tools_available:
                try:
                    topics = await asyncio.wait_for(available_topics(self.memory.service, ctx, event), 1.0)
                except TimeoutError:
                    topics = []
                except Exception as exc:
                    logger.warning('Phase3 memory topic directory unavailable: %s', type(exc).__name__)
                    topics = []
                if topics and not event.get_extra('phase3_memory_topics_added'):
                    req.system_prompt += '\n本轮可检索的部分已确认资料主题：' + '；'.join(topics)
                    event.set_extra('phase3_memory_topics_added', True)
            evidence = await recall_profile(self.memory.service, ctx, event,
                query=event.message_str,
                aliases=self.settings.get('memory_topic_aliases') or None)
            if evidence is not None:
                publication = await publish_profile(self.memory.service, ctx, event, req, evidence)
                if not publication.published:
                    # Never fall back to unchecked facts on a second surface.
                    evidence = replace(evidence, status='unavailable', coverage='unknown',
                        reason=publication.reason, records=[])
                    req.system_prompt = str(req.system_prompt or '') + '\n\n' + evidence.render()
                diagnostic = {**evidence.diagnostic(), 'native_published': publication.published,
                    'statistics_deferred': publication.statistics_deferred,
                    'publication_status': 'published' if publication.published else 'unavailable',
                    'native_ref_count': publication.native_ref_count,
                    'native_context_chars': publication.native_context_chars,
                    'native_snapshot_present': publication.native_snapshot_present}
                event.set_extra('phase3_profile_recall', diagnostic)
                if self.guard:
                    self.guard.trace(event, 'profile_recall', diagnostic)

    async def terminate(self):
        self.ready = False
        self.prepared = False
        if self.memory:
            self.memory.disable_runtime()
        if self.proactive_bridge:
            self.proactive_bridge.detach()
        if self.guard:
            self.guard.unregister_delivery_observer(self.delivery_observed)
        for task in tuple(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.memory:
            await self.memory.close()
            self.memory = None
        if self.proactive:
            self.proactive.close()
            self.proactive = None
        self.proactive_bridge = None
        self.tasks.clear()
