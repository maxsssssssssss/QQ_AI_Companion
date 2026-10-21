#!/usr/bin/env python3
"""Inspect an image without starting AstrBot or making network requests.

Run with the project lockfiles mounted read-only at /locks. Optionally mount
the unchanged pinned AstrBot checkout at /upstream to compare executable Python
sources. Output is package/version metadata and relative source paths only.
"""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import sys


def normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def inspect(locks: Path, upstream: Path | None, app: Path):
    expected = {}
    for filename in ("astrbot-requirements.txt", "build-tools.txt"):
        for line in (locks / filename).read_text().splitlines():
            match = re.match(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)==([^\s;\\]+)", line)
            if match:
                name, version = normalize(match[1]), match[2]
                if name in expected and expected[name] != version:
                    raise ValueError("Conflicting project locks")
                expected[name] = version
    installed = {}
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name")
        if name:
            installed.setdefault(normalize(name), set()).add(distribution.version)
    mismatches = [
        {"name": name, "expected": version, "actual": sorted(installed.get(name, []))}
        for name, version in expected.items() if installed.get(name) != {version}
    ]
    report = {
        "python": sys.version.split()[0],
        "python_3_12": sys.version_info[:2] == (3, 12),
        "locked_packages_checked": len(expected),
        "dependency_mismatches": mismatches,
        "additional_packages": [
            {"name": name, "versions": sorted(versions)}
            for name, versions in sorted(installed.items()) if name not in expected
        ],
        "source_comparison": {"status": "NOT_TESTED"},
        "network_used": False,
        "application_started": False,
    }
    if upstream is not None:
        wanted = {str(path.relative_to(upstream)) for path in upstream.glob("*.py")} | {
            str(path.relative_to(upstream)) for path in (upstream / "astrbot").rglob("*.py")
        }
        if len(wanted) < 2:
            raise ValueError("Pinned upstream source tree is missing")
        actual = {str(path.relative_to(app)) for path in app.glob("*.py")} | {
            str(path.relative_to(app)) for path in (app / "astrbot").rglob("*.py")
        }
        different = []
        for relative in sorted(wanted):
            source, target = upstream / relative, app / relative
            if not target.is_file() or hashlib.sha256(source.read_bytes()).digest() != \
                    hashlib.sha256(target.read_bytes()).digest():
                different.append(relative)
        extra = sorted(actual - wanted)
        report["source_comparison"] = {
            "status": "PASS" if not different and not extra else "FAIL",
            "scope": "root *.py including runtime_bootstrap.py and all astrbot/**/*.py (not frontend assets)",
            "files_checked": len(wanted),
            "different_or_missing": different,
            "additional_python_sources": extra,
        }
    report["status"] = (
        "PASS" if report["python_3_12"] and not mismatches and
        report["source_comparison"]["status"] != "FAIL" else "FAIL"
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--locks", type=Path, default=Path("/locks"))
    parser.add_argument("--upstream", type=Path)
    parser.add_argument("--app", type=Path, default=Path("/AstrBot"))
    arguments = parser.parse_args()
    result = inspect(arguments.locks, arguments.upstream, arguments.app)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "PASS" else 1)
