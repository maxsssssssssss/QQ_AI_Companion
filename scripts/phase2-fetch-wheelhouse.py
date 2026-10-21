#!/usr/bin/env python3
"""Fetch hash-locked public PyPI artifacts for CPython 3.12 on Linux amd64.

The target is the pinned Debian bookworm image (glibc 2.36), independent of
the downloader's operating system. Windows curl can be invoked from WSL so
the host's approved network path is usable. TLS remains enabled. Nothing is
installed or executed from downloaded distributions.
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import quote, urlsplit
import uuid

try:
    from packaging.tags import compatible_tags, cpython_tags
    from packaging.utils import canonicalize_name, parse_sdist_filename, parse_wheel_filename
except ImportError:
    from pip._vendor.packaging.tags import compatible_tags, cpython_tags
    from pip._vendor.packaging.utils import canonicalize_name, parse_sdist_filename, parse_wheel_filename


TARGET = {"python": "3.12", "implementation": "cp", "platform": "linux/amd64", "glibc": "2.36"}
ARCHIVE_SUFFIXES = (".whl", ".tar.gz", ".zip", ".tar.bz2")


def file_sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_locks(root):
    """Require both Phase 1 lockfiles to retain their recorded digests."""
    upstream = json.loads((root / "config/upstream-lock.json").read_text())
    descriptors = {
        "build-tools.txt": upstream["dependencies"]["build_lock_sha256"],
        "astrbot-requirements.txt": upstream["dependencies"]["sha256"],
    }
    locked = {}
    for filename, expected in descriptors.items():
        path = root / "lockfiles" / filename
        if file_sha256(path) != expected:
            raise ValueError("Phase 1 lockfile SHA256 mismatch: " + filename)
        text = path.read_text()
        pattern = r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)(.*?)(?=^[A-Za-z0-9_.-]+==|\Z)"
        for match in re.finditer(pattern, text, re.M | re.S):
            name, version = canonicalize_name(match[1]), match[2]
            hashes = set(re.findall(r"--hash=sha256:([a-f0-9]{64})", match[3]))
            if not hashes:
                raise ValueError("A locked distribution has no SHA256 hashes")
            if name in locked and locked[name] != (version, hashes):
                raise ValueError("Conflicting distribution locks: " + name)
            locked[name] = (version, hashes)
    if not locked:
        raise ValueError("No locked distributions were found")
    return locked, descriptors


def target_tags():
    platforms = []
    aliases = {17: "manylinux2014_x86_64", 12: "manylinux2010_x86_64", 5: "manylinux1_x86_64"}
    for minor in range(36, 4, -1):
        platforms.append(f"manylinux_2_{minor}_x86_64")
        if minor in aliases:
            platforms.append(aliases[minor])
    platforms.append("linux_x86_64")
    ordered = list(cpython_tags((3, 12), ["cp312"], platforms))
    ordered.extend(compatible_tags((3, 12), "cp312", platforms))
    return {tag: index for index, tag in enumerate(dict.fromkeys(ordered))}


def artifact_rank(filename, name, version, tags):
    if Path(filename).name != filename or "/" in filename or "\\" in filename:
        raise ValueError("PyPI artifact filename must not contain a path")
    if filename.endswith(".whl"):
        wheel_name, wheel_version, _, wheel_tags = parse_wheel_filename(filename)
        if canonicalize_name(wheel_name) != name or str(wheel_version) != version:
            raise ValueError("Wheel name or version differs from the lock")
        return min((tags[tag] for tag in wheel_tags if tag in tags), default=None)
    if not filename.endswith(ARCHIVE_SUFFIXES[1:]):
        return None
    # Packaging's sdist parser recognizes .tar.gz and .zip; bz2 has the same stem.
    parsed_name = filename[:-8] + ".tar.gz" if filename.endswith(".tar.bz2") else filename
    sdist_name, sdist_version = parse_sdist_filename(parsed_name)
    if canonicalize_name(sdist_name) != name or str(sdist_version) != version:
        raise ValueError("Source archive name or version differs from the lock")
    return 1_000_000


def approved_url(url, hostname):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != hostname or parsed.username or parsed.password or parsed.port:
        raise ValueError("Only the official HTTPS PyPI endpoints are allowed")
    return url


def curl_output_path(path, windows_curl):
    path = Path(path).resolve()
    if not windows_curl or os.name == "nt":
        return str(path)
    match = re.fullmatch(r"/mnt/([a-zA-Z])/(.*)", str(path))
    if not match:
        raise ValueError("Windows curl from WSL requires output under /mnt/<drive>/")
    return match[1].upper() + ":/" + match[2]


def fetch(curl, url, *, output=None):
    # -q must be first: ignore .curlrc, including any credential/TLS overrides.
    args = [
        str(curl), "-q", "--fail", "--silent", "--show-error",
        "--connect-timeout", "15", "--max-time", "300", "--retry", "2",
        "--proto", "=https", "--proto-redir", "=https", "--location",
    ]
    if output is not None:
        args += ["--output", curl_output_path(output, str(curl).lower().endswith(".exe"))]
    result = subprocess.run(args + [url], capture_output=True, timeout=930)
    if result.returncode:
        # Do not relay a host curl configuration or environment in diagnostics.
        raise RuntimeError(f"Official PyPI HTTPS download failed (curl exit {result.returncode})")
    return result.stdout


def validate_record(record, locked, output, tags):
    name = canonicalize_name(record["name"])
    if name not in locked:
        raise ValueError("Manifest contains an unlocked distribution")
    version, allowed = locked[name]
    if record["version"] != version or record["sha256"] not in allowed:
        raise ValueError("Manifest version or digest differs from Phase 1 locks")
    approved_url(record["source"], "files.pythonhosted.org")
    if artifact_rank(record["file"], name, version, tags) is None:
        raise ValueError("Cached distribution is incompatible with the fixed target")
    path = output / record["file"]
    if path.is_symlink() or not path.is_file() or file_sha256(path) != record["sha256"]:
        raise ValueError("Cached artifact SHA256 mismatch: " + record["file"])
    return name


def verify_manifest(path, locked, output, tags):
    data = json.loads(path.read_text())
    # Accept the initial deployment's list manifest as well as this versioned format.
    if isinstance(data, dict):
        if data.get("target") != TARGET:
            raise ValueError("Manifest target differs from the fixed deployment target")
        data = data["artifacts"]
    names = [validate_record(record, locked, output, tags) for record in data]
    if len(names) != len(set(names)) or set(names) != set(locked):
        raise ValueError("Manifest must cover every locked distribution exactly once")
    return data


def download_one(name, lock, curl, output, tags):
    version, allowed = lock
    url = f"https://pypi.org/pypi/{quote(name, safe='')}/{quote(version, safe='')}/json"
    metadata = json.loads(fetch(curl, approved_url(url, "pypi.org")))
    candidates = []
    for item in metadata["urls"]:
        if item.get("digests", {}).get("sha256") not in allowed:
            continue
        if item.get("packagetype") not in ("bdist_wheel", "sdist"):
            continue
        rank = artifact_rank(item["filename"], name, version, tags)
        if rank is not None:
            candidates.append((rank, item["filename"], item))
    if not candidates:
        raise ValueError("No hash-locked artifact supports the target: " + name)
    item = min(candidates, key=lambda candidate: candidate[:2])[2]
    url = approved_url(item["url"], "files.pythonhosted.org")
    destination = output / item["filename"]
    if destination.is_symlink():
        raise ValueError("Artifact destination must not be a symbolic link")
    if not destination.is_file() or file_sha256(destination) != item["digests"]["sha256"]:
        temporary = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".part")
        try:
            fetch(curl, url, output=temporary)
            actual = file_sha256(temporary)
            if actual not in allowed or actual != item["digests"]["sha256"]:
                raise ValueError("Downloaded artifact SHA256 mismatch: " + name)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    record = {
        "name": name, "version": version, "file": destination.name,
        "sha256": file_sha256(destination), "source": url,
    }
    validate_record(record, {name: lock}, output, tags)
    print(name + "==" + version + " verified", flush=True)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--curl", type=Path, help="Explicit curl binary; WSL can use Windows System32/curl.exe")
    parser.add_argument("--jobs", type=int, default=4, choices=range(1, 7))
    parser.add_argument("--platform", choices=("linux/amd64",), default="linux/amd64")
    parser.add_argument("--python-version", choices=("3.12",), default="3.12")
    parser.add_argument("--verify-only", action="store_true", help="Verify cached artifacts; no network/subprocesses")
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    output = (args.output or root / ".runtime/phase2-artifacts/wheelhouse").resolve()
    manifest = args.manifest or output.parent / "wheelhouse-manifest.json"
    locked, lock_hashes = read_locks(root)
    tags = target_tags()
    if args.verify_only:
        artifacts = verify_manifest(manifest, locked, output, tags)
        print(f"PASS: {len(artifacts)} locked artifacts verified for CPython 3.12 Linux amd64/glibc 2.36; no network")
        return
    windows_curl = Path("/mnt/c/Windows/System32/curl.exe")
    curl = args.curl or (windows_curl if windows_curl.is_file() else shutil.which("curl"))
    if not curl:
        parser.error("curl is unavailable; select an existing --curl binary")
    output.mkdir(parents=True, exist_ok=True)
    curl_output_path(output / "probe.part", str(curl).lower().endswith(".exe"))
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as executor:
        tasks = [executor.submit(download_one, name, item, curl, output, tags) for name, item in locked.items()]
        artifacts = [future.result() for future in tasks]
    record = {
        "schema_version": 1, "target": TARGET, "lock_sha256": lock_hashes,
        "artifacts": sorted(artifacts, key=lambda item: item["name"]),
    }
    temporary = manifest.with_name(manifest.name + "." + uuid.uuid4().hex + ".part")
    temporary.write_text(json.dumps(record, indent=2) + "\n")
    verify_manifest(temporary, locked, output, tags)
    temporary.replace(manifest)
    print(f"PASS: {len(artifacts)} hash-locked artifacts; manifest: {manifest}")


if __name__ == "__main__":
    main()
