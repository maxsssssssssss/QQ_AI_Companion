"""T2 component integration: pinned AstrBot events + aiocqhttp + local HTTP peer.

Run under the locked venv with PYTHONPATH=.runtime/upstream/AstrBot:$PWD and
ASTRBOT_ROOT pointing to an isolated directory. This is not real QQ acceptance.
"""
import asyncio
import importlib
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

PROJECT=Path(__file__).resolve().parents[1]
try:
    from aiohttp import web
    from aiocqhttp import CQHttp
    from astrbot.api.event import MessageChain, MessageEventResult
    from astrbot.api.message_components import Plain, Image
    from astrbot.api.platform import AstrBotMessage, MessageMember, PlatformMetadata, MessageType
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
    from astrbot.core.utils.metrics import Metric
    from astrbot.core.utils.active_event_registry import active_event_registry
    from integrations.phase1_guard.main import Phase1Guard, StarTools
    NATIVE=True
except ImportError:
    NATIVE=False


@unittest.skipUnless(NATIVE,'requires pinned AstrBot and locked dependency environment')
class NativeDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.calls=[]
        self.mode='ack'
        async def receive(request):
            self.calls.append((request.path,await request.json()))
            if self.mode=='timeout': await asyncio.sleep(2)
            if self.mode=='reject': return web.json_response({'status':'failed','retcode':100,'data':None})
            return web.json_response({'status':'ok','retcode':0,'data':{'message_id':'9001'}})
        app=web.Application(); app.router.add_post('/send_private_msg',receive)
        self.runner=web.AppRunner(app); await self.runner.setup()
        site=web.TCPSite(self.runner,'127.0.0.1',0); await site.start()
        port=site._server.sockets[0].getsockname()[1]
        self.bot=CQHttp(api_root=f'http://127.0.0.1:{port}',api_timeout_sec=3)
        self.metrics=patch.object(Metric,'upload',new=AsyncMock())
        self.metrics.start()
        self.data=patch.object(StarTools,'get_data_dir',return_value=Path(self.temp.name))
        self.data.start()
        self.context=types.SimpleNamespace(get_config=lambda *a,**kw:{},get_all_stars=lambda:[])
        self.guard=Phase1Guard(self.context,{'phase3_enabled':True,'phase3_admin_id':'10001',
            'phase3_platform_id':'test','phase3_group_window':.01,'phase3_group_max_wait':.03,
            'send_timeout_seconds':2.0})
        self.serial=0

    async def asyncTearDown(self):
        await self.guard.terminate()
        self.data.stop(); self.metrics.stop()
        await self.runner.cleanup()
        self.temp.cleanup()

    def event(self,text='测试',sender='10001'):
        self.serial+=1
        msg=AstrBotMessage();msg.type=MessageType.FRIEND_MESSAGE;msg.message_id=str(self.serial)
        msg.self_id='90000';msg.sender=MessageMember(sender,'Synthetic');msg.message=[Plain(text)]
        msg.message_str=text;msg.session_id=sender;msg.raw_message={'message':[p.toDict() for p in msg.message]}
        return AiocqhttpMessageEvent(text,msg,PlatformMetadata('aiocqhttp','synthetic','test'),sender,self.bot)

    async def ready(self,event):
        await self.guard.claim_inbound(event)
        await self.guard.group_input(event)
        await self.guard.mark_generating(event,types.SimpleNamespace())

    async def test_native_ack_progress_final_and_actual_http_order(self):
        event=self.event();await self.ready(event)
        tid=event.get_extra('phase3_turn_id')
        self.assertTrue(self.guard.store.claim_progress(tid))
        progress=await self.guard.send_controlled(event,MessageChain([Plain('我去查一下')]),kind='progress')
        event.set_extra('phase3_delivery_kind','final')
        event.set_result(MessageEventResult().message('查到了'))
        await self.guard.dispatch_text(event)
        self.assertEqual(progress.state,'sent')
        self.assertIsNone(progress.platform_message_id) # actual adapter discards it
        self.assertEqual([r[1]['message'][0]['data']['text'] for r in self.calls],['我去查一下','查到了'])
        rows=self.guard.store.phase3_snapshot()['deliveries']
        self.assertEqual([r['kind'] for r in rows],['progress','final'])
        self.assertEqual([r['state'] for r in rows],['sent','sent'])
        self.assertIsNone(event.get_result())

    async def test_direct_native_error_send_is_observed(self):
        event=self.event();await self.ready(event)
        result=await event.send(MessageChain([Plain('连接暂时失败')]))
        self.assertEqual(result.state,'sent')
        self.assertEqual(len(self.calls),1)
        self.assertEqual(len(event.get_extra('phase3_deliveries')),1)

    async def test_failed_or_lost_platform_receipts_are_not_sent(self):
        event=self.event();await self.ready(event)
        self.mode='reject'
        result=await self.guard.send_controlled(event,MessageChain([Plain('reply')]))
        self.assertEqual(result.state,'failed')
        self.mode='timeout'
        self.guard.delivery.timeout=.5
        result=await self.guard.send_controlled(event,MessageChain([Plain('reply2')]),part_index=1)
        self.assertEqual(result.state,'unknown')
        self.assertEqual(len(self.calls),2)

    async def test_unauthorized_sender_and_media_do_not_reach_http(self):
        event=self.event(sender='10002')
        await self.guard.claim_inbound(event)
        self.assertTrue(event.is_stopped())
        with self.assertRaises(PermissionError):
            await self.guard.send_controlled(event,MessageChain([Plain('no')]))
        allowed=self.event();await self.ready(allowed)
        result=await self.guard.send_controlled(allowed,MessageChain([Plain('img'),Image.fromURL('https://example.invalid/x.png')]))
        self.assertEqual(result.state,'failed')
        self.assertEqual(self.calls,[])

    async def test_three_original_records_and_one_native_model_facing_event(self):
        events=[self.event(t) for t in ['第一条','第二条','第三条']]
        for event in events: await self.guard.claim_inbound(event)
        await asyncio.gather(*(self.guard.group_input(e) for e in events))
        self.assertEqual(events[0].message_str,'第一条\n第二条\n第三条')
        self.assertFalse(events[0].is_stopped())
        self.assertTrue(events[1].is_stopped());self.assertTrue(events[2].is_stopped())
        self.assertEqual(len(self.guard.store.snapshot()['inbound']),3)
        self.assertEqual(events[0].get_extra('phase3_source_message_ids'),['1','2','3'])

    async def test_new_input_invokes_native_agent_cancellation_and_stops_old_bubbles(self):
        first=self.event('帮我查一下');await self.ready(first)
        active_event_registry.register(first)
        stopped=[]
        active_event_registry.register_agent_stop_callback(first,lambda:stopped.append(True))
        try:
            follow=self.event('别搜了，已经解决')
            await self.guard.claim_inbound(follow)
            self.assertEqual(stopped,[True])
            self.assertFalse(self.guard.turn_is_current(first))
            result=await self.guard.send_controlled(first,MessageChain([Plain('过期建议')]))
            self.assertEqual(result.state,'failed')
            self.assertEqual(self.calls,[])
        finally: active_event_registry.unregister(first)

    async def test_observer_failure_does_not_change_acknowledged_status(self):
        event=self.event();await self.ready(event)
        received=[]
        async def observer(payload): received.append(payload);raise ValueError('synthetic')
        self.guard.register_delivery_observer(observer)
        result=await self.guard.send_controlled(event,MessageChain([Plain('text')]))
        self.assertEqual(result.state,'sent')
        self.assertEqual(received[0]['source_message_ids'],['1'])
        self.guard.unregister_delivery_observer(observer)

    async def test_actual_private_parser_splits_only_semantic_markers(self):
        package='native_delivery.astrbot_plugin_private_companion'
        namespace=types.ModuleType('native_delivery');namespace.__path__=[]
        module=types.ModuleType(package);module.__path__=[str(PROJECT/'.runtime/upstream/private_companion')]
        sys.modules['native_delivery']=namespace;sys.modules[package]=module
        native=importlib.import_module(package+'.segmented_message')
        self.context.get_all_stars=lambda:[types.SimpleNamespace(module_path=package+'.main',activated=True)]
        result=MessageEventResult().message('真不错\n<<PRIVATE_COMPANION_SPLIT>>\n替你开心')
        pieces=self.guard.semantic_parts(result)
        self.assertEqual([p.get_plain_text() for p in pieces],['真不错','替你开心'])
        self.assertEqual(len(self.guard.semantic_parts(MessageEventResult().message('长答'*500))),1)
        quoted=MessageEventResult().message('```\n<<PRIVATE_COMPANION_SPLIT>>\n```')
        self.assertEqual(len(self.guard.semantic_parts(quoted)),1)
        event=self.event();await self.ready(event);event.set_result(result)
        await self.guard.dispatch_text(event)
        self.assertEqual(len(self.calls),2)
        self.assertEqual([r['part_index'] for r in self.guard.store.phase3_snapshot()['deliveries']],[0,1])
        second=self.event('再聊');await self.ready(second)
        req=types.SimpleNamespace(system_prompt='原核心人格')
        await self.guard.mark_generating(second,req)
        self.assertIn('最多四个',req.system_prompt)
        self.assertIn('原核心人格',req.system_prompt)
        await self.guard.mark_generating(second,req)
        self.assertEqual(req.system_prompt.count('[Phase3 语义气泡]'),1)
        second.set_result(MessageEventResult().message('\n<<PRIVATE_COMPANION_SPLIT>>\n'.join(['一','二','三','四'])))
        await self.guard.dispatch_text(second)
        self.assertEqual(len(self.calls),6)
        self.assertEqual([r['part_index'] for r in self.guard.store.phase3_snapshot()['deliveries']][-4:],[0,1,2,3])

    async def test_default_off_leaves_phase2_schema_and_behavior(self):
        await self.guard.terminate()
        self.data.stop()
        other=Path(self.temp.name)/'legacy';other.mkdir()
        self.data=patch.object(StarTools,'get_data_dir',return_value=other);self.data.start()
        self.guard=Phase1Guard(self.context,{'send_timeout_seconds':2.0})
        event=self.event();await self.guard.claim_inbound(event)
        event.set_result(MessageEventResult().message('旧链路'))
        await self.guard.dispatch_text(event)
        self.assertFalse(self.guard.phase3_enabled)
        self.assertIsNone(self.guard.store.db.execute("SELECT 1 FROM sqlite_master WHERE name='turns'").fetchone())
        self.assertEqual(self.guard.store.snapshot()['outbound'][0]['state'],'sent')

if __name__=='__main__':unittest.main()
