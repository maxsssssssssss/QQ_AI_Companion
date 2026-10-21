#!/usr/bin/env python3
"""Prepare first-login NapCat 4.18.38 configuration without starting QQ.

Reads two existing 0600, current-user-owned token files from STATE/secrets.
Publishes fresh configuration under DEPLOYMENT/napcat/config; existing account
configuration, nonempty configuration directories, and QQ login state are never
replaced. Run as the owner of the secret files while NapCat is stopped.

NapCat's early WebUI startup logs its token before applying core log settings.
Keep the Compose logging driver set to "none" and never collect raw startup
stdout/stderr. Disabling file/console logging does not eliminate the upstream
in-memory WebUI log subscription's initial token entries.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile


def checked_path(path: Path) -> Path:
    """Reject symlinks in existing path components, without resolving them."""
    absolute = Path(os.path.abspath(path))
    for component in (*reversed(absolute.parents), absolute):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise ValueError("Symlink paths are not accepted")
    return absolute


def read_token(path: Path) -> str:
    checked_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.geteuid()):
            raise ValueError("Token must be a current-user-owned regular 0600 file")
        if info.st_size > 514:
            raise ValueError("Invalid token size")
        raw = source.read(515)
    # The companion initializer produces URL-safe, 64-character random tokens.
    token = raw.decode("ascii")
    if token.endswith("\n"):
        token = token[:-1]
        if token.endswith("\r"):
            token = token[:-1]
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,512}", token):
        raise ValueError("Token must contain 32 to 512 URL-safe characters")
    return token


def require_fresh_destination(deployment: Path) -> Path:
    config = checked_path(deployment / "napcat" / "config")
    login_state = checked_path(deployment / "ntqq")
    if login_state.exists():
        if not login_state.is_dir() or any(login_state.iterdir()):
            raise FileExistsError("Existing QQ login state must be preserved")
    if config.exists():
        if not config.is_dir() or any(config.iterdir()):
            raise FileExistsError("Existing NapCat configuration must be preserved")
        if config.stat().st_uid != os.geteuid():
            raise PermissionError("Existing configuration directory has another owner")
    return config


def build_configs(onebot_token: str, webui_token: str) -> dict[str, dict]:
    if onebot_token == webui_token:
        raise ValueError("OneBot and WebUI tokens must be independent")
    return {
        "onebot11.json": {
            "network": {
                "httpServers": [], "httpSseServers": [], "httpClients": [],
                "websocketServers": [], "plugins": [],
                "websocketClients": [{
                    "name": "astrbot", "enable": True,
                    "url": "ws://astrbot:6199/ws",
                    "messagePostFormat": "array", "reportSelfMessage": False,
                    "reconnectInterval": 5000, "heartInterval": 30000,
                    "token": onebot_token, "debug": False,
                    "verifyCertificate": True,
                }],
            },
            "musicSignUrl": "", "enableLocalFile2Url": False,
            "parseMultMsg": False, "imageDownloadProxy": "",
            "timeout": {
                "baseTimeout": 10000, "uploadSpeedKBps": 256,
                "downloadSpeedKBps": 256, "maxTimeout": 1800000,
            },
        },
        "webui.json": {
            # Host publication remains 127.0.0.1:6099 in Compose. Binding the
            # container listener to loopback would break that port publication.
            "host": "0.0.0.0", "port": 6099, "token": webui_token, "loginRate": 3,
            "autoLoginAccount": "", "disableWebUI": False,
            "accessControlMode": "none", "ipWhitelist": [], "ipBlacklist": [],
            "enableXForwardedFor": False, "enable2FA": False, "totpSecret": "",
        },
        "napcat.json": {
            # These are real fields in the pinned NapcatConfigSchema. They
            # take effect for core logging, not all pre-login startup logging.
            "fileLog": False, "consoleLog": False,
            "fileLogLevel": "error", "consoleLogLevel": "error",
            "packetBackend": "auto", "packetServer": "",
            "o3HookMode": 0, "autoTimeSync": True,
        },
    }


def write_private_json(path: Path, value: dict) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as target:
        os.fchmod(target.fileno(), 0o600)
        json.dump(value, target, ensure_ascii=False, indent=2)
        target.write("\n")
        target.flush()
        os.fsync(target.fileno())


def configure(state: Path, deployment: Path) -> dict:
    state = checked_path(state)
    deployment = checked_path(deployment)
    config = require_fresh_destination(deployment)
    onebot_token = read_token(state / "secrets" / "onebot-token")
    webui_token = read_token(state / "secrets" / "napcat-webui-token")
    configs = build_configs(onebot_token, webui_token)

    deployment.mkdir(parents=True, exist_ok=True, mode=0o700)
    napcat = deployment / "napcat"
    napcat.mkdir(mode=0o700, exist_ok=True)
    # Atomic publication of the three files prevents a failed preparation from
    # leaving a partial usable configuration. rename refuses a nonempty target.
    staging = Path(tempfile.mkdtemp(prefix=".phase2-config-", dir=napcat))
    try:
        for name, value in configs.items():
            write_private_json(staging / name, value)
        require_fresh_destination(deployment)
        os.rename(staging, config)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {
        "status": "PREPARED", "configuration_files": sorted(configs),
        "qq_login_attempted": False, "secret_values_printed": False,
        "raw_container_logging_must_be_disabled": True,
        "upstream_initial_in_memory_token_logging_remains": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--deployment", type=Path, required=True)
    args = parser.parse_args()
    try:
        os.umask(0o077)
        result = configure(args.state, args.deployment)
    except Exception as exc:
        # Decoding/parser/IO errors may carry private values. Never print them.
        print(json.dumps({
            "status": "ERROR", "error_type": type(exc).__name__,
            "detail": "Preparation refused; preserve existing state and inspect locally",
        }))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
