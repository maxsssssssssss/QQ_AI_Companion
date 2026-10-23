"""Synthetic-only wrapper; the production-copy probe itself stays unchanged."""
import json
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests'), str(ROOT / 'scripts')]


def main():
    state = Path(sys.argv[sys.argv.index('--state') + 1])
    marker = json.loads((state / 'probe-snapshot.json').read_text(encoding='utf-8-sig'))
    if marker.get('synthetic_only') is not True or marker.get('disposable') is not True:
        raise RuntimeError('shaped_fixture_requires_fresh_synthetic_state')
    import phase3_request_shape_fixture as fixture
    sys.modules['phase3_profile_fixture'] = fixture
    from integrations.phase3_memory.profile_release import ProfileRelease
    original = ProfileRelease.apply
    async def seed_after_native_import(self, *args, **kwargs):
        result = await original(self, *args, **kwargs)
        if result.get('state') == 'APPLIED':
            await fixture.seed_unrelated_candidates(self.service, self.ctx)
        return result
    ProfileRelease.apply = seed_after_native_import
    (state / 'synthetic-expected.json').write_text(json.dumps(fixture.expected(), ensure_ascii=False, indent=2))
    sys.argv[0] = str(ROOT / 'scripts/phase3-probe-final-request.py')
    runpy.run_path(sys.argv[0], run_name='__main__')


if __name__ == '__main__':
    main()
