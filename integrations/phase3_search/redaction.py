"""Suppress native tool argument/result logs before AstrBot's logging bridge.

The native runner logs arguments before hooks. A call-wrapper rejection alone
would protect the network but not logs. This filter retains reason-code metrics
in the first-party runtime instead of exporting arbitrary tool arguments.
"""
import logging

SENSITIVE_MODULES = frozenset(("tool_loop_agent_runner", "astr_agent_tool_exec", "web_search_tools"))


def protect(record):
    if record.module in SENSITIVE_MODULES or record.name == "astrbot.trace":
        record.msg = "[Phase3] native agent/tool diagnostic withheld; use sanitized counters"
        record.args = ()
        record.exc_info = record.exc_text = record.stack_info = None
    return record


def install():
    previous = logging.getLogRecordFactory()
    if getattr(previous, "_phase3_tool_privacy", False):
        return

    def factory(*args, **kwargs):
        return protect(previous(*args, **kwargs))

    factory._phase3_tool_privacy = True
    # Preserve the Phase2 installer handle, so a later idempotent credential
    # setup does not stack factories or discard its known-secret registry.
    if hasattr(previous, "_phase2_credential_redactor"):
        factory._phase2_credential_redactor = previous._phase2_credential_redactor
    logging.setLogRecordFactory(factory)
