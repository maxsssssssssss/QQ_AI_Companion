"""Native approved-profile pagination under revocation and local failures."""
import unittest
from unittest.mock import patch

import test_phase3_profile_evidence_native as evidence
from phase3_profile_fixture import approval


class ProfilePublicationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=evidence.EvidenceTests.asyncSetUp
    asyncTearDown=evidence.EvidenceTests.asyncTearDown
    event=evidence.EvidenceTests.event
    imported=evidence.EvidenceTests.imported
    query=evidence.EvidenceTests.query

    async def collection(self):
        def entries(manifest):
            template=manifest['entries'][0]
            manifest['entries']=[{**template,'id':'scope-'+str(i),
                'text':'用户自述（游戏兴趣）：权限测试游戏'+str(i),
                'source_refs':['synthetic-acl-page:'+str(i)]} for i in range(4)]
        return await self.imported(entries)

    async def test_real_withdrawal_between_pages_publishes_no_stale_facts(self):
        manifest,release=await self.collection()
        original=self.service.store.list_memories
        withdrawn=False
        async def page(**kwargs):
            nonlocal withdrawn
            if kwargs.get('offset') and not withdrawn:
                withdrawn=True
                grant=approval(manifest);grant['withdraw_batch']=True
                await release.withdraw(manifest,grant,apply=True)
            return await original(**kwargs)
        with patch.object(self.service.store,'list_memories',page):
            result=await self.query('我玩过哪些游戏？',page_size=1,max_pages=8,timeout=3)
        self.assertTrue(withdrawn)
        self.assertEqual((result.status,result.reason),('unavailable','revision_changed'))
        self.assertFalse(result.records)
        self.assertNotIn('权限测试游戏',result.render())
        self.assertFalse(self.provider.calls)

    async def test_native_pause_between_pages_cannot_publish_previously_read_content(self):
        await self.collection()
        original=self.service.store.list_memories
        paused=False
        async def page(**kwargs):
            nonlocal paused
            if kwargs.get('offset') and not paused:
                paused=True
                await self.co.pause(self.ctx)
            return await original(**kwargs)
        with patch.object(self.service.store,'list_memories',page):
            result=await self.query('我的游戏有哪些？',page_size=1,max_pages=8,timeout=3)
        self.assertTrue(paused)
        self.assertEqual(result.status,'unavailable')
        self.assertIn(result.reason,{'revision_changed','permission_changed'})
        self.assertFalse(result.records)
        self.assertNotIn('权限测试游戏',result.render())

    async def test_later_native_read_error_is_unavailable_not_absent_or_exception(self):
        await self.collection()
        original=self.service.store.list_memories
        async def page(**kwargs):
            if kwargs.get('offset'):raise RuntimeError('synthetic_local_read_failure')
            return await original(**kwargs)
        with patch.object(self.service.store,'list_memories',page):
            result=await self.query('我的游戏有哪些？',page_size=1,max_pages=8)
        self.assertEqual(result.pages,1)
        self.assertEqual((result.status,result.reason),('unavailable','native_read_error'))
        self.assertFalse(result.records)
        self.assertNotIn('synthetic_local_read_failure',result.render())
        # The optional lookup exception did not poison the original service.
        next_result=await self.query('我的游戏有哪些？')
        self.assertEqual((next_result.status,len(next_result.records)),('found',4))

    async def test_publication_permission_check_failure_never_reuses_earlier_visibility(self):
        await self.collection()
        original=self.service._retrieval_validation_engine
        def engine(**kwargs):
            instance=original(**kwargs)
            original_filter=instance.filter_visible_candidates
            async def visible(rows,ctx,**options):
                if options.get('reason')=='approved_profile_publication':
                    raise RuntimeError('synthetic_acl_unavailable')
                return await original_filter(rows,ctx,**options)
            instance.filter_visible_candidates=visible
            return instance
        with patch.object(self.service,'_retrieval_validation_engine',engine):
            result=await self.query('我的游戏有哪些？')
        self.assertEqual((result.status,result.reason),('unavailable','native_read_error'))
        self.assertFalse(result.records)
        self.assertFalse(self.provider.calls)


if __name__=='__main__':unittest.main()
