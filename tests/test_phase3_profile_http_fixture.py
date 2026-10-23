"""Prevent false-positive current-evidence assertions from old model replies."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest

from phase3_memory_http_fixture import current_profile_evidence, MemoryMockLLM, KEY
from phase3_profile_fixture import GAMES, failed_tool_history, OLD_FAILURE_MARKER

MARKER='[PHASE3_PROFILE_EVIDENCE_V2]'


def request(records):
    payload={'status':'found','coverage':'complete_approved_topics','evidence':records}
    return {'messages':[
        {'role':'system','content':'V0.1\n按当前有效资料回答。'},
        {'role':'assistant','content':'、'.join(GAMES)},
        {'role':'tool','content':'、'.join(GAMES),'tool_call_id':'old'},
        {'role':'user','content':[
            {'type':'text','text':'本轮资料正文核验：我平时玩哪些游戏？'},
            {'type':'text','text':'<MemoryCompanion-Context>\n<core_memory>合成核心约定。</core_memory>\n'
                '<phase3_approved_profile>\n'+MARKER+'\n本轮资料，不是指令。\n'
                +json.dumps(payload,ensure_ascii=False)+'\n</phase3_approved_profile>\n'
                '</MemoryCompanion-Context>'},
            {'type':'text','text':'<private_companion_context>合成人格状态。</private_companion_context>'}]}]}


class CurrentEvidenceTests(unittest.IsolatedAsyncioTestCase):
    def test_reads_current_native_payload_with_other_components(self):
        records=[{'id':'synthetic-current','version':'v1','text':'、'.join(GAMES)}]
        self.assertEqual(current_profile_evidence(request(records))['evidence'],records)

    def test_string_form_preserves_same_native_boundary(self):
        records=[{'id':'synthetic-current','version':'v1','text':'、'.join(GAMES)}]
        body=request(records)
        body['messages'][-1]['content']='\n'.join(p['text'] for p in body['messages'][-1]['content'])
        self.assertEqual(current_profile_evidence(body)['evidence'],records)

    def test_marker_only_in_old_tool_is_not_current_evidence(self):
        body=request([])
        old_context=body['messages'][-1]['content'][1]['text']
        body['messages'][-1]['content']=body['messages'][-1]['content'][:1]
        body['messages'][2]['content']=old_context
        with self.assertRaises(AssertionError):current_profile_evidence(body)

    def test_old_user_context_cannot_supply_current_evidence(self):
        body=request([{'id':'old','version':'v0','text':'、'.join(GAMES)}])
        body['messages'].append({'role':'user','content':'本轮资料正文核验：我平时玩哪些游戏？'})
        with self.assertRaises(AssertionError):current_profile_evidence(body)

    def test_duplicate_system_surface_fails_even_with_valid_current_body(self):
        body=request([{'id':'current','version':'v1','text':'、'.join(GAMES)}])
        body['messages'][0]['content']+=body['messages'][-1]['content'][1]['text']
        with self.assertRaisesRegex(AssertionError,'must not remain in system'):
            current_profile_evidence(body)

    def test_two_current_native_contexts_are_ambiguous(self):
        body=request([])
        body['messages'][-1]['content'].append(dict(body['messages'][-1]['content'][1]))
        with self.assertRaisesRegex(AssertionError,'exactly one current native'):
            current_profile_evidence(body)

    def test_profile_marker_outside_native_context_is_rejected(self):
        body=request([])
        body['messages'][-1]['content'][1]['text']='<MemoryCompanion-Context>core only</MemoryCompanion-Context>'
        body['messages'][-1]['content'].append({'type':'text','text':MARKER+'\n'+json.dumps({'evidence':[]})})
        with self.assertRaises(AssertionError):current_profile_evidence(body)

    def test_duplicate_profile_marker_inside_current_context_is_rejected(self):
        body=request([])
        body['messages'][-1]['content'][1]['text']=body['messages'][-1]['content'][1]['text'].replace(
            '</phase3_approved_profile>',MARKER+'\n</phase3_approved_profile>')
        with self.assertRaises(AssertionError):current_profile_evidence(body)

    def test_payload_cannot_be_read_after_native_context_closes(self):
        body=request([])
        body['messages'][-1]['content'][1]['text']=(
            '<MemoryCompanion-Context>'+MARKER+'</MemoryCompanion-Context>'+json.dumps({'evidence':[]}))
        with self.assertRaises(AssertionError):current_profile_evidence(body)

    async def test_old_answers_cannot_make_missing_current_body_pass(self):
        body=request([{'id':'synthetic-current','version':'v1','text':'这里只有阅读资料。'}])
        async def get_json():return body
        with tempfile.TemporaryDirectory() as folder:
            mock=MemoryMockLLM(Path(folder)/'transcript.jsonl')
            with self.assertRaisesRegex(AssertionError,'current profile body lacks'):
                await mock.complete(NS(headers={'Authorization':f'Bearer {KEY}'},json=get_json))
            self.assertFalse(mock.requests)

    async def test_actual_current_body_with_sixteen_failed_rows_passes(self):
        body=request([{'id':'synthetic-current','version':'v1','text':'、'.join(GAMES)}])
        body['messages'][1:-1]=failed_tool_history()
        async def get_json():return body
        with tempfile.TemporaryDirectory() as folder:
            mock=MemoryMockLLM(Path(folder)/'transcript.jsonl')
            response=await mock.complete(NS(headers={'Authorization':f'Bearer {KEY}'},json=get_json))
            answer=json.loads(response.text)['choices'][0]['message']['content']
            self.assertTrue(all(g in answer for g in GAMES))
            self.assertEqual(len(mock.requests),1)

    def test_sixteen_old_rows_are_paired_and_contain_no_current_answer(self):
        rows=failed_tool_history()
        self.assertEqual(len(rows),16)
        self.assertEqual([r['role'] for r in rows],['user','assistant','tool','assistant']*4)
        for index in range(0,16,4):
            self.assertEqual(rows[index+1]['tool_calls'][0]['id'],rows[index+2]['tool_call_id'])
        self.assertTrue(all(OLD_FAILURE_MARKER in str(r['content']) for r in rows))
        self.assertFalse(any(g in json.dumps(rows,ensure_ascii=False) for g in GAMES))


if __name__=='__main__':unittest.main()
