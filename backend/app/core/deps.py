"""Shared FastAPI dependencies: authentication and consent gating."""
from typing import Optional

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import APIError
from app.core.security import bearer
from app.models import AuthSession, User
from app.services.consents import consent_state
from app.services.pets import PetNotFound, get_pet


def _authenticate(request: Request, credentials: Optional[HTTPAuthorizationCredentials],
                  db: Session, statuses: tuple[str, ...], *, allow_revoked: bool = False) -> User:
    unauthorized = APIError(401, 'AUTH_REQUIRED', '请重新登录')
    if credentials is None:
        raise unauthorized
    try:
        claims = jwt.decode(credentials.credentials, request.app.state.jwt_secret, algorithms=['HS256'],
                            issuer='rongxing', options={'require': ['sub', 'sid', 'iat', 'exp', 'iss']})
    except jwt.ExpiredSignatureError as exc:
        raise APIError(401, 'TOKEN_EXPIRED', '会话已失效，请重新登录') from exc
    except jwt.InvalidTokenError as exc:
        raise unauthorized from exc
    if type(claims['sid']) is not int or not 0 < claims['sid'] < 2**63:
        raise unauthorized
    conditions = [AuthSession.id == claims['sid'], User.status.in_(statuses)]
    if not allow_revoked:
        conditions.append(AuthSession.revoked_at.is_(None))
    user = db.scalar(select(User).join(AuthSession, AuthSession.user_id == User.id).where(*conditions))
    if user is None or str(user.id) != claims['sub']:
        raise unauthorized
    return user


def current_user(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
                 db: Session = Depends(get_db)) -> User:
    return _authenticate(request, credentials, db, ('active',))


def deletion_user(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
                  db: Session = Depends(get_db)) -> User:
    # A frozen account may still repeat its own deletion request (idempotent 200).
    return _authenticate(request, credentials, db, ('active', 'deletion_pending'), allow_revoked=True)


def consented_user(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> User:
    if not consent_state(db, user, request)['granted']:
        raise APIError(403, 'CONSENT_REQUIRED', '请先阅读并同意当前版本的协议')
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if user.role != 'admin':
        raise APIError(403, 'ADMIN_REQUIRED', '仅管理员可执行此操作')
    return user


def owned_pet(pet_id: int, user: User = Depends(consented_user), db: Session = Depends(get_db)):
    try:
        return get_pet(db, user.id, pet_id)
    except PetNotFound as exc:
        raise APIError(404, 'NOT_FOUND', '未找到这只猫咪') from exc
