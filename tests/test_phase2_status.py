"""Status output must never contain synthetic credentials, QR or user details."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("phase2_status", PROJECT / "scripts/phase2-status.py")
status = importlib.util.module_from_spec(spec)
spec.loader.exec_module(status)
TOKEN = "fixture-status-webui-token-00112233445566"
CREDENTIAL = "U1lOVEhFVElDX0NSRURFTlRJQUw="


class Response(io.BytesIO):
    status = 200


class Opener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return Response(json.dumps(value).encode())


class StatusTests(unittest.TestCase):
    def token_file(self, root):
        path = root / "state/secrets/napcat-webui-token"
        path.parent.mkdir(parents=True)
        path.write_text(TOKEN + "\n")
        path.chmod(0o600)
        return path

    def valid_status(self):
        return {"code": 0, "data": {
            "isLogin": False, "isOffline": False, "coreReady": False,
            "qrLoginAccepted": False, "loginPhase": "waiting_qrcode",
            "qrcodeurl": "SYNTHETIC_PRIVATE_QR",
            "loginError": TOKEN, "uin": "SYNTHETIC_ACCOUNT",
        }}

    def test_auth_and_status_only_with_exact_hash_header_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.token_file(root)
            opener = Opener([{"code": 0, "data": {"Credential": CREDENTIAL}}, self.valid_status()])
            result = status.inspect_napcat(root, opener)
        self.assertEqual(len(opener.calls), 2)
        auth, auth_timeout = opener.calls[0]
        query, query_timeout = opener.calls[1]
        self.assertEqual(auth.full_url, "http://127.0.0.1:6099/api/auth/login")
        self.assertEqual(json.loads(auth.data)["hash"], hashlib.sha256((TOKEN + ".napcat").encode()).hexdigest())
        self.assertEqual(query.full_url, "http://127.0.0.1:6099/api/QQLogin/CheckLoginStatus")
        self.assertEqual(query.get_header("Authorization"), "Bearer " + CREDENTIAL)
        self.assertEqual((auth_timeout, query_timeout), (5, 5))
        self.assertEqual(json.loads(query.data), {})
        self.assertEqual(set(result), {
            "status", "isLogin", "isOffline", "coreReady", "qrLoginAccepted", "loginPhase",
        })
        serialized = json.dumps(result)
        for sensitive in (TOKEN, CREDENTIAL, "SYNTHETIC_PRIVATE_QR", "SYNTHETIC_ACCOUNT"):
            self.assertNotIn(sensitive, serialized)

    def test_http_200_error_does_not_trigger_retry_or_echo_body(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.token_file(root)
            opener = Opener([{"code": -1, "message": TOKEN}])
            with self.assertRaisesRegex(status.StatusError, "^napcat_api_rejected$"):
                status.inspect_napcat(root, opener)
            self.assertEqual(len(opener.calls), 1)

    def test_redirect_is_explicitly_rejected(self):
        with self.assertRaisesRegex(status.StatusError, "^http_redirect_rejected$"):
            status.NoRedirects().redirect_request(
                None, None, 307, "redirect", {}, "https://external.invalid/" + TOKEN
            )

    def test_insecure_or_symlink_secret_rejected_before_http(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.token_file(root)
            opener = Opener([])
            path.chmod(0o644)
            with self.assertRaisesRegex(status.StatusError, "^unsafe_file_permissions$"):
                status.inspect_napcat(root, opener)
            path.chmod(0o600)
            target = path.with_suffix(".protected")
            path.rename(target)
            path.symlink_to(target)
            with self.assertRaises(OSError):
                status.inspect_napcat(root, opener)
            self.assertEqual(opener.calls, [])

    def test_nonroot_cannot_read_another_owner_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.token_file(Path(directory))
            with patch.object(status.os, "geteuid", return_value=os.stat(path).st_uid + 1):
                with self.assertRaisesRegex(status.StatusError, "^unexpected_file_owner$"):
                    status.read_private(path)

    def test_root_can_read_owner_only_service_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.token_file(Path(directory))
            with patch.object(status.os, "geteuid", return_value=0):
                self.assertEqual(status.read_private(path).decode().strip(), TOKEN)

    def test_filter_container_never_returns_arbitrary_fields(self):
        raw = {
            "image_id": "sha256:" + "a" * 64, "running": True, "status": "running",
            "health": "healthy", "restart_count": 1, "oom_killed": False,
            "env": TOKEN, "health_output": CREDENTIAL,
            "ports": {"6185/tcp": [{"HostIp": "127.0.0.1", "HostPort": "6185", "extra": TOKEN}]},
            "resources": {"memory_bytes": 1024, "memory_swap_bytes": -1,
                          "nano_cpus": 1000000000, "pids_limit": 256, "extra": TOKEN},
        }
        result = status.filter_container(raw)
        self.assertNotIn(TOKEN, json.dumps(result))
        self.assertNotIn(CREDENTIAL, json.dumps(result))
        self.assertEqual(result["ports"], [{"container_port": "6185/tcp", "host_ip": "127.0.0.1", "host_port": 6185}])
        self.assertEqual(result["resources"]["memory_swap_bytes"], -1)

    def test_policy_uses_known_fields_and_fixed_reason_codes(self):
        result = status.filter_policy({
            "status": "BLOCKED", "scope": "startup_policy_only", "reason": TOKEN,
            "real_api_tested": False, "plugin_permissions": {"secret": TOKEN},
            "raw_config": {"key": TOKEN},
        })
        self.assertNotIn(TOKEN, json.dumps(result))
        self.assertEqual(result["reason"], "unrecognized_reason")
        self.assertFalse(result["real_api_tested"])

    def test_unexpected_login_phase_is_not_echoed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.token_file(root)
            raw = self.valid_status()
            raw["data"]["loginPhase"] = TOKEN
            opener = Opener([{"code": 0, "data": {"Credential": CREDENTIAL}}, raw])
            result = status.inspect_napcat(root, opener)
            self.assertEqual(result["loginPhase"], "unknown")

    def test_default_report_does_not_authenticate_napcat(self):
        with patch.object(status, "inspect_containers", return_value={}), \
                patch.object(status, "read_private", return_value=b'{"status":"BLOCKED","scope":"startup_policy_only"}'), \
                patch.object(status, "inspect_napcat", side_effect=AssertionError("not requested")):
            report = status.build_report(Path("/synthetic"))
        self.assertNotIn("napcat", report)
        self.assertFalse(report["services_changed"])
        self.assertFalse(report["qq_login_requested"])

    def test_docker_commands_are_read_only_and_project_limited(self):
        cid = "a" * 64
        replies = [cid + "\n", json.dumps({
            "service": "astrbot", "image_id": "sha256:" + "b" * 64,
            "status": "running", "running": True,
        })]
        with patch.object(status, "command", side_effect=replies) as command:
            result = status.inspect_containers(Path("/synthetic"))
        commands = [call.args[0] for call in command.call_args_list]
        self.assertEqual(commands[0][-5:], ["ps", "--all", "--quiet", "astrbot", "napcat"])
        self.assertEqual(commands[1][:3], ["docker", "inspect", "--format"])
        self.assertNotIn(".Config.Env", commands[1][3])
        self.assertNotIn(".State.Health.Log", commands[1][3])
        self.assertEqual(result["napcat"], {"present": False})

    def test_unexpected_exception_message_is_not_reported(self):
        with patch.object(status, "inspect_containers", side_effect=RuntimeError(TOKEN)), \
                patch.object(status, "read_private", side_effect=ValueError(CREDENTIAL)):
            report = status.build_report(Path("/synthetic"))
        self.assertEqual(report["status"], "PARTIAL")
        self.assertNotIn(TOKEN, json.dumps(report))
        self.assertNotIn(CREDENTIAL, json.dumps(report))


if __name__ == "__main__":
    unittest.main()
