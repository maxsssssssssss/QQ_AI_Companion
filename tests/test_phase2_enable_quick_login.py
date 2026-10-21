"""Synthetic native-session recovery fixtures; no VPS, QQ or real credential."""
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "phase2_enable_quick_login", PROJECT / "scripts/phase2-enable-quick-login.py"
)
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)
ACCOUNT = "10001"
TOKEN = "synthetic-quick-login-token-0011223344"
CREDENTIAL = "U1lOVEhFVElDX1JFQ09WRVJZ"


def envelope(data):
    return {"code": 0, "data": data}


def login_state(ready=False, phase=None):
    return envelope({
        "isLogin": ready, "isOffline": False, "coreReady": ready,
        "qrLoginAccepted": False, "loginPhase": phase or ("ready" if ready else "waiting_qrcode"),
        "qrcodeurl": "SYNTHETIC_QR_PRIVATE", "loginError": TOKEN,
    })


def online_identity(account=ACCOUNT):
    return envelope({"uin": account, "online": True})


class Response(io.BytesIO):
    status = 200


class Opener:
    def __init__(self, responses, on_save=None):
        self.responses = list(responses)
        self.calls = []
        self.on_save = on_save

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        if request.full_url.endswith("/SetQuickLoginQQ") and self.on_save:
            self.on_save(json.loads(request.data)["uin"])
        return Response(json.dumps(result).encode())


