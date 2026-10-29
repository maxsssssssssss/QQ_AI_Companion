"""Specific-topic closure uses the same identity from decision through delivery.

Synthetic text only. Real existing Gate/Guard SQLite stores, no model or QQ.
"""
import json
import unittest
import uuid

import test_phase3_autonomy_bridge as fixture
from integrations.phase3_runtime.autonomy_contract import content_identity

SESSION = fixture.SESSION


def reply(topic, body):
    return '[PHASE3_PROACTIVE:send]\n[PHASE3_TOPIC:'+json.dumps({
        'topic':topic,'kind':'opinion','progress':'','pocket_id':''},ensure_ascii=False)+']\n'+body


class TopicControlsTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.BridgeTests.asyncSetUp

    def next_candidate(self, name):
        self.now += 7200
        self.host.native = {'id':name,'expire_at':self.now+3600,'state':'queued','context':{}}

    def seed_legacy(self, candidate, raw, confirmed, *, title='', platform='test'):
        identity = content_identity(raw)
        self.gate.db.execute('''INSERT INTO proactive_attempts
            (candidate,day,attempted,state,sent_count,part_count,topic_id)
            VALUES (?,'2026-10-27',?,'sent',1,1,?)''',(candidate,self.now-10000,identity))
        self.gate.db.commit()
        self.gate.block_topic(identity)
        turn = str(uuid.uuid5(uuid.NAMESPACE_URL,'qq-companion-proactive:proactive:'+candidate))
        self.archive.create_turn(turn,platform,SESSION,proactive=True)
        delivery = candidate+'-delivery'
        self.archive.begin_delivery(delivery,turn,'proactive',0,confirmed,[])
        self.archive.finish_delivery(delivery,'sent',receipt_source='SyntheticFixedAdapterAck')
        if title:
            self.state.begin_opportunity(candidate,evaluation_id=candidate,candidate_id=candidate,
                session=SESSION,deadline=self.now+3600,input_version='0')
            self.state.update_decision(candidate,raw_control='[PHASE3_PROACTIVE:send]',
                parsed={'decision':'send'},effective_state='sent',reason='synthetic_legacy',topic=title)
        return identity

    async def test_selected_specific_topic_is_persisted_and_used_everywhere(self):
        topic='炉石：随机法术的收益'
        self.host.response=reply(topic,'随机性像是这套牌的游乐场。')
        await self.bridge.tick_user('10001',self.host.user)
        identity=content_identity(topic)
        self.assertEqual(self.gate._get('last_topic'),identity)
        self.assertEqual(self.gate.db.execute('SELECT topic_id FROM proactive_attempts').fetchone()[0],identity)
        chosen=self.host.native['context']['phase3_autonomy']
        self.assertEqual(chosen['topic'],topic)
        self.assertEqual(chosen['candidate_id'],'candidate-1')
        self.assertEqual(chosen['topic_identity'],identity)
        self.assertEqual((await self.bridge._context(self.host.user))[3]['topic_id'],identity)
        self.assertNotEqual(identity,content_identity(self.sent[0][0]))

    async def test_closed_specific_topic_blocks_new_words_but_same_domain_new_topic_allowed(self):
        topic='炉石：随机法术的收益'
        self.host.response=reply(topic,'随机性像是这套牌的游乐场。')
        await self.bridge.tick_user('10001',self.host.user)
        self.gate.block_topic(self.gate._get('last_topic'))
        self.next_candidate('candidate-2')
        self.host.response=reply(topic,'我换个说法，抽到意外法术的波动很大。')
        await self.bridge.tick_user('10001',self.host.user)
        self.assertEqual(len(self.sent),1)
        self.assertEqual(self.state.evaluation('candidate-2')['reason'],'topic_suppressed')
        self.assertIn(topic,self.host.prompts[-1]['prompt'])
        self.next_candidate('candidate-3')
        self.host.response=reply('炉石：角色卡插画的视觉风格','卡牌边框像给插画开了扇小窗，气氛一下就变了。')
        await self.bridge.tick_user('10001',self.host.user)
        self.assertEqual(len(self.sent),2)
        self.assertEqual(self.state.evaluation('candidate-3')['effective_state'],'sent')

    async def test_old_blocked_body_hash_still_blocks_exact_body(self):
        body='旧的具体话题已经关闭。'
        self.gate.block_topic(content_identity(body))
        self.host.response=reply('换了一个标题',body)
        await self.bridge.tick_user('10001',self.host.user)
        self.assertFalse(self.sent)
        self.assertEqual(self.state.evaluation('candidate-1')['reason'],'topic_suppressed')

    async def test_legacy_semantic_marker_body_maps_to_confirmed_guard_text(self):
        raw='旧话题第一句。\n<<PRIVATE_COMPANION_SPLIT>>\n旧话题第二句。'
        visible='旧话题第一句。\n旧话题第二句'
        self.seed_legacy('legacy-plan',raw,visible)
        self.host.response=reply('一个新的伪装标题',visible)
        await self.bridge.tick_user('10001',self.host.user)
        self.assertFalse(self.sent)
        self.assertEqual(self.state.evaluation('candidate-1')['reason'],'topic_suppressed')
        closed=self.bridge.closed_topics(SESSION)
        self.assertEqual(closed[0]['confirmed_excerpt'],visible)
        self.assertNotIn('PRIVATE_COMPANION_SPLIT',closed[0]['confirmed_excerpt'])

    async def test_legacy_body_control_uses_actual_evaluation_title_without_domain_ban(self):
        topic='音乐：省略休止符会改变节奏吗'
        self.seed_legacy('legacy-specific','旧的完整正文。','旧的完整正文',title=topic)
        self.host.response=reply(topic,'同一个问题改用不同措辞说出来。')
        await self.bridge.tick_user('10001',self.host.user)
        self.assertFalse(self.sent)
        self.assertEqual(self.state.evaluation('candidate-1')['reason'],'topic_suppressed')
        self.assertIn(topic,self.host.prompts[-1]['prompt'])
        self.assertIn('旧的完整正文',self.host.prompts[-1]['prompt'])
        self.next_candidate('candidate-2')
        self.host.response=reply('音乐：给唱片封面起名字','唱片封面有时候比歌名还像一句没说完的话。')
        await self.bridge.tick_user('10001',self.host.user)
        self.assertEqual(len(self.sent),1)

    async def test_generic_candidate_labels_do_not_become_selected_topic(self):
        self.host.user['planned_proactive_topic']='音乐'
        self.gate.block_topic(content_identity('音乐'))
        self.gate.block_topic(content_identity('activity_share'))
        self.host.response=reply('音乐：没有歌词的声音叙事','纯音乐像是不着急给故事起名字。')
        await self.bridge.tick_user('10001',self.host.user)
        self.assertEqual(len(self.sent),1)
        self.assertNotEqual(self.gate._get('last_topic'),content_identity('音乐'))

    async def test_new_actual_content_same_subject_after_two_unanswered_is_allowed_after_cooldown(self):
        topic='音乐：无歌词的叙事方式'
        self.host.response=reply(topic,'旋律自己就能塑造一个有脾气的主角。')
        await self.bridge.tick_user('10001',self.host.user)
        self.next_candidate('candidate-2')
        self.host.user.update(ignored_streak=2,unanswered_proactive_count=2)
        self.host.response=reply(topic,'忽然想到另一种实验：把同样的旋律换成沙锤和口哨，故事大概就变成侦探散步了。')
        await self.bridge.tick_user('10001',self.host.user)
        self.assertEqual(len(self.sent),2)
        self.assertEqual(self.host.user['ignored_streak'],2)
        self.assertEqual(self.state.evaluation('candidate-2')['effective_state'],'sent')
        self.assertFalse((await self.bridge._context(self.host.user))[3]['topic_cooldown'])
        # The same autonomy-specific exception cannot bypass an explicit stop.
        self.gate.block_topic(content_identity(topic))
        self.next_candidate('candidate-3')
        self.host.response=reply(topic,'第三个没获准继续的角度。')
        await self.bridge.tick_user('10001',self.host.user)
        self.assertEqual(len(self.sent),2)
        self.assertEqual(self.state.evaluation('candidate-3')['reason'],'topic_suppressed')

    async def test_closed_topic_context_is_bounded_and_identity_scoped(self):
        for index in range(15):
            self.seed_legacy('old-'+str(index),'合成旧正文'+str(index),'甲'*1000+str(index),title='具体旧主题'+str(index))
        self.assertEqual(len(self.bridge.closed_topics(SESSION)),12)
        self.assertTrue(all(len(row['confirmed_excerpt'])<=600 for row in self.bridge.closed_topics(SESSION)))
        self.assertEqual(self.bridge.closed_topics('test:FriendMessage:10002'),[])
        self.seed_legacy('foreign-platform','其他平台旧正文','禁止跨平台拼接',platform='foreign')
        self.assertNotIn('禁止跨平台拼接',json.dumps(self.bridge.closed_topics(SESSION),ensure_ascii=False))

    async def test_block_between_admission_and_dispatch_still_prevents_attempt(self):
        async def changed():
            scope=self.bridge.scopes[SESSION]
            text='只是一个具体观点'
            scope['decision']={'topic':'具体观点主题','kind':'opinion'}
            self.assertEqual(self.bridge.send_admission(scope,text),'allowed')
            self.gate.block_topic(content_identity('具体观点主题'))
            result=await self.bridge.dispatch(scope,text)
            self.assertEqual(result,{'state':'blocked','reason':'topic_suppressed'})
            self.assertEqual(self.gate.db.execute('SELECT count(*) FROM proactive_attempts').fetchone()[0],0)
        self.host.before=changed
        self.host.response=reply('具体观点主题','只是一个具体观点')
        await self.bridge.tick_user('10001',self.host.user)
        self.assertFalse(self.sent)


if __name__=='__main__': unittest.main()
