"""Offline rejection tests for the deployment build wrapper."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/phase2-build-images.sh"
CODE = SCRIPT.read_text().split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
MODULE = {"__name__": "phase2_build_images"}
exec(compile(CODE, str(SCRIPT), "exec"), MODULE)


class BuildIntegrityTests(unittest.TestCase):
    def fixture(self):
        expected = MODULE["BASES"]["astrbot"]
        manifest = expected["reference"].split("@")[1]
        return expected, {
            "image_id": manifest, "descriptor_digest": manifest,
            "repo_digests": ["docker.io/" + expected["reference"]],
            "os": "linux", "architecture": "amd64",
            "rootfs_diff_ids": ["sha256:" + "a" * 64],
        }

    def test_accepts_containerd_manifest_and_classic_config_ids(self):
        expected, image = self.fixture()
        MODULE["validate_base"](image, expected)
        image["image_id"] = expected["config_id"]
        image["descriptor_digest"] = None
        MODULE["validate_base"](image, expected)

    def test_mutable_tag_without_pinned_repo_digest_is_rejected(self):
        expected, image = self.fixture()
        image["repo_digests"] = []
        with self.assertRaisesRegex(ValueError, "RepoDigest"):
            MODULE["validate_base"](image, expected)

    def test_wrong_manifest_id_and_platform_are_rejected(self):
        expected, image = self.fixture()
        image["image_id"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(ValueError, "image ID"):
            MODULE["validate_base"](image, expected)
        _, image = self.fixture()
        image["architecture"] = "arm64"
        with self.assertRaisesRegex(ValueError, "platform"):
            MODULE["validate_base"](image, expected)

    def test_output_must_retain_parent_layers(self):
        _, base = self.fixture()
        good = {**base, "rootfs_diff_ids": base["rootfs_diff_ids"] + ["sha256:" + "b" * 64]}
        MODULE["validate_derived"](good, base)
        with self.assertRaisesRegex(ValueError, "layer chain"):
            MODULE["validate_derived"]({**good, "rootfs_diff_ids": ["sha256:" + "b" * 64]}, base)

    def test_local_alias_checks_identity_even_when_repo_digest_names_change(self):
        _, image = self.fixture()
        MODULE["validate_local_tag"]({**image, "repo_digests": []}, image)
        with self.assertRaisesRegex(ValueError, "Local base tag"):
            MODULE["validate_local_tag"]({**image, "image_id": "sha256:" + "b" * 64}, image)

    def test_napcat_rewrite_accepts_only_locked_from(self):
        reference = MODULE["BASES"]["napcat"]["reference"]
        changed = MODULE["rewrite_dockerfile"]("FROM " + reference + "\nRUN true\n", "napcat", "verified:run")
        self.assertEqual(changed, "FROM verified:run\nRUN true\n")
        with self.assertRaisesRegex(ValueError, "FROM changed"):
            MODULE["rewrite_dockerfile"]("FROM mlikiowa/napcat-docker:latest\n", "napcat", "verified:run")

    def test_external_frontend_download_is_rejected(self):
        reference = MODULE["BASES"]["napcat"]["reference"]
        with self.assertRaisesRegex(ValueError, "frontend"):
            MODULE["rewrite_dockerfile"]("# syntax=docker/dockerfile:1\nFROM " + reference, "napcat", "verified:run")

    def test_wheelhouse_accepts_source_archives_but_excludes_unrelated_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wanted = ["demo.whl", "lunarcalendar.tar.gz", "source.zip", "source.tar.bz2"]
            for name in wanted + ["manifest.json", "notes.txt", "script.py", "other.tar"]:
                (root / name).write_bytes(b"fixture")
            self.assertEqual(
                [path.name for path in MODULE["distribution_files"](root)], sorted(wanted),
            )
            (root / "linked.whl").symlink_to(root / "demo.whl")
            with self.assertRaisesRegex(ValueError, "regular files"):
                MODULE["distribution_files"](root)

    def test_audit_does_not_shadow_stdlib_inspect_or_load_runtime_sitecustomize(self):
        report = '{"python_3_12":true,"source_comparison":{"status":"PASS"}}'
        with patch.dict(MODULE, {"command": lambda args, **kwargs: self.audit_command(args, report)}):
            MODULE["run_astrbot_inspection"]("sha256:" + "a" * 64, Path("/audit.py"), Path("/locks"), Path("/source"))

    def audit_command(self, args, report):
        self.assertIn("PYTHONPATH=", args)
        self.assertIn("/phase2_image_audit.py", args)
        self.assertNotIn("/inspect.py", args)
        self.assertTrue(any("dst=/phase2_image_audit.py," in item for item in args))
        self.assertFalse(any("dst=/inspect.py," in item for item in args))
        return SimpleNamespace(stdout=report)


if __name__ == "__main__":
    unittest.main()
