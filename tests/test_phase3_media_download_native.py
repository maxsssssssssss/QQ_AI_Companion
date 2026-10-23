"""Real native preprocessing + Guard + local HTTP/OneBot, synthetic only."""
import asyncio
import copy
import io
from pathlib import Path
import time
from types import SimpleNamespace as NS
import unittest

import test_phase3_delivery_native as base

if base.NATIVE:
    from aiohttp import web
    from PIL import Image as PILImage
    from astrbot.api.event import MessageEventResult
    from astrbot.api.message_components import Image,Plain
    from astrbot.api.platform import MessageType
    from astrbot.core.pipeline.preprocess_stage.stage import PreProcessStage
    from astrbot.core.utils.astrbot_path import get_astrbot_temp_path
    from integrations.phase1_guard.media_download import install,BoundedImage


@unittest.skipUnless(base.NATIVE,'pinned AstrBot required')
class NativeImageDownloadTests(unittest.IsolatedAsyncioTestCase):
    event=base.NativeDeliveryTests.event
    ready=base.NativeDeliveryTests.ready

    async def asyncSetUp(self):
        await base.NativeDeliveryTests.asyncSetUp(self)
        self.queue=asyncio.Queue()
        self.handle=install(self.queue,platform_id='test',owner_id='10001',timeout=4.0)
        self.release=asyncio.Event();self.downloads=[]
        image=io.BytesIO();PILImage.new('RGB',(3,2),'red').save(image,format='PNG');self.png=image.getvalue()
        async def download(request):
            self.downloads.append(request.path)
            if request.path=='/hang':await self.release.wait()
            if request.path=='/slow':await asyncio.sleep(.2)
            if request.path=='/gone':return web.Response(status=404)
            if request.path=='/invalid':return web.Response(body=b'not a real image',content_type='text/plain')
            return web.Response(body=self.png,content_type='image/png')
        app=web.Application();app.router.add_get('/{name}',download)
        self.image_runner=web.AppRunner(app);await self.image_runner.setup()
        site=web.TCPSite(self.image_runner,'127.0.0.1',0);await site.start()
        self.base_url=f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
        self.stage=PreProcessStage()
        await self.stage.initialize(NS(astrbot_config={'platform_settings':{},'provider_stt_settings':{}},plugin_manager=NS()))

    async def asyncTearDown(self):
        self.release.set();self.handle.restore()
        await self.image_runner.cleanup()
        await base.NativeDeliveryTests.asyncTearDown(self)

    def with_image(self,path='/hang',sender='10001'):
        event=self.event('请看图，也回应这句话',sender=sender)
        event.message_obj.message.append(Image.fromURL(self.base_url+path))
        event.message_obj.raw_message={'message':[p.toDict() for p in event.message_obj.message]}
        return event

    async def queued(self,event):
        await self.queue.put(event)
        return await self.queue.get()

    async def test_hanging_native_download_times_out_then_guard_text_delivery_and_next_input(self):
        self.handle.restore()
        self.handle=install(self.queue,platform_id='test',owner_id='10001',timeout=.5)
        event=self.with_image();raw=copy.deepcopy(event.message_obj.raw_message)
        await self.queued(event)
        self.assertIsInstance(event.message_obj.message[1],BoundedImage)
        before=time.monotonic();await self.stage.process(event)
        self.assertLess(time.monotonic()-before,1.2)
        self.assertEqual(self.downloads,['/hang'])
        self.assertEqual(event.message_obj.message[1].phase3_media_failure,'download_timeout')
        self.assertEqual(event.message_obj.raw_message,raw)
        await self.ready(event)
        self.assertFalse(event.is_stopped())
        self.assertTrue(any(isinstance(p,Plain) and 'download_timeout' in p.text for p in event.message_obj.message))
        event.set_result(MessageEventResult().message('图片本次没有读到，我可以继续和你聊。'))
        await self.guard.dispatch_text(event)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.guard.store.phase3_snapshot()['deliveries'][-1]['state'],'sent')
        next_event=self.event('继续普通聊天');await self.queued(next_event);await self.ready(next_event)
        self.assertFalse(next_event.is_stopped())
        self.assertTrue(self.guard.turn_is_current(next_event))

    async def test_successful_native_download_keeps_order_original_raw_and_serializable_components(self):
        event=self.with_image('/image');raw=copy.deepcopy(event.message_obj.raw_message)
        await self.queued(event)
        self.assertNotIn('_budget',event.message_obj.message[1].toDict()['data'])
        await self.stage.process(event)
        await self.ready(event)
        self.assertEqual(self.downloads,['/image'])
        self.assertEqual(event.message_obj.raw_message,raw)
        self.assertIsInstance(event.message_obj.message[-1],Image)
        self.assertTrue(event.message_obj.message[-1].file.startswith('base64://'))

    async def test_non_owner_group_and_other_platform_are_not_wrapped(self):
        other=self.with_image(sender='10002')
        group=self.with_image();group.message_obj.type=MessageType.GROUP_MESSAGE
        platform=self.with_image();platform.platform_meta.id='different'
        for event in (other,group,platform):
            original=event.message_obj.message[1]
            await self.queued(event)
            self.assertIs(event.message_obj.message[1],original)
            self.assertNotIsInstance(original,BoundedImage)
        self.assertEqual(self.downloads,[])

    async def test_multiple_images_share_one_deadline_and_404_does_not_retry(self):
        self.handle.restore()
        self.handle=install(self.queue,platform_id='test',owner_id='10001',timeout=.5)
        event=self.with_image();event.message_obj.message.append(Image.fromURL(self.base_url+'/hang'))
        await self.queued(event)
        before=time.monotonic();await self.stage.process(event)
        self.assertLess(time.monotonic()-before,1.2)
        self.assertEqual(self.downloads,['/hang'])
        self.assertTrue(all(p.phase3_media_failure=='download_timeout' for p in event.message_obj.message if isinstance(p,Image)))
        gone=self.with_image('/gone');await self.queued(gone);await self.stage.process(gone);await self.ready(gone)
        self.assertEqual(self.downloads,['/hang','/gone'])
        self.assertTrue(any(isinstance(p,Plain) and 'download_unavailable' in p.text for p in gone.message_obj.message))

    async def test_admission_is_ordered_scoped_and_rejected_events_never_download(self):
        self.handle.restore()
        seen=[]
        def admit(event):
            seen.append(event.get_extra('phase3_ingress_order'))
            return self.guard.admit_input(event)
        self.handle=install(self.queue,platform_id='test',owner_id='10001',timeout=.5,on_admit=admit)
        first=self.with_image('/image');await self.queued(first)
        duplicate=self.with_image();duplicate.message_obj.message_id=first.message_obj.message_id
        next_event=self.event('后来的普通文字')
        await self.queue.put(duplicate);await self.queue.put(next_event)
        self.assertIs(await self.queue.get(),next_event)
        self.assertTrue(duplicate.is_stopped())
        self.assertEqual(seen,[1,2,3])
        self.assertLess(first.get_extra('phase3_received_monotonic'),next_event.get_extra('phase3_received_monotonic'))
        other=self.with_image(sender='10002');await self.queued(other)
        self.assertIsNone(other.get_extra('phase3_ingress_order'))
        self.assertEqual(seen,[1,2,3]);self.assertEqual(self.downloads,[])

    async def test_slow_image_then_text_keep_native_source_order_and_one_turn(self):
        self.handle.restore()
        self.handle=install(self.queue,platform_id='test',owner_id='10001',timeout=4.0,on_admit=self.guard.admit_input)
        first=self.with_image('/slow');first.message_str='[图片]';first.message_obj.message=first.message_obj.message[1:]
        first.message_obj.raw_message={'message':[p.toDict() for p in first.message_obj.message]}
        await self.queued(first)
        async def process(event):
            await self.stage.process(event)
            await self.guard.claim_inbound(event)
            if not event.is_stopped():await self.guard.group_input(event)
            # The real Scheduler ends propagation after a grouped follower.
            if not event.is_stopped():await self.guard.mark_generating(event,NS())
        image_task=asyncio.create_task(process(first))
        await asyncio.sleep(.01)
        second=self.event('请看上图有几个图形');await self.queued(second)
        await asyncio.gather(image_task,process(second))
        leaders=[event for event in (first,second) if not event.is_stopped()]
        self.assertEqual(len(leaders),1)
        leader=leaders[0]
        self.assertEqual(leader.get_extra('phase3_source_message_ids'),[first.message_obj.message_id,second.message_obj.message_id])
        self.assertEqual([type(p).__name__ for p in leader.message_obj.message],['Image','Plain'])
        self.assertEqual(leader.message_obj.message[-1].text,'请看上图有几个图形')
        self.assertEqual(len(self.guard.store.snapshot()['inbound']),2)
        self.assertTrue(self.guard.turn_is_current(leader));self.assertEqual(self.downloads,['/slow'])

    async def test_admission_exception_stops_only_failed_event_and_next_input_still_works(self):
        self.handle.restore();attempts=[]
        def admit(event):
            attempts.append(event)
            if len(attempts)==1:raise RuntimeError('synthetic ledger failure')
            return self.guard.admit_input(event)
        self.handle=install(self.queue,platform_id='test',owner_id='10001',timeout=.5,on_admit=admit)
        failed=self.with_image();next_event=self.event('正常后续输入')
        await self.queue.put(failed);await self.queue.put(next_event)
        self.assertIs(await self.queue.get(),next_event)
        self.assertTrue(failed.is_stopped());self.assertEqual(self.downloads,[])
        await self.ready(next_event)
        self.assertFalse(next_event.is_stopped());self.assertTrue(self.guard.turn_is_current(next_event))

    async def test_invalid_download_reports_format_reason_and_releases_owned_temp(self):
        self.handle.restore()
        self.handle=install(self.queue,platform_id='test',owner_id='10001',timeout=4.0)
        temp=Path(get_astrbot_temp_path());before=set(temp.rglob('*'))
        event=self.with_image('/invalid');await self.queued(event);await self.stage.process(event)
        self.assertEqual(event.message_obj.message[-1].phase3_media_failure,'invalid_image')
        await self.ready(event)
        self.assertTrue(any(isinstance(p,Plain) and 'invalid_image' in p.text for p in event.message_obj.message))
        self.assertEqual({p for p in set(temp.rglob('*'))-before if p.is_file()},set())


if __name__=='__main__':unittest.main()
