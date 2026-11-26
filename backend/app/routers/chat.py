"""聊天线程与文字消息（FRD F09-1/2/5/7；录音解读属 T16）。

每宠一线程自动创建；上下文=最近 20 条+档案+最新初筛状态；
红线关键词零 LLM 附就医引导卡；LLM 失败/禁词降级模板安抚，对话不中断。
"""

from typing import Literal

import logging

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import consented_user, owned_pet
from app.core.errors import APIError
from app.core.security import utcnow
from app.models import (
    AiUsageLog,
    ChatMessage,
    ChatSession,
    HealthScreening,
    MediaUpload,
    Pet,
    RequestIdempotency,
    Task,
    User,
)
from app.services.chat import (
    ChatReplyError,
    build_persona_messages,
    detect_redline,
    fallback_reply,
    parse_chat_reply,
    redline_response,
    reformat_reply,
)
from app.services.idempotency import find_replay, hash_request, validate_key
from app.services.quota import quota_gate
from app.services.tasks import enqueue_task

router = APIRouter(prefix='/api/v1', tags=['chat'])
logger = logging.getLogger('rongxing.chat')


def chat_config(request: Request) -> dict:
    config = getattr(request.app.state, 'chat_config', None)
    if config is None:
        raise APIError(503, 'CONFIG_UNAVAILABLE', '聊天配置不可用')
    return config


class MessageInput(BaseModel, extra='forbid'):
    kind: Literal['text', 'audio'] = 'text'
    content: str | None = None
    media_id: str | None = Field(None, pattern=r'^[0-9a-f]{32}$')


def _session_payload(session: ChatSession) -> dict:
    return {'session_id': session.id, 'pet_id': session.pet_id, 'created_at': session.created_at.isoformat()}


def _signed_audio_url(request: Request, db: Session, object_key: str | None) -> str | None:
    if not object_key:
        return None
    storage = getattr(request.app.state, 'photo_storage', None)
    if storage is None:
        return None
    media = db.get(MediaUpload, object_key.rsplit('/', 1)[-1])
    if media is None or media.version_id is None:
        return None
    return storage.asset_url(media.object_key, media.version_id)


def _message_payload(
    message: ChatMessage, source: str | None = None, *, request: Request | None = None, db: Session | None = None
) -> dict:
    audio_url = message.audio_url
    if message.kind == 'audio' and request is not None and db is not None:
        audio_url = _signed_audio_url(request, db, audio_url)
    payload = {
        'id': message.id,
        'role': message.role,
        'kind': message.kind,
        'content': message.content,
        'audio_url': audio_url,
        'emotion_label': message.emotion_label,
        'emotion_confidence': float(message.emotion_confidence) if message.emotion_confidence is not None else None,
        'task_id': message.task_id,
        'created_at': message.created_at.isoformat(),
    }
    if source is not None:
        payload['source'] = source
    return payload


def _send_audio(
    session: ChatSession, body: MessageInput, request: Request, response: Response, user: User, db: Session
) -> dict:
    """F09-3：音频消息→202 入队 audio_interpret；同音频进行中任务合并（重试防重）。"""
    if not body.media_id or body.content is not None:
        raise APIError(
            400,
            'VALIDATION_ERROR',
            '请检查请求字段',
            {'fields': [{'field': 'media_id', 'message': '音频消息需提供 media_id，且不带 content'}]},
        )
    media = db.scalar(
        select(MediaUpload)
        .join(Pet, Pet.id == MediaUpload.pet_id)
        .where(
            MediaUpload.id == body.media_id,
            MediaUpload.pet_id == session.pet_id,
            Pet.user_id == user.id,
            Pet.status == 'active',
        )
    )
    if media is None:
        raise APIError(404, 'NOT_FOUND', '未找到音频上传记录')
    if media.kind != 'audio':
        raise APIError(422, 'MEDIA_INVALID', '该消息仅支持音频文件')
    if media.version_id is None:
        raise APIError(409, 'MEDIA_NOT_VERIFIED', '请先上传并确认音频')
    inflight = db.scalars(
        select(Task)
        .where(Task.pet_id == session.pet_id, Task.type == 'audio_interpret', Task.status.in_(('pending', 'running')))
        .order_by(Task.created_at, Task.id)
    ).all()
    for task in inflight:
        if task.payload.get('media_id') == media.id:
            message = db.get(ChatMessage, task.payload.get('message_id'))
            response.status_code = 200
            response.headers['Cache-Control'] = 'no-store'
            return {
                'task_id': task.id,
                'user_message': _message_payload(message, request=request, db=db),
                'pet_message': None,
                'guidance': None,
            }
    message = ChatMessage(session_id=session.id, role='user', kind='audio', audio_url=media.object_key)
    db.add(message)
    db.flush()
    task = enqueue_task(
        db,
        session.pet_id,
        'audio_interpret',
        str(message.id),
        {'media_id': media.id, 'message_id': message.id, 'session_id': session.id},
    )
    message.task_id = task.id
    db.commit()
    response.status_code = 202
    response.headers['Cache-Control'] = 'no-store'
    return {
        'task_id': task.id,
        'user_message': _message_payload(message, request=request, db=db),
        'pet_message': None,
        'guidance': None,
    }


