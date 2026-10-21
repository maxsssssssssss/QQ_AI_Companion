#!/usr/bin/env python3
"""Fetch pinned public images without a Docker daemon and verify offline archives.

Requires skopeo (an existing binary or an independently verified temporary
installation). No login or user credential file is used. This script never
starts containers. OCI source digests, config IDs and layer diffIDs are
separate values: a docker-archive import need not preserve RepoDigests.
"""
import argparse
import concurrent.futures
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


IMAGES = {
    "astrbot": {
        "repository": "soulter/astrbot",
        "digest": "sha256:0c3b8ef7485dbd15176637133c23dfbdb80675d174545a997648b9c1db7d53c1",
        "tag": "docker.io/qq-companion/astrbot-base:v4.29.1-0c3b8ef7485d",
        "oci_tag": "docker.io/soulter/astrbot:v4.29.1",
        "cache": "astrbot-oci",
    },
    "napcat-base": {
        "repository": "mlikiowa/napcat-docker",
        "digest": "sha256:132448a544ed5cfa948dff8ae19e80bf4e17896e8ab2ba1c6cbcd86f8ad3e56e",
        "tag": "docker.io/qq-companion/napcat-base:v4.18.37-132448a544ed",
        "oci_tag": "docker.io/mlikiowa/napcat-docker:v4.18.37",
        "cache": "napcat-base-oci",
    },
    "python": {
        "repository": "library/python",
        "digest": "sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258",
        "tag": "docker.io/qq-companion/python-base:3.12-34386ef0cb08",
        "oci_tag": "docker.io/library/python:3.12-slim-bookworm",
        "cache": "python-oci",
    },
}


def stream_hash(stream):
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def file_hash(path):
    with Path(path).open("rb") as stream:
        return stream_hash(stream)


def verified_blob(layout, descriptor):
    digest = descriptor["digest"]
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("Unsupported or invalid blob digest")
    path = Path(layout) / "blobs" / "sha256" / digest[7:]
    if path.stat().st_size != descriptor["size"] or file_hash(path) != digest:
        raise ValueError("Blob size or SHA256 mismatch: " + digest)
    return path


def select_manifest(source, source_digest):
    if source.get("schemaVersion") != 2:
        raise ValueError("Only image schema version 2 is supported")
    if "manifests" not in source:
        return source_digest
    matches = [
        item for item in source["manifests"]
        if item.get("platform", {}).get("os") == "linux"
        and item.get("platform", {}).get("architecture") == "amd64"
    ]
    if len(matches) != 1:
        raise ValueError("Source index must identify exactly one linux/amd64 image")
    return matches[0]["digest"]


def verify_oci(layout, manifest_digest):
    layout = Path(layout)
    index = json.loads((layout / "index.json").read_text())
    matches = [d for d in index["manifests"] if d["digest"] == manifest_digest]
    if len(matches) != 1:
        raise ValueError("Cached OCI index does not contain the pinned manifest")
    manifest = json.loads(verified_blob(layout, matches[0]).read_bytes())
    config_path = verified_blob(layout, manifest["config"])
    config = json.loads(config_path.read_bytes())
    if (config.get("os"), config.get("architecture")) != ("linux", "amd64"):
        raise ValueError("Cached image is not linux/amd64")
    diff_ids = config["rootfs"]["diff_ids"]
    if len(diff_ids) != len(manifest["layers"]):
        raise ValueError("Config diffID count differs from manifest layer count")
    for layer, expected_diff_id in zip(manifest["layers"], diff_ids):
        path = verified_blob(layout, layer)
        media_type = layer["mediaType"]
        if media_type.endswith("+gzip") or media_type.endswith(".gzip"):
            opener = gzip.open
        elif media_type.endswith(".tar"):
            opener = open
        else:
            raise ValueError("Unsupported layer compression: " + media_type)
        with opener(path, "rb") as stream:
            actual = stream_hash(stream)
        if actual != expected_diff_id:
            raise ValueError("Uncompressed layer diffID mismatch")
    return {
        "manifest_digest": manifest_digest,
        "config_id": manifest["config"]["digest"],
        "diff_ids": diff_ids,
        "compressed_bytes": sum(d["size"] for d in manifest["layers"]),
        "platform": "linux/amd64",
    }


