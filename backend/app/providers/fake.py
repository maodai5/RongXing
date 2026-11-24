import io

from PIL import Image

from app.providers.base import EmotionResult, GeneratedAsset


class FakeImageProvider:
    def __init__(self):
        self.calls = []

    async def edit(self, photo: bytes, prompt: str) -> GeneratedAsset:
        self.calls.append(prompt)
        encoded = io.BytesIO()
        Image.new('RGB', (4, 4), (200, 120, 60)).save(encoded, 'PNG')
        return GeneratedAsset(encoded.getvalue(), 'image/png')


class FakeVideoProvider:
    def __init__(self, fail_actions=()):
        self.fail_actions = set(fail_actions)
        self.calls = []

    async def image_to_video(self, image: bytes, prompt: str, *, image_url=None) -> GeneratedAsset:
        self.calls.append(prompt)
        if any(action in prompt for action in self.fail_actions):
            raise RuntimeError(f'fake provider failure for {prompt}')
        return GeneratedAsset(b'fake-video:' + prompt.encode(), 'video/mp4')


class FakeAudioProvider:
    provider = 'fake'
    model = 'fake'

    def __init__(self, emotion: str = 'happy', confidence: float = 0.82, *, fail: bool = False):
        self.emotion = emotion
        self.confidence = confidence
        self.fail = fail
        self.calls = []
        self.last_usage = None

    async def interpret(self, audio_url: str, prompt: str) -> EmotionResult:
        self.calls.append({'audio_url': audio_url, 'prompt': prompt})
        if self.fail:
            raise RuntimeError('fake audio failure')
        self.last_usage = {'tokens_in': 60, 'tokens_out': 20}
        return EmotionResult(self.emotion, self.confidence)


class FakeLLMProvider:
    provider = 'fake'
    model = 'fake'

    def __init__(self, reply: str = '{"text": "建议观察精神与食欲变化。", "tips": ["记录变化"]}', *, fail_times: int = 0):
        self.reply = reply
        self.fail_times = fail_times
        self.calls = []
        self.last_usage = None

    async def chat(self, messages: list[dict], *, temperature: float = 0.3, max_tokens: int = 800) -> str:
        self.calls.append({'messages': messages, 'temperature': temperature, 'max_tokens': max_tokens})
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError('fake llm failure')
        self.last_usage = {'tokens_in': 100, 'tokens_out': 30}
        return self.reply


class FakeVisionProvider:
    provider = 'fake'
    model = 'fake-vision'

    def __init__(self, reply: str = '{"observations": ["双眼清亮"], "suggestion": "normal", "summary": "状态良好", "advice": "继续保持"}', *, fail_times: int = 0):
        self.reply = reply
        self.fail_times = fail_times
        self.calls = []
        self.last_usage = None

    async def analyze(self, photo: bytes, prompt: str, *, mime: str = 'image/jpeg') -> str:
        self.calls.append({'bytes': len(photo), 'prompt': prompt, 'mime': mime})
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError('fake vision failure')
        self.last_usage = {'tokens_in': 220, 'tokens_out': 60}
        return self.reply
