#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
project_root="$PWD"
evidence_dir="$project_root/evidence/future-tasks"
mkdir -p "$evidence_dir"
docker_cmd=(env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_TLS -u DOCKER_TLS_VERIFY -u DOCKER_CERT_PATH docker --host=unix:///var/run/docker.sock)
"${docker_cmd[@]}" image inspect qq-ai-companion:phase1 --format '{{.Id}}' > "$evidence_dir/image-id.txt"
set +e
"${docker_cmd[@]}" run --rm --network none --read-only \
  --cap-drop ALL --security-opt no-new-privileges \
  --pids-limit 128 --memory 1g --cpus 1 --user "$(id -u):$(id -g)" \
  --tmpfs /tmp:rw,noexec,nosuid,size=128m,mode=1777 \
  --mount "type=bind,src=$project_root,dst=/harness,readonly" \
  --mount "type=bind,src=$evidence_dir,dst=/evidence" \
  -e ASTRBOT_ROOT=/tmp/astrbot-future-test \
  -e QQ_FUTURE_EVIDENCE=/evidence/result.json \
  qq-ai-companion:phase1 python /harness/tests/test_future_tasks.py \
  > "$evidence_dir/runner.log" 2>&1
test_status="$?"
set -e
tail -6 "$evidence_dir/runner.log"
printf 'Evidence: %s\n' "$evidence_dir"
exit "$test_status"