def verify_docker_archive(path, expected, tag):
    with tarfile.open(path, "r:*") as archive:
        manifests = json.load(archive.extractfile("manifest.json"))
        if len(manifests) != 1 or manifests[0].get("RepoTags") != [tag]:
            raise ValueError("Unexpected docker-archive image tags")
        manifest = manifests[0]
        config_stream = archive.extractfile(manifest["Config"])
        if config_stream is None or stream_hash(config_stream) != expected["config_id"]:
            raise ValueError("docker-archive config ID mismatch")
        if len(manifest["Layers"]) != len(expected["diff_ids"]):
            raise ValueError("docker-archive layer count mismatch")
        for name, expected_diff_id in zip(manifest["Layers"], expected["diff_ids"]):
            stream = archive.extractfile(name)
            if stream is None or stream_hash(stream) != expected_diff_id:
                raise ValueError("docker-archive layer diffID mismatch")


def write_oci_archive(layout, path, tag, source_index=None):
    """Preserve verified blobs; set archive-local names without changing blobs.

    With source_index, produce a sparse original-index archive whose only
    available platform is amd64. Importers must select linux/amd64 explicitly.
    """
    layout = Path(layout)
    top = json.loads((layout / "index.json").read_text())
    extra = {}
    if source_index is not None:
        document = json.loads(source_index)
        digest = "sha256:" + hashlib.sha256(source_index).hexdigest()
        top["manifests"] = [{
            "mediaType": document["mediaType"], "digest": digest,
            "size": len(source_index),
        }]
        extra["blobs/sha256/" + digest[7:]] = source_index
    if len(top["manifests"]) != 1:
        raise ValueError("Expected one archive root descriptor")
    top["manifests"][0]["annotations"] = {
        "org.opencontainers.image.ref.name": tag,
        "io.containerd.image.name": tag,
    }
    extra["index.json"] = json.dumps(top, separators=(",", ":")).encode()
    with tarfile.open(path, "w") as archive:
        sources = [layout / "oci-layout"]
        sources.extend(sorted((layout / "blobs" / "sha256").glob("*")))
        for source in sources:
            name = str(source.relative_to(layout))
            if name != "oci-layout" and not re.fullmatch(r"blobs/sha256/[0-9a-f]{64}", name):
                continue
            if source.is_symlink():
                raise ValueError("OCI archive inputs must not be symbolic links")
            if source.is_file() and name not in extra:
                archive.add(source, arcname=name, recursive=False)
        for name, data in extra.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))


def complete_index_metadata(layout, repository, source):
    """Cache every manifest/config and small attestation blob, never other OS layers."""
    token = None
    token_lock = threading.Lock()
    layout = Path(layout)

    def authorization():
        nonlocal token
        with token_lock:
            if token is None:
                query = urllib.parse.urlencode({
                    "service": "registry.docker.io",
                    "scope": "repository:" + repository + ":pull",
                })
                with urllib.request.urlopen("https://auth.docker.io/token?" + query, timeout=30) as response:
                    token = json.load(response)["token"]
            return "Bearer " + token

    def fetch(descriptor, kind):
        if descriptor["size"] > 16 * 1024 * 1024:
            raise ValueError("Unexpected metadata larger than 16 MiB")
        try:
            return verified_blob(layout, descriptor).read_bytes()
        except (FileNotFoundError, ValueError):
            pass
        digest = descriptor["digest"]
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError("Invalid metadata digest")
        for attempt in range(3):
            try:
                request = urllib.request.Request(
                    "https://registry-1.docker.io/v2/" + repository + "/" + kind + "/" + digest,
                    headers={
                        "Authorization": authorization(),
                        "Accept": "application/vnd.oci.image.manifest.v1+json,application/vnd.docker.distribution.manifest.v2+json",
                    },
                )
                with urllib.request.urlopen(request, timeout=30) as response:
                    data = response.read(16 * 1024 * 1024 + 1)
                if len(data) != descriptor["size"] or "sha256:" + hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError("Metadata SHA256 or size mismatch")
                (layout / "blobs" / "sha256" / digest[7:]).write_bytes(data)
                return data
            except OSError:
                if attempt == 2:
                    raise RuntimeError("Public registry metadata request failed after three attempts") from None
                time.sleep(attempt + 1)

    def complete_manifest(descriptor):
        manifest = json.loads(fetch(descriptor, "manifests"))
        wanted = [manifest["config"]]
        if descriptor.get("platform", {}).get("architecture") == "unknown":
            wanted.extend(manifest.get("layers", []))
        else:
            wanted.extend(
                item for item in manifest.get("layers", [])
                if "image.layer." not in item["mediaType"] and "rootfs.diff." not in item["mediaType"]
            )
        for item in wanted:
            fetch(item, "blobs")
        return descriptor["size"] + sum(item["size"] for item in wanted)

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        sizes = list(pool.map(complete_manifest, source["manifests"]))
    return {"manifest_count": len(sizes), "metadata_bytes": sum(sizes), "other_platform_rootfs_layers": "NOT_DOWNLOADED"}