def _owned_session(sid: int, user: User, db: Session) -> ChatSession:
    session = db.scalar(
        select(ChatSession)
        .join(Pet, Pet.id == ChatSession.pet_id)
        .where(ChatSession.id == sid, ChatSession.user_id == user.id, Pet.user_id == user.id, Pet.status == 'active')
    )
    if session is None:
        raise APIError(404, 'NOT_FOUND', '聊天线程不存在')
    return session


def _age_months(pet: Pet) -> int | None:
    if pet.birth_date is None:
        return None
    today = utcnow().date()
    months = (today.year - pet.birth_date.year) * 12 + today.month - pet.birth_date.month
    if today.day < pet.birth_date.day:
        months -= 1
    return max(months, 0)


@router.post('/pets/{pet_id}/chat/sessions', status_code=201)
def create_session(
    pet_id: int,
    request: Request,
    response: Response,
    user: User = Depends(consented_user),
    db: Session = Depends(get_db),
    config: dict = Depends(chat_config),
):
    pet = owned_pet(pet_id, user, db)
    session = db.scalar(select(ChatSession).where(ChatSession.pet_id == pet.id))
    if session is None:
        session = ChatSession(pet_id=pet.id, user_id=user.id)
        db.add(session)
        db.commit()
        db.refresh(session)
    else:
        response.status_code = 200
    response.headers['Cache-Control'] = 'no-store'
    return _session_payload(session)


@router.get('/pets/{pet_id}/chat/sessions')
def list_sessions(
    pet_id: int,
    request: Request,
    response: Response,
    user: User = Depends(consented_user),
    db: Session = Depends(get_db),
    config: dict = Depends(chat_config),
):
    pet = owned_pet(pet_id, user, db)
    sessions = db.scalars(select(ChatSession).where(ChatSession.pet_id == pet.id).order_by(ChatSession.id)).all()
    response.headers['Cache-Control'] = 'no-store'
    return {'items': [_session_payload(s) for s in sessions]}


