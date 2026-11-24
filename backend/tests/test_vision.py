"""F15 视觉 AI 照片初筛参考（qwen-vl）。

解析契约、判级映射、边界（未配置显式 503 / 解析失败 502 / 类型大小校验）、用量审计。
"""
import base64
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import os

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import AiUsageLog, Base
from app.providers.fake import FakeVisionProvider
from app.services.identity import create_email_account, verify_email
from app.services.vision import (VISION_DISCLAIMER, VisionParseError, build_vision_prompt,
                                 parse_vision_reply)
from test_auth_api import AuthAPIFixture

VALID_REPLY = json.dumps({'observations': ['双眼清亮，无分泌物', '毛色顺滑'],
                          'suggestion': 'normal', 'summary': '状态良好',
                          'advice': '继续保持'}, ensure_ascii=False)


def _png_bytes(size: int = 64) -> bytes:
    # 最小合法 PNG 头 + 填充，路由只校验类型与大小，不解码像素
    header = b'\x89PNG\r\n\x1a\n' + b'0' * max(0, size - 8)
    return header


class VisionLogicTests(unittest.TestCase):
    def test_parse_valid_reply(self):
        parsed = parse_vision_reply(VALID_REPLY)
        self.assertEqual(parsed['suggestion'], 'normal')
        self.assertEqual(len(parsed['observations']), 2)
        self.assertTrue(parsed['summary'])
        self.assertTrue(parsed['advice'])

    def test_parse_strips_code_fence(self):
        wrapped = '```json\n' + VALID_REPLY + '\n```'
        self.assertEqual(parse_vision_reply(wrapped)['suggestion'], 'normal')

    def test_parse_unknown_level_falls_back_to_observe(self):
        reply = json.dumps({'observations': ['左眼分泌物待确认'], 'suggestion': 'serious'},
                           ensure_ascii=False)
        self.assertEqual(parse_vision_reply(reply)['suggestion'], 'observe')

    def test_parse_missing_level_defaults_to_observe(self):
        self.assertEqual(parse_vision_reply('{"observations": []}')['suggestion'], 'observe')

    def test_parse_observations_clamped(self):
        reply = json.dumps({'observations': ['长' * 200] * 8, 'suggestion': 'vet'}, ensure_ascii=False)
        parsed = parse_vision_reply(reply)
        self.assertEqual(len(parsed['observations']), 5)
        self.assertTrue(all(len(item) <= 80 for item in parsed['observations']))

    def test_parse_non_json_raises(self):
        for raw in ('我觉得这只猫很好', '', '{} {}'):
            with self.assertRaises(VisionParseError):
                parse_vision_reply(raw)

    def test_parse_repairs_bare_keys(self):
        raw = '```json\n{"observations": [{精神状态: "活泼好动", 眼鼻分泌物: "无"}], "suggestion": "normal"}\n```'
        parsed = parse_vision_reply(raw)
        self.assertEqual(parsed['suggestion'], 'normal')
        self.assertEqual(parsed['observations'], ['精神状态: 活泼好动，眼鼻分泌物: 无'])

    def test_parse_dict_observations_become_strings(self):
        raw = json.dumps({'observations': [{'毛发': '浓密'}], 'suggestion': 'observe'}, ensure_ascii=False)
        self.assertEqual(parse_vision_reply(raw)['observations'], ['毛发: 浓密'])

    def test_prompt_contract(self):
        prompt = build_vision_prompt()
        for fragment in ('normal|observe|vet', 'observations', '不编造', '不做疾病诊断'):
            self.assertIn(fragment, prompt)
        self.assertIn('仅供参考', VISION_DISCLAIMER)


