"""New opt-in settings survive the pinned host without changing old permissions."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from integrations.phase3_runtime.settings import (
    AUTONOMY_DEFAULTS, FREQUENCY_PROACTIVE_POLICY, autonomy_candidate, validate_settings,
)

ROOT = Path(__file__).resolve().parents[1]
try:
    from astrbot.core.config.astrbot_config import AstrBotConfig
except ImportError:
    AstrBotConfig = None


class AutonomyConfigTests(unittest.TestCase):
    def baseline(self):
        return dict(enabled=True, persona=True, natural_chat=True, memory=True,
                    search=True, proactive=True,
                    proactive_window=deepcopy(FREQUENCY_PROACTIVE_POLICY))

    def host(self, original):
        if AstrBotConfig is None:
            self.skipTest('pinned AstrBot required')
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / 'runtime.json'
        path.write_text(json.dumps(original))
        schema = json.loads((ROOT / 'integrations/phase3_runtime/_conf_schema.json').read_text())
        return AstrBotConfig(str(path), schema=schema)

    def test_old_enabled_proactive_settings_do_not_implicitly_enable_autonomy(self):
        original = self.baseline()
        actual = self.host(original)
        self.assertEqual(actual['proactive_autonomy'], AUTONOMY_DEFAULTS)
        self.assertFalse(actual['proactive_autonomy']['enabled'])
        self.assertEqual(validate_settings(actual)['proactive_window'], original['proactive_window'])

    def test_disabled_legacy_config_roundtrips_without_increasing_permissions(self):
        actual = self.host({})
        validated = validate_settings(actual)
        self.assertFalse(validated['enabled'])
        self.assertFalse(validated['proactive'])
        self.assertFalse(validated['proactive_autonomy']['enabled'])

    def test_candidate_roundtrips_full_native_schema_with_exact_limits(self):
        original = self.baseline()
        candidate = autonomy_candidate(original)
        actual = validate_settings(self.host(candidate))
        self.assertEqual(actual['proactive_autonomy'], {**AUTONOMY_DEFAULTS, 'enabled': True})
        self.assertEqual(actual['proactive_window'], original['proactive_window'])
        self.assertNotIn('proactive_autonomy', original)

    def test_existing_custom_autonomy_limits_and_disabled_subfeatures_are_preserved(self):
        original = self.baseline()
        original['proactive_autonomy'] = {**AUTONOMY_DEFAULTS, 'enabled': True,
            'model_requests_per_day': 80, 'tool_attempts_per_day': 40,
            'supplements': False, 'self_preferences': False, 'exploration': False}
        self.assertEqual(validate_settings(self.host(original))['proactive_autonomy'],
                         original['proactive_autonomy'])

    def test_invalid_limits_types_or_window_fail_closed(self):
        for change in ({'model_requests_per_day':145}, {'tool_attempts_per_day':289},
                       {'tools_per_opportunity':4}, {'exploration_seconds':601},
                       {'enabled':'true'}, {'pocket_limit':True},
                       {'supplement_min_seconds':3600,'supplement_max_seconds':3600}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_settings({**self.baseline(),'proactive_autonomy':{**AUTONOMY_DEFAULTS,**change}})

    def test_enable_requires_existing_proactive_permission_and_never_enables_background_web(self):
        with self.assertRaises(ValueError):
            autonomy_candidate({**self.baseline(),'proactive':False})
        with self.assertRaises(ValueError):
            validate_settings({**autonomy_candidate(self.baseline()),'background_web':True})


if __name__ == '__main__':
    unittest.main()
