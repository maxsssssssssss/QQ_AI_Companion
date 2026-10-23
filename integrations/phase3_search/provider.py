"""One bounded request through the existing chat Provider's SDK client.

Pinned AstrBot text_chat(**kwargs) discards max_tokens. This adapter therefore
uses the existing Provider.client public SDK, without changing shared settings,
creating another Provider, enabling tools, or opening a second Agent loop.
"""
from types import SimpleNamespace


async def bounded_progress(provider, *, persona, action, max_tokens=96, timeout=4):
    client = getattr(provider, "client", None)
    if client is None or not hasattr(client, "with_options"):
        raise RuntimeError("bounded_chat_provider_unsupported")
    response = await client.with_options(max_retries=0, timeout=timeout).chat.completions.create(
        model=provider.get_model(),
        messages=[{"role":"system", "content":persona},
                  {"role":"user", "content":"即将核查的公开主题："+action+"。仅写一句行动前回应，不声称已查到，不用固定问候。"}],
        max_tokens=max_tokens, stream=False,
        extra_body={"thinking": {"type": "disabled"}},
    )
    if not response.choices:
        raise RuntimeError("empty_progress_response")
    message = response.choices[0].message
    return SimpleNamespace(completion_text=message.content or "", tools_call_name=message.tool_calls or [],
                           usage=response.usage)
