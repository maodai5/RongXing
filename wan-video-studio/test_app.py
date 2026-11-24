import base64
import io
import os
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
from PIL import Image

from app import CREAM, MAX_IMAGE, create_app, reference_image, square_image


class StudioTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'DASHSCOPE_API_KEY': 'test-key', 'WAN_MODEL': 'test-wan3-i2v',
                                             'WAN_IMAGE_MODEL': 'test-wan-image'}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        state = tempfile.TemporaryDirectory()
        self.addCleanup(state.cleanup)
        os.environ['WAN_STUDIO_STATE_DIR'] = state.name
        self.state = state.name
        self.client = create_app().test_client()
        self.headers = {'X-Studio-Request': '1'}

    def submit(self, photo, action='look_left', headers=None):
        return self.client.post('/api/jobs', data={'action': action, 'image': (io.BytesIO(photo), 'pet.png')},
                                headers=self.headers if headers is None else headers)

    @staticmethod
    def photo(mode='RGB', size=(480, 320)):
        image = io.BytesIO()
        Image.new(mode, size).save(image, 'PNG')
        return image.getvalue()

    def test_seven_actions_are_rendered(self):
        html = self.client.get('/').get_data(as_text=True)
        for action in ['idle', 'happy', 'sad', 'sick', 'crouch', 'look_left', 'look_right']:
            self.assertIn(f'value="{action}"', html)
        self.assertIn('Wan 3.0', html)
        self.assertIn('3 秒', html)
        self.assertIn('id="download"', html)
        self.assertIn('下载 MP4', html)
        self.assertNotIn('test-key', html)

    @staticmethod
    def video_stream(payload=b'fake-mp4', status=200, content_type='video/mp4'):
        stream = MagicMock()
        stream.__enter__.return_value = httpx.Response(
            status, headers={'Content-Type': content_type}, content=payload)
        return stream

    @patch('app.httpx.stream')
    @patch('app.httpx.request')
    def test_wan3_submit_poll_and_result(self, send, stream):
        stream.side_effect = [self.video_stream()]
        send.side_effect = [
            httpx.Response(200, json={'output': {'task_id': 'task-1', 'task_status': 'PENDING'}}),
            httpx.Response(200, json={'output': {'task_status': 'RUNNING'}}),
            httpx.Response(200, json={'output': {'task_status': 'SUCCEEDED', 'video_url': 'https://example.com/pet.mp4'}}),
            httpx.Response(200, json={'output': {'task_status': 'SUCCEEDED', 'video_url': 'https://example.com/pet.mp4'}}),
        ]
        photo = self.photo()
        result = self.submit(photo)
        self.assertEqual(result.status_code, 202)
        args, kwargs = send.call_args_list[0]
        self.assertEqual(args, ('POST', 'https://token-plan.maas.qianwenaiapi.com/api/v1/services/aigc/video-generation/video-synthesis'))
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer test-key')
        self.assertEqual(kwargs['headers']['X-DashScope-Async'], 'enable')
        payload = kwargs['json']
        self.assertEqual(payload['model'], 'test-wan3-i2v')
        self.assertEqual(payload['input']['media'], [{'type': 'first_frame', 'url': 'data:image/png;base64,' + base64.b64encode(photo).decode()}])
        self.assertIn('2.5D', payload['input']['prompt'])
        self.assertIn('#FEECD4', payload['input']['prompt'])
        self.assertIn('2.5D', payload['input']['prompt'])
        self.assertIn('画面左侧', payload['input']['prompt'])
        self.assertEqual(payload['parameters']['duration'], 3)
        self.assertEqual(payload['parameters']['resolution'], '720P')
        path = '/api/jobs/' + result.json['token']
        self.assertEqual(self.client.get(path).json, {'status': 'RUNNING'})
        done = self.client.get(path).json
        self.assertEqual(done['status'], 'SUCCEEDED')
        self.assertEqual(done['video_url'], path + '/video')
        self.assertEqual(done['download_url'], path + '/video?download=1')
        self.assertEqual(send.call_args.args, ('GET', 'https://token-plan.maas.qianwenaiapi.com/api/v1/tasks/task-1'))
        self.assertEqual(stream.call_args.args, ('GET', 'https://example.com/pet.mp4'))
        played = self.client.get(done['video_url'])
        self.assertEqual(played.status_code, 200)
        self.assertEqual(played.data, b'fake-mp4')
        self.assertEqual(played.mimetype, 'video/mp4')
        self.assertIn('private', played.headers['Cache-Control'])
        played.close()
        saved = self.client.get(done['download_url'])
        self.assertEqual(saved.status_code, 200)
        self.assertIn('attachment', saved.headers['Content-Disposition'])
        self.assertIn('.mp4', saved.headers['Content-Disposition'])
        saved.close()
        self.assertEqual(self.client.get(path).json['video_url'], path + '/video')
        self.assertEqual(stream.call_count, 1)

    @patch('app.httpx.stream')
    @patch('app.httpx.request')
    def test_video_falls_back_to_upstream_when_caching_fails(self, send, stream):
        send.side_effect = [
            httpx.Response(200, json={'output': {'task_id': 'task-2', 'task_status': 'PENDING'}}),
            httpx.Response(200, json={'output': {'task_status': 'SUCCEEDED', 'video_url': 'https://example.com/pet.mp4'}}),
        ]
        stream.side_effect = httpx.ConnectError('offline')
        token = self.submit(self.photo()).json['token']
        done = self.client.get('/api/jobs/' + token).json
        self.assertEqual(done['video_url'], 'https://example.com/pet.mp4')
        self.assertEqual(done['download_url'], 'https://example.com/pet.mp4')
        self.assertEqual(self.client.get('/api/jobs/' + token + '/video').status_code, 404)

    def test_video_route_rejects_unsigned_and_missing_tokens(self):
        self.assertEqual(self.client.get('/api/jobs/not-a-token/video').status_code, 404)
        result = create_app().test_client()
        self.assertEqual(result.get('/api/jobs/' + 'x' * 40 + '/video').status_code, 404)

    @patch('app.httpx.request', return_value=httpx.Response(200, json={'output': {'task_id': 'task-3', 'task_status': 'RUNNING'}}))
    def test_job_token_survives_restart(self, send):
        token = self.submit(self.photo()).json['token']
        restarted = create_app().test_client()
        self.assertEqual(restarted.get('/api/jobs/' + token).json, {'status': 'RUNNING'})

    def test_video_cache_requires_video_content_type(self):
        self.assertFalse(os.path.exists(os.path.join(self.state, 'videos', 'task-4.mp4')))

    @patch('app.httpx.request', return_value=httpx.Response(200, json={'output': {'task_id': 'task-1', 'task_status': 'PENDING'}}))
    def test_token_plan_compatible_base_uses_native_video_path(self, send):
        os.environ['WAN_DASHSCOPE_BASE_URL'] = 'https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1/'
        self.client = create_app().test_client()
        self.assertEqual(self.submit(self.photo()).status_code, 202)
        self.assertEqual(send.call_args.args, ('POST', 'https://token-plan.maas.qianwenaiapi.com/api/v1/services/aigc/video-generation/video-synthesis'))

    @patch('app.httpx.request', return_value=httpx.Response(200, json={'output': {'task_id': 'task-1', 'task_status': 'PENDING'}}))
    def test_transparent_upload_is_flattened_onto_cream(self, send):
        result = self.submit(self.photo('RGBA'))
        self.assertEqual(result.status_code, 202)
        url = send.call_args.kwargs['json']['input']['media'][0]['url']
        self.assertTrue(url.startswith('data:image/jpeg;base64,'))
        flat = Image.open(io.BytesIO(base64.b64decode(url.split(',', 1)[1])))
        self.assertEqual(flat.mode, 'RGB')
        self.assertEqual(flat.size, (480, 320))
        pixel = flat.getpixel((0, 0))  # JPEG 有损压缩允许 ±2 的色差
        self.assertTrue(all(abs(a - b) <= 2 for a, b in zip(pixel, CREAM)), pixel)

    @patch('app.httpx.request', return_value=httpx.Response(200, json={'output': {'task_id': 'task-1', 'task_status': 'PENDING'}}))
    def test_palette_transparency_is_flattened_too(self, send):
        image = io.BytesIO()
        palette = Image.new('P', (480, 320))
        palette.info['transparency'] = 0
        palette.save(image, 'PNG')
        self.assertEqual(self.submit(image.getvalue()).status_code, 202)
        url = send.call_args.kwargs['json']['input']['media'][0]['url']
        self.assertTrue(url.startswith('data:image/jpeg;base64,'))

    @patch('app.httpx.request')
    def test_animated_upload_is_rejected(self, send):
        frames = [Image.new('RGB', (480, 320), color) for color in ((255, 0, 0), (0, 255, 0))]
        animation = io.BytesIO()
        frames[0].save(animation, 'PNG', save_all=True, append_images=frames[1:], duration=100, loop=0)
        self.assertEqual(self.submit(animation.getvalue()).status_code, 400)
        send.assert_not_called()

    @patch('app.httpx.request')
    def test_missing_model_disables_generation(self, send):
        del os.environ['WAN_MODEL']
        self.client = create_app().test_client()
        self.assertIn('data-ready="false"', self.client.get('/').get_data(as_text=True))
        self.assertEqual(self.submit(self.photo()).status_code, 503)
        send.assert_not_called()

    def test_api_base_override_still_requires_a_trusted_https_endpoint(self):
        os.environ['WAN_DASHSCOPE_BASE_URL'] = 'https://dashscope.aliyuncs.com/api/v1/'
        self.assertEqual(create_app().test_client().get('/').status_code, 200)
        os.environ['WAN_DASHSCOPE_BASE_URL'] = 'https://token-plan.maas.qianwenaiapi.com.evil.example/compatible-mode/v1'
        with self.assertRaises(ValueError):
            create_app()

    @patch('flask.Flask.run')
    @patch('dotenv.load_dotenv')
    def test_python_entry_point_loads_local_config_and_starts_on_loopback(self, load, run):
        script = Path(__file__).resolve().with_name('app.py')
        runpy.run_path(str(script), run_name='__main__')
        load.assert_called_once_with(str(script.with_name('.env')))
        run.assert_called_once_with(host='127.0.0.1', port=5050)

    @patch('app.httpx.request')
    def test_invalid_uploads_and_unsigned_requests_do_not_contact_wan(self, send):
        for invalid in (b'broken', self.photo(size=(100, 100))):
            self.assertEqual(self.submit(invalid).status_code, 400)
        self.assertEqual(self.submit(self.photo(), action='other').status_code, 400)
        self.assertEqual(self.submit(self.photo(), headers={}).status_code, 403)
        self.assertEqual(self.client.get('/api/jobs/task-1').status_code, 404)
        self.assertEqual(self.client.get('/', headers={'Host': 'other.example'}).status_code, 400)
        send.assert_not_called()

    @patch('app.httpx.request')
    def test_max_upload_and_missing_key(self, send):
        oversized = self.client.post('/api/jobs', data=b'0' * (11 * 1024 * 1024),
                                     content_type='multipart/form-data; boundary=test', headers=self.headers)
        self.assertEqual(oversized.status_code, 413)
        del os.environ['DASHSCOPE_API_KEY']
        self.client = create_app().test_client()
        self.assertEqual(self.submit(self.photo()).status_code, 503)
        send.assert_not_called()

    @patch('app.httpx.request')
    def test_failed_task_preserves_error_code_without_leaking_raw_response(self, send):
        send.side_effect = [
            httpx.Response(200, json={'output': {'task_id': 'task-1', 'task_status': 'PENDING'}}),
            httpx.Response(200, json={'output': {'task_status': 'FAILED', 'code': 'DataInspectionFailed', 'message': 'private content'}}),
        ]
        pending = self.submit(self.photo()).json
        failed = self.client.get('/api/jobs/' + pending['token'])
        self.assertEqual(failed.json['status'], 'FAILED')
        self.assertIn('DataInspectionFailed', failed.json['detail'])
        self.assertNotIn('private content', failed.get_data(as_text=True))

    @patch('app.httpx.request', side_effect=httpx.ReadTimeout('timed out'))
    def test_submit_timeout_warns_against_duplicate_charge(self, send):
        result = self.submit(self.photo())
        self.assertEqual(result.status_code, 504)
        self.assertIn('不要立即重复提交', result.json['detail'])
        send.assert_called_once()

    def test_public_host_requires_authentication(self):
        os.environ['WAN_STUDIO_HOST'] = 'studio.example.com'
        with self.assertRaises(ValueError):
            create_app()
        os.environ['WAN_STUDIO_PASSWORD'] = 'private-password'
        self.client = create_app().test_client()
        self.assertEqual(self.client.get('https://studio.example.com/').status_code, 401)
        creds = base64.b64encode(b'studio:private-password').decode()
        self.assertEqual(self.client.get('https://studio.example.com/', headers={'Authorization': 'Basic ' + creds}).status_code, 200)



    # ---- 图生图：上传照片 → 2.5D 形象（/api/portrait）----

    PORTRAIT_OK = {'output': {'choices': [{'message': {'content': [
        {'type': 'image', 'image': 'https://example.com/pet.png'}]}}]}}

    def portrait(self, photo=None, description='', headers=None):
        data = {'description': description, 'image': (io.BytesIO(photo or self.photo()), 'pet.png')}
        return self.client.post('/api/portrait', data=data, headers=self.headers if headers is None else headers)

    @patch('app.httpx.get')
    @patch('app.httpx.request')
    def test_portrait_uses_uploaded_photo_as_reference(self, send, download):
        photo = self.photo()
        send.return_value = httpx.Response(200, json=self.PORTRAIT_OK)
        download.return_value = httpx.Response(200, content=photo, headers={'Content-Type': 'image/png'})
        result = self.portrait(photo, description='耳朵尖有一撮白毛')
        self.assertEqual(result.status_code, 200)
        image = result.json['image']
        self.assertTrue(image.startswith('data:image/png;base64,'))
        with Image.open(io.BytesIO(base64.b64decode(image.split(',', 1)[1]))) as made:
            self.assertEqual(made.size, (480, 480))
        args, kwargs = send.call_args
        self.assertEqual(args, ('POST', 'https://token-plan.maas.qianwenaiapi.com/api/v1/services/aigc/multimodal-generation/generation'))
        self.assertNotIn('X-DashScope-Async', kwargs['headers'])
        payload = kwargs['json']
        self.assertEqual(payload['model'], 'test-wan-image')
        content = payload['input']['messages'][0]['content']
        self.assertEqual(len(content), 2)
        self.assertEqual(content[0], {'image': 'data:image/png;base64,' + base64.b64encode(photo).decode()})
        text = content[1]['text']
        self.assertIn('2.5D', text)
        self.assertIn('#FEECD4', text)
        self.assertIn('以上传图同一只宠物为原型', text)
        self.assertIn('耳朵尖有一撮白毛', text)
        self.assertNotIn('{subject}', text)
        self.assertEqual(payload['parameters']['size'], '1024*1024')
        self.assertEqual(payload['parameters']['n'], 1)
        self.assertFalse(payload['parameters']['prompt_extend'])
        self.assertTrue(payload['parameters']['watermark'])
        self.assertTrue(payload['parameters']['negative_prompt'])
        download.assert_called_once_with('https://example.com/pet.png', timeout=60, follow_redirects=False)

    @patch('app.httpx.get')
    @patch('app.httpx.request')
    def test_portrait_description_is_optional(self, send, download):
        send.return_value = httpx.Response(200, json=self.PORTRAIT_OK)
        download.return_value = httpx.Response(200, content=self.photo(), headers={'Content-Type': 'image/png'})
        self.assertEqual(self.portrait().status_code, 200)
        text = send.call_args.kwargs['json']['input']['messages'][0]['content'][1]['text']
        self.assertNotIn('用户补充', text)
        self.assertIn('以上传图同一只宠物为原型', text)

    @patch('app.httpx.get')
    @patch('app.httpx.request')
    def test_portrait_flattens_transparent_upload_and_result(self, send, download):
        send.return_value = httpx.Response(200, json=self.PORTRAIT_OK)
        download.return_value = httpx.Response(200, content=self.photo('RGBA'), headers={'Content-Type': 'image/png'})
        self.assertEqual(self.portrait(self.photo('RGBA')).status_code, 200)
        reference = send.call_args.kwargs['json']['input']['messages'][0]['content'][0]['image']
        self.assertTrue(reference.startswith('data:image/jpeg;base64,'))
        self.assertTrue(self.portrait().json['image'].startswith('data:image/png;base64,'))

    def test_square_image_crops_and_resizes(self):
        wide = io.BytesIO()
        Image.new('RGB', (900, 300), (255, 0, 0)).save(wide, 'PNG')
        with Image.open(io.BytesIO(square_image(wide.getvalue()))) as made:
            self.assertEqual(made.size, (480, 480))

    def test_reference_image_is_downscaled_when_large(self):
        noise = Image.effect_noise((2400, 2400), 64).convert('RGB')
        raw = io.BytesIO()
        noise.save(raw, 'PNG')
        self.assertGreater(len(raw.getvalue()), 4 * 1024 * 1024)
        mime, data = reference_image('image/png', raw.getvalue())
        self.assertEqual(mime, 'image/jpeg')
        self.assertLess(len(data), len(raw.getvalue()))
        with Image.open(io.BytesIO(data)) as shrunk:
            self.assertLessEqual(max(shrunk.size), 2048)

    @patch('app.httpx.request')
    def test_portrait_requires_photo_and_csrf(self, send):
        self.assertEqual(self.client.post('/api/portrait', data={'description': '猫'},
                                          headers=self.headers).status_code, 400)
        self.assertEqual(self.client.post('/api/portrait',
                                          data={'image': (io.BytesIO(b'broken'), 'pet.png')},
                                          headers=self.headers).status_code, 400)
        self.assertEqual(self.portrait(description='长' * 201).status_code, 400)
        self.assertEqual(self.portrait(headers={}).status_code, 403)
        send.assert_not_called()

    @patch('app.httpx.request')
    def test_portrait_without_image_model_is_disabled(self, send):
        del os.environ['WAN_IMAGE_MODEL']
        self.client = create_app().test_client()
        self.assertIn('data-portrait-ready="false"', self.client.get('/').get_data(as_text=True))
        self.assertEqual(self.portrait().status_code, 503)
        send.assert_not_called()

    def test_upload_comes_before_portrait_and_video(self):
        html = self.client.get('/').get_data(as_text=True)
        self.assertIn('上传照片', html)
        self.assertIn('生成 2.5D 形象', html)
        self.assertIn('id="image" name="image"', html)
        self.assertIn('id="portrait-accept"', html)
        self.assertIn('id="portrait-confirm"', html)
        self.assertIn('id="download-portrait"', html)
        self.assertIn('id="progress"', html)
        self.assertIn('下载图片', html)
        self.assertIn('state/images/', html)
        self.assertIn('data-portrait-ready="true"', html)
        self.assertTrue(html.index('上传照片') < html.index('生成 2.5D 形象') < html.index('选择动作'))

    @patch('app.httpx.get')
    @patch('app.httpx.request')
    def test_portrait_rejects_unsafe_or_missing_image_url(self, send, download):
        for content in ([{'type': 'image', 'image': 'http://example.com/pet.png'}],
                        [{'type': 'text', 'text': 'no image'}], []):
            send.return_value = httpx.Response(200, json={'output': {'choices': [{'message': {'content': content}}]}})
            self.assertEqual(self.portrait().status_code, 502)
        download.assert_not_called()

    @patch('app.httpx.get')
    @patch('app.httpx.request')
    def test_portrait_download_failures_are_502(self, send, download):
        send.return_value = httpx.Response(200, json=self.PORTRAIT_OK)
        download.side_effect = httpx.ConnectError('boom')
        self.assertEqual(self.portrait().status_code, 502)
        download.side_effect = None
        for response in (httpx.Response(200, content=b'broken', headers={'Content-Type': 'image/png'}),
                         httpx.Response(200, content=self.photo(), headers={'Content-Type': 'text/html'}),
                         httpx.Response(403, content=b'', headers={'Content-Type': 'image/png'}),
                         httpx.Response(200, content=b'0' * (MAX_IMAGE + 1), headers={'Content-Type': 'image/png'})):
            download.return_value = response
            self.assertEqual(self.portrait().status_code, 502)

    @patch('app.httpx.request')
    def test_portrait_upstream_error_and_timeout(self, send):
        send.return_value = httpx.Response(400, json={'code': 'InvalidParameter', 'message': 'private detail'})
        result = self.portrait()
        self.assertEqual(result.status_code, 502)
        self.assertIn('InvalidParameter', result.json['detail'])
        self.assertNotIn('private detail', result.get_data(as_text=True))
        send.return_value = None
        send.side_effect = httpx.ReadTimeout('timed out')
        result = self.portrait()
        self.assertEqual(result.status_code, 504)
        self.assertIn('请勿连续重复提交', result.json['detail'])

    @patch('app.httpx.get')
    @patch('app.httpx.request')
    def test_portrait_is_saved_and_downloadable(self, send, download):
        send.return_value = httpx.Response(200, json=self.PORTRAIT_OK)
        download.return_value = httpx.Response(200, content=self.photo(), headers={'Content-Type': 'image/png'})
        result = self.portrait()
        self.assertEqual(result.status_code, 200)
        data = result.json
        self.assertRegex(data['image_id'], r'^[0-9a-f]{24}$')
        saved = os.path.join(self.state, 'images', data['image_id'] + '.png')
        self.assertTrue(os.path.isfile(saved))
        with Image.open(saved) as made:
            self.assertEqual(made.size, (480, 480))
        viewed = self.client.get(data['view_url'])
        self.assertEqual(viewed.status_code, 200)
        self.assertEqual(viewed.mimetype, 'image/png')
        self.assertIn('private', viewed.headers['Cache-Control'])
        viewed.close()
        downloaded = self.client.get(data['download_url'])
        self.assertEqual(downloaded.status_code, 200)
        self.assertIn('attachment', downloaded.headers['Content-Disposition'])
        downloaded.close()
        self.assertEqual(self.client.get('/api/portraits/' + '0' * 24 + '/image').status_code, 404)
        self.assertEqual(self.client.get('/api/portraits/NOTHEX/image').status_code, 404)


if __name__ == '__main__':
    unittest.main()
