"""Restore Phase 1 integration policy within the unmodified production entrypoint."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools
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
        self.report_path = StarTools.get_data_dir("phase2_policy") / "readiness.json"
        self._report("BLOCKED", reason="startup_pending")

    def _report(self, status, **details):
        report = {"status": status, "scope": "startup_policy_only", **details}
        temp = self.report_path.with_suffix(".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temp, self.report_path)

    @filter.on_astrbot_loaded(priority=1000000)
    async def apply_startup_policy(self):
        self.ready = False
        try:
            config = self.context.get_config()
            validate_policy(config, self.admin_id, self.platform_id, require_key=False)
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
            permissions = await apply_plugin_permissions(SimpleNamespace(star_context=self.context))
            prompt = (Path(get_astrbot_data_path()) / "phase2/persona.md").read_text()
            if not prompt.strip():
                raise PolicyViolation("empty_persona")
            manager = self.context.persona_manager
            if any(p.persona_id == "qq_companion" for p in manager.personas):
                await manager.update_persona("qq_companion", system_prompt=prompt, tools=[], skills=[])
            else:
                await manager.create_persona("qq_companion", prompt, tools=[], skills=[])
            # Applying the ACL/persona is useful even before the user supplies a key.
            # No QQ event is processed until both policy and provider are ready.
            validate_policy(config, self.admin_id, self.platform_id, require_key=True)
            self.ready = True
            self._report("PASS", plugins=expected, plugin_permissions=permissions,
                         automatic_tools=False, real_api_tested=False, real_qq_tested=False)
            logger.info("Phase2Policy startup policy ready; online acceptance is still separate")
        except Exception as exc:
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