class VisionAPITests(AuthAPIFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.vision = FakeVisionProvider()
        self.client.app.state.vision_provider = self.vision
        token = self.login().json()['access_token']
        self.auth = {'Authorization': f'Bearer {token}'}
        self.client.post('/api/v1/consents', headers=self.auth,
                         json={'privacy': 'p1', 'terms': 't1'}).raise_for_status()
        response = self.client.post('/api/v1/pets', json={'name': '团团'}, headers=self.auth)
        self.pet_id = response.json()['id']

    def _post_check(self, *, data=None, mime='image/png'):
        headers = {**self.auth, 'Content-Type': mime}
        return self.client.post(f'/api/v1/pets/{self.pet_id}/vision-check',
                                headers=headers, content=data or _png_bytes())

    def test_normal_flow_returns_structured_result(self):
        response = self._post_check()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['source'], 'vision')
        self.assertEqual(body['suggestion'], 'normal')
        self.assertEqual(body['model'], 'fake-vision')
        self.assertEqual(body['disclaimer'], VISION_DISCLAIMER)
        self.assertNotIn('urgent_card', body)
        self.assertEqual(self.vision.calls[0]['mime'], 'image/png')
        with Session(self.engine) as db:
            logs = db.scalars(select(AiUsageLog).where(AiUsageLog.kind == 'vision')).all()
            self.assertEqual(len(logs), 1)
            self.assertEqual(logs[0].tokens_in, 220)

    def test_vet_level_attaches_urgent_card(self):
        self.client.app.state.vision_provider = FakeVisionProvider(
            reply='{"observations": ["前肢可见伤口"], "suggestion": "vet", "summary": "外伤", "advice": "尽快就医"}')
        response = self._post_check()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['suggestion'], 'vet')
        self.assertIn('urgent_card', response.json())

    def test_unconfigured_provider_is_explicit_503(self):
        self.client.app.state.vision_provider = None
        response = self._post_check()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['code'], 'VISION_UNAVAILABLE')
        self.assertIn('DASHSCOPE_API_KEY', response.json()['message'])

    def test_provider_failure_is_visible_502(self):
        self.client.app.state.vision_provider = FakeVisionProvider(fail_times=1)
        response = self._post_check()
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()['code'], 'VISION_PROVIDER_FAILED')

    def test_parse_failure_is_visible_502(self):
        self.client.app.state.vision_provider = FakeVisionProvider(reply='这张猫看起来不错')
        response = self._post_check()
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()['code'], 'VISION_PARSE_FAILED')

    def test_rejects_non_image_type(self):
        response = self.client.post(f'/api/v1/pets/{self.pet_id}/vision-check',
                                    headers={**self.auth, 'Content-Type': 'text/plain'},
                                    content=b'text')
        self.assertEqual(response.status_code, 415)

    def test_rejects_empty_and_oversize(self):
        response = self.client.post(f'/api/v1/pets/{self.pet_id}/vision-check',
                                    headers={**self.auth, 'Content-Type': 'image/png'},
                                    content=b'')
        self.assertEqual(response.status_code, 400)
        big = _png_bytes(9 * 1024 * 1024)
        response = self.client.post(f'/api/v1/pets/{self.pet_id}/vision-check',
                                    headers={**self.auth, 'Content-Type': 'image/png'},
                                    content=big)
        self.assertEqual(response.status_code, 413)

    def test_requires_ownership(self):
        response = self.client.post('/api/v1/pets/99999/vision-check',
                                    headers={**self.auth, 'Content-Type': 'image/png'},
                                    content=_png_bytes())
        self.assertEqual(response.status_code, 404)

    def test_requires_login(self):
        response = self.client.post(f'/api/v1/pets/{self.pet_id}/vision-check',
                                    headers={'Content-Type': 'image/png'},
                                    content=_png_bytes())
        self.assertEqual(response.status_code, 401)


class VisionSettingsTests(unittest.TestCase):
    ENV_BASE = {
        'DATABASE_URL': 'sqlite:///settings-test.db',
        'JWT_SECRET': 'test-signing-key-32-bytes-or-more',
        'PRIVACY_VERSION': 'p1', 'TERMS_VERSION': 't1',
        'PRIVACY_URL': 'https://example.com/privacy',
        'TERMS_URL': 'https://example.com/terms',
    }

    def _settings(self, **extra):
        from app.core.config import Settings
        env = {**self.ENV_BASE, **extra}
        return Settings.from_env(environ=env)

    def test_zhipu_key_wires_free_vision_model(self):
        settings = self._settings(ZHIPU_API_KEY='hex.secret')
        self.assertIsNotNone(settings.vision_provider)
        self.assertEqual(settings.vision_provider.provider, 'zhipu')
        self.assertEqual(settings.vision_provider.model, 'glm-4v-flash')

    def test_dashscope_key_wires_qwen_vl(self):
        settings = self._settings(DASHSCOPE_API_KEY='sk-test')
        self.assertIsNotNone(settings.vision_provider)
        self.assertEqual(settings.vision_provider.provider, 'dashscope')
        self.assertEqual(settings.vision_provider.model, 'qwen-vl-max')

    def test_zhipu_takes_priority_over_dashscope(self):
        settings = self._settings(ZHIPU_API_KEY='hex.secret', DASHSCOPE_API_KEY='sk-test')
        self.assertEqual(settings.vision_provider.provider, 'zhipu')

    def test_zhipu_key_wires_free_chat_model(self):
        settings = self._settings(ZHIPU_API_KEY='hex.secret')
        self.assertIsNotNone(settings.chat_llm_provider)
        self.assertEqual(settings.chat_llm_provider.provider, 'zhipu')
        self.assertEqual(settings.chat_llm_provider.model, 'glm-4-flash')

    def test_dashscope_key_wires_qwen_chat(self):
        settings = self._settings(DASHSCOPE_API_KEY='sk-test')
        self.assertIsNotNone(settings.chat_llm_provider)
        self.assertEqual(settings.chat_llm_provider.provider, 'dashscope')
        self.assertEqual(settings.chat_llm_provider.model, 'qwen-plus')

    def test_chat_zhipu_takes_priority_over_dashscope(self):
        settings = self._settings(ZHIPU_API_KEY='hex.secret', DASHSCOPE_API_KEY='sk-test')
        self.assertEqual(settings.chat_llm_provider.provider, 'zhipu')

    def test_no_key_leaves_chat_provider_none(self):
        settings = self._settings()
        self.assertIsNone(settings.chat_llm_provider)
        self.assertIsNone(settings.vision_provider)

    def test_no_key_means_no_provider(self):
        self.assertIsNone(self._settings().vision_provider)
