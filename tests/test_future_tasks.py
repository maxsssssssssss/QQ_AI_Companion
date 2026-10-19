"""Characterize AstrBot's real scheduler and SQLite persistence, offline.

The scheduler, triggers, job lifecycle and database are unmodified upstream
implementations. Only a basic job's application callback is a synthetic
success/failure fixture. These tests explicitly record the native limitation
that scheduled one-shot failures are deleted; they do not claim reliable retry.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from astrbot.core.cron.manager import CronJobManager
from astrbot.core.db.sqlite import SQLiteDatabase


OBSERVATIONS: list[dict] = []


class NativeFutureTaskTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="qq-native-cron-")
        self.path = Path(self.directory.name) / "scheduler.db"
        self.db = SQLiteDatabase(str(self.path))
        await self.db.initialize()
        self.manager = CronJobManager(self.db)

    async def asyncTearDown(self):
        await self.manager.shutdown()
        # Upstream schedules next_run_time persistence as an asyncio task.
        await asyncio.sleep(0.1)
        await self.db.engine.dispose()
        self.directory.cleanup()

    async def wait_for_deleted(self, job_id: str):
        for _ in range(100):
            if await self.db.get_cron_job(job_id) is None:
                return
            await asyncio.sleep(0.025)
        self.fail(f"native scheduler did not remove one-shot job {job_id}")

    async def make_scheduled_one_shot(self, callback):
        job = await self.manager.add_basic_job(
            name="synthetic one-shot", cron_expression="0 0 1 1 *",
            handler=callback, timezone="Asia/Shanghai", persistent=True,
        )
        return await self.manager.update_job(
            job.job_id, run_once=True,
            payload={"run_at": (datetime.now(timezone.utc) + timedelta(seconds=0.35)).isoformat()},
        )

    async def test_scheduled_once_success_runs_once_and_is_deleted(self):
        count = 0
        fired = asyncio.Event()

        async def callback(**_payload):
            nonlocal count
            count += 1
            fired.set()

        job = await self.make_scheduled_one_shot(callback)
        await asyncio.wait_for(fired.wait(), timeout=3)
        await self.wait_for_deleted(job.job_id)
        await asyncio.sleep(0.1)
        self.assertEqual(count, 1)
        self.assertIsNone(self.manager.scheduler.get_job(job.job_id))
        OBSERVATIONS.append({"case": "scheduled_once_success", "callback_count": count,
                             "database_row_retained": False})

    async def test_scheduled_once_failure_is_deleted_without_retry(self):
        count = 0
        fired = asyncio.Event()

        async def failing_callback(**_payload):
            nonlocal count
            count += 1
            fired.set()
            raise RuntimeError("PHASE1_SYNTHETIC_SCHEDULED_FAILURE")

        job = await self.make_scheduled_one_shot(failing_callback)
        await asyncio.wait_for(fired.wait(), timeout=3)
        await self.wait_for_deleted(job.job_id)
        await asyncio.sleep(0.1)
        self.assertEqual(count, 1)
        self.assertIsNone(self.manager.scheduler.get_job(job.job_id))
        OBSERVATIONS.append({"case": "scheduled_once_failure", "callback_count": count,
                             "database_row_retained": False, "native_retry_available": False,
                             "limitation": "finally deletes a scheduled run_once job even after failure"})

    async def test_manual_run_failure_retains_job_and_error(self):
        async def failing_callback(**_payload):
            raise RuntimeError("PHASE1_SYNTHETIC_MANUAL_FAILURE")

        job = await self.manager.add_basic_job(
            name="synthetic manual failure", cron_expression="0 0 1 1 *",
            handler=failing_callback, enabled=False, persistent=True,
        )
        await self.manager.update_job(
            job.job_id, run_once=True, payload={"run_at": "2099-01-01T09:00:00+08:00"},
        )
        await self.manager.run_job_now(job.job_id)
        durable = await self.db.get_cron_job(job.job_id)
        self.assertIsNotNone(durable)
        self.assertEqual(durable.status, "failed")
        self.assertEqual(durable.last_error, "PHASE1_SYNTHETIC_MANUAL_FAILURE")
        OBSERVATIONS.append({"case": "manual_once_failure", "database_row_retained": True,
                             "status": durable.status, "last_error": durable.last_error})

    async def test_recurring_failure_retains_job_and_error(self):
        async def failing_callback(**_payload):
            raise RuntimeError("PHASE1_SYNTHETIC_RECURRING_FAILURE")

        job = await self.manager.add_basic_job(
            name="synthetic recurring failure", cron_expression="0 0 1 1 *",
            handler=failing_callback, persistent=True,
        )
        # Invoke the same native entrypoint used by APScheduler, without mocks.
        await self.manager._run_job(job.job_id)
        durable = await self.db.get_cron_job(job.job_id)
        self.assertEqual(durable.status, "failed")
        self.assertEqual(durable.last_error, "PHASE1_SYNTHETIC_RECURRING_FAILURE")
        self.assertIsNotNone(self.manager.scheduler.get_job(job.job_id))
        OBSERVATIONS.append({"case": "recurring_failure", "database_row_retained": True,
                             "status": durable.status, "next_run_scheduled": True})

    async def test_active_once_timezone_and_database_restart(self):
        expected_local = datetime(2099, 1, 1, 9, 15, tzinfo=ZoneInfo("Asia/Shanghai"))
        job = await self.manager.add_active_job(
            name="synthetic future reminder", cron_expression=None,
            payload={"note": "Synthetic future reminder; never delivered in this test"},
            run_once=True, run_at=expected_local.replace(tzinfo=None),
            timezone="Asia/Shanghai", persistent=True,
        )
        first = self.manager.get_next_run_time(job.job_id)
        self.assertEqual(first, expected_local.astimezone(timezone.utc))
        await asyncio.sleep(0.1)
        await self.manager.shutdown()
        await self.db.engine.dispose()

        self.db = SQLiteDatabase(str(self.path))
        await self.db.initialize()
        self.manager = CronJobManager(self.db)
        # start only needs Context when a job executes. This 2099 fixture stays
        # pending; no LLM, platform or real account is created.
        await self.manager.start(None)
        restored = await self.db.get_cron_job(job.job_id)
        second = self.manager.get_next_run_time(job.job_id)
        self.assertEqual(second, first)
        self.assertEqual(restored.timezone, "Asia/Shanghai")
        self.assertTrue(restored.run_once)
        self.assertTrue(restored.persistent)
        OBSERVATIONS.append({"case": "active_once_timezone_restart", "timezone": restored.timezone,
                             "expected_utc": expected_local.astimezone(timezone.utc).isoformat(),
                             "before_restart": first.isoformat(), "after_restart": second.isoformat(),
                             "real_sqlite_reopened": True})


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(NativeFutureTaskTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    evidence = {"upstream": "AstrBot v4.29.1", "commit": "dffe922773725e9c178392c423676ad65044748c",
                "tests_run": result.testsRun, "tests_passed": result.wasSuccessful(),
                "failure_count": len(result.failures), "error_count": len(result.errors),
                "scope": "native scheduler characterization; no QQ, no LLM and no delivery reliability claim",
                "observations": OBSERVATIONS}
    if destination := os.environ.get("QQ_FUTURE_EVIDENCE"):
        Path(destination).write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    raise SystemExit(0 if result.wasSuccessful() else 1)
