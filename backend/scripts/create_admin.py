'''Create or refresh the local application administrator account.

Usage (from backend/, after 'alembic upgrade head'):

    python -m scripts.create_admin
'''
from __future__ import annotations

import argparse
from datetime import datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import utcnow
from app.models import User, UserCredential
from app.services.identity import (
    create_email_account, normalize_email, password_hasher, verify_email,
)

ADMIN_EMAIL = 'admin@rongxing.app'
ADMIN_PASSWORD = 'Rongxing2026'
ADMIN_NICKNAME = '管理员'


def create_admin(session: Session, email: str, password: str, now: datetime | None = None) -> dict:
    email = normalize_email(email)
    now = now or utcnow()
    credential = session.scalar(select(UserCredential).where(
        UserCredential.type == 'email', UserCredential.identifier == email,
    ))
    created = credential is None
    if created:
        token = create_email_account(session, email, password, now)
        verify_email(session, token, now)
        credential = session.scalar(select(UserCredential).where(
            UserCredential.type == 'email', UserCredential.identifier == email,
        ))
    else:
        credential.secret_hash = password_hasher.hash(password)
        credential.verified_at = credential.verified_at or now
        credential.verification_hash = None
        credential.verification_expires_at = None
    user = session.get(User, credential.user_id)
    user.role = 'admin'
    user.status = 'active'
    user.deleted_at = None
    if user.nickname in ('', '猫咪主人'):
        user.nickname = ADMIN_NICKNAME
    session.flush()
    return {'email': email, 'user_id': user.id, 'role': user.role, 'created': created}


def main() -> None:
    parser = argparse.ArgumentParser(description='创建或更新本地应用管理员账号（仅限开发环境）')
    parser.add_argument('--email', default=ADMIN_EMAIL, help='管理员邮箱')
    parser.add_argument('--password', default=ADMIN_PASSWORD,
                        help='管理员密码（8–128 位，含字母和数字）')
    args = parser.parse_args()

    settings = Settings.from_env()
    engine = create_engine(settings.database_url, pool_pre_ping=True, pool_recycle=1800)
    try:
        with Session(engine) as session:
            summary = create_admin(session, args.email, args.password)
            session.commit()
    finally:
        engine.dispose()

    action = '已创建' if summary['created'] else '已更新'
    print(f"管理员账号{action}：{summary['email']}（user_id={summary['user_id']}）")
    print(f"密码：{args.password}")


if __name__ == '__main__':
    main()
