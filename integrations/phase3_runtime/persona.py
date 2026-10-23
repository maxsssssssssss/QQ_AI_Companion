"""One persona authority; private profile never enters this renderer."""
from hashlib import sha256
import re

MARKER = "[QQ_COMPANION_PERSONA_V0_1]"


def render_persona(template: str, preferred_address: str = "你") -> str:
    if template.count(MARKER) != 1 or template.count("{{preferred_address}}") != 1:
        raise ValueError("invalid_persona_template")
    if not isinstance(preferred_address, str) or not re.fullmatch(r"[\w\u4e00-\u9fff]{1,16}", preferred_address):
        raise ValueError("invalid_preferred_address")
    return template.replace("{{preferred_address}}", preferred_address).strip()


async def install_persona(manager, prompt: str, *, tools=()):
    """Called once by existing Phase2 policy, before it declares readiness."""
    if prompt.count(MARKER) != 1:
        raise ValueError("missing_single_persona_authority")
    if any(p.persona_id == "qq_companion" for p in manager.personas):
        await manager.update_persona("qq_companion", system_prompt=prompt, tools=list(tools), skills=[])
    else:
        await manager.create_persona("qq_companion", prompt, tools=list(tools), skills=[])
    return {"version": "0.1", "sha256": sha256(prompt.encode()).hexdigest(), "source": "phase3_runtime"}
