#!/usr/bin/env bash
# Build from a pinned upstream commit and checked-in package artifact hashes.
set -euo pipefail
project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"
export UV_CACHE_DIR="$project_root/.runtime/uv-cache"
export BUILDX_CONFIG="$project_root/.runtime/buildx"

docker_local() {
    env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_TLS \
        -u DOCKER_TLS_VERIFY -u DOCKER_CERT_PATH \
        docker --host=unix:///var/run/docker.sock "$@"
}
docker_local info --format 'Docker {{.ServerVersion}} ({{.Driver}})'

readarray -t upstream < <(python3 - <<'PY'
import json
from pathlib import Path
lock = json.loads(Path('config/upstream-lock.json').read_text())
for value in [lock['astrbot']['repository'], lock['astrbot']['commit'],
              lock['container']['base_image'], lock['container']['image']]:
    print(value)
PY
)
source_dir="$project_root/.runtime/upstream/AstrBot"
if [[ ! -d "$source_dir/.git" ]]; then
    if [[ -e "$source_dir" ]]; then
        echo 'Refusing to overwrite an existing non-Git upstream directory.' >&2
        exit 1
    fi
    mkdir -p "$(dirname "$source_dir")"
    git init --quiet "$source_dir"
    git -C "$source_dir" remote add origin "${upstream[0]}"
    git -C "$source_dir" fetch --depth 1 origin "${upstream[1]}"
    git -C "$source_dir" checkout --quiet --detach FETCH_HEAD
fi
if [[ "$(git -C "$source_dir" rev-parse HEAD)" != "${upstream[1]}" ]]; then
    echo 'Upstream checkout is not the pinned commit; preserve it and resolve manually.' >&2
    exit 1
fi
if [[ -n "$(git -C "$source_dir" status --porcelain --untracked-files=normal)" ]]; then
    echo 'Upstream checkout has local changes; refusing to replace or build it.' >&2
    exit 1
fi

mkdir -p .runtime
build_context="$(mktemp -d "$project_root/.runtime/build-context.XXXXXX")"
trap 'rm -rf "$build_context"' EXIT
git -C "$source_dir" archive HEAD > "$build_context/upstream.tar"
python3 - "$build_context/upstream.tar" <<'PY'
import hashlib
import json
import sys
from pathlib import Path
lock = json.loads(Path('config/upstream-lock.json').read_text())
checks = [(Path(sys.argv[1]), lock['astrbot']['source_archive_sha256']),
          (Path(lock['dependencies']['lock']), lock['dependencies']['sha256']),
          (Path(lock['dependencies']['build_lock']), lock['dependencies']['build_lock_sha256'])]
for path, expected in checks:
    observed = hashlib.sha256(path.read_bytes()).hexdigest()
    if observed != expected:
        raise SystemExit(f'Integrity check failed for {path.name}')
print('Verified upstream archive and dependency lock SHA256 hashes.')
PY
mkdir "$build_context/AstrBot"
tar -xf "$build_context/upstream.tar" -C "$build_context/AstrBot"
rm "$build_context/upstream.tar"
cp Dockerfile "$build_context/Dockerfile"
cp -a lockfiles "$build_context/lockfiles"

# A combined trusted CA bundle is mounted only for networked build steps.
# Docker retains its configured proxy and registry credentials.
ca_bundle="${SSL_CERT_FILE:-/etc/ssl/certs/ca-certificates.crt}"
if [[ ! -r "$ca_bundle" ]]; then
    echo 'A readable trusted CA bundle is required; TLS verification stays enabled.' >&2
    exit 1
fi
docker_local build --progress plain --platform linux/amd64 \
    --secret "id=ca_bundle,src=$ca_bundle" \
    --build-arg "PYTHON_BASE=${upstream[2]}" \
    -t "${upstream[3]}" "$build_context"
docker_local image inspect "${upstream[3]}" \
    --format 'Built {{.Id}} as {{index .RepoTags 0}}; default user={{.Config.User}}'
