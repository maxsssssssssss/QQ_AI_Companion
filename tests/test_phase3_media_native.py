"""Image lifecycle and native request shape, with synthetic pixels only.

No QQ, remote URL fetch, paid provider, or real user media is used here.
"""
import base64
import io
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image as PILImage

try:
    from astrbot.api.message_components import Image, Plain, Reply
    from astrbot.core.agent.message import ImageURLPart, Message, TextPart, ThinkPart
    from astrbot.core.provider.entities import ProviderRequest
    from astrbot.core.utils.image_input import prepare_request_images
    from integrations.phase1_guard import media, recovery, media_history
    NATIVE = True
except ImportError:
    NATIVE = False


@unittest.skipUnless(NATIVE, 'pinned AstrBot required')
class NativeMediaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def png(self, name='fixture.png', size=(40, 30), color='red'):
        path = self.root/name
        PILImage.new('RGB', size, color).save(path)
        return path

    def image_part(self, ref):
        return ImageURLPart(image_url=ImageURLPart.ImageURL(url=ref))

    def messages(self, *users):
        return [Message(role='system', content='Synthetic persona'), *users,
                Message(role='assistant', content=[ThinkPart(think='never shown')])]

    async def test_snapshot_survives_source_cleanup_and_native_request_capture(self):
        path = self.png()
        original = [Plain('有几个图形？'), Image.fromFileSystem(path)]
        copied, receipts = await media.snapshot_components(original)
        path.unlink()
        self.assertEqual(receipts[0]['state'], 'ready')
        self.assertEqual((receipts[0]['width'], receipts[0]['height']), (40, 30))
        self.assertEqual(original[1].path, str(path))
        req = ProviderRequest(prompt=original[0].text, image_urls=[copied[1].file])
        payload = await req.assemble_context()
        image = payload['content'][1]
        self.assertEqual(image['type'], 'image_url')
        self.assertTrue(image['image_url']['url'].startswith('data:image/png;base64,'))
        self.assertNotIn(str(path), json.dumps(receipts))

    async def test_order_survives_image_then_text_two_images_and_emoji(self):
        red = self.png('red.png'); blue = self.png('blue.png', color='blue')
        parts, receipts = await media.snapshot_components([
            Image.fromFileSystem(red), Plain('🙂第一张'), Image.fromFileSystem(blue), Plain('第二张')])
        self.assertEqual([type(p).__name__ for p in parts], ['Image','Plain','Image','Plain'])
        self.assertEqual([r['index'] for r in receipts], [0,1])
        req = ProviderRequest(prompt='比较两张，按原顺序', image_urls=[parts[0].file, parts[2].file])
        payload = await req.assemble_context()
        got = [p['image_url']['url'] for p in payload['content'] if p['type']=='image_url']
        want = [media.snapshot_ref(str(p))[0] for p in [red, blue]]
        self.assertEqual(got, want)

    async def test_native_path_mapping_source_is_local_and_reply_stays_grouped(self):
        path = self.png()
        component = Image(file='/not-present-in-astrbot/napcat.png', url=str(path))
        quote = Reply(id='synthetic-message', chain=[Plain('引用'), component])
        parts, receipts = await media.snapshot_components([Plain('前文'), quote])
        self.assertIsInstance(parts[1], Reply)
        self.assertIsInstance(parts[1].chain[1], Image)
        self.assertTrue(receipts[0]['quoted'])
        self.assertEqual(receipts[0]['source'], 'local')

    async def test_deleted_or_expired_source_is_explicit_unavailable_not_silent_drop(self):
        path = self.png(); path.unlink()
        parts, receipts = await media.snapshot_components([
            Image.fromFileSystem(path), Image.fromURL('https://example.invalid/expired?secret=synthetic')])
        self.assertTrue(all(isinstance(p, Plain) for p in parts))
        self.assertEqual([r['reason'] for r in receipts], ['source_unavailable','download_unavailable'])
        self.assertTrue(all('没有看到图像内容' in p.text for p in parts))
        self.assertNotIn('secret', json.dumps(receipts))

    async def test_current_missing_image_and_old_missing_image_differ_in_recovery(self):
        old = Message(role='user', content=[self.image_part(str(self.root/'gone.png'))])
        new = Message(role='user', content=[TextPart(text='现在这张呢'),
            self.image_part('https://example.invalid/expired')])
        result = await recovery.recovery_context(self.messages(old, new))
        self.assertIn('历史消息中的图片', result[1]['content'])
        self.assertIn('当前消息的图片', result[2]['content'])
        self.assertIn('现在这张呢', result[2]['content'])
        self.assertNotIn('https://', json.dumps(result))

    async def test_image_history_followup_keeps_pixels_and_current_text(self):
        payload, _ = media.snapshot_ref(str(self.png()))
        old = Message(role='user', content=[TextPart(text='上一张图'),self.image_part(payload)])
        new = Message(role='user', content='它是什么颜色？')
        result = await recovery.recovery_context(self.messages(old,new))
        self.assertEqual(result[1]['content'][1]['image_url']['url'], payload)
        self.assertEqual(result[2]['content'], '它是什么颜色？')
        self.assertNotIn('never shown', json.dumps(result))

    async def test_native_request_preserves_small_screenshot_resolution_at_configured_bound(self):
        path = self.png(size=(2400,1600))
        req = ProviderRequest(prompt='读取截图文字', image_urls=[str(path)])
        class Event:
            def track_temporary_local_file(self, value): pass
        await prepare_request_images(req, Event(), max_size=8192, output_dir=self.root,
                                     prepared={}, finalize=True, supports_image=True)
        payload = await req.assemble_context()
        part = next(p for p in payload['content'] if p['type']=='image_url')
        data = base64.b64decode(part['image_url']['url'].partition(',')[2])
        with PILImage.open(io.BytesIO(data)) as image:
            self.assertEqual(image.size, (2400,1600))

    async def test_bad_format_size_and_count_are_bounded_and_text_survives(self):
        bmp = self.root/'wrong.png'; PILImage.new('RGB',(2,2)).save(bmp, format='BMP')
        with self.assertRaisesRegex(media.MediaUnavailable, 'unsupported_format'):
            media.snapshot_ref(str(bmp))
        png = self.png()
        with self.assertRaisesRegex(media.MediaUnavailable, 'size_limit'):
            media.snapshot_ref(str(png), max_bytes=5)
        parts, receipts = await media.snapshot_components(
            [Image.fromFileSystem(png), Image.fromFileSystem(png), Plain('继续普通文字')], max_images=1)
        self.assertEqual(receipts[1]['reason'], 'image_count_limit')
        self.assertEqual(parts[-1].text, '继续普通文字')
        with self.assertRaisesRegex(media.MediaUnavailable, 'invalid_encoding'):
            media.snapshot_ref('data:image/png;base64,!!!')

    async def test_recovery_with_media_keeps_tool_pairs_without_mutating_history(self):
        payload, _ = media.snapshot_ref(str(self.png()))
        messages = self.messages(Message(role='user', content=[self.image_part(payload)]))
        messages[2:2] = [Message(role='assistant', content=[], tool_calls=[{'id':'c1','type':'function',
            'function':{'name':'memory_companion_navigate','arguments':'{}'}}]),
            Message(role='tool', tool_call_id='c1', content='合成工具结果')]
        before = [m.model_dump() for m in messages]
        result = await recovery.recovery_context(messages)
        self.assertEqual(result[2]['tool_calls'][0]['id'], result[3]['tool_call_id'])
        self.assertEqual(before, [m.model_dump() for m in messages])

    async def test_unavailable_legacy_media_does_not_break_next_text_turn(self):
        old = Message(role='user', content=[self.image_part('/expired/fixture.png')])
        result = await recovery.recovery_context(self.messages(old,Message(role='user',content='早上好')))
        self.assertEqual(result[-2]['content'],'早上好')
        self.assertIn('历史消息中的图片', result[1]['content'])

    async def test_original_still_image_restored_after_native_preview_at_agent_boundary(self):
        # Lossless synthetic noise exceeds AstrBot's 512 KiB preview cap.
        import random
        path = self.root/'large.png'
        PILImage.frombytes('RGB',(800,800),random.Random(3).randbytes(800*800*3)).save(path)
        self.assertGreater(path.stat().st_size,512*1024)
        copied, _ = await media.snapshot_components([Plain('比较细节'),Image.fromFileSystem(path)])
        req = ProviderRequest(prompt='比较细节',image_urls=[copied[1].file])
        class Event:
            def track_temporary_local_file(self, value): pass
        await prepare_request_images(req,Event(),max_size=8192,output_dir=self.root,prepared={})
        payload = await req.assemble_context()
        before = next(p['image_url']['url'] for p in payload['content'] if p['type']=='image_url')
        original, _ = media.snapshot_ref(str(path))
        self.assertNotEqual(before,original)
        messages = [Message.model_validate(payload)]
        result = await media.restore_current_images(messages,copied)
        self.assertEqual(result['state'],'originals_restored')
        after = next(p.image_url.url for p in messages[0].content if isinstance(p,ImageURLPart))
        self.assertEqual(after,original)
        # Same native message (and therefore ordinary history persistence) owns
        # the original bytes; there is no secondary media history/store.
        followup = self.messages(*messages,Message(role='user',content='细节是什么？'))
        continuation = await recovery.recovery_context(followup)
        self.assertIn(original,json.dumps(continuation))

    async def test_restore_does_not_guess_quoted_or_mismatched_image_mapping(self):
        path=self.png();data,_=media.snapshot_ref(str(path))
        current=Message(role='user',content=[self.image_part(data)])
        original=current.model_dump()
        result=await media.restore_current_images([current],[Image.fromFileSystem(path),Image.fromFileSystem(path)])
        self.assertEqual(result['reason'],'source_mapping_mismatch')
        quote=Reply(id='synthetic',chain=[Image.fromFileSystem(path)])
        result=await media.restore_current_images([current],[quote])
        self.assertEqual(result['reason'],'quoted_image_mapping')
        self.assertEqual(current.model_dump(),original)

    async def test_recovery_current_image_has_priority_over_old_image_budget(self):
        data,_=media.snapshot_ref(str(self.png()))
        old=[Message(role='user',content=[self.image_part(data)]) for _ in range(8)]
        current=Message(role='user',content=[TextPart(text='当前图'),self.image_part(data)])
        result=await recovery.recovery_context(self.messages(*old,current))
        self.assertIn('image_count_limit',result[1]['content'])
        self.assertEqual(result[-2]['content'][1]['image_url']['url'],data)

    def sanitize_history(self,contexts):
        path=Path(__file__).resolve().parents[1]/'.runtime/upstream/private_companion/tool_history_sanitizer.py'
        spec=importlib.util.spec_from_file_location('native_private_media_sanitizer',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module.sanitize_history_image_blocks(contexts)[0]

    async def test_actual_private_history_cleanup_restored_only_image_positions(self):
        data,_=media.snapshot_ref(str(self.png()))
        contexts=[{'role':'system','content':'保持原有记忆'},
            {'role':'user','content':[{'type':'text','text':'先图'},
             {'type':'image_url','image_url':{'url':data}}, {'type':'text','text':'后文'}]},
            {'role':'assistant','tool_calls':[{'id':'read-1','type':'function','function':{'name':'read','arguments':'{}'}}],'content':None},
            {'role':'tool','tool_call_id':'read-1','content':'原生资料结果'}]
        plan=await media_history.capture(contexts)
        cleaned=self.sanitize_history(contexts)
        self.assertNotIn('image_url',json.dumps(cleaned))
        stats=media_history.restore(cleaned,plan)
        self.assertEqual(stats['restored_images'],1)
        self.assertEqual(cleaned,contexts)
        messages=[Message.model_validate(row) for row in cleaned]+[Message(role='user',content='图片细节追问')]
        recovered=await recovery.recovery_context(messages)
        self.assertEqual(recovered[1]['content'][1]['image_url']['url'],data)

    async def test_history_bridge_does_not_overwrite_intervening_memory_or_tool_edits(self):
        data,_=media.snapshot_ref(str(self.png()))
        contexts=[{'role':'user','content':[{'type':'text','text':'原文'},self.image_part(data).model_dump()]}]
        plan=await media_history.capture(contexts);cleaned=self.sanitize_history(contexts)
        cleaned[0]['content'][0]['text']='另一个原生清理器已修改文本'
        before=json.dumps(cleaned)
        stats=media_history.restore(cleaned,plan)
        self.assertEqual(stats['unmatched_messages'],1);self.assertEqual(json.dumps(cleaned),before)

    async def test_history_bridge_marks_expired_and_over_budget_images_explicitly(self):
        data,_=media.snapshot_ref(str(self.png()))
        contexts=[{'role':'user','content':[self.image_part(str(self.root/'expired.png')).model_dump()]}]
        contexts += [{'role':'user','content':[self.image_part(data).model_dump()]} for _ in range(8)]
        plan=await media_history.capture(contexts);cleaned=self.sanitize_history(contexts)
        stats=media_history.restore(cleaned,plan)
        self.assertEqual(stats['restored_images'],8);self.assertEqual(stats['unavailable_images'],1)
        self.assertIn('image_count_limit',cleaned[0]['content'][0]['text'])
        expired=contexts[:1];plan=await media_history.capture(expired);cleaned=self.sanitize_history(expired)
        media_history.restore(cleaned,plan)
        self.assertIn('历史消息中的图片',cleaned[0]['content'][0]['text'])
        self.assertIn('source_unavailable',cleaned[0]['content'][0]['text'])


@unittest.skipUnless(NATIVE, 'pinned AstrBot required')
class NativeProviderFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp import web
        from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
        from integrations.phase1_guard import media_provider
        self.media_provider=media_provider
        self.calls=[];self.reject_all=False
        async def complete(request):
            payload=await request.json();self.calls.append(payload)
            if len(self.calls)==1 or self.reject_all:
                return web.json_response({'error':{'code':'invalid_attachment','message':'invalid_attachment'}},status=400)
            return web.json_response({'id':'synthetic','object':'chat.completion','created':0,'model':'deepseek-flash',
                'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':'这次图片没有读取成功。'}}],
                'usage':{'prompt_tokens':12,'completion_tokens':8,'total_tokens':20}})
        app=web.Application();app.router.add_post('/v1/chat/completions',complete)
        self.runner=web.AppRunner(app);await self.runner.setup()
        site=web.TCPSite(self.runner,'127.0.0.1',0);await site.start()
        port=site._server.sockets[0].getsockname()[1]
        self.provider=ProviderOpenAIOfficial({'id':'synthetic-vision','type':'openai_chat_completion',
            'key':['synthetic-not-a-key'],'model':'deepseek-flash','api_base':f'http://127.0.0.1:{port}/v1',
            'timeout':3}, {})
        self.handle=media_provider.install(self.provider)
        image=io.BytesIO();PILImage.new('RGB',(2,2),'red').save(image,format='PNG')
        self.data='data:image/png;base64,'+base64.b64encode(image.getvalue()).decode()

    async def asyncTearDown(self):
        self.handle.restore()
        await self.provider.client.close()
        await self.runner.cleanup()

    async def test_actual_native_provider_http400_then_same_model_truthful_text_fallback(self):
        import asyncio,copy
        rows=[{'role':'user','content':[{'type':'text','text':'图里是什么？'},
            {'type':'image_url','image_url':{'url':self.data}}]}]
        before=copy.deepcopy(rows);receipts=[]
        token=self.media_provider.bind_turn('synthetic-turn',receipts.append)
        try:
            response=await asyncio.wait_for(self.provider.text_chat(contexts=rows,request_max_retries=1),8)
        finally:
            self.media_provider.reset_turn(token)
        self.assertEqual(len(self.calls),2)
        self.assertEqual([r['model'] for r in self.calls],['deepseek-flash','deepseek-flash'])
        self.assertIn('image_url',json.dumps(self.calls[0]))
        self.assertNotIn('image_url',json.dumps(self.calls[1]))
        self.assertIn(self.media_provider.NOTICE,json.dumps(self.calls[1],ensure_ascii=False))
        self.assertEqual(response.completion_text,'这次图片没有读取成功。')
        self.assertEqual(rows,before)
        self.assertEqual(receipts,[{'turn_id':'synthetic-turn','state':'native_text_fallback',
            'removed_images':1,'affected_messages':1,'model_changed':False}])
        self.assertIs(self.media_provider.install(self.provider),self.handle)
        self.handle.restore();self.handle.restore()
        self.assertNotIn('_remove_image_from_context',vars(self.provider))

    async def test_repeated_image_error_stops_after_native_single_fallback(self):
        import asyncio
        self.reject_all=True
        with self.assertRaises(Exception):
            await asyncio.wait_for(self.provider.text_chat(prompt='这张图呢？',image_urls=[self.data],request_max_retries=1),8)
        self.assertEqual(len(self.calls),2)
        self.assertEqual(self.handle.calls,1)
        self.assertEqual(self.handle.removed_images,1)


if __name__ == '__main__':
    unittest.main()
