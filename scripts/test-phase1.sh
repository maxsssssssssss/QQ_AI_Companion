#!/usr/bin/env bash
# Every invocation creates new evidence; failures retain their exit codes.
set -euo pipefail
cd "$(dirname "$0")/.."
project_root="$PWD"
suite_id="$(date -u +%Y%m%dT%H%M%SZ)-$$"
suite_dir="$project_root/evidence/runs/phase1-$suite_id"
mkdir -p "$suite_dir"
docker_cmd=(env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_TLS -u DOCKER_TLS_VERIFY -u DOCKER_CERT_PATH docker --host=unix:///var/run/docker.sock)
set +e
bash scripts/doctor.sh --json > "$suite_dir/doctor.json"
doctor_status=$?
python3 scripts/run_unit_tests.py "$suite_dir/unit.json" > "$suite_dir/unit.log" 2>&1
unit_status=$?
"${docker_cmd[@]}" run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --user "$(id -u):$(id -g)" \
  --pids-limit 256 --memory 2g --cpus 2 \
  --tmpfs /tmp:rw,noexec,nosuid,size=256m,mode=1777 \
  --mount "type=bind,src=$project_root/tests,dst=/tests,readonly" \
  --mount "type=bind,src=$suite_dir,dst=/evidence" \
  -e ASTRBOT_ROOT=/tmp/native-cron -e PYTHONDONTWRITEBYTECODE=1 \
  -e QQ_FUTURE_EVIDENCE=/evidence/future-tasks.json \
  qq-ai-companion:phase1 python /tests/test_future_tasks.py > "$suite_dir/future-tasks.log" 2>&1
future_status=$?
bash scripts/test-offline.sh
integration_status=$?
set -e
integration_dir="$(cat .runtime/latest-evidence)"
python3 scripts/report_phase1.py "$suite_dir" "$integration_dir" \
  "$doctor_status" "$unit_status" "$future_status" "$integration_status"
