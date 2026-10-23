"""Actual native import/filter/revision reads, without real owner facts."""
import asyncio
import copy
from dataclasses import replace
import hashlib
import importlib
import json
import unittest
from unittest.mock import patch
import test_phase3_memory_native as native
from integrations.phase3_memory.profile_release import ProfileRelease
from integrations.phase3_runtime.profile_recall import recall_profile, intent
from phase3_profile_fixture import manifest, approval, GAMES

class EvidenceTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = native.NativeMemoryTests.asyncSetUp
    asyncTearDown = native.NativeMemoryTests.asyncTearDown
    event = native.NativeMemoryTests.event
    async def imported(self, modify=None):
        m=manifest(self.ctx)
        if modify: modify(m)
        release=ProfileRelease(self.service,self.ctx,self.path/'import'/'ledger.sqlite3')
        await release.apply(m,approval(m),apply=True)
        return m,release
    async def query(self,text,**kw):
        return await recall_profile(self.service,self.ctx,self.event(text),query=text,**kw)
    async def test_multiple_topics_synonyms_and_relations(self):
        await self.imported()
        for q,expected in [('我平时玩哪些游戏？',GAMES),('我的口味是什么？',('南瓜汤','芹菜')),('我看什么小说？',('海底两万里',)),('我的游戏和饮食资料有哪些？',(*GAMES,'南瓜汤'))]:
            r=await self.query(q)
            self.assertEqual(r.status,'found',r)
            self.assertTrue(all(x in str(r.records) for x in expected))
        self.assertEqual((await self.query('我最喜欢什么游戏？')).relation,'favorite')
        self.assertEqual((await self.query('我玩得最久的游戏？')).relation,'longest')
        self.assertEqual((await self.query('我昨天玩哪些游戏？')).temporal,'recent')
        self.assertFalse(self.provider.calls)
    async def test_greeting_not_additional_lookup(self):
        self.assertIsNone(await self.query('你好，今天怎么样'))
        self.assertIsNone(intent('游戏发布了新的更新'))
    async def test_render_compacts_provenance_but_native_reference_and_sources_stay_valid(self):
        sources=['synthetic-section:'+str(i)+':'+('x'*70) for i in range(33)]
        def change(m):m['entries'][0]['source_refs']=sources
        await self.imported(change)
        recall=await self.query('我平时玩哪些游戏？')
        original=copy.deepcopy(recall.records)
        rendered=recall.render();payload=json.loads(rendered[rendered.index('{'):])
        projected=payload['evidence'][0]
        native_ref=importlib.import_module(type(self.service).__module__.rsplit('.',1)[0]+'.memory_revision').memory_ref
        stored=await self.service.store.get_memory(projected['id'])
        self.assertEqual(native_ref(stored),{'id':projected['id'],'version':projected['version']})
        self.assertEqual(projected['text'],stored.content)
        self.assertEqual(stored.metadata['source_refs'],sources)
        self.assertEqual(projected['source_count'],len(sources))
        expected=hashlib.sha256(json.dumps(sources,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        self.assertEqual(projected['source_refs_sha256'],expected)
        self.assertEqual(recall.records,original)
        self.assertNotIn('synthetic-section:',rendered)
        self.assertFalse(self.provider.calls)
    async def test_complete_long_record_and_partial_budget(self):
        def change(m):m['entries'][0]['text']='用户自述（游戏兴趣）：'+ '；'.join('合成游戏'+str(n) for n in range(180))
        await self.imported(change)
        r=await self.query('我玩过哪些游戏？',page_size=2)
        self.assertEqual(r.status,'found');self.assertIn('合成游戏179',r.records[0]['text'])
        self.assertGreater(len(r.records[0]['text']),320)
        limited=await self.query('我的游戏和饮食资料有哪些？',max_chars=100)
        self.assertEqual(limited.status,'partial');self.assertEqual(limited.reason,'content_budget')
        self.assertTrue(limited.records)
    async def test_scope_revocation_and_current_revision(self):
        m,release=await self.imported()
        r=await self.query('我的游戏有哪些？');self.assertTrue(r.records)
        self.ctx=replace(self.ctx,bot_id='another-bot')
        self.assertFalse((await self.query('我的游戏有哪些？')).records)
        self.ctx=replace(self.ctx,bot_id='synthetic-bot')
        g=approval(m);g['withdraw_batch']=True
        await release.withdraw(m,g,apply=True)
        self.assertEqual((await self.query('我的游戏有哪些？')).status,'not_found')
    async def test_unavailable_distinct_from_absent(self):
        await self.imported()
        async def slow(**kw):await asyncio.sleep(.2)
        with patch.object(self.service.store,'list_memories',slow):
            r=await self.query('我的游戏有哪些？',timeout=.01)
        self.assertEqual((r.status,r.reason),('unavailable','timeout'))
        await self.co.pause(self.ctx)
        r=await self.query('我的游戏有哪些？')
        self.assertEqual((r.status,r.reason),('unavailable','recall_disabled'))
    async def test_changed_during_pagination_not_published(self):
        await self.imported()
        original=self.service.store.memory_revision
        count=0
        async def revision():
            nonlocal count
            count+=1
            return str(await original())+":"+str(count)
        with patch.object(self.service.store,'memory_revision',revision):r=await self.query('我的游戏有哪些？')
        self.assertEqual(r.reason,'revision_changed');self.assertFalse(r.records)
    async def test_bounded_pages_keep_reliable_partial_and_exclude_l3(self):
        def change(m):
            original=m['entries'][0]
            m['entries']=[{**original,'id':'g'+str(i),
                'text':'用户自述（游戏兴趣）：合成游戏'+str(i),
                'source_refs':['synthetic-page:'+str(i)]} for i in range(9)]
        await self.imported(change)
        full=await self.query('我玩过哪些游戏？',page_size=3,max_pages=4)
        self.assertEqual((full.status,len(full.records)),('found',9))
        partial=await self.query('我玩过哪些游戏？',page_size=3,max_pages=2)
        self.assertEqual((partial.status,len(partial.records),partial.reason),('partial',6,'page_budget'))
        original=self.service.store.list_memories
        async def later_page(**kw):
            if kw.get('offset',0)>0:await asyncio.sleep(2)
            return await original(**kw)
        with patch.object(self.service.store,'list_memories',later_page):
            timed=await self.query('我玩过哪些游戏？',page_size=3,timeout=1.0)
        self.assertEqual((timed.status,len(timed.records),timed.reason),('partial',3,'timeout'))
        record=await self.service.store.get_memory(full.records[0]['id'])
        await self.service.store.update_memory_payload(record.id,metadata={**record.metadata,'profile_layer':'L3'})
        remaining=await self.query('我玩过哪些游戏？')
        self.assertEqual(len(remaining.records),8)
    async def test_optional_intent_failure_is_unavailable_not_chat_exception(self):
        with patch.object(self.service.intent_builder,'build',side_effect=RuntimeError('synthetic')):
            self.assertIsNone(await self.query('你好'))
            result=await self.query('我平时玩哪些游戏？')
        self.assertEqual((result.status,result.reason),('unavailable','intent_unavailable'))
    async def test_authorized_prefetch_does_not_require_tool_call_availability(self):
        from types import SimpleNamespace as NS
        from integrations.phase3_runtime.main import Phase3Runtime
        self.ctx=replace(self.ctx,platform='aiocqhttp')
        await self.imported()
        runtime=Phase3Runtime(self.host,{'enabled':True,'persona':True,'natural_chat':True,
            'memory':True,'memory_recall_guidance':True})
        runtime.ready=True;runtime.admin_id=self.ctx.user_id;runtime.platform_id='fixture';runtime.memory=self.co
        self.co.remember_context(self.ctx)
        event=self.event('我平时玩哪些游戏？','local-only')
        event.is_private_chat=lambda:True;event.get_platform_name=lambda:'aiocqhttp'
        event.get_platform_id=lambda:'fixture';event.get_extra=lambda key:None;event.set_extra=lambda *args:None
        req=NS(system_prompt='V0.1',func_tool=None)
        await runtime.guide_memory_recall(event,req)
        actual='\n'.join(part.text for part in req.extra_user_content_parts)
        self.assertTrue(all(x in actual for x in GAMES))
        self.assertNotIn('[PHASE3_PROFILE_EVIDENCE_V2]',req.system_prompt)
        self.assertTrue(req.memory_companion_injection_state['injected'])
        self.assertNotIn('[PHASE3_MEMORY_RECALL_V1]',req.system_prompt)
        self.assertFalse(self.provider.calls)
    async def test_topic_directory_await_precedes_final_native_publication(self):
        from types import SimpleNamespace as NS
        from integrations.phase3_runtime.main import Phase3Runtime
        self.ctx=replace(self.ctx,platform='aiocqhttp')
        m,release=await self.imported()
        runtime=Phase3Runtime(self.host,{'enabled':True,'persona':True,'natural_chat':True,
            'memory':True,'memory_recall_guidance':True})
        runtime.ready=True;runtime.admin_id=self.ctx.user_id;runtime.platform_id='fixture';runtime.memory=self.co
        self.co.remember_context(self.ctx)
        event=self.event('我平时玩哪些游戏？','topic-await')
        event.is_private_chat=lambda:True;event.get_platform_name=lambda:'aiocqhttp'
        event.get_platform_id=lambda:'fixture';event.get_extra=lambda key:None;event.set_extra=lambda *args:None
        req=NS(system_prompt='V0.1',func_tool=NS(tools=[NS(name='memory_companion_navigate',active=True)]))
        async def withdraw_in_directory(*args):
            grant=approval(m);grant['withdraw_batch']=True
            await release.withdraw(m,grant,apply=True)
            return []
        with patch('integrations.phase3_runtime.main.available_topics',withdraw_in_directory):
            await runtime.guide_memory_recall(event,req)
        actual='\n'.join(part.text for part in req.extra_user_content_parts)
        self.assertFalse(any(x in actual for x in GAMES))
        self.assertIn('"status": "not_found"',actual)
        self.assertNotIn('[PHASE3_PROFILE_EVIDENCE_V2]',req.system_prompt)
    async def test_l1_promoted_same_id_native_core_and_withdrawal(self):
        from integrations.phase3_memory.core_profile import promote_l1
        def change(m):m['entries'][3]['layer']='L1'
        m,release=await self.imported(change)
        result=await promote_l1(release,m,approval(m),apply=True)
        self.assertEqual(result['count'],1)
        core=await self.service.core_memories_for_context(self.ctx)
        self.assertEqual(len(core),1);self.assertEqual(core[0].id,result['changed_ids'][0])
        self.assertEqual(core[0].content,m['entries'][3]['text'])
        again=await promote_l1(release,m,approval(m),apply=True)
        self.assertFalse(again['changed_ids'])
        g=approval(m);g['withdraw_batch']=True
        await release.withdraw(m,g,apply=True)
        self.assertFalse(await self.service.core_memories_for_context(self.ctx))