class FakeClock:
    def __init__(self):
        self.now = 0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class QuickLoginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        token = self.root / "state/secrets/napcat-webui-token"
        token.parent.mkdir(parents=True)
        token.write_text(TOKEN)
        token.chmod(0o600)
        self.config = self.root / "napcat/config/webui.json"
        self.config.parent.mkdir(parents=True)
        self.config.write_text(json.dumps({"token": TOKEN, "autoLoginAccount": ""}))
        self.config.chmod(0o600)
        self.clock = FakeClock()

    def tearDown(self):
        self.temp.cleanup()

    def persist(self, account):
        data = json.loads(self.config.read_text())
        data["autoLoginAccount"] = account
        self.config.write_text(json.dumps(data))

    def run_flow(self, responses, *, save=True, current_session=False):
        opener = Opener(responses, self.persist if save else None)
        if current_session:
            report = recovery.save_current_session(self.root, apply=True, opener=opener)
        else:
            report = recovery.enable(
                self.root, apply=True, opener=opener, clock=self.clock, sleep=self.clock.sleep,
            )
        encoded = json.dumps(report)
        for value in (ACCOUNT, TOKEN, CREDENTIAL, "SYNTHETIC_QR_PRIVATE"):
            self.assertNotIn(value, encoded)
        for request, timeout in opener.calls:
            self.assertEqual(timeout, 5)
            self.assertTrue(request.full_url.startswith("http://127.0.0.1:6099/api/"))
            self.assertNotIn("RefreshQR", request.full_url)
            self.assertNotIn("PasswordLogin", request.full_url)
        return report, [request.full_url.rsplit("/", 1)[1] for request, _ in opener.calls]

    def start(self, accounts=None):
        return [envelope({"Credential": CREDENTIAL}), envelope([ACCOUNT] if accounts is None else accounts)]

    def test_apply_required_before_any_file_or_network_access(self):
        with patch.object(recovery, "authenticate", side_effect=AssertionError("must not run")):
            report = recovery.enable(self.root)
        self.assertEqual(report["status"], "APPLY_REQUIRED")
        self.assertEqual(report["quick_login_attempts"], 0)

    def test_unique_saved_account_recovers_once_then_persists(self):
        report, calls = self.run_flow(self.start() + [
            login_state(), envelope(None), login_state(True), online_identity(), envelope(None),
        ])
        self.assertEqual(report["status"], "COMPLETE")
        self.assertEqual(calls.count("SetQuickLogin"), 1)
        self.assertEqual(calls.count("SetQuickLoginQQ"), 1)
        self.assertTrue(report["auto_login_persisted"])
        self.assertEqual(json.loads(self.config.read_text())["token"], TOKEN)

    def test_already_online_only_persists_after_unique_list(self):
        report, calls = self.run_flow(self.start() + [login_state(True), online_identity(), envelope(None)])
        self.assertEqual(report["status"], "COMPLETE")
        self.assertNotIn("SetQuickLogin", calls)
        self.assertTrue(report["initially_ready"])
        self.assertEqual(report["status_checks"], 1)
        self.assertLess(calls.index("GetQQLoginInfo"), calls.index("SetQuickLoginQQ"))

    def test_empty_multiple_or_invalid_saved_accounts_stop_before_login(self):
        for accounts in ([], [ACCOUNT, "10002"], [{"uin": ACCOUNT}], ["not-numeric"]):
            with self.subTest(accounts=accounts):
                report, calls = self.run_flow(self.start(accounts))
                self.assertEqual(report["status"], "STOP")
                self.assertNotIn("SetQuickLogin", calls)
                self.assertNotIn("SetQuickLoginQQ", calls)

    def test_login_timeout_is_never_retried_but_status_can_confirm_success(self):
        report, calls = self.run_flow(self.start() + [
            login_state(), TimeoutError(TOKEN), login_state(True), online_identity(), envelope(None),
        ])
        self.assertEqual(report["status"], "COMPLETE")
        self.assertEqual(report["quick_login_result"], "UNKNOWN")
        self.assertEqual(calls.count("SetQuickLogin"), 1)

    def test_rejected_login_is_not_retried_or_persisted(self):
        report, calls = self.run_flow(self.start() + [
            login_state(), {"code": -1, "message": TOKEN},
        ])
        self.assertEqual(report["reason"], "QUICK_LOGIN_REJECTED")
        self.assertEqual(calls.count("SetQuickLogin"), 1)
        self.assertNotIn("SetQuickLoginQQ", calls)

    def test_waiting_state_is_bounded_to_six_reads_and_one_login(self):
        report, calls = self.run_flow(self.start() + [
            login_state(), envelope(None), *[login_state() for _ in range(5)],
        ])
        self.assertEqual(report["reason"], "QQ_LOGIN_NOT_READY_WITHIN_BUDGET")
        self.assertEqual(report["status_checks"], 6)
        self.assertEqual(calls.count("SetQuickLogin"), 1)
        self.assertNotIn("SetQuickLoginQQ", calls)
        self.assertLessEqual(sum(self.clock.sleeps), 30)

    def test_unknown_authentication_or_status_stops_without_mutation(self):
        for responses in (
            [{"code": -1, "message": TOKEN}],
            [envelope({"require2FA": True})],
            self.start() + [envelope({"loginPhase": TOKEN})],
        ):
            report, calls = self.run_flow(responses)
            self.assertEqual(report["status"], "STOP")
            self.assertNotIn("SetQuickLogin", calls)
            self.assertNotIn("SetQuickLoginQQ", calls)

    def test_in_progress_scan_is_not_interrupted_by_quick_login(self):
        report, calls = self.run_flow(self.start() + [login_state(False, "qrcode_scanned")])
        self.assertEqual(report["reason"], "LOGIN_ALREADY_IN_PROGRESS_OR_UNAVAILABLE")
        self.assertNotIn("SetQuickLogin", calls)

    def test_persistence_response_alone_does_not_prove_file_was_written(self):
        report, calls = self.run_flow(self.start() + [login_state(True), online_identity(), envelope(None)], save=False)
        self.assertEqual(report["reason"], "AUTO_LOGIN_PERSISTENCE_NOT_VERIFIED")
        self.assertEqual(calls.count("SetQuickLoginQQ"), 1)
        self.assertFalse(report["auto_login_persisted"])

    def test_save_timeout_does_not_retry_or_claim_success(self):
        report, calls = self.run_flow(self.start() + [login_state(True), online_identity(), TimeoutError(TOKEN)])
        self.assertEqual(report["reason"], "AUTO_LOGIN_SAVE_UNCONFIRMED")
        self.assertEqual(calls.count("SetQuickLoginQQ"), 1)
        self.assertFalse(report["auto_login_persisted"])

    def test_default_rejects_cached_account_mismatch_on_every_ready_path(self):
        for setup, attempts in (
            ([login_state(True)], 0),
            ([login_state(), envelope(None), login_state(True)], 1),
            ([login_state(), TimeoutError(TOKEN), login_state(True)], 1),
        ):
            with self.subTest(attempts=attempts, setup_length=len(setup)):
                report, calls = self.run_flow(self.start() + setup + [online_identity("10002")])
                self.assertEqual(report["status"], "STOP")
                self.assertEqual(report["reason"], "CURRENT_SESSION_ACCOUNT_MISMATCH")
                self.assertEqual(calls.count("SetQuickLogin"), attempts)
                self.assertNotIn("SetQuickLoginQQ", calls)
                self.assertFalse(report["auto_login_save_attempted"])
                self.assertNotIn("10002", json.dumps(report))
                self.assertEqual(json.loads(self.config.read_text())["autoLoginAccount"], "")

    def test_default_rejects_missing_invalid_or_offline_current_identity(self):
        for identity in (
            {}, {"uin": ACCOUNT, "online": False}, {"uin": ACCOUNT, "online": 1},
            {"uin": 10001, "online": True}, {"uin": "not-an-account", "online": True},
        ):
            with self.subTest(identity=identity):
                report, calls = self.run_flow(self.start() + [login_state(True), envelope(identity)])
                self.assertEqual(report["reason"], "CURRENT_SESSION_IDENTITY_UNAVAILABLE")
                self.assertNotIn("SetQuickLogin", calls)
                self.assertNotIn("SetQuickLoginQQ", calls)

    def test_default_identity_read_failure_never_retries_login_or_saves(self):
        for response in (TimeoutError(TOKEN), {"code": -1, "message": TOKEN}):
            report, calls = self.run_flow(self.start() + [
                login_state(), envelope(None), login_state(True), response,
            ])
            self.assertEqual(report["reason"], "CURRENT_SESSION_IDENTITY_UNAVAILABLE")
            self.assertEqual(calls.count("SetQuickLogin"), 1)
            self.assertNotIn("SetQuickLoginQQ", calls)

    def test_default_rejects_reconnecting_state_before_identity_or_save(self):
        report, calls = self.run_flow(self.start() + [login_state(True, "reconnecting")])
        self.assertEqual(report["reason"], "CURRENT_SESSION_NOT_READY")
        self.assertNotIn("GetQQLoginInfo", calls)
        self.assertNotIn("SetQuickLogin", calls)
        self.assertNotIn("SetQuickLoginQQ", calls)

    def test_redirect_rejection_never_follows_or_retries_login(self):
        report, calls = self.run_flow(self.start() + [
            login_state(), recovery.status_api.StatusError("http_redirect_rejected"),
        ])
        self.assertEqual(report["reason"], "QUICK_LOGIN_REJECTED")
        self.assertEqual(calls.count("SetQuickLogin"), 1)
        self.assertNotIn("SetQuickLoginQQ", calls)

    def test_native_offline_response_stops_status_loop(self):
        offline = login_state(False, "offline")
        report, calls = self.run_flow(self.start() + [
            login_state(), envelope(None), offline,
        ])
        self.assertEqual(report["reason"], "QQ_LOGIN_REQUIRES_USER")
        self.assertEqual(calls.count("CheckLoginStatus"), 2)
        self.assertNotIn("SetQuickLoginQQ", calls)

    def test_save_current_requires_apply_before_any_access(self):
        with patch.object(recovery, "authenticate", side_effect=AssertionError("must not run")):
            report = recovery.save_current_session(self.root)
        self.assertEqual(report["status"], "APPLY_REQUIRED")
        self.assertFalse(report["auto_login_save_attempted"])

    def test_save_current_online_identity_does_not_attempt_login_or_use_cached_list(self):
        report, calls = self.run_flow([
            envelope({"Credential": CREDENTIAL}), login_state(True),
            envelope({"uin": ACCOUNT, "online": True, "nick": TOKEN, "uid": TOKEN}),
            login_state(True), envelope(None),
        ], current_session=True)
        self.assertEqual(report["status"], "COMPLETE")
        self.assertEqual(calls, [
            "login", "CheckLoginStatus", "GetQQLoginInfo",
            "CheckLoginStatus", "SetQuickLoginQQ",
        ])
        self.assertTrue(report["current_session_verified"])
        self.assertTrue(report["auto_login_persisted"])

    def test_save_current_stops_if_not_ready_before_or_after_identity_read(self):
        for responses in (
            [envelope({"Credential": CREDENTIAL}), login_state()],
            [
                envelope({"Credential": CREDENTIAL}), login_state(True),
                envelope({"uin": ACCOUNT, "online": True}), login_state(),
            ],
            [envelope({"Credential": CREDENTIAL}), login_state(True, "reconnecting")],
        ):
            report, calls = self.run_flow(responses, current_session=True)
            self.assertEqual(report["reason"], "CURRENT_SESSION_NOT_READY")
            self.assertNotIn("SetQuickLogin", calls)
            self.assertNotIn("SetQuickLoginQQ", calls)

    def test_save_current_rejects_missing_invalid_or_offline_identity(self):
        for identity in (
            {}, {"uin": ACCOUNT, "online": False},
            {"uin": ACCOUNT, "online": 1}, {"uin": 10001, "online": True},
            {"uin": "not-an-account", "online": True},
        ):
            report, calls = self.run_flow([
                envelope({"Credential": CREDENTIAL}), login_state(True), envelope(identity),
            ], current_session=True)
            self.assertEqual(report["reason"], "CURRENT_SESSION_IDENTITY_UNAVAILABLE")
            self.assertNotIn("SetQuickLogin", calls)
            self.assertNotIn("SetQuickLoginQQ", calls)

    def test_save_current_never_retries_uncertain_write_or_claims_unverified_persistence(self):
        for response, expected in (
            (TimeoutError(TOKEN), "AUTO_LOGIN_SAVE_UNCONFIRMED"),
            (envelope(None), "AUTO_LOGIN_PERSISTENCE_NOT_VERIFIED"),
        ):
            report, calls = self.run_flow([
                envelope({"Credential": CREDENTIAL}), login_state(True),
                envelope({"uin": ACCOUNT, "online": True}), login_state(True), response,
            ], current_session=True, save=False)
            self.assertEqual(report["reason"], expected)
            self.assertEqual(calls.count("SetQuickLoginQQ"), 1)
            self.assertNotIn("SetQuickLogin", calls)
            self.assertFalse(report["auto_login_persisted"])


if __name__ == "__main__":
    unittest.main()
