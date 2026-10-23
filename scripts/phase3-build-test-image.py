#!/usr/bin/env python3
"""Build a local-only test dependency image offline; no upstream plugin sources."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
BASE = "qq-companion/python-base:3.12-34386ef0cb08"
EXPECTED = "sha256:4f228bc1cbcfc794e4878312cfc241d368eff55a21c08d1e2bb263116c4ef524"
IMPORTED_MANIFEST = "sha256:3ca5f41bc71a7bde93b47dc4aba98d8fc33531c8863a343103f60c92303e4025"


def main():
    observed = subprocess.check_output(["docker", "image", "inspect", BASE, "--format", "{{.Id}}"], text=True).strip()
    # Docker's containerd image store reports the reconstructed Docker archive
    # manifest instead of the OCI config ID. Both are pinned, not learned from
    # whichever tag happens to exist at build time.
    if observed not in {EXPECTED, IMPORTED_MANIFEST}:
        raise SystemExit("pinned Python base not loaded or mismatched")
    provenance = json.loads((ROOT / ".runtime/phase2-images/python-provenance.json").read_text())
    layers = json.loads(subprocess.check_output(["docker", "image", "inspect", BASE, "--format", "{{json .RootFS.Layers}}"], text=True))
    if layers != provenance["diff_ids"]:
        raise SystemExit("Python layer chain mismatch")
    lock = json.loads((ROOT / "config/upstream-lock.json").read_text())
    for path_key, hash_key in (("lock", "sha256"), ("build_lock", "build_lock_sha256")):
        content = (ROOT / lock["dependencies"][path_key]).read_bytes()
        if hashlib.sha256(content).hexdigest() != lock["dependencies"][hash_key]:
            raise SystemExit("lock hash mismatch")
    process = subprocess.Popen(["docker", "build", "--network", "none", "--pull=false", "-t", "qq-companion:phase3-test", "-"], stdin=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=process.stdin, mode="w|gz", format=tarfile.GNU_FORMAT) as archive:
            archive.add(ROOT / "deploy/phase3/test.Dockerfile", arcname="Dockerfile")
            for name in ("astrbot-requirements.txt", "build-tools.txt"):
                archive.add(ROOT / "lockfiles" / name, arcname="lockfiles/" + name)
            for file in sorted((ROOT / ".runtime/phase2-artifacts/wheelhouse").iterdir()):
                if file.is_symlink() or not file.is_file():
                    raise ValueError("nonregular_wheelhouse_entry")
                if file.name.endswith((".whl", ".tar.gz", ".zip")):
                    archive.add(file, arcname="wheelhouse/" + file.name)
    finally:
        process.stdin.close()
    raise SystemExit(process.wait())


if __name__ == "__main__":
    main()
