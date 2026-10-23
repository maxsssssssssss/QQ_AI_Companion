"""Synthetic input-cohort tests; no real QQ or model requests."""
import asyncio
from pathlib import Path
import tempfile
import unittest
from integrations.phase1_guard.store import GuardStore
from integrations.phase1_guard.turns import TurnCoordinator


class TurnTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=GuardStore(Path(self.temp.name)/'guard.sqlite3')
        self.store.enable_phase3()
        self.turns=TurnCoordinator(self.store,window=.025,max_wait=.06,max_messages=8)
        self.count=0

    def tearDown(self):
        self.store.close(); self.temp.cleanup()

    def add(self,text,session='p:FriendMessage:10001',**kwargs):
        self.count+=1
        key=('p',session,str(self.count))
        self.store.claim(*key,'10001',text,[])
        turn, superseded=self.turns.observe(key,text,**kwargs)
        return key,turn,superseded

    async def test_three_inputs_one_full_ordered_turn_no_text_dedup(self):
        k,t,_=self.add('第一条'+('长'*400))
        pending=asyncio.create_task(self.turns.wait(k))
        await asyncio.sleep(.003)
        k2,_,_=self.add('补充')
        k3,_,_=self.add('补充')
        self.assertIsNone(await self.turns.wait(k2))
        self.assertIsNone(await self.turns.wait(k3))
        result=await pending
        self.assertEqual(result.source_ids,[k[2],k2[2],k3[2]])
        self.assertEqual(result.texts,['第一条'+('长'*400),'补充','补充'])
        self.assertEqual(len(self.store.snapshot()['inbound']),3)
        self.assertTrue(self.turns.generating(t.turn_id))

    async def test_generating_followup_supersedes_and_carries_sources(self):
        k,t,_=self.add('查一下游戏更新')
        await self.turns.wait(k); self.turns.generating(t.turn_id)
        k2,t2,superseded=self.add('我是说国服')
        self.assertTrue(superseded)
        self.assertFalse(self.turns.is_current(t.turn_id))
        self.assertEqual(t2.source_ids,[k[2],k2[2]])
        self.assertEqual(t2.predecessor,t.turn_id)
        self.assertIs(await self.turns.wait(k2),t2)

    async def test_resolved_and_cancel_request_invalidates_old_output(self):
        for correction in ('已经解决了','别搜了'):
            k,t,_=self.add('帮我看看')
            await self.turns.wait(k); self.turns.generating(t.turn_id)
            k2,t2,old=self.add(correction)
            self.assertTrue(old)
            self.assertFalse(self.turns.is_current(t.turn_id))
            self.assertEqual(t2.texts[-1],correction)
            await self.turns.wait(k2)
            self.turns.complete(t2.turn_id)

    async def test_independent_sessions_and_completed_turns_are_isolated(self):
        k1,t1,_=self.add('A')
        k2,t2,_=self.add('B',session='p:FriendMessage:10002')
        await asyncio.gather(self.turns.wait(k1),self.turns.wait(k2))
        self.assertNotEqual(t1.turn_id,t2.turn_id)
        self.turns.complete(t1.turn_id)
        k3,t3,_=self.add('新问题')
        self.assertEqual(t3.source_ids,[k3[2]])
        self.assertEqual(t2.texts,['B'])

    async def test_max_wait_caps_sliding_window(self):
        k,t,_=self.add('1')
        start=asyncio.get_running_loop().time()
        task=asyncio.create_task(self.turns.wait(k))
        for _ in range(4):
            await asyncio.sleep(.01)
            self.add('更多')
        await task
        self.assertLess(asyncio.get_running_loop().time()-start,.4)
        self.assertIn(t.phase,{'ready','superseded'})
        self.assertLessEqual(t.created+self.turns.max_wait, t.updated+self.turns.window+.06)

    async def test_max_messages_caps_collection_and_media_does_not_wait(self):
        self.turns.max_messages=2
        k,t,_=self.add('1'); self.add('2')
        self.assertIs(await self.turns.wait(k),t)
        self.turns.complete(t.turn_id)
        k,t,_=self.add('[image]',groupable=False)
        self.assertIs(await self.turns.wait(k),t)

    async def test_duplicate_identity_does_not_extend_cohort(self):
        k,t,_=self.add('1')
        t2,old=self.turns.observe(k,'changed')
        self.assertIs(t,t2)
        self.assertEqual(t.texts,['1'])

    async def test_first_prepared_event_claims_waiter_without_reordering_sources(self):
        first,turn,_=self.add('[先到图片]')
        second,_,_=self.add('后到但已准备好的文字')
        selected=await self.turns.wait(second,claim_leadership=True)
        self.assertIs(selected,turn)
        self.assertEqual(turn.leader_id,second[2])
        self.assertEqual(turn.source_ids,[first[2],second[2]])
        self.assertEqual(turn.texts,['[先到图片]','后到但已准备好的文字'])
        self.assertIsNone(await self.turns.wait(first,claim_leadership=True))

    async def test_cancelled_claimed_waiter_finishes_turn_and_new_input_recovers(self):
        first,old,_=self.add('会被取消的收集轮')
        task=asyncio.create_task(self.turns.wait(first,claim_leadership=True))
        await asyncio.sleep(.001)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(old.phase,'cancelled')
        self.assertIsNone(old.waiter_id)
        second,new,_=self.add('新的合法输入')
        self.assertIs(await self.turns.wait(second,claim_leadership=True),new)
        self.assertEqual(new.source_ids,[second[2]])
        self.assertEqual(len(self.store.snapshot()['inbound']),2)

    async def test_only_one_prepared_event_claims_a_collecting_turn(self):
        keys=[self.add(str(i))[0] for i in range(3)]
        results=await asyncio.gather(*(self.turns.wait(key,claim_leadership=True) for key in reversed(keys)))
        self.assertEqual(sum(item is not None for item in results),1)
        self.assertEqual(results[0].leader_id,keys[-1][2])
        self.assertEqual(results[0].source_ids,[key[2] for key in keys])

    async def test_non_groupable_interrupts_collecting_without_two_current_turns(self):
        key,old,_=self.add('先说明问题')
        pending=asyncio.create_task(self.turns.wait(key))
        await asyncio.sleep(.001)
        image,new,superseded=self.add('[图片]',groupable=False)
        self.assertTrue(superseded)
        self.assertIsNone(await pending)
        self.assertFalse(self.turns.is_current(old.turn_id))
        self.assertIs(await self.turns.wait(image),new)
        self.assertEqual(new.source_ids,[key[2],image[2]])
        self.assertEqual(new.texts,['先说明问题','[图片]'])
        self.assertEqual(self.store.get_turn(old.turn_id)['state'],'superseded')

    async def test_current_identity_is_required_even_if_stale_phase_is_ready(self):
        key,old,_=self.add('旧轮')
        await self.turns.wait(key)
        key2,new,_=self.add('新轮')
        old.phase='ready'  # an obsolete callback cannot revive the old event
        self.assertFalse(self.turns.is_current(old.turn_id))
        self.assertFalse(self.turns.generating(old.turn_id))
        self.assertTrue(self.turns.is_current(new.turn_id))

    async def test_finite_burst_supersedes_once_then_recovers_with_all_sources(self):
        key,old,_=self.add('原问题')
        await self.turns.wait(key);self.turns.generating(old.turn_id)
        successors=[];sources=[key[2]]
        for i in range(20):
            incoming,turn,superseded=self.add('补充'+str(i))
            successors.append((incoming,turn,superseded));sources.append(incoming[2])
        # No await in admission: all already-arrived input is one cohort.
        # max_messages wakes the leader, but must not drop admitted messages.
        self.assertEqual(sum(int(item[2]) for item in successors),1)
        self.assertEqual(len({item[1].turn_id for item in successors}),1)
        new=await asyncio.wait_for(self.turns.wait(successors[0][0]),.2)
        self.assertEqual(new.source_ids,sources)
        self.assertEqual(self.store.source_ids(new.turn_id),sources)
        self.assertTrue(self.turns.generating(new.turn_id))
        self.turns.complete(new.turn_id)
        next_key,next_turn,_=self.add('突发结束后的新问题')
        self.assertEqual(next_turn.source_ids,[next_key[2]])
        self.assertIs(await self.turns.wait(next_key),next_turn)

    async def test_finite_generation_interjections_only_latest_can_reply(self):
        turns=[]
        for i in range(4):
            key,turn,_=self.add('逐次补充'+str(i))
            await self.turns.wait(key);self.turns.generating(turn.turn_id)
            turns.append(turn)
        self.assertEqual([self.turns.is_current(t.turn_id) for t in turns],[False,False,False,True])
        self.assertEqual(turns[-1].texts,['逐次补充'+str(i) for i in range(4)])
        self.turns.complete(turns[-1].turn_id)
        key,new,_=self.add('下一轮')
        self.assertEqual(new.texts,['下一轮'])

    async def test_cancelled_turn_cannot_generate_or_send(self):
        k,t,_=self.add('1')
        self.turns.cancel(t.turn_id)
        self.assertIsNone(await self.turns.wait(k))
        self.assertFalse(self.turns.generating(t.turn_id))

    async def test_invalid_engineering_bounds_rejected(self):
        for options in ({'window':float('nan')},{'window':4,'max_wait':3},{'max_messages':0}):
            with self.assertRaises(ValueError): TurnCoordinator(self.store,**options)

    async def test_abandoned_runner_expires_without_deleting_source_archive(self):
        now=[100.0]
        self.turns=TurnCoordinator(self.store,window=0,max_wait=0,clock=lambda:now[0])
        key,turn,_=self.add('原始输入')
        await self.turns.wait(key);self.turns.generating(turn.turn_id)
        now[0]+=901
        self.assertFalse(self.turns.is_current(turn.turn_id))
        self.assertEqual(self.turns.by_id,{})
        self.assertEqual(self.turns.message_turn,{})
        self.assertEqual(self.store.snapshot()['inbound'][0]['text'],'原始输入')
        self.assertEqual(self.store.get_turn(turn.turn_id)['state'],'cancelled')

if __name__ == '__main__': unittest.main()
