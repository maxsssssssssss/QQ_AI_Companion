#!/usr/bin/env bash
# Build verified, already-imported production bases. Never pull images or start QQ.
set -euo pipefail
umask 077
exec python3 - "$@" <<'PY'
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid


BASES = {
    "astrbot": {
        "reference": "soulter/astrbot@sha256:0c3b8ef7485dbd15176637133c23dfbdb80675d174545a997648b9c1db7d53c1",
        "config_id": "sha256:6bbe494e579a23773007e0db771a9a06ef5312127283874d105f45933b1a3567",
        "version": "4.29.1",
    },
    "napcat": {
        "reference": "mlikiowa/napcat-docker@sha256:132448a544ed5cfa948dff8ae19e80bf4e17896e8ab2ba1c6cbcd86f8ad3e56e",
        "config_id": "sha256:2efb1af46d6b9cc28600a8883fe8c5331d6599888aa03e907a23ad4d2682a2cb",
        "version": "4.18.38",
    },
}
ASTRBOT_COMMIT = "dffe922773725e9c178392c423676ad65044748c"
NAPCAT_ZIP_SHA256 = "735f2e633247df3ffd6e8ee8b84d7039c315b2655f5807e5f0029f93d2c30016"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def command(args, *, accepted=(0,), timeout=120):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode not in accepted:
        # Do not print an image's complete configuration or inherited environment.
        raise RuntimeError(f"{args[0]} {args[1]} failed (exit {result.returncode})")
    return result


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checked_copy(source, destination):
    source, destination = Path(source), Path(destination)
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"Expected a regular input file: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    destination.chmod(0o644)
    digest = sha256(source)
    if sha256(destination) != digest:
        raise ValueError("Build input changed during copying")
    return digest


def distribution_files(wheelhouse):
    suffixes = (".whl", ".tar.gz", ".zip", ".tar.bz2")
    files = sorted(path for path in Path(wheelhouse).iterdir() if path.name.endswith(suffixes))
    if not files:
        raise ValueError("Dockerfile needs a wheelhouse but no distribution archives are present")
    if any(path.is_symlink() or not path.is_file() for path in files):
        raise ValueError("Wheelhouse distributions must be regular files")
    return files


def inspect_image(reference):
    # Capture full JSON only in memory; serialize a non-secret allowlist below.
    result = json.loads(command(["docker", "image", "inspect", reference]).stdout)
    if len(result) != 1:
        raise ValueError("Expected exactly one local image")
    image = result[0]
    image_id = image.get("Id", "")
    if not DIGEST.fullmatch(image_id):
        raise ValueError("Docker returned an invalid immutable image ID")
    return {
        "image_id": image_id,
        "descriptor_digest": image.get("Descriptor", {}).get("digest"),
        "repo_digests": image.get("RepoDigests", []),
        "os": image.get("Os"),
        "architecture": image.get("Architecture"),
        "rootfs_diff_ids": image.get("RootFS", {}).get("Layers", []),
    }


def canonical_ref(reference):
    return reference.removeprefix("docker.io/")


def validate_base(image, expected):
    reference = expected["reference"]
    manifest = reference.split("@", 1)[1]
    if canonical_ref(reference) not in {canonical_ref(r) for r in image["repo_digests"]}:
        raise ValueError("Local base does not carry the required upstream RepoDigest")
    if (image["os"], image["architecture"]) != ("linux", "amd64"):
        raise ValueError("Base platform differs from locked linux/amd64")
    # Docker's containerd store can expose a manifest ID; classic store uses config ID.
    if image["image_id"] not in {manifest, expected["config_id"]}:
        raise ValueError("Base image ID differs from both pinned manifest and config")
    if image["descriptor_digest"] not in (None, manifest):
        raise ValueError("Base descriptor differs from the pinned manifest")
    if not image["rootfs_diff_ids"] or not all(DIGEST.fullmatch(d) for d in image["rootfs_diff_ids"]):
        raise ValueError("Base rootfs diffIDs are missing or malformed")


def validate_derived(image, base):
    if (image["os"], image["architecture"]) != ("linux", "amd64"):
        raise ValueError("Built image has the wrong platform")
    parent_layers = base["rootfs_diff_ids"]
    if image["rootfs_diff_ids"][:len(parent_layers)] != parent_layers:
        raise ValueError("Built image does not retain the verified base layer chain")


def validate_local_tag(image, verified_base):
    # Some image stores scope RepoDigests to the inspected repository name.
    # The original reference is validated separately; aliases must be identical.
    for field in ("image_id", "descriptor_digest", "os", "architecture", "rootfs_diff_ids"):
        if image[field] != verified_base[field]:
            raise ValueError("Local base tag no longer identifies the verified image")


