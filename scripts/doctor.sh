#!/usr/bin/env bash
# Read-only checks: no environment dump, network request, mutation, or repair.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
PYTHONDONTWRITEBYTECODE=1 python3 - "$@" <<'PY'
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path('scripts').resolve()))
from configuration import validate_config, local_timestamp

parser = argparse.ArgumentParser(description='Read-only Phase 1 configuration and isolation checks')
parser.add_argument('--json', action='store_true', help='emit structured results')
args = parser.parse_args()
results = []

def check(name, function):
    try:
        detail = function()
    except Exception as exc:
        # Error details contain paths/statuses only. Never print environment or file contents.
        detail = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
        results.append({'id': name, 'status': 'FAIL', 'detail': detail})
    else:
        results.append({'id': name, 'status': 'PASS', 'detail': detail})

def command(argv, *, timeout=20, env=None):
    result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=timeout, env=env)
    if result.returncode:
        raise RuntimeError(f'{argv[0]} read-only check exited {result.returncode}; output withheld to avoid credential disclosure')
    return result.stdout.decode('utf-8', errors='replace')

def required_files():
    required = ['Dockerfile', 'README.md', '.gitignore', '.env.example',
                'config/offline.json', 'config/upstream-lock.json', 'config/plugins-lock.json',
                'launch_astrbot.py', 'scripts/bootstrap.sh', 'scripts/backup.py',
                'scripts/test-offline.sh', 'scripts/configuration.py', 'tests/test_configuration.py',
                'persona/core_persona.md', 'persona/relationship.md',
                'persona/user_profile.example.md']
    missing = [name for name in required if not Path(name).is_file()]
    if missing:
        raise ValueError('missing required files: ' + ', '.join(missing))
    return f'{len(required)} required project files exist'

def configuration():
    config = validate_config('config/offline.json')
    for field in ('persona_dir', 'plugin_lock'):
        selected = Path(config['phase1']['paths'][field])
        if not selected.exists():
            raise ValueError(f'phase1.paths.{field}: configured path does not exist')
        if not selected.resolve().is_relative_to(Path.cwd().resolve()):
            raise ValueError(f'phase1.paths.{field}: resolves outside the project')
    return 'offline mock provider, loopback OneBot, allowlist, paths and plugin candidates validated'

def timezone():
    expected = [('2026-10-19T00:00:00Z', '2026-10-19T08:00:00+08:00'),
                ('2026-10-19T20:30:00Z', '2026-10-20T04:30:00+08:00')]
    if any(local_timestamp(source) != target for source, target in expected):
        raise ValueError('timezone: UTC conversion or day rollover differs from Asia/Shanghai')
    return 'Asia/Shanghai UTC+08:00 conversion and next-day rollover verified'

def hashes():
    lock = json.loads(Path('config/upstream-lock.json').read_text())
    dependencies = lock['dependencies']
    for file_key, hash_key in [('lock', 'sha256'), ('build_lock', 'build_lock_sha256')]:
        path = Path(dependencies[file_key])
        if path.is_absolute() or not path.resolve().is_relative_to(Path.cwd().resolve()):
            raise ValueError(f'dependencies.{file_key}: must remain inside the project')
        if hashlib.sha256(path.read_bytes()).hexdigest() != dependencies[hash_key]:
            raise ValueError(f'dependencies.{file_key}: SHA256 mismatch')
    base = lock['container']['base_image']
    if not re.search(r'@sha256:[0-9a-f]{64}$', base):
        raise ValueError('container.base_image: missing immutable digest')
    if not re.fullmatch(r'[0-9a-f]{40}', lock['astrbot']['commit']):
        raise ValueError('astrbot.commit: missing immutable commit')
    return 'upstream commit, base-image digest and both dependency-lock SHA256 values valid'

def plugin_lock():
    lock = json.loads(Path('config/plugins-lock.json').read_text())
    config = validate_config('config/offline.json')
    plugins = lock['plugins']
    ids = {plugin['id'] for plugin in plugins}
    if not set(config['phase1']['plugins']).issubset(ids):
        raise ValueError('phase1.plugins: candidate missing from plugins-lock.json')
    for plugin in plugins:
        if not re.fullmatch(r'[0-9a-f]{40}', plugin.get('commit', '')):
            raise ValueError(f"plugins.{plugin.get('id', '?')}.commit: missing immutable commit")
        if not plugin.get('license_status'):
            raise ValueError(f"plugins.{plugin['id']}.license_status: audit missing")
    return f'{len(plugins)} candidates pinned; recorded license restrictions remain applicable'

