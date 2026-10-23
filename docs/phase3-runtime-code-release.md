# Phase 3 AstrBot-only source release

This controller is a narrow alternative for the reviewed profile publication fix. It does not replace the Phase 2 full-backup procedure or declare real QQ acceptance. It changes only `phase3_runtime/main.py`, `profile_recall.py`, and the new `profile_publication.py` inside the existing AstrBot data/plugins directory. There is no database migration, configuration change, image pull, container creation, or NapCat stop/start command.

## Preconditions and manifest

Use `scripts/phase3-deploy-runtime-code.py` from a private, reviewed release directory. The default is read-only PLAN; only `--apply` changes the server. The controller requires:

- The original `phase3-20261023-experience-v2-r2/source/operation.json` receipt and its reviewed SHA-256. It must identify the original AstrBot/NapCat pair, `backup_run_id=phase3-experience-v2-20261023-r2`, `restart_owned_by=experience_controller`, and both `production_resumed` and `candidate_applied` as true. The existing receipt has no `status` field; the controller validates its actual completion fields rather than manufacturing a replacement receipt.
- The complete 47-entry r2 first-party overlay source inventory and hashes, including `main.py` and `profile_recall.py`. The already-existing Phase 2 `data/plugins/phase2_policy/plugin_permissions.py` is separately protected at SHA-256 `9c76c3f8429ecccaa8267364cb23b47c86a0e833b121e9ee0cbe2e946b80a17e`; it was not changed by the r2 overlay and must remain unchanged. The new publication helper must not exist in the old inventory or current installation. All other unknown extra first-party source files are rejected.
- Exact locked AstrBot/NapCat images, existing Compose container IDs, project/service labels, the AstrBot state mount, and all three upstream plugin source tree hashes. NapCat cannot have a writable mount overlapping the AstrBot backup scope.
- Exact `compose.yml`, `.env`, `data/cmd_config.json`, and the entire `data/config` file inventory and hashes. Values are never printed.
- No active collecting/ready/generating turn or pending delivery/outbound row. This check is repeated after stopping AstrBot. Rows are never deleted or marked complete to pass a gate.

The source bundle contains nine files, preserving their repository-relative paths: this controller, the three candidate runtime files, `scripts/backup.py`, `scripts/phase3-install-overlay.py`, `scripts/prepare_plugins.py`, `config/plugins-lock.json`, and `config/upstream-lock.json`. Helpers and lockfiles must match the hashes pinned in the controller. The source manifest is metadata in addition to those nine files:

```json
{
  "schema": "qq-companion.astrbot-code-release.v1",
  "base_release": "phase3-20261023-experience-v2-r2",
  "base_receipt_sha256": "reviewed receipt SHA-256",
  "files": {"repository/relative/path": "reviewed bundle file SHA-256"},
  "baseline_source_files": {"data/plugins/phase.../file.py": "reviewed r2 installed SHA-256"},
  "protected_config_files": {"data/cmd_config.json": "SHA-256", "data/config/example.json": "SHA-256"},
  "compose_sha256": "SHA-256",
  "env_sha256": "SHA-256"
}
```

Build `baseline_source_files` from the established r2 release inventory and compare it against the current server; do not accept current unknown source as a new baseline. Build config inventory from the verified unchanged server configuration. Resolve all nine bundle hashes after tests and review, including the controller itself. Do not include credentials or private content in this manifest. Production configuration and backups stay private on the server.

## Execution

The following variables are illustrative paths chosen by the release owner, not instructions to create a second deployment:

```bash
python3 "$release_source/scripts/phase3-deploy-runtime-code.py" \
  --source "$release_source" \
  --deployment /opt/qq-ai-companion/deployment \
  --base-receipt "$r2_receipt" \
  --run-id phase3-profile-publication-r3
```

After PLAN passes, repeat the command with `--apply`. The controller locks this release path, verifies the manifest again, stops **only the same AstrBot container ID**, and uses fixed Phase 1 `backup.py` to snapshot `deployment/state`. It restores that archive into a new isolated directory and validates inventory hashes, permissions, and all SQLite databases before publishing any source. The restored copy is never started.

