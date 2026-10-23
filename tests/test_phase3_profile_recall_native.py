"""Pinned native retrieval after reviewed import, not a fake recall response."""
import json
from types import SimpleNamespace as NS
import unittest

import test_phase3_memory_native as native
from integrations.phase3_memory.profile_release import ProfileRelease
from integrations.phase3_runtime.recall import MARKER, append_guidance, available_topics
from phase3_profile_fixture import manifest, approval, GAMES


class ProfileRecallTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = native.NativeMemoryTests.asyncSetUp
    asyncTearDown = native.NativeMemoryTests.asyncTearDown
    event = native.NativeMemoryTests.event

    async def imported(self):
        m=manifest(self.ctx);release=ProfileRelease(self.service,self.ctx,self.path/'import'/'ledger.sqlite3')
        await release.apply(m,approval(m),apply=True)
        return m,release

    async def test_navigate_reads_import_without_requiring_chat_source(self):
        await self.imported()
        event=self.event('我平时玩哪些游戏？只根据你记得的资料回答，不要上网','games')
        result=await self.service.query_for_model(event,'navigate',action='search',query='游戏',limit=6)
        r=result.get('result',result)
        self.assertTrue(r.get('ok'),r)
        text='\n'.join(e.get('content','') for e in r.get('evidence',[]))
        self.assertTrue(all(g in text for g in GAMES),r)
        self.assertEqual(self.provider.calls,[])
        # Imported external facts are not fake original chat messages.
        rows=await self.service.store.recent_timeline(limit=100,session_id=self.ctx.session_id)
        self.assertFalse(any(any(g in row['content'] for g in GAMES) for row in rows))

    async def test_directory_has_only_visible_headings_and_tracks_revocation(self):
        m,release=await self.imported()
        event=self.event('游戏','directory')
        topics=await available_topics(self.service,self.ctx,event)
        self.assertEqual(set(topics),{'游戏兴趣','阅读','饮食','交流'})
        self.assertFalse(any(g in str(topics) for g in GAMES))
        grant=approval(m);grant['withdraw_batch']=True
        await release.withdraw(m,grant,apply=True)
        self.assertEqual(await available_topics(self.service,self.ctx,event),[])

    async def test_revoke_then_navigate_cannot_return_stale_import(self):
        m,release=await self.imported()
        await self.service.query_for_model(self.event('游戏','before'),'navigate',action='search',query='游戏')
        grant=approval(m);grant['withdraw_batch']=True
        await release.withdraw(m,grant,apply=True)
        result=await self.service.query_for_model(self.event('游戏','after'),'navigate',action='search',query='游戏')
        self.assertFalse(any(g in json.dumps(result,ensure_ascii=False) for g in GAMES))

    async def test_cross_bot_and_paused_recall_are_not_bypassed(self):
        await self.imported()
        event=self.event('游戏','other');event.get_self_id=lambda:'unrelated-bot'
        r=await self.service.query_for_model(event,'navigate',action='search',query='游戏')
        self.assertFalse(any(g in json.dumps(r,ensure_ascii=False) for g in GAMES))
        await self.co.pause(self.ctx)
        r=await self.service.query_for_model(self.event('游戏','paused'),'navigate',action='search',query='游戏')
        self.assertFalse(any(g in json.dumps(r,ensure_ascii=False) for g in GAMES))

    async def test_guidance_bound_to_authorized_native_availability_and_switch(self):
        from integrations.phase3_runtime.main import Phase3Runtime
        runtime=Phase3Runtime(self.host,{'enabled':True,'persona':True,'natural_chat':True,
            'memory':True,'memory_recall_guidance':True})
        runtime.ready=True;runtime.admin_id=self.ctx.user_id;runtime.platform_id='fixture';runtime.memory=self.co
        self.co.remember_context(self.ctx)
        event=self.event('哪些游戏','guidance');event.is_private_chat=lambda:True
        event.get_platform_name=lambda:'aiocqhttp';event.get_platform_id=lambda:'fixture';event.get_extra=lambda key:None
        event.set_extra=lambda *args:None
        req=NS(system_prompt='UNCHANGED_PERSONA',func_tool=NS(tools=[NS(name='memory_companion_navigate',active=True)]))
        await runtime.guide_memory_recall(event,req);await runtime.guide_memory_recall(event,req)
        self.assertEqual(req.system_prompt.count(MARKER),1)
        self.assertTrue(req.system_prompt.startswith('UNCHANGED_PERSONA'))
        for blocked in ('switch','acl','pause','tool'):
            req.system_prompt='UNCHANGED_PERSONA'
            runtime.settings['memory_recall_guidance']=blocked!='switch'
            runtime.admin_id='other' if blocked=='acl' else self.ctx.user_id
            req.func_tool.tools=[] if blocked=='tool' else [NS(name='memory_companion_navigate',active=True)]
            event.get_extra=lambda key:True if blocked=='pause' else None
            await runtime.guide_memory_recall(event,req)
            self.assertNotIn(MARKER,req.system_prompt)

    def test_guidance_does_not_enable_missing_or_disabled_tools(self):
        for tool in (None,NS(tools=[]),NS(tools=[NS(name='memory_companion_navigate',active=False)])):
            req=NS(system_prompt='V0.1',func_tool=tool)
            self.assertFalse(append_guidance(req))
            self.assertEqual(req.system_prompt,'V0.1')


if __name__=='__main__':unittest.main()
