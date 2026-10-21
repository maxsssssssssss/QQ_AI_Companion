"""Offline integrity tests for the Phase 2 transport verifier."""
import gzip
import hashlib
import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "phase2-fetch-images.py"
SPEC = importlib.util.spec_from_file_location("phase2_images", MODULE_PATH)
images = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(images)


def digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "blobs" / "sha256").mkdir(parents=True)

    def blob(self, data, media_type):
        value = digest(data)
        (self.root / "blobs" / "sha256" / value[7:]).write_bytes(data)
        return {"digest": value, "size": len(data), "mediaType": media_type}

    def fixture(self, *, wrong_diff_id=False):
        layer = b"synthetic tar bytes, never extracted"
        packed = self.blob(gzip.compress(layer), "application/vnd.oci.image.layer.v1.tar+gzip")
        config = json.dumps({
            "os": "linux", "architecture": "amd64",
            "rootfs": {"type": "layers", "diff_ids": [digest(b"wrong" if wrong_diff_id else layer)]},
        }).encode()
        cfg = self.blob(config, "application/vnd.oci.image.config.v1+json")
        manifest = json.dumps({"schemaVersion": 2, "config": cfg, "layers": [packed]}).encode()
        descriptor = self.blob(manifest, "application/vnd.oci.image.manifest.v1+json")
        (self.root / "index.json").write_text(json.dumps({"schemaVersion": 2, "manifests": [descriptor]}))
        return descriptor["digest"], packed, config, layer

    def test_valid_oci_and_docker_archive(self):
        manifest_digest, _, config, layer = self.fixture()
        verified = images.verify_oci(self.root, manifest_digest)
        target = self.root / "image.tar.gz"
        tag = "local/fixture:pinned"
        members = {
            "config.json": config,
            "layer.tar": layer,
            "manifest.json": json.dumps([{"Config": "config.json", "RepoTags": [tag], "Layers": ["layer.tar"]}]).encode(),
        }
        with tarfile.open(target, "w:gz") as archive:
            for name, data in members.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        images.verify_docker_archive(target, verified, tag)
        with self.assertRaisesRegex(ValueError, "tags"):
            images.verify_docker_archive(target, verified, "different:tag")
        with self.assertRaisesRegex(ValueError, "config ID"):
            images.verify_docker_archive(target, {**verified, "config_id": digest(b"replacement")}, tag)

    def test_modified_compressed_blob_rejected(self):
        manifest_digest, layer, _, _ = self.fixture()
        path = self.root / "blobs" / "sha256" / layer["digest"][7:]
        path.write_bytes(path.read_bytes()[:-1] + b"x")
        with self.assertRaisesRegex(ValueError, "SHA256"):
            images.verify_oci(self.root, manifest_digest)

    def test_inconsistent_diff_id_rejected(self):
        manifest_digest, _, _, _ = self.fixture(wrong_diff_id=True)
        with self.assertRaisesRegex(ValueError, "diffID"):
            images.verify_oci(self.root, manifest_digest)

    def test_unsafe_blob_digest_rejected(self):
        with self.assertRaisesRegex(ValueError, "digest"):
            images.verified_blob(self.root, {"digest": "../../private", "size": 0})

    def test_index_requires_unique_platform(self):
        item = {"digest": "sha256:" + "0" * 64, "platform": {"os": "linux", "architecture": "amd64"}}
        self.assertEqual(images.select_manifest({"schemaVersion": 2, "manifests": [item]}, "unused"), item["digest"])
        with self.assertRaisesRegex(ValueError, "exactly one"):
            images.select_manifest({"schemaVersion": 2, "manifests": [item, item]}, "unused")

    def test_sparse_archive_preserves_original_index_bytes(self):
        manifest_digest, _, _, _ = self.fixture()
        source = json.dumps({
            "schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [{"digest": manifest_digest, "platform": {"os": "linux", "architecture": "amd64"}}],
        }).encode()
        target = self.root / "sparse.tar"
        tag = "docker.io/library/fixture:fixed"
        images.write_oci_archive(self.root, target, tag, source)
        with tarfile.open(target) as archive:
            top = json.load(archive.extractfile("index.json"))
            self.assertEqual(top["manifests"][0]["digest"], digest(source))
            self.assertEqual(top["manifests"][0]["annotations"]["io.containerd.image.name"], tag)
            self.assertEqual(archive.extractfile("blobs/sha256/" + digest(source)[7:]).read(), source)

    def test_complete_cached_metadata_needs_no_network(self):
        self.fixture()
        index = json.loads((self.root / "index.json").read_text())
        index["manifests"][0]["platform"] = {"os": "linux", "architecture": "amd64"}
        with patch.object(images.urllib.request, "urlopen") as request:
            summary = images.complete_index_metadata(self.root, "library/fixture", index)
        request.assert_not_called()
        self.assertEqual(summary["manifest_count"], 1)
        self.assertEqual(summary["other_platform_rootfs_layers"], "NOT_DOWNLOADED")

    def test_archive_excludes_interrupted_download_and_unrelated_files(self):
        self.fixture()
        (self.root / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')
        (self.root / "oci-put-blob123").write_bytes(b"incomplete public blob")
        (self.root / "unrelated.txt").write_bytes(b"not image content")
        (self.root / "blobs" / "sha256" / "partial").write_bytes(b"unfinished")
        target = self.root / "image.tar"
        images.write_oci_archive(self.root, target, "fixture:pinned")
        with tarfile.open(target) as archive:
            names = archive.getnames()
            self.assertIn("oci-layout", names)
            self.assertIn("index.json", names)
            self.assertEqual(len(names), 5)
            self.assertNotIn("oci-put-blob123", names)
            self.assertNotIn("unrelated.txt", names)
            self.assertNotIn("blobs/sha256/partial", names)
            self.assertNotIn("image.tar", names)


if __name__ == "__main__":
    unittest.main()
