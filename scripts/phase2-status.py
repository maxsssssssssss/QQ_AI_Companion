#!/usr/bin/env python3
"""Read sanitized deployment status without starting or changing services.

Run as root when reading container-owned policy state. --napcat performs one
WebUI authentication and one status read; it never refreshes QR or logs in QQ.
Raw Docker output, HTTP bodies, credentials and exception text are not printed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

DEFAULT_DEPLOYMENT = Path("/opt/qq-ai-companion/deployment")
SERVICES = ("astrbot", "napcat")
CONTAINER_STATES = {"created", "running", "paused", "restarting", "removing", "exited", "dead"}
HEALTH_STATES = {"starting", "healthy", "unhealthy"}
LOGIN_PHASES = {
    "waiting_qrcode", "generating_qrcode", "qrcode_scanned", "initializing",
    "ready", "offline", "reconnecting",
}
POLICY_REASONS = {
    "startup_pending", "deepseek_not_enabled", "missing_deepseek_key",
    "required_plugin_missing_or_wrong_version", "empty_persona",
    "invalid_admin_id", "invalid_platform_id", "unexpected_administrators",
    "whitelist_disabled", "unexpected_whitelist", "shared_sessions",
    "segmented_reply_enabled", "unexpected_platform_count", "unexpected_platform",
    "unexpected_onebot_endpoint", "weak_onebot_token", "computer_tools_enabled",
    "file_extraction_enabled", "cron_tools_enabled", "unexpected_agent_runner",
    "unexpected_persona", "unexpected_default_model", "automatic_model_retries",
    "implicit_model_fallback", "image_rendering_enabled", "unexpected_timezone",
    "unexpected_provider_sources", "unexpected_provider_endpoint",
    "unexpected_models", "unexpected_model_mapping", "unbounded_thinking",
    "unbounded_output", "flash_model_disabled",
} | {
    "unsafe_" + option for option in (
        "wl_ignore_admin_on_group", "wl_ignore_admin_on_friend", "reply_with_mention",
        "reply_with_quote", "no_permission_reply", "streaming_response", "web_search",
        "show_tool_use_status", "show_tool_call_result",
    )
}
MAX_FILE_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 65536

# Project only these fields inside Docker; do not even fetch container
# environment, command lines, healthcheck output or other arbitrary metadata.
INSPECT_TEMPLATE = """{
"service":{{json (index .Config.Labels "com.docker.compose.service")}},
"image_id":{{json .Image}},
"running":{{json .State.Running}},
"status":{{json .State.Status}},
"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}},
"restart_count":{{json .RestartCount}},
"oom_killed":{{json .State.OOMKilled}},
"ports":{{json .NetworkSettings.Ports}},
"resources":{"memory_bytes":{{json .HostConfig.Memory}},
"memory_swap_bytes":{{json .HostConfig.MemorySwap}},
"nano_cpus":{{json .HostConfig.NanoCpus}},
"pids_limit":{{json .HostConfig.PidsLimit}}}
}"""


class StatusError(Exception):
    """Only fixed internal error codes may reach the report."""


def read_private(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
            raise StatusError("unsafe_file_permissions")
        if os.geteuid() != 0 and info.st_uid != os.geteuid():
            raise StatusError("unexpected_file_owner")
        if info.st_size > MAX_FILE_BYTES:
            raise StatusError("oversized_file")
        value = stream.read(MAX_FILE_BYTES + 1)
        if len(value) > MAX_FILE_BYTES:
            raise StatusError("oversized_file")
        return value


def command(arguments: list[str]) -> str:
    try:
        result = subprocess.run(
            arguments, capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise StatusError("docker_command_unavailable") from None
    if result.returncode:
        raise StatusError("docker_command_failed")
    if len(result.stdout) > MAX_FILE_BYTES:
        raise StatusError("oversized_docker_output")
    return result.stdout


def _integer(value, *, minimum=0):
    return value if type(value) is int and value >= minimum else None


def filter_container(raw: dict) -> dict:
    image = raw.get("image_id")
    result = {
        "present": True,
        "image_id": image if isinstance(image, str) and
        re.fullmatch(r"sha256:[0-9a-f]{64}", image) else None,
        "running": raw.get("running") if type(raw.get("running")) is bool else None,
        "status": raw.get("status") if raw.get("status") in CONTAINER_STATES else "unknown",
        "health": raw.get("health") if raw.get("health") in HEALTH_STATES else "unknown",
        "restart_count": _integer(raw.get("restart_count")),
        "oom_killed": raw.get("oom_killed") if type(raw.get("oom_killed")) is bool else None,
        "ports": [],
        "resources": {},
    }
    for container_port, bindings in (raw.get("ports") or {}).items():
        if not re.fullmatch(r"[0-9]{1,5}/(tcp|udp)", container_port):
            continue
        for binding in bindings or []:
            host_ip, host_port = binding.get("HostIp"), binding.get("HostPort")
            # Only numeric addresses/ports. Never copy arbitrary Docker strings.
            if isinstance(host_ip, str) and re.fullmatch(r"[0-9a-fA-F:.]{1,45}", host_ip) and \
                    isinstance(host_port, str) and host_port.isdigit() and \
                    1 <= int(host_port) <= 65535:
                result["ports"].append({
                    "container_port": container_port, "host_ip": host_ip,
                    "host_port": int(host_port),
                })
    resources = raw.get("resources") or {}
    for name in ("memory_bytes", "memory_swap_bytes", "nano_cpus", "pids_limit"):
        result["resources"][name] = _integer(resources.get(name), minimum=-1)
    return result


def inspect_containers(deployment: Path) -> dict:
    output = command([
        "docker", "compose", "--project-directory", str(deployment),
        "-f", str(deployment / "compose.yml"), "ps", "--all", "--quiet", *SERVICES,
    ])
    identifiers = output.split()
    if len(identifiers) > len(SERVICES) or any(
        not re.fullmatch(r"[0-9a-f]{12,64}", value) for value in identifiers
    ):
        raise StatusError("unexpected_compose_output")
    result = {name: {"present": False} for name in SERVICES}
    seen = set()
    for identifier in identifiers:
        raw = json.loads(command(["docker", "inspect", "--format", INSPECT_TEMPLATE, identifier]))
        service = raw.get("service")
        if service not in SERVICES or service in seen:
            raise StatusError("unexpected_compose_service")
        seen.add(service)
        result[service] = filter_container(raw)
    return result


def filter_policy(raw: dict) -> dict:
    result = {
        "status": raw.get("status") if raw.get("status") in {"PASS", "BLOCKED"} else "UNKNOWN",
        "scope": "startup_policy_only",
    }
    if raw.get("scope") != "startup_policy_only":
        result["status"] = "UNKNOWN"
    if "reason" in raw:
        result["reason"] = raw["reason"] if raw["reason"] in POLICY_REASONS else "unrecognized_reason"
    for name in ("automatic_tools", "real_api_tested", "real_qq_tested"):
        if type(raw.get(name)) is bool:
            result[name] = raw[name]
    return result


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise StatusError("http_redirect_rejected")


def post_json(opener, endpoint: str, body: dict, credential: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if credential is not None:
        headers["Authorization"] = "Bearer " + credential
    request = Request(
        "http://127.0.0.1:6099/api/" + endpoint,
        data=json.dumps(body).encode("utf-8"), headers=headers, method="POST",
    )
    try:
        with opener.open(request, timeout=5) as response:
            if response.status != 200:
                raise StatusError("http_request_failed")
            body_bytes = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body_bytes) > MAX_RESPONSE_BYTES:
            raise StatusError("oversized_http_response")
        parsed = json.loads(body_bytes)
    except StatusError:
        raise
    except (OSError, HTTPError, URLError, ValueError):
        raise StatusError("http_request_failed") from None
    if not isinstance(parsed, dict) or type(parsed.get("code")) is not int or parsed["code"] != 0:
        raise StatusError("napcat_api_rejected")
    return parsed


def inspect_napcat(deployment: Path, opener=None) -> dict:
    token = read_private(deployment / "state/secrets/napcat-webui-token").decode("ascii").rstrip("\r\n")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,512}", token):
        raise StatusError("invalid_webui_token")
    if opener is None:
        opener = build_opener(ProxyHandler({}), NoRedirects())
    digest = hashlib.sha256((token + ".napcat").encode("utf-8")).hexdigest()
    auth = post_json(opener, "auth/login", {"hash": digest}).get("data")
    if not isinstance(auth, dict):
        raise StatusError("invalid_authentication_response")
    if auth.get("require2FA") is True:
        raise StatusError("authentication_requires_2fa")
    credential = auth.get("Credential")
    if not isinstance(credential, str) or not 1 <= len(credential) <= 16384 or \
            not re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", credential):
        raise StatusError("invalid_authentication_response")
    raw = post_json(opener, "QQLogin/CheckLoginStatus", {}, credential).get("data")
    if not isinstance(raw, dict):
        raise StatusError("invalid_status_response")
    result = {"status": "READ"}
    for name in ("isLogin", "isOffline", "qrLoginAccepted", "coreReady"):
        if type(raw.get(name)) is not bool:
            raise StatusError("invalid_status_response")
        result[name] = raw[name]
    phase = raw.get("loginPhase")
    result["loginPhase"] = phase if phase in LOGIN_PHASES else "unknown"
    # qrcodeurl/loginError and all other response fields are deliberately omitted.
    return result


def build_report(deployment: Path, *, napcat=False) -> dict:
    report = {"status": "INSPECTED", "services_changed": False, "qq_login_requested": False}
    checks = {
        "containers": lambda: inspect_containers(deployment),
        "astrbot_policy": lambda: filter_policy(json.loads(read_private(
            deployment / "state/data/plugin_data/phase2_policy/readiness.json"
        ))),
    }
    if napcat:
        checks["napcat"] = lambda: inspect_napcat(deployment)
    for name, check in checks.items():
        try:
            report[name] = check()
        except StatusError as exc:
            report[name] = {"status": "UNAVAILABLE", "reason": str(exc)}
            report["status"] = "PARTIAL"
        except Exception:
            report[name] = {"status": "UNAVAILABLE", "reason": "status_read_failed"}
            report["status"] = "PARTIAL"
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment", type=Path, default=DEFAULT_DEPLOYMENT)
    parser.add_argument("--napcat", action="store_true", help="Authenticate once and read QQ status once")
    args = parser.parse_args()
    report = build_report(args.deployment, napcat=args.napcat)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["status"] == "INSPECTED" else 1)