The backup result is `backups/<run-id>/astrbot-only-result.json`, explicitly marked `napcat_snapshot: NOT_PERFORMED` and `phase2_full_backup: NOT_REPLACED`. NapCat remains running; its ID, image, StartedAt, RestartCount and running state are compared throughout. Existing full Phase 2 backups remain untouched. This is not a consistent full-system or NapCat login-state snapshot.

Before publication, `astrbot-code-journal/journal.json` records exactly three target paths, old/new hashes and ownership/mode. Original existing source files are saved under its `original` directory. The fixed overlay helper atomically publishes each file. Full source/config/upstream validation runs again, then the original AstrBot container is started. Success is `INSTALLED_AWAITING_HEALTH_AND_QQ`: health, plugin load, OneBot reconnection and real QQ acceptance remain separate release-owner checks.

Each operation writes a private immutable-name receipt under `operations/<run-id>-install.json` or `-rollback.json`, plus a latest result file. Run IDs and snapshot directories cannot be reused.

## Failure and source-only rollback

Caught publication/start failures first validate all original journal files and every current target against the known old/new hashes, then restore **only the three source paths** and start the same AstrBot container. The new helper is removed if it did not previously exist. No data archive, database, chat row or configuration is restored to the live installation. An unknown source hash or corrupt journal fails closed; mixed or unverified source is not started.

For a completed candidate that fails a later health/QQ check, use the same reviewed bundle and original journal:

```bash
python3 "$release_source/scripts/phase3-deploy-runtime-code.py" \
  --source "$release_source" \
  --deployment /opt/qq-ai-companion/deployment \
  --base-receipt "$r2_receipt" \
  --run-id phase3-profile-publication-r3-rollback \
  --rollback
```

Review PLAN, then repeat with `--apply`. This takes a new stopped AstrBot snapshot and isolated restore verification **including all new chats since release**, restores old source only, and starts the original container. Live databases remain the current ones. A fully installed candidate whose original container is already exited is supported without first starting the faulty candidate, provided active/pending guards are clear. Pending state is preserved and conservatively blocks this controller; it does not silently reconcile or delete it.

If rollback fails before source modification, the controller validates and preserves the entering candidate version: a previously running candidate is resumed; a previously exited candidate stays exited. If source rollback itself was begun, only verified r2 source is eligible for recovery/start. A NapCat external restart invalidates the release result but does not prevent independent recovery of known AstrBot source; NapCat is never commanded by this script.

Limits: a process kill between file publications can leave a mixed-source installation. The current CLI refuses an unrecognized partial installation; it does not claim automatic crash recovery. The journal/backup must be inspected before a narrowly scoped repair. The controller never restores an entire data directory, and never reports its isolated restore as a real QQ or full-system recovery test.

## Isolated evidence

`tests/test_phase3_runtime_code_release.py` uses the actual locked Phase 1 ZIP/SQLite backup and restore implementation, actual atomic source publisher, synthetic 47-file baseline and two SQLite databases, and a simulated Docker command/inspect boundary. It does not run production containers or paid APIs. Upstream hash comparison is independently exercised with a wrong tree hash; the tiny fixture does not redistribute upstream source trees.

20 tests pass: readonly PLAN, actual r2 operation completion fields, baseline/config/helper/native-lock drift, active/pending gates and stop race, verified backup before publication, partial replace failure, start failure, corrupt journal, NapCat restart/mount mismatch, source rollback preserving newly added chat, rollback preflight failure preserving candidate state, exited-candidate rollback, NapCat restarting during independent AstrBot recovery, and unavailable final NapCat state never being reported as unchanged. Evidence and final source hashes are in `evidence/phase3/v2-runtime-code-release-20261024/`.

The first real PLAN safely rejected the pre-existing Phase 2 permissions helper because the r2 overlay inventory did not include it; no production file was modified. Revision 2 adds only the exact legacy path/hash above and a test rejecting either a changed legacy hash or another unknown file. **21/21** tests pass; the original 20-test evidence remains unchanged, and the revision is recorded separately in `evidence/phase3/v2-runtime-code-release-20261024-r2/`.
