import io
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import httpx
from PIL import Image

import app as studio


def photo_bytes(size=(640, 480)):
    image = io.BytesIO()
    Image.new('RGB', size, (230, 190, 140)).save(image, 'JPEG')
    return image.getvalue()


class StudioTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            'ZHIPU_API_KEY': 'test-key',
            'ZHIPU_VISION_MODEL': 'glm-5.3-flash',
            'ZHIPU_IMAGE_MODEL': 'glm-image',
            'ZHIPU_IMAGE_SIZE': '1024x1024',
            'JIMENG_CLI': sys.executable,
            'JIMENG_VIDEO_MODEL': 'Seedance_2.5',
            'JIMENG_PROJECT_ID': 'test-project',
        }, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        state = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(state.cleanup)
        self.state = state.name
        self.release = threading.Event()

        def blocking(job, update):
            update(stage='生成中')
            self.release.wait(5)
            raise studio.JobError('测试中止')

        self.client = studio.create_app(state_dir=self.state, worker=blocking).test_client()
        self.headers = {'X-Studio-Request': '1'}

    def tearDown(self):
        self.release.set()

    def write_image(self, image_id='a' * 24):
        folder = os.path.join(self.state, 'images')
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, image_id + '.png'), 'wb') as saved:
            saved.write(photo_bytes())
        return image_id

    def test_index_is_upload_flow_with_actions(self):
        html = self.client.get('/').get_data(as_text=True)
        self.assertIn('id="image"', html)
        self.assertIn('glm-5.3-flash', html)
        self.assertIn('glm-image', html)
        self.assertIn('Seedance_2.5', html)
        self.assertIn('name="action"', html)
        self.assertIn('id="duration"', html)
        self.assertIn('待机', html)
        self.assertIn('提示词配置区', html)
        # 提示词只在 app.py 配置区维护，页面不再提供输入框
        self.assertNotIn('id="pet-desc"', html)
        self.assertNotIn('id="compare-prompt"', html)
        self.assertNotIn('Wan 3.0', html)
        self.assertNotIn('test-key', html)

    def test_portrait_requires_photo(self):
        self.assertEqual(self.client.post('/api/portrait', headers=self.headers).status_code, 400)
        self.assertEqual(self.client.post('/api/portrait').status_code, 403)

    @patch('app.httpx.request')
    def test_portrait_pipeline_saves_image(self, send):
        with patch.object(studio, 'COMPARE_PROMPT', ''):
            send.side_effect = [
                httpx.Response(200, json={'choices': [{'message': {'content': '橘白猫，白色胸口，金色眼睛，坐姿'}}]}),
                httpx.Response(200, json={'data': [{'url': 'https://example.com/pet.png'}]}),
                httpx.Response(200, headers={'Content-Type': 'image/png'}, content=photo_bytes((1024, 1024))),
            ]
            result = self.client.post('/api/portrait', headers=self.headers, data={
                'image': (io.BytesIO(photo_bytes()), 'cat.jpg'),
            }, content_type='multipart/form-data')
        self.assertEqual(result.status_code, 200)
        data = result.json
        self.assertTrue(data['image_id'])
        self.assertTrue(data['image'].startswith('data:image/png;base64,'))
        vision = send.call_args_list[0]
        self.assertEqual(vision.kwargs['json']['model'], 'glm-5.3-flash')
        gen = send.call_args_list[1]
        prompt = gen.kwargs['json']['prompt']
        self.assertIn('橘白猫', prompt)
        self.assertIn('cartoon 2.5D', prompt)
        self.assertNotIn('Owner notes', prompt)
        saved = self.client.get(data['download_url'])
        self.assertEqual(saved.status_code, 200)
        with Image.open(io.BytesIO(saved.data)) as image:
            self.assertEqual(image.size, (studio.PORTRAIT_SIZE, studio.PORTRAIT_SIZE))

    @patch('app.httpx.request')
    def test_compare_prompt_renders_extra_portrait_only_for_web(self, send):
        with patch.object(studio, 'COMPARE_PROMPT', '改成像素风'):
            send.side_effect = [
                httpx.Response(200, json={'choices': [{'message': {'content': '橘白猫'}}]}),
                httpx.Response(200, json={'data': [{'url': 'https://example.com/a.png'}]}),
                httpx.Response(200, headers={'Content-Type': 'image/png'}, content=photo_bytes((1024, 1024))),
                httpx.Response(200, json={'data': [{'url': 'https://example.com/b.png'}]}),
                httpx.Response(200, headers={'Content-Type': 'image/png'}, content=photo_bytes((1024, 1024))),
            ]
            result = self.client.post('/api/portrait', headers=self.headers, data={
                'image': (io.BytesIO(photo_bytes()), 'cat.jpg'),
            }, content_type='multipart/form-data')
        self.assertEqual(result.status_code, 200)
        data = result.json
        self.assertIn('像素风', send.call_args_list[3].kwargs['json']['prompt'])
        self.assertNotEqual(data['image_id'], data['compare']['image_id'])
        self.assertTrue(data['compare']['image'].startswith('data:image/png;base64,'))
        # 两张形象都能单独下载
        self.assertEqual(self.client.get(data['compare']['download_url']).status_code, 200)

    @patch('app.httpx.request')
    def test_compare_disabled_by_default(self, send):
        with patch.object(studio, 'COMPARE_PROMPT', ''):
            send.side_effect = [
                httpx.Response(200, json={'choices': [{'message': {'content': '橘白猫'}}]}),
                httpx.Response(200, json={'data': [{'url': 'https://example.com/a.png'}]}),
                httpx.Response(200, headers={'Content-Type': 'image/png'}, content=photo_bytes((1024, 1024))),
            ]
            result = self.client.post('/api/portrait', headers=self.headers, data={
                'image': (io.BytesIO(photo_bytes()), 'cat.jpg'),
            }, content_type='multipart/form-data')
        self.assertEqual(result.status_code, 200)
        self.assertNotIn('compare', result.json)
        self.assertEqual(len(send.call_args_list), 3)

    @patch('app.httpx.request')
    def test_portrait_upstream_error_is_reported(self, send):
        send.return_value = httpx.Response(429, json={'error': {'code': '1305', 'message': '访问量过大'}})
        result = self.client.post('/api/portrait', headers=self.headers, data={
            'image': (io.BytesIO(photo_bytes()), 'cat.jpg'),
        }, content_type='multipart/form-data')
        self.assertEqual(result.status_code, 502)
        self.assertIn('1305', result.json['detail'])

    def test_video_validations(self):
        image_id = self.write_image()
        bad_image = {'image_id': 'b' * 24, 'action': 'happy', 'duration': 5}
        self.assertEqual(self.client.post('/api/videos', json=bad_image, headers=self.headers).status_code, 404)
        bad_action = {'image_id': image_id, 'action': 'dance', 'duration': 5}
        self.assertEqual(self.client.post('/api/videos', json=bad_action, headers=self.headers).status_code, 400)
        bad_duration = {'image_id': image_id, 'action': 'happy', 'duration': 2}
        self.assertEqual(self.client.post('/api/videos', json=bad_duration, headers=self.headers).status_code, 400)

    def test_video_creates_job_with_action_prompt(self):
        image_id = self.write_image()
        result = self.client.post('/api/videos', json={
            'image_id': image_id, 'action': 'happy', 'duration': 5,
        }, headers=self.headers)
        self.assertEqual(result.status_code, 202)
        job_id = result.json['job_id']
        with open(os.path.join(self.state, 'jobs', job_id, 'job.json'), encoding='utf-8') as saved:
            job = json.load(saved)
        self.assertEqual(job['action'], '开心')
        with open(os.path.join(self.state, 'jobs', job_id, 'prompt.txt'), encoding='utf-8') as saved:
            prompt = saved.read()
        self.assertIn('开心', prompt)
        self.assertIn('#FEECD4', prompt)
        self.assertIn('Negative prompt', prompt)
        self.assertTrue(os.path.isfile(os.path.join(self.state, 'jobs', job_id, 'first.png')))
        status = self.client.get('/api/videos/' + job_id).json
        self.assertIn(status['status'], ('queued', 'running'))
        self.assertEqual(self.client.get('/api/videos/' + 'f' * 16).status_code, 404)

    def test_worker_downloads_video_and_reports_credits(self):
        job_dir = os.path.join(self.state, 'jobs', 'job1')
        os.makedirs(job_dir)
        for name in ('prompt.txt', 'first.png'):
            with open(os.path.join(job_dir, name), 'wb') as saved:
                saved.write(b'x')
        job = {'id': 'job1', 'job_dir': job_dir, 'state_dir': self.state, 'cli': 'jimeng-novel',
               'video_model': 'Seedance_2.5', 'project_id': 'test-project', 'duration': 5,
               'ratio': '1:1', 'resolution': '720p'}
        calls = []

        def fake_run(job_arg, args, timeout):
            calls.append(args)
            if args[0] == 'node' and args[1] == 'result':
                with open(os.path.join(job_dir, 'job1.mp4'), 'wb') as saved:
                    saved.write(b'fake-mp4')
                return {'status': 'success', 'data': {'items': [{'status': 'succeeded', 'credits': {'deducted': 36}}]}}
            return {'status': 'success', 'data': {'items': [{'status': 'running'}]}}

        with patch('app.run_cli', side_effect=fake_run):
            result = studio.run_video_job(job, lambda **fields: None)
        self.assertEqual(result['credits'], 36)
        self.assertTrue(os.path.isfile(os.path.join(self.state, 'videos', 'job1.mp4')))
        self.assertIn('duration=5', calls[0])

    def test_worker_reports_failed_generation(self):
        job_dir = os.path.join(self.state, 'jobs', 'job2')
        os.makedirs(job_dir)
        job = {'id': 'job2', 'job_dir': job_dir, 'state_dir': self.state, 'cli': 'jimeng-novel',
               'video_model': 'Seedance_2.5', 'project_id': 'test-project', 'duration': 5,
               'ratio': '1:1', 'resolution': '720p'}
        responses = [
            {'status': 'success', 'data': {}},
            {'status': 'success', 'data': {'items': [{'status': 'failed', 'error': {'message': '敏感内容'}}]}},
        ]
        with patch('app.run_cli', side_effect=responses):
            with self.assertRaises(studio.JobError) as caught:
                studio.run_video_job(job, lambda **fields: None)
        self.assertIn('敏感内容', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
