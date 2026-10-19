#!/usr/bin/env python3
"""Offline state backup using only the Python standard library.

The caller MUST stop AstrBot and every plugin/process that writes source_state
before create_backup(). The helper cannot prove that writers have stopped.
Pass the whole state directory, including data, configuration and plugin data.
Archives are never overwritten. Restore requires a nonexistent destination.
SHA-256 detects corruption; it does not authenticate an archive's publisher.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import tempfile
import zipfile


class BackupError(ValueError):
    """The snapshot is unsafe, inconsistent, corrupt, or cannot be restored."""


FORMAT = "qq-ai-companion-offline-state"
SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".db3"}
MANIFEST_LIMIT = 16 * 1024 * 1024


def _safe_path(name: str) -> str:
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise BackupError("Invalid archive path")
    path = PurePosixPath(name)
    if path.is_absolute() or path.as_posix() != name:
        raise BackupError(f"Noncanonical archive path: {name!r}")
    if any(part in {".", ".."} or ":" in part for part in path.parts):
        raise BackupError(f"Unsafe archive path: {name!r}")
    if not path.parts:
        raise BackupError("Empty archive path")
    return name


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree(root: Path) -> tuple[dict, dict]:
    files, directories = {}, {}
    for directory, child_dirs, child_files in os.walk(root, followlinks=False):
        for child in sorted(child_dirs + child_files):
            path = Path(directory) / child
            relative = _safe_path(path.relative_to(root).as_posix())
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise BackupError(f"Links are not supported: {relative}")
            if stat.S_ISDIR(info.st_mode):
                directories[relative] = stat.S_IMODE(info.st_mode) & 0o777
            elif stat.S_ISREG(info.st_mode):
                if info.st_nlink != 1:
                    raise BackupError(f"Hard links are not supported: {relative}")
                files[relative] = {
                    "sha256": _hash(path),
                    "size": info.st_size,
                    "mode": stat.S_IMODE(info.st_mode) & 0o777,
                }
            else:
                raise BackupError(f"Special files are not supported: {relative}")
    return files, directories


def _check_sqlite(root: Path, files: dict) -> list[str]:
    checked = []
    for relative in sorted(files):
        path = root / relative
        with path.open("rb") as stream:
            sqlite_header = stream.read(16) == b"SQLite format 3\x00"
        if path.suffix.lower() not in SQLITE_SUFFIXES and not sqlite_header:
            continue
        try:
            # SQLite readers can update/create WAL shared-memory files even
            # with mode=ro. Check a private copy, keeping the snapshot exact.
            with tempfile.TemporaryDirectory(prefix=".qq-sqlite-check-", dir=root.parent) as temp:
                database = Path(temp) / path.name
                shutil.copy2(path, database)
                for suffix in ("-wal", "-shm", "-journal"):
                    if relative + suffix in files:
                        shutil.copy2(root / (relative + suffix), Path(str(database) + suffix))
                with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
                    result = db.execute("PRAGMA integrity_check").fetchall()
            if result != [("ok",)]:
                raise BackupError(f"SQLite integrity check failed: {relative}")
        except sqlite3.DatabaseError as exc:
            raise BackupError(f"SQLite integrity check failed: {relative}: {exc}") from exc
        checked.append(relative)
    return checked


def create_backup(source_state: str | Path, archive_path: str | Path) -> dict:
    """Snapshot a stopped application's entire state; return its manifest.

    Requires all state writers stopped. The destination archive must not exist
    and must be outside source_state. Source symlinks and hard links are rejected.
    """
    source = Path(source_state).absolute()
    archive = Path(archive_path).absolute()
    if source.is_symlink() or not source.is_dir():
        raise BackupError("Source must be a real state directory")
    source = source.resolve()
    if archive.resolve().is_relative_to(source):
        raise BackupError("Archive must be outside the source state directory")
    if os.path.lexists(archive):
        raise BackupError("Archive already exists; refusing to overwrite it")
    archive.parent.mkdir(parents=True, exist_ok=True)
    before_files, before_dirs = _tree(source)
    with tempfile.TemporaryDirectory(prefix=".qq-backup-", dir=archive.parent) as temp:
        stage = Path(temp) / "state"
        shutil.copytree(source, stage, symlinks=True)
        copied_files, copied_dirs = _tree(stage)
        after_files, after_dirs = _tree(source)
        if (before_files, before_dirs) != (copied_files, copied_dirs) or (
            before_files, before_dirs
        ) != (after_files, after_dirs):
            raise BackupError("Source changed during snapshot; stop all state writers")
        databases = _check_sqlite(stage, copied_files)
        if _tree(stage) != (copied_files, copied_dirs):
            raise BackupError("SQLite validation changed the snapshot")
        manifest = {
            "format": FORMAT,
            "version": 1,
            "root_mode": stat.S_IMODE(source.stat().st_mode) & 0o777,
            "files": copied_files,
            "directories": copied_dirs,
            "sqlite_integrity_checked": databases,
        }
        pending = Path(temp) / "snapshot.zip"
        with zipfile.ZipFile(pending, "w", compression=zipfile.ZIP_DEFLATED) as output:
            output.writestr("manifest.json", json.dumps(manifest, sort_keys=True, indent=2))
            for relative in sorted(copied_files):
                output.write(stage / relative, "state/" + relative)
        os.chmod(pending, 0o600)
        # Hard-link publication is atomic and cannot replace an existing path.
        try:
            os.link(pending, archive)
        except FileExistsError as exc:
            raise BackupError("Archive already exists; refusing to overwrite it") from exc
    return manifest


def _read_manifest(archive: zipfile.ZipFile) -> dict:
    entries = archive.infolist()
    names = [entry.filename for entry in entries]
    if len(names) != len(set(names)):
        raise BackupError("Duplicate archive entries")
    for entry in entries:
        _safe_path(entry.filename)
        mode = entry.external_attr >> 16
        if entry.is_dir() or stat.S_IFMT(mode) not in {0, stat.S_IFREG}:
            raise BackupError(f"Archive links or special entries are forbidden: {entry.filename}")
        if entry.flag_bits & 1:
            raise BackupError("Encrypted archive entries are unsupported")
    if "manifest.json" not in names:
        raise BackupError("Missing manifest")
    if archive.getinfo("manifest.json").file_size > MANIFEST_LIMIT:
        raise BackupError("Manifest exceeds size limit")
    try:
        manifest = json.loads(archive.read("manifest.json"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise BackupError("Invalid manifest JSON") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT or manifest.get("version") != 1:
        raise BackupError("Unsupported manifest format")
    files, directories = manifest.get("files"), manifest.get("directories")
    if not isinstance(files, dict) or not isinstance(directories, dict):
        raise BackupError("Invalid manifest file/directory maps")
    for mode in [manifest.get("root_mode"), *directories.values()]:
        if type(mode) is not int or not 0 <= mode <= 0o777:
            raise BackupError("Invalid directory mode")
    for relative in list(files) + list(directories):
        _safe_path(relative)
        for parent in PurePosixPath(relative).parents:
            if str(parent) != "." and str(parent) not in directories:
                raise BackupError("Manifest omits a parent directory")
    if files.keys() & directories.keys():
        raise BackupError("Manifest file/directory paths collide")
    for relative, metadata in files.items():
        if not isinstance(metadata, dict):
            raise BackupError("Invalid file metadata")
        digest, size, mode = metadata.get("sha256"), metadata.get("size"), metadata.get("mode")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise BackupError("Invalid SHA-256 digest")
        if type(size) is not int or size < 0 or type(mode) is not int or not 0 <= mode <= 0o777:
            raise BackupError("Invalid file size or mode")
        if "state/" + relative not in names or archive.getinfo("state/" + relative).file_size != size:
            raise BackupError(f"File missing or size mismatch: {relative}")
    if set(names) != {"manifest.json", *("state/" + name for name in files)}:
        raise BackupError("Unlisted archive entries")
    return manifest


def restore_backup(archive_path: str | Path, fresh_destination: str | Path) -> dict:
    """Verify and restore into a new directory, never an existing directory.

    Path/link checks, hashes and SQLite integrity checks finish before creating
    the destination. Restore is offline; callers start AstrBot only after return.
    """
    archive_path = Path(archive_path).absolute()
    destination = Path(fresh_destination).absolute()
    if os.path.lexists(destination):
        raise BackupError("Restore destination already exists; refusing to overwrite it")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix=".qq-restore-", dir=destination.parent) as temp:
            stage = Path(temp) / "state"
            stage.mkdir(mode=0o700)
            with zipfile.ZipFile(archive_path) as archive:
                manifest = _read_manifest(archive)
                for relative in sorted(manifest["directories"], key=lambda p: len(PurePosixPath(p).parts)):
                    (stage / relative).mkdir(mode=0o700)
                for relative, metadata in manifest["files"].items():
                    output = stage / relative
                    with archive.open("state/" + relative) as incoming, output.open("xb") as outgoing:
                        shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
                    if output.stat().st_size != metadata["size"] or _hash(output) != metadata["sha256"]:
                        raise BackupError(f"Checksum mismatch: {relative}")
                checked = _check_sqlite(stage, manifest["files"])
                if manifest.get("sqlite_integrity_checked") != checked:
                    raise BackupError("Manifest SQLite integrity inventory does not match actual files")
                for relative, metadata in manifest["files"].items():
                    if _hash(stage / relative) != metadata["sha256"]:
                        raise BackupError(f"SQLite validation changed snapshot: {relative}")
            # Reserve the new destination without replacing even an empty
            # existing directory. No untrusted archive content is extracted here.
            try:
                destination.mkdir(mode=0o700)
            except FileExistsError as exc:
                raise BackupError("Restore destination already exists") from exc
            try:
                for child in stage.iterdir():
                    shutil.move(str(child), destination / child.name)
                for relative, metadata in manifest["files"].items():
                    os.chmod(destination / relative, metadata["mode"])
                for relative, mode in sorted(manifest["directories"].items(), key=lambda item: -len(PurePosixPath(item[0]).parts)):
                    os.chmod(destination / relative, mode)
                os.chmod(destination, manifest["root_mode"])
            except BaseException:
                shutil.rmtree(destination)
                raise
        return manifest
    except (zipfile.BadZipFile, EOFError, RuntimeError) as exc:
        raise BackupError(f"Invalid backup archive: {exc}") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["create", "restore"])
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    operation = create_backup if args.action == "create" else restore_backup
    try:
        manifest = operation(args.source, args.destination)
    except (BackupError, OSError) as exc:
        parser.exit(1, f"backup failed: {exc}\n")
    print(json.dumps({"action": args.action, "files": len(manifest["files"]),
                      "sqlite_integrity_checked": manifest["sqlite_integrity_checked"]}))


if __name__ == "__main__":
    main()