def tracked_safety():
    names = sorted(set(filter(None, command(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z']).split('\0'))))
    indexed = set(filter(None, command(['git', 'ls-files', '-z']).split('\0')))
    violations = []
    suspicious_token = re.compile(rb'(?:sk-[A-Za-z0-9]{24,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,}|AKIA[A-Z0-9]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)')
    for name in filter(None, names):
        path = Path(name)
        if (path.suffix.lower() in {'.db', '.sqlite', '.sqlite3', '.pem', '.key'}
                or path.name in {'.env', 'credentials.json', 'credentials', 'id_rsa', 'id_ed25519'}
                or path.parts[0] in {'data', '.runtime', 'backups', 'logs'}):
            violations.append(name + ' (private/runtime file tracked)')
            continue
        if path.is_file() and path.stat().st_size < 2_000_000:
            if suspicious_token.search(path.read_bytes()):
                violations.append(name + ' (possible credential; value withheld)')
        if name in indexed:
            staged = subprocess.run(['git', 'show', ':' + name], stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, check=True).stdout
            if suspicious_token.search(staged):
                violations.append(name + ' (possible staged credential; value withheld)')
    if violations:
        raise ValueError('tracked-files: ' + '; '.join(violations))
    inventory = hashlib.sha256('\n'.join(names).encode()).hexdigest()
    return f'{len(names)} tracked/nonignored delivery files plus Git index scanned; no runtime DB or recognized credential pattern; inventory SHA256={inventory}; heuristic only'

def ignored_paths():
    names = ['.env', 'data/example.sqlite', '.runtime/example', 'backups/example.tar', 'logs/example.log']
    missing = []
    for name in names:
        result = subprocess.run(['git', 'check-ignore', '--no-index', '-q', '--', name],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode:
            missing.append(name)
    if missing:
        raise ValueError('.gitignore: must ignore ' + ', '.join(missing))
    return 'local credentials, runtime data, logs and backups are ignored'

docker_env = dict(os.environ)
for name in ['DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH']:
    docker_env.pop(name, None)
docker = ['docker', '--host=unix:///var/run/docker.sock']

def daemon():
    version = command(docker + ['info', '--format', '{{.ServerVersion}}'], env=docker_env).strip()
    return 'managed local Docker daemon reachable: ' + version

def image():
    lock = json.loads(Path('config/upstream-lock.json').read_text())
    tag = lock['container']['image']
    output = command(docker + ['image', 'inspect', tag, '--format', '{{json .Config.User}} {{json .Id}}'], env=docker_env).strip()
    user, image_id = output.split(' ', 1)
    if json.loads(user) != '10001:10001':
        raise ValueError('image.Config.User: expected non-root 10001:10001')
    return 'local image available with non-root default: ' + json.loads(image_id)

def no_public_ports():
    output = command(docker + ['ps', '--filter', 'ancestor=qq-ai-companion:phase1',
                              '--format', '{{.ID}}'], env=docker_env)
    for container in output.split():
        bindings = json.loads(command(docker + ['inspect', container, '--format', '{{json .HostConfig.PortBindings}}'], env=docker_env))
        if bindings:
            raise ValueError('container.PortBindings: Phase 1 must not publish any ports')
    return 'no published ports on running Phase 1 containers'

for name, function in [('files', required_files), ('config', configuration),
                       ('timezone', timezone), ('dependency_hashes', hashes),
                       ('plugin_pins', plugin_lock), ('tracked_safety', tracked_safety),
                       ('gitignore', ignored_paths), ('docker_daemon', daemon),
                       ('docker_image', image), ('no_public_ports', no_public_ports)]:
    check(name, function)
passed = all(result['status'] == 'PASS' for result in results)
report = {'status': 'PASS' if passed else 'FAIL', 'scope': 'read-only preflight; does not replace end-to-end acceptance', 'checks': results}
if args.json:
    print(json.dumps(report, indent=2))
else:
    for result in results:
        print(f"{result['status']} {result['id']}: {result['detail']}")
    print('Doctor ' + report['status'] + '; end-to-end acceptance is separate.')
sys.exit(0 if passed else 1)
PY