def run_skopeo(args, *command, capture=False):
    argv = [args.skopeo]
    if args.policy:
        argv.extend(["--policy", args.policy])
    temporary = Path(args.output).resolve() / "temporary"
    temporary.mkdir(parents=True, exist_ok=True)
    argv.extend(["--tmpdir", str(temporary)])
    argv.extend(command)
    return subprocess.run(
        argv, check=True, stdout=subprocess.PIPE if capture else None,
        env={**os.environ, "TMPDIR": str(temporary)},
    ).stdout


def prepare_image(args, name):
    spec = IMAGES[name]
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    source_path = output / (name + "-source.json")
    if args.reuse_cache and source_path.exists():
        raw = source_path.read_bytes()
    else:
        raw = run_skopeo(
            args, "inspect", "--raw", "--no-creds", "--tls-verify=true",
            "docker://docker.io/" + spec["repository"] + "@" + spec["digest"],
            capture=True,
        )
    if "sha256:" + hashlib.sha256(raw).hexdigest() != spec["digest"]:
        raise ValueError("Original registry manifest/index digest mismatch")
    source_path.write_bytes(raw)
    manifest_digest = select_manifest(json.loads(raw), spec["digest"])
    layout = output / spec["cache"]
    if not (args.reuse_cache and (layout / "index.json").exists()):
        run_skopeo(
            args, "copy", "--src-no-creds", "--src-tls-verify=true",
            "--retry-times", "3", "--preserve-digests",
            "docker://docker.io/" + spec["repository"] + "@" + manifest_digest,
            "oci:" + str(layout) + ":verified-amd64",
        )
    verified = verify_oci(layout, manifest_digest)
    tar_path = output / (name + ".docker.tar")
    tar_temporary = output / (name + "." + uuid.uuid4().hex + ".docker.tar")
    run_skopeo(
        args, "copy", "oci:" + str(layout),
        "docker-archive:" + str(tar_temporary) + ":" + spec["tag"],
    )
    verify_docker_archive(tar_temporary, verified, spec["tag"])
    tar_temporary.replace(tar_path)
    gzip_path = output / (name + ".docker.tar.gz")
    partial = gzip_path.with_suffix(gzip_path.suffix + ".partial")
    with tar_path.open("rb") as src, partial.open("wb") as dst:
        with gzip.GzipFile(filename="", mode="wb", fileobj=dst, mtime=0, compresslevel=3) as packed:
            shutil.copyfileobj(src, packed, 1024 * 1024)
    partial.replace(gzip_path)
    verify_docker_archive(gzip_path, verified, spec["tag"])
    oci_path = output / (name + ".oci.tar")
    write_oci_archive(layout, oci_path, spec["oci_tag"])
    record = {
        "source": "docker.io/" + spec["repository"] + "@" + spec["digest"],
        "source_digest": spec["digest"],
        "source_document": str(source_path),
        "tag": spec["tag"],
        **verified,
        "docker_archive": str(gzip_path),
        "docker_archive_sha256": file_hash(gzip_path),
        "docker_archive_bytes": gzip_path.stat().st_size,
        "oci_archive": str(oci_path),
        "oci_archive_sha256": file_hash(oci_path),
        "oci_archive_tag": spec["oci_tag"],
        "repo_digests_after_docker_load": "MUST_VERIFY_ON_TARGET",
        "note": "OCI archive contains the selected amd64 manifest; original multi-platform index is preserved separately as source evidence.",
    }
    if "manifests" in json.loads(raw):
        record["index_metadata"] = complete_index_metadata(layout, spec["repository"], json.loads(raw))
        sparse = output / (name + ".source-index.oci.tar")
        upstream_tag = "docker.io/library/python:3.12-slim-bookworm" if name == "python" else spec["tag"]
        write_oci_archive(layout, sparse, upstream_tag, raw)
        record.update({
            "sparse_source_index_archive": str(sparse),
            "sparse_source_index_sha256": file_hash(sparse),
            "sparse_import_requirement": "linux/amd64 only; original index digest retained; target importer behavior MUST BE VERIFIED",
            "sparse_archive_tag": upstream_tag,
        })
    (output / (name + "-provenance.json")).write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(record), flush=True)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skopeo", default="skopeo")
    parser.add_argument("--policy", help="Existing skopeo signature policy path")
    parser.add_argument("--output", required=True, help="Ignored or temporary artifact directory")
    parser.add_argument("--image", choices=IMAGES, action="append")
    parser.add_argument("--reuse-cache", action="store_true")
    args = parser.parse_args()
    for name in args.image or IMAGES:
        prepare_image(args, name)


if __name__ == "__main__":
    main()
