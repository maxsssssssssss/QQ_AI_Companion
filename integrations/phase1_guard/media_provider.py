"""Truthful media failure notices around the pinned Provider instance only.

The native Provider still chooses whether a recognized image error allows one
text retry. This adapter adds missing-image semantics and safe diagnostics; it
never selects another model, sends a message, or creates a retry of its own.
"""
from contextvars import ContextVar
import copy

NOTICE = ('[本次模型接口未能接受这份图片，图像已从重试请求中移除。'
          '本次没有可用的图片内容，不能声称看过、不能猜测图中文字或画面；'
          '请明确说明图片本次不可用，仍可回答有依据的文字部分。]')
_diagnostic_binding = ContextVar('phase3_media_fallback_diagnostic', default=None)


def bind_turn(turn_id, sink):
    """Bind to the existing Agent task; sink receives counts/enums + turn_id."""
    return _diagnostic_binding.set((turn_id, sink))


def reset_turn(token):
    _diagnostic_binding.reset(token)


class FallbackHandle:
    def __init__(self, provider):
        self.provider = provider
        self.original = provider._remove_image_from_context
        self.had_instance_method = '_remove_image_from_context' in vars(provider)
        self.calls = 0
        self.removed_images = 0

        async def remove(contexts):
            affected = {}
            for index, row in enumerate(contexts):
                content = row.get('content')
                if isinstance(content, list):
                    count = sum(isinstance(p, dict) and p.get('type') == 'image_url' for p in content)
                    if count:
                        affected[index] = count
            # The native helper mutates rows. Keep that fallback request local;
            # original visual conversation/history must retain source evidence.
            result = await self.original(copy.deepcopy(contexts))
            for index in affected:
                row = result[index]
                content = row.get('content')
                if isinstance(content, list):
                    row['content'] = [*content, {'type': 'text', 'text': NOTICE}]
                else:
                    row['content'] = str(content or '') + '\n' + NOTICE
            if affected:
                self.calls += 1
                count = sum(affected.values())
                self.removed_images += count
                binding = _diagnostic_binding.get()
                if binding:
                    turn_id, sink = binding
                    try:
                        sink({'turn_id': turn_id, 'state': 'native_text_fallback',
                              'removed_images': count, 'affected_messages': len(affected),
                              'model_changed': False})
                    except Exception:
                        # Diagnostic sink failure cannot destroy the truthful
                        # request; no send ledger or permission gate is bypassed.
                        pass
            return result

        self.wrapper = remove
        provider._remove_image_from_context = remove

    def restore(self):
        if self.provider._remove_image_from_context is self.wrapper:
            if self.had_instance_method:
                self.provider._remove_image_from_context = self.original
            else:
                del self.provider._remove_image_from_context
        if getattr(self.provider, '_phase3_media_fallback_handle', None) is self:
            del self.provider._phase3_media_fallback_handle


def install(provider):
    """Idempotent, fixed native method only; caller restores at plugin shutdown."""
    from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
    existing = getattr(provider, '_phase3_media_fallback_handle', None)
    if isinstance(existing, FallbackHandle) and provider._remove_image_from_context is existing.wrapper:
        return existing
    native = getattr(provider, '_remove_image_from_context', None)
    if not isinstance(provider, ProviderOpenAIOfficial) or getattr(native, '__func__', None) is not ProviderOpenAIOfficial._remove_image_from_context:
        return None
    handle = FallbackHandle(provider)
    provider._phase3_media_fallback_handle = handle
    return handle
