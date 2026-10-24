"""Failure-only round retention around AstrBot 4.29.1 native compression.

The pinned native ``LLMSummaryCompressor`` returns its input object when no
summary was produced. Only that result (or an ordinary compressor exception)
activates the fallback. Successful summaries and the manager's token-based
fallback remain native. No history, upstream class or global registry is edited.

``_ACTIVE_AGENT_RUNNERS`` is a private, version-pinned integration boundary.
AstrBot registers the runner before OnAgentBegin, which precedes request context
processing. Installation verifies both the event and exact run-context object;
a matching conversation name alone is never sufficient.
"""
from __future__ import annotations


PINNED_ASTRBOT_VERSION = "4.29.1"
MAX_FALLBACK_TURNS = 200


class FailureRoundFallback:
    """Delegate once to a native compressor, retaining whole rounds on failure."""

    def __init__(self, native, limit):
        from astrbot.core.agent.context.compressor import LLMSummaryCompressor

        if not isinstance(native, LLMSummaryCompressor):
            raise ValueError("native_llm_compressor_required")
        if type(limit) is not int or not 1 <= limit <= MAX_FALLBACK_TURNS:
            raise ValueError("invalid_context_fallback_turns")
        self.native = native
        self.limit = limit

    def should_compress(self, messages, current_tokens, max_tokens):
        return self.native.should_compress(messages, current_tokens, max_tokens)

    async def __call__(self, messages):
        from astrbot.core.agent.context.round_utils import split_into_rounds

        try:
            result = await self.native(messages)
        except Exception:
            # Native normally converts summary failures to its original input.
            # Unexpected ordinary failures use the same bounded fallback. Do
            # not catch BaseException: cancellation must interrupt the request.
            result = messages
        if result is not messages:
            return result

        first_non_system = next(
            (index for index, msg in enumerate(messages) if msg.role != "system"),
            len(messages),
        )
        rounds = split_into_rounds(messages[first_non_system:])
        if len(rounds) <= self.limit:
            return messages
        return messages[:first_non_system] + [
            msg for complete_round in rounds[-self.limit:] for msg in complete_round
        ]


def install_for_run(event, run_context, limit) -> bool:
    """Install once on the exact registered native run; 0 disables our wrapper.

    Return True only when the requested wrapper is installed (including an
    idempotent repeat). Unsupported versions, mismatched/stale runs, different
    compression strategies and invalid limits remain unchanged and return False.
    """
    if type(limit) is not int or not 0 <= limit <= MAX_FALLBACK_TURNS:
        return False
    import astrbot
    from astrbot.core.agent.context.compressor import LLMSummaryCompressor
    from astrbot.core.agent.context.manager import ContextManager
    from astrbot.core.pipeline.process_stage.follow_up import _ACTIVE_AGENT_RUNNERS

    if astrbot.__version__ != PINNED_ASTRBOT_VERSION:
        return False
    umo = getattr(event, "unified_msg_origin", None)
    if not isinstance(umo, str) or not umo:
        return False
    runner = _ACTIVE_AGENT_RUNNERS.get(umo)
    if runner is None or getattr(runner, "run_context", None) is not run_context:
        return False
    if getattr(getattr(run_context, "context", None), "event", None) is not event:
        return False
    manager = getattr(runner, "request_context_manager", None)
    if not isinstance(manager, ContextManager) or manager.config.enforce_max_turns != -1:
        return False
    compressor = manager.compressor
    if isinstance(compressor, FailureRoundFallback):
        if limit == 0:
            manager.compressor = compressor.native
            return False
        if compressor.limit == limit:
            return True
        compressor = compressor.native
    if limit == 0 or not isinstance(compressor, LLMSummaryCompressor):
        return False
    manager.compressor = FailureRoundFallback(compressor, limit)
    return True
