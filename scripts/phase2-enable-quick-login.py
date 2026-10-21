#!/usr/bin/env python3
"""Enable the pinned NapCat's native saved-session login, only with --apply.

Uses one uniquely saved account. Never reads a password, changes device
identity, refreshes QR, or retries a login/persistence request. Unknown login
request outcomes permit only bounded status reads. Output contains no account,
token, QR, response body or raw exception. Native NapCat may itself generate a
QR when saved-ticket login fails; the user must handle further authentication.
The separate --save-current-session mode only saves an already online session;
it never attempts login and does not depend on the cached saved-account list.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import time
from urllib.request import ProxyHandler, build_opener

_spec = importlib.util.spec_from_file_location(
    "phase2_status_private", Path(__file__).with_name("phase2-status.py")
)
status_api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(status_api)

MAX_STATUS_CHECKS = 6
STATUS_BUDGET_SECONDS = 30
STATUS_INTERVAL_SECONDS = 5


class RecoveryError(Exception):
    """Only constant codes are used; never forward an upstream message."""


def authenticate(deployment: Path, opener) -> str:
    token = status_api.read_private(
        deployment / "state/secrets/napcat-webui-token"
    ).decode("ascii").rstrip("\r\n")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,512}", token):
        raise RecoveryError("INVALID_WEBUI_CREDENTIAL")
    digest = hashlib.sha256((token + ".napcat").encode("utf-8")).hexdigest()
    data = status_api.post_json(opener, "auth/login", {"hash": digest}).get("data")
    if not isinstance(data, dict) or data.get("require2FA") is True:
        raise RecoveryError("WEBUI_AUTHENTICATION_UNAVAILABLE")
    credential = data.get("Credential")
    if not isinstance(credential, str) or not 1 <= len(credential) <= 16384 or \
            not re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", credential):
        raise RecoveryError("WEBUI_AUTHENTICATION_UNAVAILABLE")
    return credential


def read_login_status(opener, credential: str) -> dict:
    data = status_api.post_json(
        opener, "QQLogin/CheckLoginStatus", {}, credential
    ).get("data")
    if not isinstance(data, dict) or data.get("loginPhase") not in status_api.LOGIN_PHASES:
        raise RecoveryError("LOGIN_STATE_UNKNOWN")
    fields = ("isLogin", "isOffline", "qrLoginAccepted", "coreReady")
    if any(type(data.get(name)) is not bool for name in fields):
        raise RecoveryError("LOGIN_STATE_UNKNOWN")
    # Deliberately discard qrcodeurl, loginError and any unexpected fields.
    return {**{name: data[name] for name in fields}, "loginPhase": data["loginPhase"]}


def is_ready(data: dict) -> bool:
    return data["isLogin"] is True and data["coreReady"] is True and data["isOffline"] is False


def read_online_account(opener, credential: str) -> str:
    # Pinned 4.18.38 returns the selfInfo captured at OneBot initialization,
    # plus online from the live core.selfInfo. Never use the human admin ID.
    identity = status_api.post_json(
        opener, "QQLogin/GetQQLoginInfo", {}, credential
    ).get("data")
    if not isinstance(identity, dict) or identity.get("online") is not True:
        raise RecoveryError("CURRENT_SESSION_IDENTITY_UNAVAILABLE")
    account = identity.get("uin")
    if not isinstance(account, str) or not re.fullmatch(r"[1-9][0-9]{4,14}", account):
        raise RecoveryError("CURRENT_SESSION_IDENTITY_UNAVAILABLE")
    return account


def persist_account(deployment: Path, opener, credential: str, account: str) -> None:
    status_api.post_json(
        opener, "QQLogin/SetQuickLoginQQ", {"uin": account}, credential
    )
    # The upstream write helper can warn and return without writing.
    # Verify the actual mounted file, not merely its in-memory API cache.
    config = json.loads(status_api.read_private(deployment / "napcat/config/webui.json"))
    if not isinstance(config, dict) or config.get("autoLoginAccount") != account:
        raise RecoveryError("AUTO_LOGIN_PERSISTENCE_NOT_VERIFIED")


def save_current_session(deployment: Path, *, apply=False, opener=None) -> dict:
    """Persist an authenticated, online self identity without any login call."""
    report = {
        "status": "APPLY_REQUIRED", "current_session_verified": False,
        "auto_login_save_attempted": False, "auto_login_persisted": False,
    }
    if not apply:
        return report
    report["status"] = "STOP"
    opener = opener or build_opener(ProxyHandler({}), status_api.NoRedirects())
    stage = "WEBUI_AUTHENTICATION_UNAVAILABLE"
    try:
        credential = authenticate(deployment, opener)
        stage = "CURRENT_SESSION_NOT_READY"
        current = read_login_status(opener, credential)
        if not is_ready(current) or current["loginPhase"] != "ready":
            raise RecoveryError(stage)
        stage = "CURRENT_SESSION_IDENTITY_UNAVAILABLE"
        account = read_online_account(opener, credential)
        stage = "CURRENT_SESSION_NOT_READY"
        current = read_login_status(opener, credential)
        if not is_ready(current) or current["loginPhase"] != "ready":
            raise RecoveryError(stage)
        report["current_session_verified"] = True
        stage = "AUTO_LOGIN_SAVE_UNCONFIRMED"
        report["auto_login_save_attempted"] = True
        persist_account(deployment, opener, credential, account)
        report["auto_login_persisted"] = True
        report["status"] = "COMPLETE"
    except RecoveryError as exc:
        report["reason"] = str(exc)
    except Exception:
        report["reason"] = stage
    return report


def enable(deployment: Path, *, apply=False, opener=None, clock=None, sleep=None) -> dict:
    report = {
        "status": "APPLY_REQUIRED", "saved_account_count": 0,
        "quick_login_attempts": 0, "quick_login_result": "NOT_ATTEMPTED",
        "status_checks": 0, "initially_ready": False, "ready": False,
        "auto_login_save_attempted": False, "auto_login_persisted": False,
    }
    if not apply:
        return report
    report["status"] = "STOP"
    clock, sleep = clock or time.monotonic, sleep or time.sleep
    opener = opener or build_opener(ProxyHandler({}), status_api.NoRedirects())
    stage = "WEBUI_AUTHENTICATION_UNAVAILABLE"
    try:
        credential = authenticate(deployment, opener)
        stage = "SAVED_ACCOUNTS_UNAVAILABLE"
        accounts = status_api.post_json(
            opener, "QQLogin/GetQuickLoginList", {}, credential
        ).get("data")
        if not isinstance(accounts, list) or any(
            not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{4,14}", value)
            for value in accounts
        ):
            raise RecoveryError("SAVED_ACCOUNTS_INVALID")
        accounts = set(accounts)
        report["saved_account_count"] = len(accounts)
        if len(accounts) != 1:
            raise RecoveryError("SAVED_ACCOUNT_NOT_UNIQUE")
        account = next(iter(accounts))
        stage = "LOGIN_STATE_UNKNOWN"
        deadline = clock() + STATUS_BUDGET_SECONDS
        report["status_checks"] += 1
        current = read_login_status(opener, credential)
        report["initially_ready"] = report["ready"] = is_ready(current)
        if not report["ready"]:
            if current["loginPhase"] not in {"waiting_qrcode", "offline"} or any(
                current[name] for name in ("isLogin", "isOffline", "qrLoginAccepted", "coreReady")
            ):
                raise RecoveryError("LOGIN_ALREADY_IN_PROGRESS_OR_UNAVAILABLE")
            stage = "QUICK_LOGIN_REQUEST_UNKNOWN"
            report["quick_login_attempts"] = 1
            try:
                status_api.post_json(
                    opener, "QQLogin/SetQuickLogin", {"uin": account}, credential
                )
                report["quick_login_result"] = "ACCEPTED"
            except status_api.StatusError as exc:
                if str(exc) != "http_request_failed":
                    # A rejected API response, redirect, malformed envelope or
                    # other explicit failure does not authorize another attempt.
                    report["quick_login_result"] = "REJECTED"
                    raise RecoveryError("QUICK_LOGIN_REJECTED") from None
                # A network timeout may occur after native login was accepted.
                # Never send the mutation again; only read status below.
                report["quick_login_result"] = "UNKNOWN"
            stage = "LOGIN_STATE_UNKNOWN"
            first_poll = True
            while report["status_checks"] < MAX_STATUS_CHECKS:
                if not first_poll:
                    remaining = deadline - clock()
                    # Reserve the API helper's full 5-second HTTP timeout.
                    if remaining < STATUS_INTERVAL_SECONDS + 5:
                        break
                    sleep(STATUS_INTERVAL_SECONDS)
                first_poll = False
                if deadline - clock() < 5:
                    break
                report["status_checks"] += 1
                current = read_login_status(opener, credential)
                report["ready"] = is_ready(current)
                if report["ready"]:
                    break
                if current["isOffline"] or current["loginPhase"] == "offline":
                    raise RecoveryError("QQ_LOGIN_REQUIRES_USER")
            if not report["ready"]:
                raise RecoveryError("QQ_LOGIN_NOT_READY_WITHIN_BUDGET")
        stage = "CURRENT_SESSION_NOT_READY"
        if current["loginPhase"] != "ready":
            raise RecoveryError(stage)
        stage = "CURRENT_SESSION_IDENTITY_UNAVAILABLE"
        # A unique cached candidate does not identify the current session.
        # Verify both an existing online session and any recovered session
        # before changing the account used at the next worker startup.
        if read_online_account(opener, credential) != account:
            raise RecoveryError("CURRENT_SESSION_ACCOUNT_MISMATCH")
        # No environment/argv account setting: native WebUI persists the value
        # in the existing private config used at next worker startup.
        stage = "AUTO_LOGIN_SAVE_UNCONFIRMED"
        report["auto_login_save_attempted"] = True
        persist_account(deployment, opener, credential, account)
        report["auto_login_persisted"] = True
        report["status"] = "COMPLETE"
    except RecoveryError as exc:
        report["reason"] = str(exc)
    except Exception:
        # Never expose urllib/JSON/OS exceptions: they may embed private inputs.
        report["reason"] = stage
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment", type=Path, default=status_api.DEFAULT_DEPLOYMENT)
    parser.add_argument("--apply", action="store_true", help="Allow one native saved-session login and persistence")
    parser.add_argument(
        "--save-current-session", action="store_true",
        help="Only persist an already online, verified self identity; never attempt login",
    )
    options = parser.parse_args()
    action = save_current_session if options.save_current_session else enable
    report = action(options.deployment, apply=options.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["status"] == "COMPLETE" else 1)
