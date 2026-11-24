"""聊天人设与安全边界（FRD F09-1/2/7）。

人设上下文=档案+最新初筛状态+免责；输出 JSON {reply, emotion}；
禁词（确诊/治疗/诊断）或解析失败抛 ChatReplyError，由 API 降级模板安抚。
红线关键词命中走 redline_response：零 LLM、附就医引导卡（附录 F 文案）。
"""
import json
import re
from pathlib import Path
from typing import Optional

import yaml

EMOTIONS = ('happy', 'relaxed', 'annoyed', 'fearful', 'painful', 'seeking_attention')
FORBIDDEN_WORDS = ('确诊', '治疗', '诊断')
CHAT_DISCLAIMER = 'AI 结果仅供日常健康管理参考,不替代专业兽医诊断'
REDLINE_SUBTITLE = '该症状可能危及生命,请不要等待观察,尽快前往具备急诊能力的宠物医院。'
_FENCE = re.compile(r'^\s*```(?:json)?\s*|\s*```\s*$', re.IGNORECASE)
_SICK_STATES = ('observe', 'vet', 'urgent')
_SEX_LABELS = {'male': '弟弟', 'female': '妹妹', 'unknown': '小朋友'}
_BODY_LABELS = {'slim': '偏瘦', 'standard': '标准', 'chubby': '偏胖'}


class ChatReplyError(ValueError):
    """LLM 输出不可用（格式错误或含禁词），应降级模板。"""


FORMAT_REMINDER = ('你上一次的回复不是 JSON。请忘记刚才的输出，'
                   '严格只输出一个 JSON 对象：{"reply": "一句人设回复", "emotion": "六种情绪之一"}，'
                   '不要输出表情符号、解释或任何 JSON 以外的内容。')


def load_chat_config(directory: Path) -> Optional[dict]:
    path = Path(directory) / 'chat.yaml'
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8'))
        version = data['version']
        if not isinstance(version, str) or not version or len(version) > 32:
            raise ValueError('bad version')
        for key in ('context_window', 'input_max', 'reply_max'):
            if not isinstance(data[key], int) or data[key] <= 0:
                raise ValueError(f'{key} must be a positive integer')
        keywords = data['redline_keywords']
        if not isinstance(keywords, list) or not keywords or not all(isinstance(k, str) and k.strip() for k in keywords):
            raise ValueError('redline_keywords must be a non-empty string list')
        for key in ('disclaimer', 'fallback_reply', 'redline_reply'):
            value = data[key]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f'{key} must be non-empty')
        # 免责文案本身含「诊断」一词（边界声明），禁词检查只约束回复模板。
        for key in ('fallback_reply', 'redline_reply', 'audio_fallback_reply'):
            if any(word in data[key] for word in FORBIDDEN_WORDS):
                raise ValueError(f'{key}: forbidden word')
        threshold = data['audio_confidence_threshold']
        if not isinstance(threshold, (int, float)) or not 0 < threshold <= 1:
            raise ValueError('audio_confidence_threshold must be in (0, 1]')
        for key in ('audio_min_seconds', 'audio_max_seconds'):
            if not isinstance(data[key], int) or data[key] <= 0:
                raise ValueError(f'{key} must be a positive integer')
        if data['audio_min_seconds'] >= data['audio_max_seconds']:
            raise ValueError('audio_min_seconds must be below audio_max_seconds')
        for key in ('unrecognized_label', 'audio_footer', 'audio_fallback_reply'):
            value = data[key]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f'{key} must be non-empty')
        return {'version': version, 'context_window': data['context_window'],
                'input_max': data['input_max'], 'reply_max': data['reply_max'],
                'disclaimer': data['disclaimer'], 'redline_keywords': list(keywords),
                'fallback_reply': data['fallback_reply'], 'redline_reply': data['redline_reply'],
                'audio_confidence_threshold': float(threshold),
                'audio_min_seconds': data['audio_min_seconds'], 'audio_max_seconds': data['audio_max_seconds'],
                'unrecognized_label': data['unrecognized_label'], 'audio_footer': data['audio_footer'],
                'audio_fallback_reply': data['audio_fallback_reply']}
    except Exception as exc:
        raise RuntimeError(f'Invalid chat config: {path}') from exc


