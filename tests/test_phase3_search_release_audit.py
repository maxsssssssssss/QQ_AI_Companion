"""Independent release privacy and on/off regression checks; no credentials."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


stage = load("release_audit_stage", "phase3-stage.py")
review = load("release_audit_review", "phase3-profile-review.py")
package = load("release_audit_package", "phase3-package.py")


class ReleaseAuditTests(unittest.TestCase):
    def test_native_on_off_candidate_overrides_legacy_and_nested_switches(self):
        from integrations.phase3_runtime.settings import private_config
        from integrations.phase3_runtime.proactive import native_proactive_config
        from integrations.phase3_memory.bridge import native_config
        original = {"max_daily_messages": 99, "enable_llm_proactive_message": True,
                    "enable_web_exploration": True,
                    "proactive_reach_config": {"max_daily_messages": 99},
                    "advanced": {"enable_daily_plan": True, "enable_message_debounce": True},
                    "preserved": {"synthetic_label": "keep"}}
        before = copy.deepcopy(original)
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                candidate = native_proactive_config(private_config(original), "10001", enabled=enabled)
                # Disk config stays cold even after approval; the runtime can
                # enable its in-memory instance only after all startup checks.
                self.assertEqual(candidate["max_daily_messages"], 0)
                self.assertEqual(candidate["proactive_reach_config"]["max_daily_messages"], 0)
                self.assertFalse(candidate["enable_llm_proactive_message"])
                self.assertFalse(candidate["enable_web_exploration"])
                self.assertFalse(candidate["advanced"]["enable_daily_plan"])
                self.assertFalse(candidate["advanced"]["enable_message_debounce"])
                self.assertFalse(candidate["basic_config"]["default_proactive_enabled"])
                self.assertEqual(candidate["basic_config"]["target_user_ids"], ["10001"])
                self.assertEqual(candidate["preserved"], original["preserved"])
                memory = native_config({"memory_summary": {"enabled": True},
                                        "retrieval": {"embedding_enabled": True},
                                        "memory_injection": {"enabled": True}}, enabled=enabled)
                self.assertIs(memory["memory_summary"]["enabled"], enabled)
                self.assertIs(memory["private_companion_bridge"]["accept_external_records"], enabled)
                self.assertFalse(memory["memory_capture"]["enabled"])
                self.assertFalse(memory["retrieval"]["embedding_enabled"])
                # Disabling the new automatic extraction does not silently
                # delete/disable the explicitly retained Phase2 recall policy.
                self.assertTrue(memory["memory_injection"]["enabled"])
        self.assertEqual(original, before)

    def test_switch_off_does_not_reuse_a_search_key(self):
        base = stage.helpers.build_config("10001", "napcat", "synthetic-onebot-" + "x" * 40, "SyntheticPassword9")
        base["provider_sources"][0].update(enable=True, key=["synthetic-chat-key-not-real"])
        base["provider_settings"].update(enable=True, websearch_deepseek_key=["synthetic-old-search-key"])
        base["provider"][0]["enable"] = True
        settings = dict(enabled=True, persona=True, natural_chat=True, search=False, memory=False, proactive=False)
        candidate = stage.candidate(base, settings=settings, admin_id="10001", platform_id="napcat")
        self.assertEqual(candidate["provider_settings"]["websearch_deepseek_key"], [])
        self.assertFalse(candidate["provider_settings"]["web_search"])
        self.assertEqual(candidate["provider_sources"][0]["key"], base["provider_sources"][0]["key"])
        self.assertEqual(base["provider_settings"]["websearch_deepseek_key"], ["synthetic-old-search-key"])

    def test_private_review_escapes_active_content_and_cannot_enter_repository(self):
        source = '## 一、合成资料\n### 1.1\n<img src="https://example.invalid/x" onerror="alert(1)">\n<script>alert(2)</script>'
        html = review.render(source)
        self.assertNotIn("<img", html)
        self.assertNotIn("<script", html)
        self.assertIn("&lt;img", html)
        self.assertIn("default-src 'none'", html)
        self.assertIn("form-action 'none'", html)
        with self.assertRaises(ValueError):
            review.write(source, ROOT / ".runtime/private-review-must-not-be-created.html")

    def test_private_review_no_clobber_and_owner_only(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "review.html"
            review.write("## 一、合成\n### 1.1\n合成偏好", output)
            original = output.read_bytes()
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError): review.write("changed", output)
            self.assertEqual(output.read_bytes(), original)

    def test_release_list_excludes_runtime_and_private_state(self):
        names = [p.relative_to(ROOT).as_posix() for p in package.files()]
        self.assertIn("integrations/phase3_search/main.py", names)
        self.assertIn("persona/phase3-v0.1.md", names)
        self.assertFalse(any(n.startswith((".runtime/", "evidence/", "data/", "backups/")) for n in names))
        self.assertFalse(any(n.endswith((".pem", ".sqlite3", ".db", ".env", ".log")) for n in names))
        self.assertFalse(any("__pycache__" in n or "upstream/" in n for n in names))


if __name__ == "__main__":
    unittest.main(verbosity=2)
