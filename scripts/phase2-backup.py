#!/usr/bin/env python3
"""Stopped-service backup and isolated restore verification.

Without --apply, inspect file types only and never stop services or write files.
With --apply, record the originally running services, stop both services, take
snapshots, and restore only those originally running services in finally.
Restored copies are never attached to containers or used to log in QQ.

AstrBot uses the unchanged Phase 1 backup.py, including SQLite validation.
NapCat config and QQ login state are opaque bytes: encrypted *.db files must
not be interpreted as SQLite. Private manifests may contain account filenames;
only counts, aggregate digests, and fixed result codes reach stdout.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import zipfile

import backup

DEFAULT_DEPLOYMENT = Path("/opt/qq-ai-companion/deployment")
SERVICES = ("astrbot", "napcat")
OPAQUE_FORMAT = "qq-ai-companion-phase2-opaque"
INSPECT = (
    '{"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
    '"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
    '"running":{{json .State.Running}},"status":{{json .State.Status}}}'
)


class Phase2BackupError(ValueError):
    """Fixed internal reason codes only."""


def run_command(arguments, *, timeout=45):
    try:
        result = subprocess.run(
            arguments, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise Phase2BackupError("docker_command_unavailable") from None
    if result.returncode or len(result.stdout) > 65536:
        raise Phase2BackupError("docker_command_failed")
    return result.stdout


class ComposeServices:
    def __init__(self, deployment):
        self.prefix = [
            "docker", "compose", "--project-directory", str(deployment),
            "-f", str(deployment / "compose.yml"),
        ]

    def running(self):
        identifiers = run_command(self.prefix + ["ps", "--all", "--quiet", *SERVICES]).split()
        if len(identifiers) != len(SERVICES) or any(
            not re.fullmatch(r"[0-9a-f]{12,64}", item) for item in identifiers
        ):
            raise Phase2BackupError("expected_existing_service_containers")
        found, running = set(), set()
        for identifier in identifiers:
            try:
                item = json.loads(run_command(["docker", "inspect", "--format", INSPECT, identifier]))
            except (ValueError, TypeError):
                raise Phase2BackupError("invalid_service_state") from None
            service = item.get("service")
            if service not in SERVICES or service in found or item.get("project") != "qq-ai-companion":
                raise Phase2BackupError("unexpected_service_container")
            if type(item.get("running")) is not bool or item.get("status") not in {"created", "running", "exited"}:
                raise Phase2BackupError("unstable_service_state")
            if item["running"] != (item["status"] == "running"):
                raise Phase2BackupError("inconsistent_service_state")
            found.add(service)
            if item["running"]:
                running.add(service)
        return running

    def stop(self):
        run_command(self.prefix + ["stop", "--timeout", "30", *SERVICES])

    def start(self, services):
        # start cannot create new containers, build/pull images, or start deps.
        if services:
            run_command(self.prefix + ["start", *sorted(services)])


def audit_tree(root):
    """lstat only; never follow links, read file contents, or return filenames."""
    if root.is_symlink() or not root.is_dir():
        raise Phase2BackupError("invalid_source_directory")
    result = dict(files=0, directories=1, bytes=0, symlinks=0, hardlinks=0, special_files=0)

    def walk_error(_error):
        raise Phase2BackupError("source_metadata_unavailable")

    for directory, directories, files in os.walk(root, followlinks=False, onerror=walk_error):
        for name in directories + files:
            path = Path(directory) / name
            backup._safe_path(path.relative_to(root).as_posix())
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                result["symlinks"] += 1
            elif stat.S_ISDIR(info.st_mode):
                result["directories"] += 1
            elif stat.S_ISREG(info.st_mode):
                result["files"] += 1
                result["bytes"] += info.st_size
                if info.st_nlink != 1:
                    result["hardlinks"] += 1
            else:
                result["special_files"] += 1
    return result


def audit_reason(audits):
    for field, reason in (
        ("symlinks", "source_symlink_rejected"),
        ("hardlinks", "source_hardlink_rejected"),
        ("special_files", "source_special_file_rejected"),
    ):
        if any(item[field] for item in audits.values()):
            return reason
    return None


def require_private_directory(path):
    if path.is_symlink() or not path.is_dir():
        raise Phase2BackupError("unsafe_output_directory")
    info = path.stat()
    if stat.S_IMODE(info.st_mode) != 0o700 or (os.geteuid() != 0 and info.st_uid != os.geteuid()):
        raise Phase2BackupError("unsafe_output_permissions")


def write_json_new(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def opaque_inventory(sources):
    files, directories = {}, {}
    for prefix, source in sources.items():
        current_files, current_dirs = backup._tree(source)
        directories[prefix] = stat.S_IMODE(source.stat().st_mode) & 0o777
        files.update({prefix + "/" + name: metadata for name, metadata in current_files.items()})
        directories.update({prefix + "/" + name: mode for name, mode in current_dirs.items()})
    return files, directories


def create_opaque_backup(sources, archive):
    """Snapshot opaque config/login bytes without invoking any SQLite reader."""
    if os.path.lexists(archive):
        raise Phase2BackupError("archive_already_exists")
    before = opaque_inventory(sources)
    with tempfile.TemporaryDirectory(prefix=".opaque-snapshot-", dir=archive.parent) as temporary:
        stage = Path(temporary) / "opaque"
        stage.mkdir(mode=0o700)
        for prefix, source in sources.items():
            shutil.copytree(source, stage / prefix, symlinks=True)
        copied = backup._tree(stage)
        if copied != before or opaque_inventory(sources) != before:
            raise Phase2BackupError("source_changed_during_snapshot")
        manifest = {"format": OPAQUE_FORMAT, "version": 1, "files": copied[0],
                    "directories": copied[1], "sqlite_validation": "NOT APPLICABLE"}
        pending = Path(temporary) / "opaque.zip"
        with zipfile.ZipFile(pending, "w", compression=zipfile.ZIP_DEFLATED) as output:
            output.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
            for name in sorted(manifest["files"]):
                output.write(stage / name, "opaque/" + name)
        pending.chmod(0o600)
        os.link(pending, archive)
    return manifest


def restore_opaque_backup(archive, destination, expected_manifest):
    """Verify against this run's trusted manifest, then restore a fresh copy."""
    if os.path.lexists(destination):
        raise Phase2BackupError("restore_destination_already_exists")
    expected_names = {"manifest.json", *("opaque/" + name for name in expected_manifest["files"])}
    with tempfile.TemporaryDirectory(prefix=".opaque-restore-", dir=destination.parent) as temporary:
        stage = Path(temporary) / "opaque"
        stage.mkdir(mode=0o700)
        with zipfile.ZipFile(archive) as incoming:
            entries = incoming.infolist()
            names = [entry.filename for entry in entries]
            if len(names) != len(set(names)) or set(names) != expected_names:
                raise Phase2BackupError("opaque_archive_inventory_mismatch")
            for entry in entries:
                backup._safe_path(entry.filename)
                if entry.is_dir() or stat.S_IFMT(entry.external_attr >> 16) not in {0, stat.S_IFREG}:
                    raise Phase2BackupError("opaque_archive_special_entry")
                if entry.flag_bits & 1:
                    raise Phase2BackupError("opaque_archive_encryption_unsupported")
            if incoming.getinfo("manifest.json").file_size > backup.MANIFEST_LIMIT:
                raise Phase2BackupError("opaque_manifest_too_large")
            if json.loads(incoming.read("manifest.json")) != expected_manifest:
                raise Phase2BackupError("opaque_manifest_mismatch")
            for name in sorted(expected_manifest["directories"], key=lambda value: len(Path(value).parts)):
                backup._safe_path(name)
                (stage / name).mkdir(mode=0o700)
            for name, metadata in expected_manifest["files"].items():
                backup._safe_path(name)
                entry = incoming.getinfo("opaque/" + name)
                if entry.file_size != metadata["size"]:
                    raise Phase2BackupError("opaque_file_size_mismatch")
                target = stage / name
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with incoming.open(entry) as source, os.fdopen(fd, "wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                if backup._hash(target) != metadata["sha256"]:
                    raise Phase2BackupError("opaque_file_digest_mismatch")
        # Reserve atomically: even an existing empty destination is rejected.
        destination.mkdir(mode=0o700)
        for child in stage.iterdir():
            shutil.move(str(child), destination / child.name)


def protect_and_verify_restore(root, manifest):
    files, directories = backup._tree(root)
    if set(files) != set(manifest["files"]) or set(directories) != set(manifest["directories"]):
        raise Phase2BackupError("restored_inventory_mismatch")
    for name, metadata in files.items():
        expected = manifest["files"][name]
        if (metadata["sha256"], metadata["size"]) != (expected["sha256"], expected["size"]):
            raise Phase2BackupError("restored_digest_mismatch")
        os.chmod(root / name, 0o600)
    for name in directories:
        os.chmod(root / name, 0o700)
    root.chmod(0o700)


def safe_failure(error):
    if isinstance(error, Phase2BackupError):
        return str(error)
    if isinstance(error, backup.BackupError):
        return "sqlite_integrity_failed" if "SQLite" in str(error) else "snapshot_validation_failed"
    if isinstance(error, (KeyboardInterrupt, InterruptedError)):
        return "operation_interrupted"
    if isinstance(error, (zipfile.BadZipFile, EOFError)):
        return "archive_integrity_failed"
    return "backup_operation_failed"


def exercise(deployment, run_id, *, apply=False, services=None):
    report = {
        "scope": "stopped_backup_isolated_restore", "status": "NOT TESTED",
        "apply": bool(apply), "source_audit": {}, "services_initially_running_count": 0,
        "service_state_restored": None, "backup_integrity": "NOT TESTED",
        "restore_integrity": "NOT TESTED", "restored_copy_started": False,
        "qq_login_or_chat_verified": False,
    }
    original = None
    stop_attempted = False
    output = None
    try:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", run_id):
            raise Phase2BackupError("invalid_run_identifier")
        deployment = Path(deployment).absolute()
        if any(path.is_symlink() for path in (deployment, *deployment.parents)):
            raise Phase2BackupError("symlink_deployment_path")
        require_private_directory(deployment)
        sources = {"astrbot": deployment / "state",
                   "napcat_config": deployment / "napcat/config",
                   "qq_login_state": deployment / "ntqq"}
        if any(path.is_symlink() for root in sources.values() for path in (root, root.parent)):
            raise Phase2BackupError("symlink_source_path")
        parent, output = deployment / "backups", deployment / "backups" / run_id
        if parent.exists() or parent.is_symlink():
            require_private_directory(parent)
        if os.path.lexists(output):
            raise Phase2BackupError("backup_run_already_exists")
        report["source_audit"] = {name: audit_tree(path) for name, path in sources.items()}
        if not apply:
            reason = audit_reason(report["source_audit"])
            report["status"] = "BLOCKED" if reason else "NOT TESTED"
            report["reason"] = reason or "explicit_apply_required"
            return report
        estimated_bytes = sum(item["bytes"] for item in report["source_audit"].values())
        if shutil.disk_usage(deployment).free < estimated_bytes * 4 + 64 * 1024 * 1024:
            raise Phase2BackupError("insufficient_backup_space")
        services = services or ComposeServices(deployment)
        original = services.running()
        if not isinstance(original, set) or not original.issubset(SERVICES):
            raise Phase2BackupError("invalid_original_service_set")
        report["services_initially_running_count"] = len(original)
        parent.mkdir(mode=0o700, exist_ok=True)
        require_private_directory(parent)
        output.mkdir(mode=0o700)
        write_json_new(output / "operation.json", {
            "originally_running_services": sorted(original),
            "restored_copy_must_never_start": True,
        })
        # An unsuccessful stop can still have stopped one service. Restore the
        # original set even when stop() raises or is interrupted.
        stop_attempted = True
        services.stop()
        if services.running():
            raise Phase2BackupError("services_not_fully_stopped")
        # Runtime lock links may disappear during graceful shutdown. Any
        # remaining link or special file is rejected, never removed or followed.
        report["source_audit"] = {name: audit_tree(path) for name, path in sources.items()}
        reason = audit_reason(report["source_audit"])
        if reason:
            raise Phase2BackupError(reason)
        astrbot_archive, opaque_archive = output / "astrbot-state.zip", output / "napcat-opaque.zip"
        astrbot_manifest = backup.create_backup(sources["astrbot"], astrbot_archive)
        opaque_manifest = create_opaque_backup(
            {"config": sources["napcat_config"], "ntqq": sources["qq_login_state"]},
            opaque_archive,
        )
        report["backup_integrity"] = "PASS"
        restore = output / "isolated-restore"
        restore.mkdir(mode=0o700)
        restored_manifest = backup.restore_backup(astrbot_archive, restore / "astrbot-state")
        if restored_manifest != astrbot_manifest:
            raise Phase2BackupError("astrbot_manifest_mismatch")
        restore_opaque_backup(opaque_archive, restore / "napcat", opaque_manifest)
        protect_and_verify_restore(restore / "astrbot-state", astrbot_manifest)
        protect_and_verify_restore(restore / "napcat", opaque_manifest)
        report.update({
            "status": "PASS", "restore_integrity": "PASS",
            "astrbot_file_count": len(astrbot_manifest["files"]),
            "opaque_file_count": len(opaque_manifest["files"]),
            "sqlite_integrity_checked_count": len(astrbot_manifest["sqlite_integrity_checked"]),
            "opaque_sqlite_integrity": "NOT APPLICABLE",
            "astrbot_archive_sha256": backup._hash(astrbot_archive),
            "opaque_archive_sha256": backup._hash(opaque_archive),
        })
    except BaseException as error:
        report["status"] = "FAIL" if stop_attempted else "BLOCKED"
        report["reason"] = safe_failure(error)
    finally:
        if stop_attempted:
            try:
                if original:
                    services.start(original)
                restored = services.running()
                report["services_restored_running_count"] = len(restored)
                report["service_state_restored"] = restored == original
                if restored != original:
                    raise Phase2BackupError("service_state_mismatch_after_restore")
            except BaseException:
                report["status"] = "FAIL"
                report["service_state_restored"] = False
                report["service_restore_error"] = "original_service_state_not_restored"
    # Never write into an already-existing run on a failed preflight.
    if stop_attempted and output is not None:
        try:
            write_json_new(output / "result.json", report)
        except BaseException:
            report["status"] = "FAIL"
            report["report_save_error"] = "private_report_not_saved"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment", type=Path, default=DEFAULT_DEPLOYMENT)
    parser.add_argument("--run-id", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)

    def interrupted(_number, _frame):
        raise InterruptedError()

    signal.signal(signal.SIGTERM, interrupted)
    report = exercise(args.deployment, args.run_id, apply=args.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] in {"PASS", "NOT TESTED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
