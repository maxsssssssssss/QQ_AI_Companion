#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
project_root="$PWD"
test_entry="${PHASE3_TEST_ENTRY:-run_phase3_integration.py}"
case "$test_entry" in
  run_phase3_integration.py|run_phase3_proactive_probe.py|run_phase3_profile_pipeline.py) ;;
  *) exit 2 ;;
esac
run_id="$(date -u +%Y%m%dT%H%M%SZ)-$$"
state_dir="$project_root/.runtime/phase3-runs/$run_id"
evidence_dir="$project_root/evidence/phase3/$run_id"
mkdir -p "$state_dir" "$evidence_dir"
mounts=()
for area in scripts tests config persona integrations lockfiles; do
  mounts+=(--mount "type=bind,src=$project_root/$area,dst=/harness/$area,readonly")
done
docker image inspect qq-companion:phase3-test --format '{{.Id}}' > "$evidence_dir/image-id.txt"
set +e
docker run --rm --name "qq-phase3-$run_id" --network none --read-only \
  --cap-drop ALL --security-opt no-new-privileges --memory 3g --cpus 2 --pids-limit 256 \
  --user "$(id -u):$(id -g)" --tmpfs /tmp:rw,nosuid,size=256m,mode=1777 \
  "${mounts[@]}" \
  --mount "type=bind,src=$project_root/.runtime/upstream,dst=/upstream,readonly" \
  --mount "type=bind,src=$state_dir,dst=/state" \
  --mount "type=bind,src=$evidence_dir,dst=/evidence" \
  -e PYTHONPATH=/harness:/upstream/AstrBot -e PYTHONDONTWRITEBYTECODE=1 \
  -e HTTP_PROXY= -e HTTPS_PROXY= -e ALL_PROXY= -e http_proxy= -e https_proxy= -e all_proxy= \
  -e NO_PROXY=localhost,127.0.0.1 -e no_proxy=localhost,127.0.0.1 \
  qq-companion:phase3-test python "/harness/tests/$test_entry" \
  > "$evidence_dir/runner.log" 2>&1
result=$?
set -e
printf '%s\n' "$evidence_dir" > .runtime/latest-phase3-evidence
printf 'Phase3 isolated exit=%s evidence=%s\n' "$result" "$evidence_dir"
exit "$result"
