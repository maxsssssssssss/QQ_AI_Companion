"""Bound model-facing provenance size without losing native facts or sources."""
import copy
import hashlib
import json
import unittest

from integrations.phase3_runtime.profile_recall import Recall, project_record_for_model


def record(text='用户自述（游戏兴趣）：这是合成正文。', sources=None):
    return {'id':'mem_synthetic_profile','version':'a'*64,'text':text,
        'confidence':'user_confirmed','layer':'L2',
        'source_refs':['synthetic-section:1'] if sources is None else sources}


class ProfileRenderTests(unittest.TestCase):
    def payload(self, recall):
        rendered=recall.render()
        return json.loads(rendered[rendered.index('{'):])

    def test_full_body_and_native_identity_precede_compact_provenance(self):
        body='用户自述（合成资料）：'+('中英文 Emoji🙂 与换行\n'*280)+'末尾内容必须完整。'
        original=record(body,['synthetic-source:'+str(i)+'x'*90 for i in range(33)])
        projected=project_record_for_model(original)
        self.assertEqual(projected['text'],body)
        for key in ('id','version','confidence','layer'):
            self.assertEqual(projected[key],original[key])
        self.assertEqual(list(projected),['text','id','version','confidence','layer','source_count','source_refs_sha256'])
        self.assertNotIn('source_refs',projected)
        self.assertEqual(projected['source_count'],33)

    def test_render_does_not_mutate_original_source_list_or_records(self):
        original=record(sources=['synthetic-A','synthetic-B'])
        records=[original];before=copy.deepcopy(records)
        recall=Recall(status='found',coverage='complete_approved_topics',records=records)
        refs=original['source_refs']
        first=recall.render();second=recall.render()
        self.assertEqual(first,second)
        self.assertEqual(records,before)
        self.assertIs(recall.records,records)
        self.assertIs(recall.records[0]['source_refs'],refs)

    def test_thousands_of_source_locators_do_not_expand_model_payload(self):
        one=Recall(records=[record(sources=['synthetic-source:0'])])
        many=Recall(records=[record(sources=['synthetic-source:'+str(i)+'x'*220 for i in range(2000)])])
        self.assertLessEqual(len(many.render())-len(one.render()),4)
        payload=self.payload(many)['evidence'][0]
        self.assertEqual(payload['source_count'],2000)
        self.assertNotIn('synthetic-source:',many.render())
        self.assertEqual(payload['text'],many.records[0]['text'])

    def test_digest_stably_identifies_exact_ordered_sources(self):
        refs=['synthetic-第一段:1','synthetic-second:2']
        item=record(sources=refs)
        expected=hashlib.sha256(json.dumps(refs,ensure_ascii=False,sort_keys=True,
            separators=(',',':')).encode('utf-8')).hexdigest()
        self.assertEqual(project_record_for_model(item)['source_refs_sha256'],expected)
        self.assertEqual(project_record_for_model(copy.deepcopy(item))['source_refs_sha256'],expected)
        changed=record(sources=list(reversed(refs)))
        self.assertNotEqual(project_record_for_model(changed)['source_refs_sha256'],expected)
        self.assertEqual(project_record_for_model(changed)['text'],item['text'])

    def test_empty_evidence_and_missing_sources_keep_status_contract(self):
        recall=Recall(status='unavailable',coverage='partial',relation='longest',temporal='recent')
        payload=self.payload(recall)
        self.assertEqual((payload['status'],payload['coverage'],payload['evidence']),('unavailable','partial',[]))
        self.assertEqual((payload['relation'],payload['temporal']),('longest','recent'))
        item=record();item.pop('source_refs')
        self.assertEqual(project_record_for_model(item)['source_count'],0)
        self.assertNotIn('source_refs',item)


if __name__=='__main__':unittest.main()
