"""Full native publication/permission boundary; only synthetic owner records."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import importlib
import hashlib
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch, Mock

import test_phase3_memory_native as native
from phase3_profile_fixture import manifest, approval, GAMES, failed_tool_history
from integrations.phase3_memory.profile_release import ProfileRelease
from integrations.phase3_memory.core_profile import promote_l1
from integrations.phase3_runtime.profile_recall import Recall, recall_profile
from integrations.phase3_runtime.profile_publication import publish_profile, PROFILE_OPEN, STRATEGY


class PublicationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = native.NativeMemoryTests.asyncSetUp
    asyncTearDown = native.NativeMemoryTests.asyncTearDown
    event = native.NativeMemoryTests.event

    def request(self):
        return NS(prompt='我平时玩哪些游戏？', system_prompt='V0.1 人格',
            contexts=failed_tool_history(), extra_user_content_parts=[], func_tool=object())

    def body(self, req):
        return '\n'.join(part.text for part in req.extra_user_content_parts)

    async def prepared(self, *, optional=False):
        m=manifest(self.ctx);m['entries'][3]['layer']='L1'
        release=ProfileRelease(self.service,self.ctx,self.path/'profile-ledger.sqlite3')
        await release.apply(m,approval(m),apply=True)
        await promote_l1(release,m,approval(m),apply=True)
        event=self.event('我平时玩哪些游戏？');req=self.request()
        ctx=replace(self.ctx,message_id='profile-turn',message_text=event.message_str)
        core=await self.service.core_memories_for_context(ctx)
        injection=self.service.injection.compose(ctx,[],max_chars=5000,core_memories=core,
            intent_context='合成本轮意图提示' if optional else '')
        self.assertTrue(await self.service._publish_reply_memory_context(ctx,req,event=event,
            injection=injection,slot_map={},strategy_id='test.native_initial'))
        evidence=await recall_profile(self.service,ctx,event,query=event.message_str)
        self.assertTrue(evidence.records)
        return ctx,event,req,evidence,m,release,core

    async def test_preserves_l1_history_tools_and_native_receipts_once(self):
        ctx,event,req,evidence,_,_,core=await self.prepared()
        before=deepcopy(req.contexts);tools=req.func_tool
        result=await publish_profile(self.service,ctx,event,req,evidence)
        self.assertTrue(result.published,result)
        self.assertEqual(req.contexts,before);self.assertIs(req.func_tool,tools)
        self.assertEqual(req.system_prompt,'V0.1 人格')
        text=self.body(req)
        self.assertIn(core[0].content,text)
        self.assertTrue(all(g in text for g in GAMES))
        refs=req.memory_companion_injection_state['memory_refs']
        self.assertEqual({r['id'] for r in refs},{core[0].id,evidence.records[0]['id']})
        snap=req.memory_companion_continuity_snapshot
        self.assertEqual(snap['coverage']['strategy_id'],STRATEGY)
        self.assertEqual(snap['coverage']['available_history_items'],16)
        self.assertTrue(snap['coverage']['memory_refs_revalidated'])
        self.assertEqual(snap['current_turn_digest'],hashlib.sha256(event.message_str.encode()).hexdigest())
        self.assertIn('message:'+event.message_obj.message_id,snap['immediate_refs'])
        self.assertEqual(event.memory_companion_injection_state['memory_refs'],refs)
        again=await publish_profile(self.service,ctx,event,req,evidence)
        self.assertTrue(again.published,again)
        self.assertEqual(self.body(req).count(PROFILE_OPEN),1)
        self.assertEqual(self.body(req).count('<MemoryCompanion-Context>'),1)
        self.assertFalse(self.provider.calls)

    async def test_revoked_profile_cannot_restore_old_native_context(self):
        ctx,event,req,evidence,m,release,_=await self.prepared()
        grant=approval(m);grant['withdraw_batch']=True
        await release.withdraw(m,grant,apply=True)
        result=await publish_profile(self.service,ctx,event,req,evidence)
        self.assertFalse(result.published)
        self.assertEqual(self.body(req),'')
        self.assertFalse(req.memory_companion_injection_state['injected'])
        self.assertEqual(len(req.contexts),16)

    async def test_old_native_ref_revision_change_cannot_be_restored(self):
        ctx,event,req,evidence,_,_,core=await self.prepared()
        await self.service.store.update_memory_payload(core[0].id,content='用户自述（交流）：合成新纠错。')
        result=await publish_profile(self.service,ctx,event,req,evidence)
        self.assertFalse(result.published)
        self.assertEqual(self.body(req),'')
        self.assertEqual(req.prompt,'我平时玩哪些游戏？')

    async def test_native_acl_validation_error_fails_closed_without_stopping_chat(self):
        ctx,event,req,evidence,*_=await self.prepared()
        async def failed(*args,**kwargs):raise RuntimeError('synthetic ACL unavailable')
        with patch.object(self.service,'_validate_reply_memory_refs',failed):
            result=await publish_profile(self.service,ctx,event,req,evidence)
        self.assertFalse(result.published);self.assertEqual(self.body(req),'')
        self.assertEqual(len(req.contexts),16)

    async def test_timeout_and_cancel_remove_unvalidated_current_only(self):
        for cancel in (False,True):
            ctx,event,req,evidence,*_=await self.prepared()
            started=asyncio.Event()
            async def slow(*args,**kwargs):
                started.set();await asyncio.Event().wait()
            with patch.object(self.service,'_validate_reply_memory_refs',slow):
                task=asyncio.create_task(publish_profile(self.service,ctx,event,req,evidence,
                    timeout=2 if cancel else .5))
                await asyncio.wait_for(started.wait(),2)
                if cancel:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):await task
                else:
                    result=await task
                    self.assertEqual(result.reason,'publication_timeout')
            self.assertEqual(self.body(req),'');self.assertEqual(len(req.contexts),16)

    async def test_statistics_failure_keeps_confirmed_publish_and_native_role_boundary(self):
        ctx,event,req,evidence,*_=await self.prepared(optional=True)
        module=importlib.import_module(type(self.service).__module__.rsplit('.',1)[0]+'.role_input_sources')
        owner=module.RoleInputSources(self.service)
        observer=Mock(wraps=owner.published)
        owner.published=observer
        async def failed(*args,**kwargs):raise RuntimeError('synthetic statistics unavailable')
        with patch.object(self.service,'_role_input_sources',owner,create=True),patch.object(self.service.store,'mark_injected',failed):
            result=await publish_profile(self.service,ctx,event,req,evidence)
        self.assertTrue(result.published,result);self.assertTrue(result.statistics_deferred)
        self.assertEqual(observer.call_count,1)
        injection=observer.call_args.args[3]
        self.assertFalse(injection.role_input_complete)
        self.assertTrue(all(g in self.body(req) for g in GAMES))
        self.assertTrue(req.memory_companion_injection_state['injected'])

    async def test_partial_unavailable_and_scope_pause_keep_honest_status(self):
        ctx,event,req,evidence,*_=await self.prepared()
        partial=replace(evidence,status='partial',coverage='partial',reason='page_budget')
        self.assertTrue((await publish_profile(self.service,ctx,event,req,partial)).published)
        self.assertIn('"status": "partial"',self.body(req))
        unavailable=Recall(status='unavailable',reason='timeout')
        self.assertTrue((await publish_profile(self.service,ctx,event,req,unavailable)).published)
        self.assertIn('"status": "unavailable"',self.body(req))
        self.assertNotIn(GAMES[0],self.body(req))
        self.assertNotIn(evidence.records[0]['id'],req.memory_companion_injection_state['injected_memory_ids'])
        await self.co.pause(ctx)
        self.assertFalse((await publish_profile(self.service,ctx,event,req,evidence)).published)
        self.assertEqual(self.body(req),'')

    async def test_correction_during_post_append_statistics_is_not_published(self):
        ctx,event,req,evidence,*_=await self.prepared()
        original=self.service.store.mark_injected
        async def correct_then_mark(ids):
            await self.service.store.update_memory_payload(evidence.records[0]['id'],
                content='用户自述（游戏兴趣）：这是合成纠错后的新正文。')
            await original(ids)
        with patch.object(self.service.store,'mark_injected',correct_then_mark):
            result=await publish_profile(self.service,ctx,event,req,evidence)
        self.assertFalse(result.published)
        self.assertEqual(self.body(req),'')
        self.assertFalse(req.memory_companion_injection_state['injected'])
        self.assertEqual(len(req.contexts),16)

    async def test_missing_or_conflicting_continuity_never_grants_stale_facts(self):
        ctx,event,req,evidence,*_=await self.prepared()
        req.memory_companion_continuity_snapshot['continuity_refs']=[]
        result=await publish_profile(self.service,ctx,event,req,evidence)
        self.assertEqual(result.reason,'native_continuity_mismatch')
        self.assertEqual(self.body(req),'')

    async def test_historical_markers_never_delete_original_rows(self):
        ctx,event,req,evidence,*_=await self.prepared()
        req.contexts.append({'role':'user','content':'合成引用 <MemoryCompanion-Context>原文</MemoryCompanion-Context>'})
        before=deepcopy(req.contexts)
        result=await publish_profile(self.service,ctx,event,req,evidence)
        self.assertEqual(result.reason,'historical_context_marker')
        self.assertEqual(req.contexts,before);self.assertEqual(self.body(req),'')

    async def test_duplicate_blocks_inside_one_part_are_rejected(self):
        ctx,event,req,evidence,*_=await self.prepared()
        req.extra_user_content_parts[0].text += self.body(req)
        result=await publish_profile(self.service,ctx,event,req,evidence)
        self.assertEqual(result.reason,'ambiguous_native_context')
        self.assertEqual(self.body(req),'');self.assertEqual(len(req.contexts),16)


if __name__=='__main__':unittest.main()
