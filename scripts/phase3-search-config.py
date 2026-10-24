#!/usr/bin/env python3
"""Save a narrow general-public search candidate; never activate or call tools.

Only the Phase3 Search configuration is read. No credentials, budget database,
model, QQ transport or service is opened. Identity and all existing budgets are
preserved; candidate files remain private and outside live data/source trees.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat

ROOT = Path(__file__).resolve().parents[1]
CONFIG_NAME = 'phase3_search_config.json'
CHANGED_FIELDS = ('scope', 'public_terms', 'fetch_hosts')
LIMIT_FIELDS = {'progress_timeout', 'tool_timeout', 'turn_timeout', 'max_calls',
                'max_result_chars', 'max_progress_chars', 'max_progress_tokens'}


def make_candidate(base):
    """Preserve every existing field except the three approved scope fields."""
    if not isinstance(base, dict): raise ValueError('search_config_object_required')
    if base.get('scope', 'reviewed') not in ('reviewed', 'general_public'):
        raise ValueError('unknown_search_scope')
    if type(base.get('enabled', False)) is not bool:
        raise ValueError('invalid_search_enabled')
    admin, platform = base.get('admin_id'), base.get('platform_id')
    if not isinstance(admin, str) or not re.fullmatch(r'[1-9][0-9]{4,19}', admin):
        raise ValueError('valid_private_admin_required')
    if not isinstance(platform, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', platform):
        raise ValueError('valid_platform_identity_required')
    for key, cap in (('public_terms', 64), ('fetch_hosts', 32)):
        values = base.get(key, [])
        if (not isinstance(values, list) or len(values) > cap
                or any(not isinstance(value, str) or not value.strip()
                       or len(value) > 253 or any(ord(char) < 32 for char in value) for value in values)):
            raise ValueError('invalid_reviewed_scope_list')
    daily = base.get('daily_attempt_limit', 60)
    if type(daily) is not int or not 1 <= daily <= 60:
        raise ValueError('daily_attempt_limit_must_not_exceed_60')
    limits = base.get('limits', {})
    if not isinstance(limits, dict) or set(limits) - LIMIT_FIELDS:
        raise ValueError('invalid_search_limits')
    calls = limits.get('max_calls', 3)
    if type(calls) is not int or not 1 <= calls <= 3:
        raise ValueError('max_calls_must_not_exceed_3')
    for key in ('progress_timeout', 'tool_timeout', 'turn_timeout'):
        if key in limits:
            value = limits[key]
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 120:
                raise ValueError('invalid_search_timeout')
    for key, low, high in (('max_result_chars', 128, 20000), ('max_progress_chars', 1, 240),
                           ('max_progress_tokens', 1, 256)):
        if key in limits and (type(limits[key]) is not int or not low <= limits[key] <= high):
            raise ValueError('invalid_search_output_budget')
    try: json.dumps(base, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError): raise ValueError('json_configuration_required') from None
    candidate = deepcopy(base)
    candidate.update(scope='general_public', public_terms=[], fetch_hosts=[])
    return candidate


def _no_links(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('symlink_path_rejected')


def _read_private(path):
    _no_links(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077
                or info.st_size > 1024 * 1024):
            raise ValueError('protected_regular_config_required')
        raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024: raise ValueError('oversized_config')
        return raw


def _write_private(path, content):
    _no_links(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(content); stream.flush(); os.fsync(stream.fileno())


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError('duplicate_configuration_key')
        result[key] = value
    return result


def save_candidate(source, output):
    source, output = Path(source).absolute(), Path(output).absolute()
    _no_links(source); _no_links(output)
    if source.name != CONFIG_NAME: raise ValueError('phase3_search_config_source_required')
    if output.exists(): raise FileExistsError('new_candidate_directory_required')
    if not output.parent.is_dir(): raise ValueError('existing_candidate_parent_required')
    destination = output.resolve()
    boundary = source.parent.parent if source.parent.name == 'config' else source.parent
    for blocked in (boundary.resolve(), ROOT):
        if destination == blocked or blocked in destination.parents:
            raise ValueError('candidate_must_be_outside_live_data_and_source_tree')
    if '.git' in destination.parts or any((parent / '.git').is_file()
           or (parent / '.git/HEAD').is_file() or (parent / '.git').is_symlink()
           for parent in (destination, *destination.parents)):
        raise ValueError('private_candidate_must_be_outside_git_worktree')
    original = _read_private(source)
    base = json.loads(original.decode('utf-8-sig'), object_pairs_hook=_unique_object)
    candidate = make_candidate(base)
    bom = b'\xef\xbb\xbf' if original.startswith(b'\xef\xbb\xbf') else b''
    encoded = bom + (json.dumps(candidate, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()
    if _read_private(source) != original: raise ValueError('source_changed_during_preparation')
    output.mkdir(mode=0o700); output.chmod(0o700)
    _write_private(output / ('baseline-' + CONFIG_NAME), original)
    _write_private(output / ('candidate-' + CONFIG_NAME), encoded)
    unchanged = _read_private(source) == original
    manifest = {'status': 'SAVED_NOT_APPLIED' if unchanged else 'STALE_DO_NOT_APPLY',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'source_config_sha256': hashlib.sha256(original).hexdigest(),
        'candidate_config_sha256': hashlib.sha256(encoded).hexdigest(),
        'source_unchanged': unchanged, 'production_applied': False, 'service_restarted': False,
        'budget_database_opened': False, 'credentials_read': False, 'model_calls': 0, 'qq_messages': 0,
        'changed_fields': list(CHANGED_FIELDS),
        'activation': 'Wait for explicit coordinated activation. Recheck the source baseline, merge only the three reviewed fields into current configuration, and include the reviewed matching search code. Do not apply this full snapshot or any pending release automatically.'}
    _write_private(output / 'manifest.json', (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode())
    if not unchanged: raise ValueError('source_changed_during_preparation')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-config', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    try: result = save_candidate(args.source_config, args.output_dir)
    except Exception as exc:
        # Only stable reason codes; paths, JSON input, identities and credentials
        # are intentionally excluded even on parser or filesystem errors.
        result = {'status': 'FAILED', 'error_type': type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False))
    return int(result['status'] != 'SAVED_NOT_APPLIED')


if __name__ == '__main__': raise SystemExit(main())