def rewrite_dockerfile(content, kind, local_tag):
    if re.search(r"^\s*#\s*syntax\s*=", content, flags=re.M):
        raise ValueError("External Dockerfile frontend could trigger an image download")
    from_lines = re.findall(r"^FROM\s+(.+)$", content, flags=re.M | re.I)
    expected = "${ASTRBOT_BASE}" if kind == "astrbot" else BASES[kind]["reference"]
    if from_lines != [expected]:
        raise ValueError("Dockerfile FROM changed; review the new base before building")
    if kind == "astrbot":
        declaration = "ARG ASTRBOT_BASE=" + BASES[kind]["reference"]
        if declaration not in content.splitlines():
            raise ValueError("AstrBot default base differs from the pinned source")
        return content
    return content.replace("FROM " + expected, "FROM " + local_tag, 1)


def save_record(path, record):
    temporary = path.with_suffix(".partial")
    temporary.write_text(json.dumps(record, indent=2) + "\n")
    temporary.replace(path)


def run_astrbot_inspection(reference, script, locks, upstream):
    args = [
        "docker", "run", "--rm", "--pull", "never", "--network", "none",
        "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--memory", "768m", "--pids-limit", "128", "--user", "0:0",
        "--env", "PYTHONPATH=",
        "--entrypoint", "python",
    ]
    for source, target in ((script, "/phase2_image_audit.py"), (locks, "/locks"), (upstream, "/upstream")):
        if "," in str(source):
            raise ValueError("Mount source paths must not contain commas")
        args += ["--mount", f"type=bind,src={source},dst={target},readonly"]
    result = command(
        args + [reference, "/phase2_image_audit.py", "--upstream", "/upstream"], accepted=(0, 1),
    )
    report = json.loads(result.stdout)
    if not report.get("python_3_12") or report.get("source_comparison", {}).get("status") != "PASS":
        raise ValueError("AstrBot executable sources or Python version differ from Phase 1")
    return report


