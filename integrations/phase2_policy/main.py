"""Restore Phase 1 integration policy within the unmodified production entrypoint."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .plugin_permissions import apply_plugin_permissions
from .policy import PolicyViolation, validate_policy
from .redaction import install_redaction


class Phase2Policy(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        # sitecustomize already covers startup. Reinstalling is idempotent and
        # retains old secrets if this plugin is reloaded after a config change.
        install_redaction(context.get_config(), config_getter=context.get_config)
        super().__init__(context)
        self.admin_id = str(config.get("admin_id", ""))
        self.platform_id = str(config.get("platform_id", ""))
        self.ready = False
        self.report_path = Path(get_astrbot_data_path()) / "plugin_data/phase2_policy/readiness.json"
        try:
            self._report("BLOCKED", reason="startup_pending")
        except OSError:
            # Keep the instance and its deny-by-default event hook loaded even
            # when readiness storage is unavailable. Startup retries reporting.
            logger.error("Phase2Policy readiness storage unavailable; inbound remains blocked")

    def _report(self, status, **details):
        report = {"status": status, "scope": "startup_policy_only", **details}
        self.report_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp = self.report_path.with_suffix(".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temp, self.report_path)

    @filter.on_astrbot_loaded(priority=1000000)
    async def apply_startup_policy(self):
        self.ready = False
        extension = None
        try:
            config = self.context.get_config()
            extensions = [p.star_cls for p in self.context.get_all_stars()
                          if str(p.module_path).endswith(".phase3_runtime.main")
                          and p.activated and p.star_cls is not None
                          and getattr(p.star_cls, "enabled", False)]
            if len(extensions) > 1:
                raise PolicyViolation("duplicate_phase3_runtime")
            extension = extensions[0] if extensions else None
            capabilities = extension.policy_capabilities() if extension else None
            expected = {
                "astrbot_plugin_message_recorder": "0.0.4",
                "astrbot_plugin_memory_companion": "2.3.1",
                "astrbot_plugin_private_companion": "6.7.0",
                "phase1_guard": "0.1.0",
            }
            plugins = self.context.get_all_stars()
            for directory, version in expected.items():
                matched = [p for p in plugins
                           if str(p.module_path).endswith(f".{directory}.main")]
                if len(matched) != 1 or not matched[0].activated or \
                        matched[0].star_cls is None or str(matched[0].version) != version:
                    raise PolicyViolation("required_plugin_missing_or_wrong_version")
            guards = [p.star_cls for p in plugins
                      if str(p.module_path).endswith(".phase1_guard.main")]
            requested_phase3 = bool(getattr(guards[0], "phase3_enabled", False))
            runtime_config_path = Path(get_astrbot_data_path()) / "config/phase3_runtime_config.json"
            if runtime_config_path.exists():
                try:
                    raw = runtime_config_path.read_text(encoding='utf-8-sig')
                    if len(raw) > 65536:
                        raise ValueError("oversize")
                    requested_config = json.loads(raw)
                    if not isinstance(requested_config, dict) or \
                            type(requested_config.get("enabled", False)) is not bool:
                        raise ValueError("invalid_enabled")
                    requested_phase3 = requested_phase3 or requested_config.get("enabled", False)
                except (OSError, ValueError, TypeError):
                    raise PolicyViolation("invalid_phase3_runtime_config") from None
            if requested_phase3:
                # A requested Phase3 installation must never silently degrade
                # to Phase2 when runtime construction or plugin loading fails.
                runtime_rows = [p for p in plugins
                                if str(p.module_path).endswith(".phase3_runtime.main")]
                if len(runtime_rows) != 1 or extension is None or \
                        str(runtime_rows[0].version) != "0.1.0":
                    raise PolicyViolation("requested_phase3_runtime_unavailable")
                for plugin in plugins:
                    if any(part.startswith("phase3_") for part in str(plugin.module_path).split(".")[-2:-1]) and \
                            (not plugin.activated or plugin.star_cls is None):
                        raise PolicyViolation("requested_phase3_plugin_unavailable")
            validate_policy(config, self.admin_id, self.platform_id, require_key=False, capabilities=capabilities)
            permissions = await apply_plugin_permissions(SimpleNamespace(star_context=self.context))
            # Phase 3 prepare only wires services; activate starts background work
            # after validation. Phase 2 retains its pre-key ACL/persona setup.
            if extension:
                validate_policy(config, self.admin_id, self.platform_id, require_key=True, capabilities=capabilities)
            prompt = (await extension.prepare(self.admin_id, self.platform_id) if extension else
                      (Path(get_astrbot_data_path()) / "phase2/persona.md").read_text())
            if not prompt.strip():
                raise PolicyViolation("empty_persona")
            manager = self.context.persona_manager
            tools = ["web_search_deepseek", "deepseek_fetch_web_page"] if capabilities and capabilities.get("search") else []
            if extension:
                tools.extend(extension.memory_tool_names())
            if any(p.persona_id == "qq_companion" for p in manager.personas):
                await manager.update_persona("qq_companion", system_prompt=prompt, tools=tools, skills=[])
            else:
                await manager.create_persona("qq_companion", prompt, tools=tools, skills=[])
            # Applying the ACL/persona is useful even before the user supplies a key.
            # No QQ event is processed until both policy and provider are ready.
            validate_policy(config, self.admin_id, self.platform_id, require_key=True, capabilities=capabilities)
            if extension:
                await extension.activate()
            self._report("PASS", plugins=expected, plugin_permissions=permissions,
                         automatic_tools=bool(capabilities and capabilities.get("search")), real_api_tested=False, real_qq_tested=False)
            self.ready = True
            logger.info("Phase2Policy startup policy ready; online acceptance is still separate")
        except Exception as exc:
            self.ready = False
            if extension is not None:
                try:
                    await extension.terminate()
                except Exception:
                    logger.error("Phase3 runtime cleanup failed during blocked startup")
            reason = str(exc) if isinstance(exc, PolicyViolation) else type(exc).__name__
            self._report("BLOCKED", reason=reason)
            logger.error("Phase2Policy failed closed: %s", reason)

    @filter.event_message_type(filter.EventMessageType.ALL, priority=2000000)
    async def restrict_inbound(self, event: AstrMessageEvent):
        if event.get_platform_name() != "aiocqhttp":
            return
        if not self.ready or str(event.get_platform_id()) != self.platform_id or \
                not event.is_private_chat() or str(event.get_sender_id()) != self.admin_id:
            event.stop_event()
