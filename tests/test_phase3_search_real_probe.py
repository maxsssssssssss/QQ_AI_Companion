"""No API calls: real-probe budgets and credential/body output contract."""
import importlib.util
import asyncio
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("real_search_probe",ROOT/"scripts/phase3-test-search-real.py")
probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)


class RealProbeBudgetTests(unittest.TestCase):
    def test_daily_budget_persists_and_stops_without_reset(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"budget.sqlite3"
            for _ in range(15): probe.reserve_budget(path)
            with self.assertRaisesRegex(ValueError,"daily_attempt_limit"): probe.reserve_budget(path)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT attempts FROM web_daily_attempts").fetchone()[0],60)
            self.assertEqual(path.stat().st_mode & 0o777,0o600)

    def test_near_ten_cny_stops_before_any_further_attempt(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"budget.sqlite3"
            probe.reserve_budget(path)
            with sqlite3.connect(path) as db:
                db.execute("UPDATE acceptance_cost SET reserved_cny=9")
            with self.assertRaisesRegex(ValueError,"near_ten_cny"): probe.reserve_budget(path)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT attempts FROM web_daily_attempts").fetchone()[0],4)

    def test_four_native_compatible_payloads_have_no_private_question(self):
        native_policy=probe.PublicPolicy
        async def public_dns(_host): return ("93.184.216.34",)
        with patch.object(probe,"PublicPolicy",lambda **kwargs:native_policy(**kwargs,resolver=public_dns)):
            suite=asyncio.run(probe.suite_payload())
        self.assertEqual(len(suite["cases"]),2)
        for case in suite["cases"]:
            self.assertEqual(set(case["search"]),{"query","count","need_content"})
            self.assertEqual(case["search"]["count"],3)
            self.assertFalse(case["search"]["need_content"])
            self.assertEqual(set(case["fetch"]),{"url"})
            self.assertTrue(case["fetch"]["url"].startswith("https://api-docs.deepseek.com/"))


if __name__=="__main__": unittest.main()