def main():
    parser = argparse.ArgumentParser(
        description="Build pinned imported images; no docker pull, service startup, or production .env changes."
    )
    parser.add_argument("--apply", action="store_true", help="Create local image tags and build images")
    parser.add_argument("--repo", type=Path, default=Path("/opt/qq-ai-companion/repo"))
    parser.add_argument("--source-root", type=Path, default=Path("/opt/qq-ai-companion/phase2-src"))
    parser.add_argument("--build-root", type=Path, default=Path("/opt/qq-ai-companion/deployment/build"))
    parser.add_argument("--only", choices=("astrbot", "napcat", "both"), default="both")
    parser.add_argument("--dependency-network", choices=("default", "none"), default="default",
                        help="RUN network only; choose none when Dockerfile uses a complete local wheelhouse")
    args = parser.parse_args()
    if not args.apply:
        parser.error("--apply is required; --help performs no Docker operations")
    repo, source, build = (p.resolve(strict=True) for p in (args.repo, args.source_root, args.build_root))
    kinds = list(BASES) if args.only == "both" else [args.only]
    lock = json.loads((repo / "config/upstream-lock.json").read_text())
    if lock["astrbot"]["commit"] != ASTRBOT_COMMIT:
        raise ValueError("Phase 1 source commit changed; review required")
    lock_hashes = {
        "astrbot-requirements.txt": lock["dependencies"]["sha256"],
        "build-tools.txt": lock["dependencies"]["build_lock_sha256"],
    }
    for name, expected in lock_hashes.items():
        if sha256(repo / "lockfiles" / name) != expected:
            raise ValueError("Phase 1 dependency lock hash mismatch")
    bases = {}
    for kind in kinds:
        bases[kind] = inspect_image(BASES[kind]["reference"])
        validate_base(bases[kind], BASES[kind])
    upstream = repo / ".runtime/upstream/AstrBot"
    if "astrbot" in kinds:
        if command(["git", "-C", str(upstream), "rev-parse", "HEAD"]).stdout.strip() != ASTRBOT_COMMIT:
            raise ValueError("AstrBot checkout is not the Phase 1 commit")
        if command(["git", "-C", str(upstream), "status", "--porcelain", "--untracked-files=normal"]).stdout:
            raise ValueError("AstrBot source checkout has local changes")
    if "napcat" in kinds and sha256(build / "napcat/NapCat.Shell.zip") != NAPCAT_ZIP_SHA256:
        raise ValueError("NapCat release archive hash differs from the verified 4.18.38 asset")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    run = build / "runs" / run_id
    run.mkdir(parents=True, mode=0o700)
    record_path = run / "build-provenance.json"
    record = {
        "status": "RUNNING", "run_id": run_id, "platform": "linux/amd64",
        "astrbot_commit": ASTRBOT_COMMIT, "lock_sha256": lock_hashes,
        "pull_images": False, "dependency_run_network": args.dependency_network,
        "services_started": False, "images": {},
    }
    save_record(record_path, record)
    try:
        for kind in kinds:
            base = BASES[kind]
            local_tag = f"qq-companion-verified-{kind}-base:{run_id}"
            output_tag = f"qq-ai-companion-{kind}:phase2-{run_id}"
            context = run / kind
            context.mkdir()
            original = source / "deploy/phase2" / (kind + ".Dockerfile")
            content = rewrite_dockerfile(original.read_text(), kind, local_tag)
            item = record["images"][kind] = {
                "status": "PREPARING", "base_reference": base["reference"],
                "target_version": base["version"],
                "base_expected_config_id": base["config_id"], "base": bases[kind],
                "local_base_tag": local_tag, "output_tag": output_tag,
                "Dockerfile_sha256": sha256(original), "input_sha256": {},
                "context": str(context),
            }
            (context / "Dockerfile").write_text(content)
            item["effective_Dockerfile_sha256"] = sha256(context / "Dockerfile")
            if kind == "astrbot":
                for filename in lock_hashes:
                    checked_copy(repo / "lockfiles" / filename, context / "lockfiles" / filename)
                security = {
                    "sitecustomize.py": source / "scripts/phase2-sitecustomize.py",
                    "secret_redaction.py": source / "integrations/phase2_policy/redaction.py",
                }
                for filename, path in security.items():
                    item["input_sha256"]["security/" + filename] = checked_copy(
                        path, context / "security" / filename,
                    )
                if re.search(r"^\s*COPY\s+(?:--\S+\s+)*wheelhouse(?:/|\s)", content, flags=re.M):
                    wheelhouse = build / "astrbot/wheelhouse"
                    for distribution in distribution_files(wheelhouse):
                        item["input_sha256"]["wheelhouse/" + distribution.name] = checked_copy(
                            distribution, context / "wheelhouse" / distribution.name,
                        )
                report = run_astrbot_inspection(
                    base["reference"], source / "scripts/phase2-inspect-image.py",
                    repo / "lockfiles", upstream,
                )
                item["base_inspection"] = report
            else:
                item["input_sha256"]["NapCat.Shell.zip"] = checked_copy(
                    build / "napcat/NapCat.Shell.zip", context / "NapCat.Shell.zip",
                )
            command(["docker", "image", "tag", base["reference"], local_tag])
            validate_local_tag(inspect_image(local_tag), bases[kind])
            item["status"] = "BUILDING"
            save_record(record_path, record)
            metadata = context / "buildkit-metadata.json"
            build_args = [
                "docker", "build", "--builder", "default", "--pull=false",
                "--platform", "linux/amd64", "--network", args.dependency_network,
                "--progress", "plain", "--metadata-file", str(metadata),
                "--tag", output_tag, "--file", str(context / "Dockerfile"),
            ]
            if kind == "astrbot":
                build_args += ["--build-arg", "ASTRBOT_BASE=" + local_tag]
            result = subprocess.run(build_args + [str(context)], timeout=3600)
            if result.returncode:
                raise RuntimeError(f"{kind} Docker build failed (exit {result.returncode})")
            validate_base(inspect_image(base["reference"]), base)
            validate_local_tag(inspect_image(local_tag), bases[kind])
            final_image = inspect_image(output_tag)
            validate_derived(final_image, bases[kind])
            item["final"] = final_image
            item["buildkit_metadata"] = str(metadata)
            if kind == "astrbot":
                report = run_astrbot_inspection(
                    final_image["image_id"], source / "scripts/phase2-inspect-image.py",
                    repo / "lockfiles", upstream,
                )
                item["final_inspection"] = report
                if report["status"] != "PASS":
                    raise ValueError("Built AstrBot does not match all Phase 1 dependency locks")
            else:
                result = command([
                    "docker", "run", "--rm", "--pull", "never", "--network", "none",
                    "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                    "--memory", "128m", "--pids-limit", "32", "--entrypoint", "sha256sum",
                    final_image["image_id"], "/app/NapCat.Shell.zip",
                ])
                if result.stdout.split()[0] != NAPCAT_ZIP_SHA256:
                    raise ValueError("Built NapCat release archive changed")
                item["release_archive_sha256"] = NAPCAT_ZIP_SHA256
                item["qq_login_started"] = False
            item["status"] = "PASS"
            save_record(record_path, record)
        record["status"] = "PASS"
        save_record(record_path, record)
    except Exception as error:
        record["status"] = "FAIL"
        record["error"] = str(error)
        save_record(record_path, record)
        print(f"Build failed; evidence preserved at {record_path}", file=sys.stderr)
        raise
    print("Verified build provenance: " + str(record_path))
    for kind, item in record["images"].items():
        print(kind.upper() + "_IMAGE=" + item["final"]["image_id"])
    print("No service was started. Production configuration was not changed.")


if __name__ == "__main__":
    main()
PY
