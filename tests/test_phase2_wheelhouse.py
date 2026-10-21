"""Tests of offline target selection and download boundary validation."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "phase2_wheelhouse", Path(__file__).resolve().parents[1] / "scripts/phase2-fetch-wheelhouse.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class WheelhouseTests(unittest.TestCase):
    def setUp(self):
        self.tags = MODULE.target_tags()

    def test_fixed_target_rejects_windows_arm_and_newer_glibc(self):
        for platform in ("win_amd64", "manylinux_2_17_aarch64", "manylinux_2_39_x86_64"):
            self.assertIsNone(MODULE.artifact_rank(f"demo-1.0-cp312-cp312-{platform}.whl", "demo", "1.0", self.tags))
        self.assertIsNotNone(MODULE.artifact_rank("demo-1.0-cp311-abi3-manylinux_2_34_x86_64.whl", "demo", "1.0", self.tags))
        self.assertIsNotNone(MODULE.artifact_rank("demo-1.0-py3-none-any.whl", "demo", "1.0", self.tags))

    def test_source_archive_fallback_and_path_rejection(self):
        for suffix in (".tar.gz", ".zip", ".tar.bz2"):
            self.assertEqual(MODULE.artifact_rank("demo-1.0" + suffix, "demo", "1.0", self.tags), 1_000_000)
        with self.assertRaisesRegex(ValueError, "path"):
            MODULE.artifact_rank("../demo-1.0.tar.gz", "demo", "1.0", self.tags)

    def test_only_official_https_without_credentials(self):
        for url in ("http://pypi.org/simple", "https://evil.test/file", "https://user:pass@pypi.org/simple"):
            with self.assertRaisesRegex(ValueError, "HTTPS"):
                MODULE.approved_url(url, "pypi.org")
        self.assertEqual(MODULE.approved_url("https://pypi.org/pypi/demo/1/json", "pypi.org"), "https://pypi.org/pypi/demo/1/json")

    def test_windows_curl_paths_do_not_mix_wsl_paths(self):
        with patch.object(MODULE.os, "name", "posix"):
            self.assertEqual(MODULE.curl_output_path(Path("/mnt/d/Downloads/my wheels/demo.whl"), True), "D:/Downloads/my wheels/demo.whl")
            with self.assertRaisesRegex(ValueError, "/mnt"):
                MODULE.curl_output_path(Path("/tmp/demo.whl"), True)

    def test_tampered_cache_rejected_against_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            filename = "demo-1.0.tar.gz"
            (root / filename).write_bytes(b"expected archive")
            sha = MODULE.file_sha256(root / filename)
            locked = {"demo": ("1.0", {sha})}
            record = {"name": "demo", "version": "1.0", "file": filename, "sha256": sha, "source": "https://files.pythonhosted.org/packages/demo.tar.gz"}
            MODULE.validate_record(record, locked, root, self.tags)
            (root / filename).write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "SHA256"):
                MODULE.validate_record(record, locked, root, self.tags)


if __name__ == "__main__":
    unittest.main()
