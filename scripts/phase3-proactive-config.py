#!/usr/bin/env python3
"""Save continuous proactive policy in a new protected candidate, never activate.

Optionally inherit a verified, unapplied runtime candidate (e.g. compression)
whose baseline still matches live configuration. No QQ/model/database access.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from integrations.phase3_runtime.settings import continuous_proactive_candidate
from integrations.phase3_runtime.proactive import native_proactive_config

spec = importlib.util.spec_from_file_location("proactive_private_config_helpers", Path(__file__).with_name("phase3-context-config.py"))
private = importlib.util.module_from_spec(spec)
spec.loader.exec_module(private)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def save_candidate(runtime_source, private_source, identity_source, output, *, pending_manifest=None):
    paths = [Path(runtime_source), Path(private_source), Path(identity_source)]
    output = Path(output)
    destination = output.resolve()
    if output.exists() or output.is_symlink():
        raise FileExistsError("new_output_directory_required")
    for source in paths:
        boundary = source.parent.parent if source.parent.name == "config" else source.parent
        if destination == boundary.resolve() or boundary.resolve() in destination.parents:
            raise ValueError("candidate_must_be_outside_live_data")
    if any((parent / ".git").is_file() or (parent / ".git/HEAD").is_file()
           for parent in (destination, *destination.parents)):
        raise ValueError("private_candidate_must_be_outside_git_worktree")
    original = [private._read_private(path) for path in paths]
    runtime, native, identity = [json.loads(raw.decode("utf-8-sig")) for raw in original]
    if runtime.get("proactive", False) is not False:
        raise ValueError("source_must_remain_disabled")
    inherited = None
    if pending_manifest is not None:
        pending_manifest = Path(pending_manifest)
        inherited_raw = private._read_private(pending_manifest)
        inherited = json.loads(inherited_raw.decode("utf-8-sig"))
        candidate_raw = private._read_private(pending_manifest.parent / "candidate-phase3_runtime_config.json")
        if (inherited.get("status") != "SAVED_NOT_APPLIED"
            or inherited.get("production_applied") is not False
            or inherited.get("source_runtime_config_sha256") != digest(original[0])
            or inherited.get("candidate_runtime_config_sha256") != digest(candidate_raw)):
            raise ValueError("pending_runtime_candidate_stale_or_unverified")
        runtime = json.loads(candidate_raw.decode("utf-8-sig"))
    candidate = continuous_proactive_candidate(runtime)
    native_candidate = native_proactive_config(native, str(identity["admin_id"]), enabled=False,
                                               policy=candidate["proactive_window"])
    encoded = private._encode_config(candidate, original[0])
    native_encoded = private._encode_config(native_candidate, original[1])
    def unchanged():
        return all(private._read_private(path) == before for path, before in zip(paths, original))
    if not unchanged():
        raise ValueError("source_changed_during_preparation")
    output.mkdir(mode=0o700)
    for name, data in (("baseline-phase3_runtime_config.json", original[0]),
                       ("candidate-phase3_runtime_config.json", encoded),
                       ("baseline-private_companion_config.json", original[1]),
                       ("candidate-private_companion_config.json", native_encoded)):
        private._write_private(output / name, data)
    manifest = {
        "status": "SAVED_NOT_APPLIED", "created_at": datetime.now(timezone.utc).isoformat(),
        "source_runtime_config_sha256": digest(original[0]),
        "candidate_runtime_config_sha256": digest(encoded),
        "source_private_config_sha256": digest(original[1]),
        "candidate_private_config_sha256": digest(native_encoded),
        "inherited_pending_manifest": str(pending_manifest) if inherited else None,
        "inherited_pending_manifest_sha256": digest(inherited_raw) if inherited else None,
        "preserved_context_compression_fallback_turns": candidate.get("context_compression_fallback_turns", 0),
        "source_unchanged": unchanged(), "production_applied": False, "service_restarted": False,
        "proactive_enabled": False, "native_capability_changed": False, "model_calls": 0, "qq_messages": 0,
        "policy": candidate["proactive_window"], "timezone": "Asia/Shanghai",
        "allowed_hours": "09:00:00–23:59:59", "scheduled_expiry": None,
        "native_cold_start": {"generation_enabled": False, "max_daily_messages": 0},
        "activation": "Wait for coordinated activation. Merge reviewed fields into current configs; do not copy stale full configurations. Keep earlier compression candidate and combine its cmd_config changes when approved. Back up before installing matching runtime sources and migrating only the verified target with daily_limit=5. Runtime and Guard stay off until explicit activation.",
    }
    if not manifest["source_unchanged"]:
        manifest["status"] = "STALE_DO_NOT_APPLY"
    private._write_private(output / "manifest.json", (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode())
    if not manifest["source_unchanged"]:
        raise ValueError("source_changed_during_preparation")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-config", type=Path, required=True)
    parser.add_argument("--private-config", type=Path, required=True)
    parser.add_argument("--identity-config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--pending-manifest", type=Path)
    args = parser.parse_args()
    print(json.dumps(save_candidate(args.runtime_config, args.private_config, args.identity_config,
                                    args.output_dir, pending_manifest=args.pending_manifest), ensure_ascii=False))
