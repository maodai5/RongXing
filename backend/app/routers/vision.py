"""F15 视觉 AI 照片初筛参考（qwen-vl）。

上传猫咪照片返回结构化观察与建议等级；未配置 DASHSCOPE_API_KEY 时显式 503，不静默降级。
图片以原始字节直传，Content-Type 标明图片类型，无需 multipart 依赖。
"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import consented_user, owned_pet
from app.core.errors import APIError
from app.models import AiUsageLog, User
from app.routers.screening import REDLINE_CARD
from app.services.vision import (VISION_DISCLAIMER, VisionParseError, parse_vision_reply,
                                 build_vision_prompt)

router = APIRouter(prefix='/api/v1/pets', tags=['vision'])

MAX_IMAGE_BYTES = 8 * 1024 * 1024
ALLOWED_MIME = {'image/jpeg', 'image/png', 'image/webp'}


def _validate_image(request: Request, data: bytes) -> str:
    mime = (request.headers.get('content-type') or '').split(';')[0].strip().lower()
    if mime not in ALLOWED_MIME:
        raise APIError(415, 'UNSUPPORTED_MEDIA_TYPE', '仅支持 JPEG / PNG / WebP 图片')
    if not data:
        raise APIError(400, 'EMPTY_FILE', '图片内容为空')
    if len(data) > MAX_IMAGE_BYTES:
        raise APIError(413, 'PAYLOAD_TOO_LARGE', '图片超过 8MB，请压缩后重试')
    return mime


@router.post('/{pet_id}/vision-check')
async def vision_check(pet_id: int, request: Request,
                       user: User = Depends(consented_user),
                       db: Session = Depends(get_db)):
    pet = owned_pet(pet_id, user, db)
    provider = getattr(request.app.state, 'vision_provider', None)
    if provider is None:
        raise APIError(503, 'VISION_UNAVAILABLE',
                       '视觉模型未配置：请在 backend/.env 设置 DASHSCOPE_API_KEY 后重启后端')

    data = await request.body()
    mime = _validate_image(request, data)
    try:
        raw = await provider.analyze(data, build_vision_prompt(), mime=mime)
    except Exception as exc:  # 网络/鉴权/限流统一对外可见
        raise APIError(502, 'VISION_PROVIDER_FAILED', f'视觉模型调用失败：{exc}') from exc

    try:
        result = parse_vision_reply(raw)
    except VisionParseError as exc:
        raise APIError(502, 'VISION_PARSE_FAILED', '视觉模型返回了无法解析的内容',
                       detail={'preview': str(exc)[:200]}) from exc

    usage = getattr(provider, 'last_usage', None) or {}
    db.add(AiUsageLog(user_id=user.id, task_id=None,
                       provider=getattr(provider, 'provider', 'unknown'),
                       model=getattr(provider, 'model', 'unknown'), kind='vision',
                       tokens_in=usage.get('tokens_in'), tokens_out=usage.get('tokens_out')))
    db.commit()

    response = {'pet_id': pet.id, 'source': 'vision',
                'model': getattr(provider, 'model', 'unknown'),
                'suggestion': result['suggestion'],
                'observations': result['observations'],
                'summary': result['summary'], 'advice': result['advice'],
                'disclaimer': VISION_DISCLAIMER}
    if result['suggestion'] == 'vet':
        response['urgent_card'] = REDLINE_CARD
    return response
