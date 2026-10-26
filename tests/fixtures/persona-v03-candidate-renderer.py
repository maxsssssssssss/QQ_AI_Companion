"""One persona authority; private profile never enters this renderer."""
from hashlib import sha256
import re

MARKER = "[QQ_COMPANION_PERSONA_V0_1]"
CURRENT_TEMPLATE = "phase3-v0.3-candidate.md"
VERSIONS = {MARKER: ("0.1", 1), "[QQ_COMPANION_PERSONA_V0_2]": ("0.2", 3),
            "[QQ_COMPANION_PERSONA_V0_3]": ("0.3", 4),
            "[QQ_COMPANION_PERSONA_V0_3_CANDIDATE]": ("0.3-candidate", 6)}


def persona_version(prompt: str) -> tuple[str, int]:
    markers = re.findall(r"\[QQ_COMPANION_PERSONA_[^\]\r\n]*\]", prompt)
    if len(markers) != 1 or markers[0] not in VERSIONS:
        raise ValueError("missing_single_persona_authority")
    return VERSIONS[markers[0]]


def render_persona(template: str, preferred_address: str = "你") -> str:
    _, address_count = persona_version(template)
    if template.count("{{preferred_address}}") != address_count:
        raise ValueError("invalid_persona_template")
    if not isinstance(preferred_address, str) or not re.fullmatch(r"[\w\u4e00-\u9fff]{1,16}", preferred_address):
        raise ValueError("invalid_preferred_address")
    return template.replace("{{preferred_address}}", preferred_address).strip()


async def install_persona(manager, prompt: str, *, tools=()):
    """Called once by existing Phase2 policy, before it declares readiness."""
    version, _ = persona_version(prompt)
    if any(p.persona_id == "qq_companion" for p in manager.personas):
        await manager.update_persona("qq_companion", system_prompt=prompt, tools=list(tools), skills=[])
    else:
        await manager.create_persona("qq_companion", prompt, tools=list(tools), skills=[])
    return {"version": version, "sha256": sha256(prompt.encode()).hexdigest(), "source": "phase3_runtime"}
