"""DashScope adapters for avatar generation.

Externally unverified: live acceptance needs DASHSCOPE_API_KEY and a reachable
image URL; the fake providers cover the local pipeline until then.
"""
import asyncio
import base64
import json

import httpx

from app.providers.base import EmotionResult, GeneratedAsset

DASHSCOPE_BASE = 'https://dashscope.aliyuncs.com/api/v1'


class DashScopeError(RuntimeError):
    pass


class DashScopeImageProvider:
    def __init__(self, api_key: str, model: str = 'qwen-image-edit', timeout: float = 120):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    async def edit(self, photo: bytes, prompt: str) -> GeneratedAsset:
        encoded = base64.b64encode(photo).decode()
        payload = {'model': self.model,
                   'input': {'messages': [{'role': 'user', 'content': [
                       {'image': f'data:image/png;base64,{encoded}'}, {'text': prompt}]}]},
                   'parameters': {'watermark': False}}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f'{DASHSCOPE_BASE}/services/aigc/multimodal-generation/generation',
                                         json=payload, headers={'Authorization': f'Bearer {self.api_key}'})
            data = response.json()
            if response.status_code != 200:
                raise DashScopeError(f'image edit failed: {data}')
            url = data['output']['choices'][0]['message']['content'][0]['image']
            image = await client.get(url)
            image.raise_for_status()
            return GeneratedAsset(image.content, 'image/png')


class DashScopeLLMProvider:
    """qwen-max 文本生成（初筛建议）。last_usage 供 ai_usage_logs 审计。"""

    provider = 'dashscope'

    def __init__(self, api_key: str, model: str = 'qwen-max', timeout: float = 60, transport=None):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.transport = transport
        self.last_usage: dict | None = None

    async def chat(self, messages: list[dict], *, temperature: float = 0.3, max_tokens: int = 800) -> str:
        payload = {'model': self.model, 'input': {'messages': messages},
                   'parameters': {'result_format': 'message', 'temperature': temperature, 'max_tokens': max_tokens}}
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            response = await client.post(f'{DASHSCOPE_BASE}/services/aigc/text-generation/generation',
                                         json=payload, headers={'Authorization': f'Bearer {self.api_key}'})
            data = response.json()
            if response.status_code != 200:
                raise DashScopeError(f'llm chat failed: {data}')
            try:
                content = data['output']['choices'][0]['message']['content']
            except (KeyError, IndexError, TypeError) as exc:
                raise DashScopeError(f'llm chat returned no content: {data}') from exc
            usage = data.get('usage') or {}
            self.last_usage = {'tokens_in': usage.get('input_tokens'), 'tokens_out': usage.get('output_tokens')}
            return content


class DashScopeAudioProvider:
    """qwen2-audio 情绪解读（F09-3）。外部未验证：需 DASHSCOPE_API_KEY 与可达音频 URL。"""

    provider = 'dashscope'

    def __init__(self, api_key: str, model: str = 'qwen2-audio-instruct', timeout: float = 60, transport=None):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.transport = transport
        self.last_usage: dict | None = None

    async def interpret(self, audio_url: str, prompt: str) -> EmotionResult:
        payload = {'model': self.model,
                   'input': {'messages': [{'role': 'user', 'content': [{'audio': audio_url}, {'text': prompt}]}]},
                   'parameters': {'result_format': 'message'}}
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            response = await client.post(f'{DASHSCOPE_BASE}/services/aigc/multimodal-generation/generation',
                                         json=payload, headers={'Authorization': f'Bearer {self.api_key}'})
            data = response.json()
            if response.status_code != 200:
                raise DashScopeError(f'audio interpret failed: {data}')
            try:
                parts = data['output']['choices'][0]['message']['content']
                text = next(part['text'] for part in parts if 'text' in part)
                parsed = json.loads(text.strip())
                emotion = parsed['emotion']
                confidence = float(parsed['confidence'])
            except (KeyError, IndexError, TypeError, ValueError, StopIteration) as exc:
                raise DashScopeError(f'audio interpret returned no usable result: {data}') from exc
            usage = data.get('usage') or {}
            self.last_usage = {'tokens_in': usage.get('input_tokens'), 'tokens_out': usage.get('output_tokens')}
            return EmotionResult(emotion, confidence)


class DashScopeVisionProvider:
    """qwen-vl 照片观察分析（视觉初筛参考）。需 DASHSCOPE_API_KEY。"""

    provider = 'dashscope'

    def __init__(self, api_key: str, model: str = 'qwen-vl-max', timeout: float = 60, transport=None):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.transport = transport
        self.last_usage: dict | None = None

    async def analyze(self, photo: bytes, prompt: str, *, mime: str = 'image/jpeg') -> str:
        encoded = base64.b64encode(photo).decode()
        payload = {'model': self.model,
                   'input': {'messages': [{'role': 'user', 'content': [
                       {'image': f'data:{mime};base64,{encoded}'}, {'text': prompt}]}]},
                   'parameters': {'result_format': 'message', 'temperature': 0.1}}
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            response = await client.post(
                f'{DASHSCOPE_BASE}/services/aigc/multimodal-generation/generation',
                json=payload, headers={'Authorization': f'Bearer {self.api_key}'})
            data = response.json()
            if response.status_code != 200:
                raise DashScopeError(f'vision analyze failed: {data}')
            try:
                parts = data['output']['choices'][0]['message']['content']
                text = next(part['text'] for part in parts if 'text' in part)
            except (KeyError, IndexError, TypeError, StopIteration) as exc:
                raise DashScopeError(f'vision analyze returned no text: {data}') from exc
            usage = data.get('usage') or {}
            self.last_usage = {'tokens_in': usage.get('input_tokens'), 'tokens_out': usage.get('output_tokens')}
            return text


class DashScopeVideoProvider:
    def __init__(self, api_key: str, model: str = 'wanx2.1-i2v-turbo', timeout: float = 120,
                 poll_seconds: float = 5, max_wait_seconds: float = 480):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.poll_seconds = poll_seconds
        self.max_wait_seconds = max_wait_seconds

    async def image_to_video(self, image: bytes, prompt: str, *, image_url=None) -> GeneratedAsset:
        if not image_url:
            raise DashScopeError('i2v requires a reachable image URL')
        payload = {'model': self.model, 'input': {'img_url': image_url, 'prompt': prompt},
                   'parameters': {'resolution': '720P', 'duration': 5, 'prompt_extend': True}}
        headers = {'Authorization': f'Bearer {self.api_key}', 'X-DashScope-Async': 'enable'}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            created = await client.post(f'{DASHSCOPE_BASE}/services/aigc/video-generation/video-synthesis',
                                        json=payload, headers=headers)
            data = created.json()
            if created.status_code != 200:
                raise DashScopeError(f'video submit failed: {data}')
            task_id = data['output']['task_id']
            deadline = asyncio.get_event_loop().time() + self.max_wait_seconds
            while True:
                await asyncio.sleep(self.poll_seconds)
                polled = await client.get(f'{DASHSCOPE_BASE}/tasks/{task_id}',
                                          headers={'Authorization': f'Bearer {self.api_key}'})
                output = polled.json()['output']
                if output['task_status'] == 'SUCCEEDED':
                    video = await client.get(output['video_url'])
                    video.raise_for_status()
                    return GeneratedAsset(video.content, 'video/mp4')
                if output['task_status'] in ('FAILED', 'UNKNOWN'):
                    raise DashScopeError(f'video task {output["task_status"]}: {output}')
                if asyncio.get_event_loop().time() > deadline:
                    raise DashScopeError('video task timed out')
