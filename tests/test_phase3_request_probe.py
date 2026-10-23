"""Pure inspection contract; native smoke is a separate offline container run."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('request_probe', Path(__file__).resolve().parents[1] / 'scripts/phase3-probe-final-request.py')
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class RequestProbeTests(unittest.TestCase):
    def test_current_native_evidence_not_historical_tool_result(self):
        from types import SimpleNamespace as NS
        body=probe.MARKER+'\n資料\n'+json.dumps({'evidence':[{'id':'current','text':'synthetic'}]})
        block='<MemoryCompanion-Context>'+body+'</MemoryCompanion-Context>'
        req=NS(system_prompt='',extra_user_content_parts=[NS(text=block)],
            contexts=[{'role':'tool','content':body.replace('current','old')}])
        self.assertEqual(probe.request_evidence_records(req)[0]['id'],'current')
        req.extra_user_content_parts=[]
        self.assertEqual(probe.request_evidence_records(req),[])
        req.system_prompt=body
        self.assertEqual(probe.request_evidence_records(req)[0]['id'],'current')
        req.extra_user_content_parts=[NS(text=block),NS(text=block)]
        with self.assertRaisesRegex(ValueError,'ambiguous'):
            probe.request_evidence_records(req)

    def test_exact_profile_core_and_empty_notice_locations_without_plaintext_report(self):
        record = {'id': 'synthetic-L2', 'version': 3, 'text': '合成正文：喜爱“测试”游戏。'}
        evidence = probe.MARKER + '\n资料数据\n' + json.dumps({'evidence': [record]}, ensure_ascii=False)
        payload = {'model': 'synthetic-model', 'messages': [
            {'role': 'system', 'content': evidence},
            {'role': 'user', 'content': [{'type': 'text', 'text': '合成核心'}, {'type': 'text', 'text': probe.EMPTY_RECALL}]}],
            'tools': [{'type': 'function', 'function': {'name': 'memory_companion_navigate'}}]}
        result = probe.analyze(payload, records=probe.evidence_records(evidence), core_records=[{'id': 'core-id', 'text': '合成核心'}])
        self.assertEqual(result['profile_evidence_matches'][0]['occurrences'][0]['message'], 0)
        self.assertEqual(result['native_empty_recall_positions'][0]['message'], 1)
        self.assertEqual(result['native_empty_recall_positions'][0]['part'], 1)
        self.assertEqual(result['core_memory_matches'][0]['occurrences'][0]['part'], 0)
        self.assertEqual(result['tools'], ['memory_companion_navigate'])
        public = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(record['text'], public)
        self.assertNotIn('合成核心', public)

    def test_json_escaped_evidence_still_identified(self):
        text = '测试"双引号"和\n下一行'
        payload = {'messages': [{'role': 'system', 'content': json.dumps({'text': text}, ensure_ascii=False)}]}
        match = probe.analyze(payload, records=[{'id': 'x', 'text': text}])['profile_evidence_matches'][0]
        self.assertFalse(match['occurrences'])
        self.assertEqual(len(match['json_string_occurrences']), 1)

    def test_bom_credentials_and_search_contract_preserved_on_copy(self):
        with tempfile.TemporaryDirectory() as name:
            state = Path(name)
            (state / 'data/config').mkdir(parents=True)
            config = {'platform': [{'id': 'fixture'}], 'provider_sources': [{'key': ['secret-fixture'], 'api_base': 'https://api.deepseek.com'}],
                'provider_settings': {'web_search': True, 'websearch_provider': 'deepseek', 'websearch_deepseek_key': ['secret-fixture']}}
            (state / 'data/cmd_config.json').write_text('\ufeff' + json.dumps(config))
            (state / 'data/config/phase3_runtime_config.json').write_text(json.dumps({'search': True, 'proactive': True}))
            probe.sanitize_configs(state)
            current = json.loads((state / 'data/cmd_config.json').read_text())
            self.assertEqual(current['platform'], [])
            self.assertEqual(current['provider_sources'][0]['key'], ['isolated-probe-dummy'])
            self.assertTrue(current['provider_settings']['web_search'])
            self.assertEqual(current['provider_settings']['websearch_deepseek_key'], ['isolated-probe-dummy'])
            self.assertTrue(json.loads((state / 'data/config/phase3_runtime_config.json').read_text())['search'])
            self.assertEqual((state / 'data/cmd_config.json').stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
