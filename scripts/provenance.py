"""Fingerprint every tracked executable/configuration input, excluding reports."""
import hashlib
from pathlib import Path
import subprocess


def source_manifest(root):
    root = Path(root)
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
    directories = {"scripts", "tests", "config", "persona", "integrations", "lockfiles"}
    files = {"Dockerfile", "launch_astrbot.py", ".gitignore", ".env.example"}
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in sorted(filter(None, names))
            if Path(name).parts[0] in directories or name in files}
