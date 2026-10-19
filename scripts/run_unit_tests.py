#!/usr/bin/env python3
"""Run first-party file/SQLite/config tests and preserve a machine-readable result."""
import json
from pathlib import Path
import sys
import unittest

root = Path(__file__).resolve().parents[1]
suite = unittest.TestSuite()
for filename in ("test_backup.py", "test_configuration.py", "test_guard.py", "test_export.py"):
    if not (root / "tests" / filename).is_file():
        raise SystemExit(f"Required test file missing: {filename}")
    suite.addTests(unittest.defaultTestLoader.discover(str(root / "tests"), pattern=filename))
result = unittest.TextTestRunner(verbosity=2).run(suite)
payload = {"tests_run": result.testsRun, "passed": result.wasSuccessful(),
           "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped)}
if len(sys.argv) > 1:
    Path(sys.argv[1]).write_text(json.dumps(payload, indent=2) + "\n")
raise SystemExit(0 if result.wasSuccessful() and result.testsRun > 0 and not result.skipped else 1)
