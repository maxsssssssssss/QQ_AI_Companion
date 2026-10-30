#!/usr/bin/env python3
"""PLAN by default: add an interruptible 0.8–1.8s first-bubble delay.

Reuse the immutable AstrBot-only release transaction in a private module instance.
Its consistent backup, isolated restore check, in-flight work check and narrow
rollback remain intact. Databases, native preferences and NapCat are not written.
The manifest has the same hash fields as the autonomy controller; its `files`
must be exactly PINNED + this CONTROLLER + TARGETS.values(). Take all baseline
hashes from the deployed closeout state, not from a local checkout.
Only the two first-bubble configuration fields are owned; later bubbles keep
2–4s pacing, search progress remains immediate, and send accounting is unchanged.
"""
import argparse
import ast
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

HELPER = 'scripts/phase3-deploy-autonomy.py'
HELPER_SHA = '44186b21400e90c1451dd79642c12cff2f7d9b3e459d812d581766fda4d13d2c'
path = Path(__file__).with_name(Path(HELPER).name)
if hashlib.sha256(path.read_bytes()).hexdigest() != HELPER_SHA:
    raise RuntimeError('locked_autonomy_release_helper_required')
spec = importlib.util.spec_from_file_location('first_bubble_release_engine', path)
engine = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = engine
spec.loader.exec_module(engine)

SCHEMA = 'qq-companion.first-bubble-delay-release.v1'
BASE = 'phase3-20261029-autonomy-closeout-r2'
CONTROLLER = 'scripts/phase3-deploy-first-bubble-delay.py'
PINNED = {**engine.PINNED, HELPER: HELPER_SHA}
TARGETS = {'data/plugins/phase1_guard/' + name: 'integrations/phase1_guard/' + name
           for name in ('main.py', 'bubbles.py', '_conf_schema.json')}
CONFIG = 'data/config/phase1_guard_config.json'
LATER = {'phase3_bubble_min_interval': 2.0, 'phase3_bubble_max_interval': 4.0}
OLD = {'phase3_first_bubble_min_delay': 0.0, 'phase3_first_bubble_max_delay': 0.0}
NEW = {'phase3_first_bubble_min_delay': 0.8, 'phase3_first_bubble_max_delay': 1.8}
ReleaseError, Services = engine.ReleaseError, engine.Services
safe, sha, read = engine.safe, engine.sha, engine.read


def config_patch(original, source):
    if not isinstance(original, dict) or any(type(original.get(k)) not in (int, float)
            or original[k] != v for k, v in LATER.items()):
        raise ReleaseError('reviewed_guard_2_4_pacing_required')
    present = [k in original for k in OLD]
    if any(present) and (not all(present) or any(
            type(original.get(k)) not in (int, float) or original[k] != 0 for k in OLD)):
        raise ReleaseError('first_bubble_baseline_must_be_absent_or_disabled')
    result = deepcopy(original)
    result.update(NEW)
    return result


_validate = engine.validate


def validate_candidate_semantics(source):
    """Reject a hashed but stale Guard package before touching live services."""
    schema = read(safe(source, 'integrations/phase1_guard/_conf_schema.json'))
    if any(not isinstance(schema.get(key), dict)
           or schema[key].get('type') != 'float'
           or type(schema[key].get('default')) not in (int, float)
           or schema[key]['default'] != 0 for key in OLD):
        raise ReleaseError('first_bubble_disabled_schema_required')
    tree = ast.parse(safe(source, 'integrations/phase1_guard/main.py').read_text())
    pacing_calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name) and node.func.id == 'Pacing']
    expected_keys = set(OLD)
    if not any(expected_keys <= {node.value for node in ast.walk(call)
                                if isinstance(node, ast.Constant) and isinstance(node.value, str)}
               for call in pacing_calls):
        raise ReleaseError('first_bubble_guard_configuration_binding_required')
    module = engine.load('first_bubble_candidate_pacing',
                         safe(source, 'integrations/phase1_guard/bubbles.py'))
    try:
        off = module.Pacing(2.0, 4.0)
        on = module.Pacing(2.0, 4.0, first_minimum=0.8, first_maximum=1.8)
        valid = (off.first_minimum == off.first_maximum == off.first_delay() == 0
                 and on.minimum == 2.0 and on.maximum == 4.0
                 and on.first_minimum == 0.8 and on.first_maximum == 1.8
                 and 0.8 <= on.first_delay() <= 1.8)
    except (AttributeError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ReleaseError('first_bubble_pacing_runtime_required')


def validate(source, deployment, installed=False, *, config_mode='baseline'):
    manifest = read(safe(source, 'source-manifest.json'))
    expected = set(PINNED) | {CONTROLLER} | set(TARGETS.values())
    observed = {p.relative_to(source).as_posix()
                for name in ('scripts', 'config', 'integrations')
                for p in safe(source, name).rglob('*')
                if p.is_file() and '__pycache__' not in p.parts}
    if observed != expected:
        raise ReleaseError('exact_first_bubble_source_package_required')
    if not set(TARGETS).issubset(manifest.get('baseline_source_files', {})):
        raise ReleaseError('existing_three_guard_files_required')
    result = _validate(source, deployment, installed, config_mode=config_mode)
    validate_candidate_semantics(source)
    return result


def result_metadata(value):
    if isinstance(value, dict) and value.get('operation') in (
            'install_autonomy', 'rollback_owned_code_and_fields'):
        value = dict(value)
        value['operation'] = ('install_first_bubble_delay' if value['operation'] == 'install_autonomy'
                              else 'rollback_first_bubble_delay')
        value['later_bubble_seconds'] = dict(LATER)
        value['first_bubble_seconds'] = dict(OLD if value['operation'].startswith('rollback') else NEW)
        value['progress_delay_seconds'] = 0
        value['send_accounting_modified'] = False
        value['runtime_configuration_modified'] = False
    return value


_private_json = engine.private_json


def private_json(path, value):
    return _private_json(path, result_metadata(value))


# Only this private engine instance is specialized. The original helper bytes
# remain pinned and unmodified. Its controller self-hash must identify this file.
engine.__file__ = __file__
for name in ('SCHEMA', 'BASE', 'CONTROLLER', 'PINNED', 'TARGETS', 'CONFIG',
             'config_patch', 'validate', 'private_json'):
    setattr(engine, name, globals()[name])


def execute(args, services=None):
    return result_metadata(engine.execute(args, services))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--deployment', type=Path, default=Path('/opt/qq-ai-companion/deployment'))
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--rollback', action='store_true')
    try:
        result = execute(parser.parse_args())
    except Exception as exc:
        result = {'status': 'FAILED', 'reason': str(exc) if isinstance(exc, ReleaseError)
                  else type(exc).__name__}
    print(json.dumps(result))
    raise SystemExit(result['status'].startswith(('FAILED', 'BLOCKED')))
