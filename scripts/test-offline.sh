#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
project_root="$PWD"
run_id="$(date -u +%Y%m%dT%H%M%SZ)-$$"
state_dir="$project_root/.runtime/runs/$run_id"
evidence_dir="$project_root/evidence/runs/$run_id"
mkdir -p "$state_dir" "$evidence_dir"
docker_cmd=(env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_TLS -u DOCKER_TLS_VERIFY -u DOCKER_CERT_PATH docker --host=unix:///var/run/docker.sock)
"${docker_cmd[@]}" image inspect qq-ai-companion:phase1 --format '{{.Id}}' > "$evidence_dir/image-id.txt"
mounts=()
for area in scripts tests config persona integrations; do
  mounts+=(--mount "type=bind,src=$project_root/$area,dst=/harness/$area,readonly")
done
mounts+=(--mount "type=bind,src=$project_root/launch_astrbot.py,dst=/harness/launch_astrbot.py,readonly")
set +e
"${docker_cmd[@]}" run --rm --name "qq-phase1-$run_id" \
  --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  --pids-limit 256 --memory 4g --cpus 2 --user "$(id -u):$(id -g)" \
  --tmpfs /tmp:rw,noexec,nosuid,size=256m,mode=1777 \
  "${mounts[@]}" \
  --mount "type=bind,src=$project_root/.runtime/upstream,dst=/plugins,readonly" \
  --mount "type=bind,src=$state_dir,dst=/state" \
  --mount "type=bind,src=$evidence_dir,dst=/evidence" \
  -e HTTP_PROXY= -e HTTPS_PROXY= -e ALL_PROXY= \
  -e http_proxy= -e https_proxy= -e all_proxy= \
  -e NO_PROXY=localhost,127.0.0.1 -e no_proxy=localhost,127.0.0.1 \
  -e PYTHONDONTWRITEBYTECODE=1 \
  qq-ai-companion:phase1 python /harness/tests/run_integration.py \
  2>&1 | tee "$evidence_dir/runner.log"
test_status="${PIPESTATUS[0]}"
set -e
printf '%s\n' "$evidence_dir" > "$project_root/.runtime/latest-evidence"
printf 'Evidence: %s\n' "$evidence_dir"
exit "$test_status"
