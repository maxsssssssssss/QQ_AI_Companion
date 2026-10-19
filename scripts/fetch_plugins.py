#!/usr/bin/env python3
"""Fetch locked upstream plugins into ignored local evaluation storage.

This command uses existing Git authentication and TLS verification. It neither
publishes sources nor grants any redistribution license. The resulting third-
party folders must stay outside project commits, images and release archives.
"""
import argparse
import json
from pathlib import Path
import subprocess

from prepare_plugins import PROJECT, source_tree_sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=PROJECT / ".runtime/upstream")
    args = parser.parse_args()
    manifest = json.loads((PROJECT / "config/plugins-lock.json").read_text())
    args.sources.mkdir(parents=True, exist_ok=True)
    for plugin in manifest["plugins"]:
        source = args.sources / plugin["id"]
        if not source.exists():
            subprocess.run(["git", "init", "--quiet", str(source)], check=True)
            subprocess.run(["git", "-C", str(source), "remote", "add", "origin", plugin["repository"]], check=True)
            subprocess.run(["git", "-C", str(source), "fetch", "--depth", "1", "origin", plugin["commit"]], check=True)
            subprocess.run(["git", "-C", str(source), "checkout", "--quiet", "--detach", "FETCH_HEAD"], check=True)
        actual = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        if actual != plugin["commit"]:
            raise SystemExit(f"Preserving existing checkout with unexpected commit: {source}")
        if source_tree_sha256(source) != plugin["source_tree_sha256"]:
            raise SystemExit(f"Preserving existing checkout with unexpected contents: {source}")
        print(f"Verified {plugin['id']} {actual}; unchanged local evaluation source")


if __name__ == "__main__":
    main()
