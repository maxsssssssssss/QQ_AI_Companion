#!/usr/bin/env bash
# Explicit host preparation; preserves SSH configuration and firewall; no reboot.
set -euo pipefail
if [[ "${1:-}" != "--apply" || "$EUID" -ne 0 ]]; then
    echo 'Usage: sudo bash scripts/phase2-provision.sh --apply' >&2
    exit 2
fi
. /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" == 24.04 && "$(uname -m)" == x86_64 ]] || {
    echo 'This deployment is reviewed only for Ubuntu 24.04 x86_64.' >&2
    exit 1
}
if command -v docker >/dev/null; then
    running_containers="$(docker ps -q)" || {
        echo 'Existing Docker installation cannot be inspected; review required.' >&2
        exit 1
    }
    if [[ -n "$running_containers" ]]; then
        echo 'Existing running containers require review before host preparation.' >&2
        exit 1
    fi
fi

export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=l
export UCF_FORCE_CONFFOLD=1
# Preserve existing configuration; APT signature checking remains enabled.
apt-get update
apt-get -y --no-remove -o Dpkg::Options::=--force-confold upgrade
apt-get -y --no-remove --no-install-recommends \
    -o Dpkg::Options::=--force-confold install \
    docker.io docker-compose-v2 docker-buildx \
    ca-certificates git curl python3 python3-venv unzip

timedatectl set-timezone Asia/Shanghai
install -d -m 0750 -o ubuntu -g ubuntu /opt/qq-ai-companion
install -d -m 0700 -o ubuntu -g ubuntu /opt/qq-ai-companion/deployment

python3 - <<'PY'
import json
from pathlib import Path

path = Path("/etc/docker/daemon.json")
settings = json.loads(path.read_text()) if path.exists() else {}
desired = {
    "log-driver": "json-file",
    "log-opts": {"max-size": "10m", "max-file": "3"},
    "live-restore": True,
}
for key, value in desired.items():
    if key in settings and settings[key] != value:
        raise SystemExit(f"Existing Docker {key} differs; review required, not overwritten")
settings.update(desired)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(settings, indent=2) + "\n")
path.chmod(0o644)
PY
dockerd --validate --config-file=/etc/docker/daemon.json
systemctl enable docker
systemctl restart docker
usermod -aG docker ubuntu
# Ubuntu's OpenSSH upgrade can leave only ssh.socket active. Restore the
# already-authorized service without altering its configuration or host keys.
systemctl start ssh.service
systemctl is-active docker ssh
docker version --format 'Docker server {{.Server.Version}}'
docker compose version
docker buildx version
sshd -t
if [[ -f /var/run/reboot-required ]]; then
    echo 'REBOOT_REQUIRED: no reboot has been performed.'
fi
echo 'Host preparation finished. No application containers have been started.'
