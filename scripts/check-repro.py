#!/usr/bin/env python3
"""Rebuild and run the offline integration suite from a fresh local Git clone.

This checks tracked source with no .runtime cache. Docker's content-addressed
image/build cache may be reused; network is allowed only for dependency setup.
The caller's existing checkout and third-party sources are never reset.
"""
import json
from pathlib import Path
import subprocess
import tempfile
from datetime import datetime, timezone

from provenance import source_manifest

root = Path(__file__).resolve().parents[1]
baseline = source_manifest(root)
head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
fresh = Path(tempfile.mkdtemp(prefix="qq-phase1-repro-")) / "project"
logdir = root / "evidence/runs" / ("repro-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
logdir.mkdir(parents=True)
steps = []


def run(name, args, cwd):
    with (logdir / (name + ".log")).open("w") as log:
        completed = subprocess.run(args, cwd=cwd, stdout=log, stderr=subprocess.STDOUT)
    steps.append({"step": name, "command": args, "exit_code": completed.returncode})
    print(name, "PASS" if completed.returncode == 0 else "FAIL", flush=True)
    if completed.returncode:
        raise RuntimeError(f"Reproduction failed at {name}; see {logdir}")


error = None
try:
    run("clone", ["git", "clone", "--no-hardlinks", str(root), str(fresh)], root)
    if baseline != source_manifest(fresh):
        raise RuntimeError("Working source differs from committed snapshot; commit source before reproduction")
    if (fresh / ".runtime").exists():
        raise RuntimeError("Fresh clone unexpectedly contains a runtime cache")
    run("bootstrap", ["bash", "scripts/bootstrap.sh"], fresh)
    run("fetch-plugins", ["python3", "scripts/fetch_plugins.py"], fresh)
    run("doctor", ["bash", "scripts/doctor.sh", "--json"], fresh)
    run("integration", ["bash", "scripts/test-offline.sh"], fresh)
except Exception as exc:
    error = str(exc)
report = {"status": "FAIL" if error else "PASS", "source_commit": head,
          "source_manifest": baseline, "fresh_clone": str(fresh),
          "steps": steps, "error": error, "evidence_directory": str(logdir.relative_to(root)),
          "scope": "Fresh Git checkout, no project runtime cache; pinned Docker build cache may be reused; real offline upstream-process suite executed"}
latest = fresh / ".runtime/latest-evidence"
if latest.is_file():
    runpath = Path(latest.read_text().strip())
    report["integration_result"] = json.loads((runpath / "results.json").read_text())
(root / "evidence/repro.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
raise SystemExit(1 if error else 0)
