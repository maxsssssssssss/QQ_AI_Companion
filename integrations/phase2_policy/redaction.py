"""Redact configured credentials before AstrBot's logging sinks see a record.

AstrBot 4.29.1 feeds both Loguru stdout and its WebUI LogBroker from stdlib
logging. Its OpenAI provider logs the first 12 key characters on HTTP 429.
Install this module before importing AstrBot; it has no AstrBot dependency.
This protects logging APIs, not arbitrary print(), native code or chat storage.
"""
from __future__ import annotations

import logging
import re
from threading import RLock
import traceback

REDACTED = "[REDACTED]"
_COMMON_API_KEY = re.compile(r"(?i)(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{4,}")
_KEY_PREFIX_MESSAGE = re.compile(r"(?i)(当前\s*Key\s*[:：]\s*)[^\s,'\"}\]\[]+")
_BEARER = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/-]+=*")


def configured_secrets(config: dict) -> set[str]:
    """Collect only known credential fields, never user messages or personas."""
    values = set()

    def retain(value):
        if isinstance(value, str) and value:
            values.add(value)
        elif isinstance(value, (list, tuple)):
            for item in value:
                retain(item)

    for source in config.get("provider_sources", []):
        retain(source.get("key"))
    for provider in config.get("provider", []):
        retain(provider.get("key"))
    for platform in config.get("platform", []):
        for field in ("ws_reverse_token", "access_token", "token"):
            retain(platform.get(field))
    dashboard = config.get("dashboard", {})
    for field in ("jwt_secret", "password", "pbkdf2_password"):
        retain(dashboard.get(field))
    # Retain exact keys and the partial value emitted by the pinned provider.
    return values | {value[:12] for value in values if len(value) >= 12}


class CredentialRedactor:
    def __init__(self):
        self._lock = RLock()
        self._values: tuple[str, ...] = ()
        self._config_getters = []

    def update(self, config):
        # Retain old keys as well: in-flight requests can still refer to them.
        with self._lock:
            self._values = tuple(sorted(
                set(self._values) | configured_secrets(config),
                key=len, reverse=True,
            ))

    def register_config_getter(self, getter):
        with self._lock:
            if getter is not None and getter not in self._config_getters:
                self._config_getters.append(getter)

    def text(self, value) -> str:
        # The Dashboard can replace a disabled provider key while running.
        # Read the live in-memory config before logging, without disk I/O.
        with self._lock:
            getters = tuple(self._config_getters)
        for getter in getters:
            self.update(getter())
        result = str(value)
        with self._lock:
            values = self._values
        for secret in values:
            result = result.replace(secret, REDACTED)
        # Also cover a newly submitted key before a Dashboard request commits
        # it to the live config, including the upstream partial-key warning.
        result = _COMMON_API_KEY.sub(REDACTED, result)
        result = _KEY_PREFIX_MESSAGE.sub(lambda match: match[1] + REDACTED, result)
        result = _BEARER.sub(lambda match: match[1] + REDACTED, result)
        return result

    def _exception(self, exc_info) -> str:
        try:
            # Unlike Loguru diagnose=True, this never renders frame locals.
            return self.text("".join(traceback.format_exception(*exc_info)))
        except Exception:
            return "[exception details unavailable]"

    def redact_record(self, record):
        try:
            message = self.text(record.getMessage())
        except Exception:
            # A formatting failure must not make logging print raw args.
            message = "[log message formatting unavailable]"
        if record.exc_info:
            message += "\n" + self._exception(record.exc_info)
        elif record.exc_text:
            message += "\n" + self.text(record.exc_text)
        record.msg, record.args = message, ()
        # Neither handler may inspect the original exception or its locals.
        record.exc_info = record.exc_text = None
        if record.stack_info:
            record.stack_info = self.text(record.stack_info)
        return record

    def _metadata(self, value):
        if isinstance(value, dict):
            return {self.text(key): self._metadata(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._metadata(item) for item in value]
        if isinstance(value, str):
            return self.text(value)
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return self.text(value)

    def patch_loguru(self, record):
        """Protect direct Loguru calls, including bound metadata/exceptions."""
        try:
            message = self.text(record["message"])
            if record.get("exception"):
                message += "\n" + self._exception(record["exception"])
            record["message"] = message
            record["extra"] = self._metadata(record.get("extra", {}))
        except Exception:
            record["message"] = "[log record unavailable]"
            record["extra"] = {}
        # AstrBot's console format requires these fields. A direct Loguru call
        # lacks its per-logger enrichment; allowing formatting to fail would
        # make Loguru print an unfiltered active-exception diagnostic to stderr.
        extra = record["extra"]
        level = record.get("level")
        level_name = getattr(level, "name", "INFO")
        level_no = getattr(level, "no", 20)
        defaults = {
            "plugin_tag": "[Core]",
            "short_levelname": {
                "DEBUG": "DBUG", "INFO": "INFO", "WARNING": "WARN",
                "ERROR": "ERRO", "CRITICAL": "CRIT",
            }.get(level_name, "INFO"),
            "astrbot_version_tag": " [v4.29.1]" if level_no >= 30 else "",
            "source_file": "phase2.logging",
            "source_line": record.get("line", 0),
            "is_trace": False, "category": "system", "source": "core",
            "plugin_name": None, "platform_id": None, "platform_type": None,
        }
        for name, value in defaults.items():
            extra.setdefault(name, value)
        # Suppress Loguru's independent traceback/locals renderer.
        record["exception"] = None


def install_redaction(config: dict, *, config_getter=None) -> CredentialRedactor:
    """Install once, then extend secrets without stacking record factories.

    Loguru's global patcher is shared by all its bound/patched loggers. The
    pinned AstrBot uses a per-logger metadata patch, which remains in place.
    """
    from loguru import logger as loguru_logger

    previous = logging.getLogRecordFactory()
    redactor = getattr(previous, "_phase2_credential_redactor", None)
    if redactor is None:
        redactor = CredentialRedactor()

        def factory(*args, **kwargs):
            return redactor.redact_record(previous(*args, **kwargs))

        factory._phase2_credential_redactor = redactor
        redactor.update(config)
        logging.setLogRecordFactory(factory)
    else:
        redactor.update(config)
    redactor.register_config_getter(config_getter)
    loguru_logger.configure(patcher=redactor.patch_loguru)
    return redactor
