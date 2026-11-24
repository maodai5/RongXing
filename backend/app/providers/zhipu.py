"""智谱 AI 适配（glm-4 系列，OpenAI 兼容协议）。

glm-4-flash / glm-4v-flash 免费；需 ZHIPU_API_KEY。
"""
import base64

import httpx

ZHIPU_BASE = 'https://open.bigmodel.cn/api/paas/v4'


class ZhipuError(RuntimeError):
    pass


class ZhipuLLMProvider:
    """glm-4 文本生成（宠物聊天）。需 ZHIPU_API_KEY。"""

    provider = 'zhipu'

    def __init__(self, api_key: str, model: str = 'glm-4-flash', timeout: float = 60, transport=None):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.transport = transport
        self.last_usage: dict | None = None

    async def chat(self, messages: list[dict], *, temperature: float = 0.3, max_tokens: int = 800) -> str:
        payload = {'model': self.model, 'messages': messages,
                   'temperature': temperature, 'max_tokens': max_tokens}
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            response = await client.post(f'{ZHIPU_BASE}/chat/completions', json=payload,
                                         headers={'Authorization': f'Bearer {self.api_key}'})
            data = response.json()
            if response.status_code != 200:
                raise ZhipuError(f'llm chat failed: {data}')
            try:
                content = data['choices'][0]['message']['content']
            except (KeyError, IndexError, TypeError) as exc:
                raise ZhipuError(f'llm chat returned no content: {data}') from exc
            usage = data.get('usage') or {}
            self.last_usage = {'tokens_in': usage.get('prompt_tokens'), 'tokens_out': usage.get('completion_tokens')}
            return content


class ZhipuVisionProvider:
    """glm-4v 照片观察分析（视觉初筛参考）。需 ZHIPU_API_KEY。"""

    provider = 'zhipu'

    def __init__(self, api_key: str, model: str = 'glm-4v-flash', timeout: float = 60, transport=None):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.transport = transport
        self.last_usage: dict | None = None

    async def analyze(self, photo: bytes, prompt: str, *, mime: str = 'image/jpeg') -> str:
        encoded = base64.b64encode(photo).decode()
        payload = {'model': self.model, 'temperature': 0.1,
                   'messages': [{'role': 'user', 'content': [
                       {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{encoded}'}},
                       {'type': 'text', 'text': prompt}]}]}
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            response = await client.post(f'{ZHIPU_BASE}/chat/completions', json=payload,
                                         headers={'Authorization': f'Bearer {self.api_key}'})
            data = response.json()
            if response.status_code != 200:
                raise ZhipuError(f'vision analyze failed: {data}')
            try:
                content = data['choices'][0]['message']['content']
            except (KeyError, IndexError, TypeError) as exc:
                raise ZhipuError(f'vision analyze returned no content: {data}') from exc
            usage = data.get('usage') or {}
            self.last_usage = {'tokens_in': usage.get('prompt_tokens'), 'tokens_out': usage.get('completion_tokens')}
            return content