@router.post('/chat/sessions/{sid}/messages', status_code=201)
async def send_message(
    sid: int,
    body: MessageInput,
    request: Request,
    response: Response,
    user: User = Depends(consented_user),
    db: Session = Depends(get_db),
    config: dict = Depends(chat_config),
):
    session = _owned_session(sid, user, db)
    if body.kind == 'audio':
        return _send_audio(session, body, request, response, user, db)
    # F09-6：离线补发带同一 Idempotency-Key；命中缓存直接回放，不产生重复消息。
    key = validate_key(request.headers.get('Idempotency-Key'))
    request_hash = hash_request(request.url.path, body.model_dump(mode='json')) if key else None
    row = None
    if key:
        replay = find_replay(db, user.id, key, request_hash)
        if replay is not None:
            response.headers['Cache-Control'] = 'no-store'
            response.headers['Idempotent-Replay'] = 'true'
            return replay
        row = RequestIdempotency(user_id=user.id, key=key, request_hash=request_hash)
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            replay = find_replay(db, user.id, key, request_hash)
            if replay is None:
                raise APIError(409, 'IDEMPOTENCY_CONFLICT', '该幂等键已用于不同的请求')
            response.headers['Cache-Control'] = 'no-store'
            response.headers['Idempotent-Replay'] = 'true'
            return replay
    content = (body.content or '').strip()
    if not content or len(content) > config['input_max']:
        raise APIError(
            400,
            'VALIDATION_ERROR',
            '请检查请求字段',
            {'fields': [{'field': 'content', 'message': f'消息需为 1–{config["input_max"]} 字'}]},
        )
    quota_gate(db, user.id, 'chat_msg')  # F11-1：文字消息计数；音频解读与幂等回放不计
    user_message = ChatMessage(session_id=session.id, role='user', kind='text', content=content)
    db.add(user_message)
    db.flush()
    window = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.session_id == session.id, ChatMessage.content.is_not(None))
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .limit(config['context_window'])
    ).all()
    history = [{'role': m.role, 'content': m.content} for m in reversed(window)]
    pet = db.get(Pet, session.pet_id)
    latest = db.scalar(
        select(HealthScreening)
        .where(HealthScreening.pet_id == pet.id)
        .order_by(HealthScreening.created_at.desc(), HealthScreening.id.desc())
    )
    guidance = None
    if detect_redline(content, config):
        # F09-7 / FRD 全局规则：红线流程不经过 LLM。
        result = redline_response(config)
        guidance = result['guidance']
    else:
        result = None
        provider = getattr(request.app.state, 'chat_llm_provider', None)
        if provider is not None:
            pet_profile = {
                'name': pet.name,
                'species': pet.species,
                'breed': pet.breed,
                'sex': pet.sex,
                'age_months': _age_months(pet),
                'neutered': pet.neutered,
                'body_type': pet.body_type,
            }
            messages = build_persona_messages(pet_profile, latest.state if latest else None, history, config)
            try:
                raw = await provider.chat(messages, temperature=0.7, max_tokens=300)
                try:
                    parsed = parse_chat_reply(raw, config)
                except ChatReplyError:
                    # 上下文变长后模型常跟着历史用纯文本作答，改写一次保住真实回复
                    parsed = await reformat_reply(provider, raw, config)
                result = {**parsed, 'source': 'llm'}
                usage = getattr(provider, 'last_usage', None) or {}
                db.add(
                    AiUsageLog(
                        user_id=user.id,
                        task_id=None,
                        provider=provider.provider,
                        model=provider.model,
                        kind='llm',
                        tokens_in=usage.get('tokens_in'),
                        tokens_out=usage.get('tokens_out'),
                    )
                )
            except Exception:
                # 降级前留下原因，避免"AI 没回复"时无从排查
                logger.warning('chat llm failed, falling back to template', exc_info=True)
                db.add(
                    AiUsageLog(
                        user_id=user.id,
                        task_id=None,
                        provider=getattr(provider, 'provider', 'unknown'),
                        model=getattr(provider, 'model', 'unknown'),
                        kind='llm',
                    )
                )
        if result is None:
            result = fallback_reply(config)
    pet_message = ChatMessage(
        session_id=session.id, role='pet', kind='text', content=result['reply'], emotion_label=result['emotion']
    )
    db.add(pet_message)
    db.flush()
    payload = {
        'user_message': _message_payload(user_message),
        'pet_message': _message_payload(pet_message, result['source']),
        'guidance': guidance,
    }
    if row is not None:
        row.status_code = 201
        row.response_json = payload
    db.commit()
    response.headers['Cache-Control'] = 'no-store'
    return payload


@router.get('/chat/sessions/{sid}/messages')
def list_messages(
    sid: int,
    request: Request,
    response: Response,
    limit: int = Query(50, ge=1, le=200),
    before: int | None = Query(None),
    user: User = Depends(consented_user),
    db: Session = Depends(get_db),
    config: dict = Depends(chat_config),
):
    session = _owned_session(sid, user, db)
    query = select(ChatMessage).where(ChatMessage.session_id == session.id)
    if before is not None:
        query = query.where(ChatMessage.id < before)
    items = db.scalars(query.order_by(ChatMessage.id.desc()).limit(limit)).all()
    response.headers['Cache-Control'] = 'no-store'
    return {
        'items': [_message_payload(m, request=request, db=db) for m in items],
        'next_before': items[-1].id if len(items) == limit else None,
    }