def detect_redline(text: str, config: dict) -> bool:
    return any(keyword in text for keyword in config['redline_keywords'])


def pet_profile_lines(pet: dict) -> list[str]:
    profile = [f"名字:{pet['name']}", f"种类:{pet.get('species') or 'cat'}"]
    if pet.get('breed'):
        profile.append(f"品种:{pet['breed']}")
    profile.append(f"性别:{_SEX_LABELS.get(pet.get('sex'), '小朋友')}")
    if pet.get('age_months') is not None:
        profile.append(f"月龄:{pet['age_months']}个月")
    profile.append('已绝育' if pet.get('neutered') else '未绝育')
    if pet.get('body_type'):
        profile.append(f"体型:{_BODY_LABELS.get(pet['body_type'], pet['body_type'])}")
    return profile


def persona_tone(screening_state: Optional[str]) -> str:
    if screening_state in _SICK_STATES:
        return '你最近身体不适(最新初筛提示需要关注),回复要体现不舒服、蔫蔫的语气,可以提到自己没精神或肚子不舒服,但绝不能说出具体病名或做医学判断。'
    return '你最近状态不错,回复活泼亲人。'


def build_persona_messages(pet: dict, screening_state: Optional[str], history: list[dict], config: dict) -> list[dict]:
    """system 人设 + 最近上下文（history 已按窗口截取，role=user/pet）。"""
    profile = pet_profile_lines(pet)
    tone = persona_tone(screening_state)
    system = (
        f"你扮演一只宠物猫,以第一人称和主人撒娇聊天。档案:{'、'.join(profile)}。{tone}"
        f"只聊日常陪伴与感受,不做诊断、不开处方、不推荐药物;回复严禁出现「确诊」「治疗」「诊断」字样。"
        f"回复不超过{config['reply_max']}字。"
        f"仅输出 JSON:{{\"reply\": \"一句人设回复\", \"emotion\": \"happy/relaxed/annoyed/fearful/painful/seeking_attention 之一\"}}。"
        f"JSON 之外一个字都不要输出,禁止单独输出表情符号。"
        f"免责边界:{config['disclaimer']}。"
    )
    messages = [{'role': 'system', 'content': system}]
    for item in history:
        messages.append({'role': 'user' if item['role'] == 'user' else 'assistant',
                         'content': item['content'] or ''})
    return messages


def _extract_json_object(text: str) -> Optional[str]:
    """从夹杂说明文字的输出中截取第一个完整的 JSON 对象。"""
    start = text.find('{')
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == '\\':
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return None


def parse_chat_reply(raw: str, config: dict) -> dict:
    text = _FENCE.sub('', raw or '').strip()
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        candidate = _extract_json_object(text)
        if candidate is None:
            raise ChatReplyError('chat reply is not valid JSON')
        try:
            data = json.loads(candidate)
        except ValueError as exc:
            raise ChatReplyError('chat reply is not valid JSON') from exc
    if not isinstance(data, dict):
        raise ChatReplyError('chat reply is not a JSON object')
    reply = data.get('reply')
    if not isinstance(reply, str) or not reply.strip():
        raise ChatReplyError('empty reply')
    if any(word in reply for word in FORBIDDEN_WORDS):
        raise ChatReplyError('chat reply contains forbidden words')
    emotion = data.get('emotion')
    if emotion not in EMOTIONS:
        emotion = 'relaxed'
    return {'reply': reply.strip()[:config['reply_max']], 'emotion': emotion}


def fallback_reply(config: dict) -> dict:
    return {'reply': config['fallback_reply'], 'emotion': 'relaxed', 'source': 'template'}


def redline_response(config: dict) -> dict:
    return {'reply': config['redline_reply'], 'emotion': 'painful', 'source': 'template',
            'guidance': {'title': '建议立即就医', 'text': REDLINE_SUBTITLE, 'route': 'screening'}}
