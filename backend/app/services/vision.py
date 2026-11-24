"""F15 视觉初筛参考：qwen-vl 照片观察的结构化解析与判级映射。

只描述照片可见现象，判级仅三档；解析失败显式报错，不静默降级。
"""
import json
import re

VISION_DISCLAIMER = 'AI 视觉分析仅供参考，不构成诊疗建议；如有异常请及时就医。'
SUGGESTION_LEVELS = ('normal', 'observe', 'vet')
MAX_OBSERVATIONS = 5
OBSERVATION_MAX_CHARS = 80

VISION_PROMPT = (
    '你是猫健康观察助手。请分析这张猫咪照片，只输出 JSON，不要输出任何其它文字：\n'
    '{"observations": ["最多5条观察"], "suggestion": "normal|observe|vet", '
    '"summary": "一句话总结", "advice": "一句话建议"}\n'
    '要求：\n'
    '1. observations 只描述照片中可见的现象：精神状态、眼鼻分泌物、毛发、姿态、'
    '可见外伤、呼吸状态、环境隐患；照片里无法确认的内容要注明"待确认"。\n'
    '2. 不编造照片以外的信息，不做疾病诊断，不给出药物建议。\n'
    '3. observations 必须至少 1 条：没有异常时也给出对照片的客观描述（如姿态、毛发、眼神）。\n'
    '3. suggestion 判级：照片可见明显外伤、呼吸窘迫、严重萎靡等 → vet；'
    '轻微异常或存在待确认项 → observe；无可疑现象 → normal。\n'
    '4. summary 与 advice 用中文，各不超过 60 字。'
)


class VisionParseError(ValueError):
    """模型输出无法解析为约定 JSON。"""


def build_vision_prompt() -> str:
    return VISION_PROMPT


_BARE_KEY = re.compile(r'([{,]\s*)([^"\'\[\]{}:]+?)\s*:')


def _repair_json(text: str) -> str:
    """给未加引号的字段名补引号（glm-4v-flash 偶发输出非标准 JSON）。"""
    return _BARE_KEY.sub(lambda m: f'{m.group(1)}"{m.group(2).strip()}":', text)


def _extract_json(raw: str) -> str:
    text = raw.strip()
    if text.startswith('```'):
        text = text.strip('`').strip()
        if text.startswith('json'):
            text = text[4:].strip()
    start = text.find('{')
    end = text.rfind('}')
    if start == -1 or end <= start:
        raise VisionParseError('no json object in reply')
    body = text[start:end + 1]
    try:
        json.loads(body)
        return body
    except json.JSONDecodeError:
        repaired = _repair_json(body)
        try:
            json.loads(repaired)
        except json.JSONDecodeError as exc:
            raise VisionParseError(f'unrepairable json: {exc.msg}') from exc
        return repaired


def _clean_observations(values) -> list[str]:
    items = []
    for value in values if isinstance(values, list) else []:
        if isinstance(value, dict):
            value = '，'.join(f'{key}: {item}' for key, item in value.items()
                              if isinstance(item, str)) or None
        if not isinstance(value, str):
            continue
        text = value.strip()[:OBSERVATION_MAX_CHARS]
        if text:
            items.append(text)
        if len(items) >= MAX_OBSERVATIONS:
            break
    return items


def parse_vision_reply(raw: str) -> dict:
    """解析模型回复；判级未知一律降为 observe，宁保守不漏报。"""
    try:
        data = json.loads(_extract_json(raw))
    except (json.JSONDecodeError, VisionParseError) as exc:
        raise VisionParseError(f'invalid vision reply: {raw[:120]}') from exc
    if not isinstance(data, dict):
        raise VisionParseError('vision reply is not an object')
    suggestion = data.get('suggestion')
    if suggestion not in SUGGESTION_LEVELS:
        suggestion = 'observe'
    return {
        'observations': _clean_observations(data.get('observations')),
        'suggestion': suggestion,
        'summary': str(data.get('summary') or '').strip()[:120],
        'advice': str(data.get('advice') or '').strip()[:120],
    }
