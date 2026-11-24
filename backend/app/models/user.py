"""Accounts, credentials, sessions and verification mail."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class User(Base):
    __tablename__ = 'users'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nickname: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default='active')
    role: Mapped[str] = mapped_column(String(16), default='user', server_default='user', nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class UserCredential(Base):
    __tablename__ = 'user_credentials'
    __table_args__ = (UniqueConstraint('type', 'identifier', name='uk_credential_type_identifier'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True, nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    identifier: Mapped[str] = mapped_column(String(128), nullable=False)
    secret_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    verification_hash: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True)
    verification_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Consent(Base):
    __tablename__ = 'consents'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True, nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())


class AuthSession(Base):
    __tablename__ = 'auth_sessions'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True, nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class RefreshToken(Base):
    __tablename__ = 'refresh_tokens'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey('auth_sessions.id'), index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class VerificationMail(Base):
    __tablename__ = 'verification_mail'

    credential_id: Mapped[int] = mapped_column(ForeignKey('user_credentials.id', ondelete='CASCADE'), primary_key=True)
    nonce: Mapped[str] = mapped_column(String(48), nullable=False)
    queued_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AuthRateBucket(Base):
    __tablename__ = 'auth_rate_buckets'

    source_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    minute: Mapped[datetime] = mapped_column(DateTime, primary_key=True, index=True)
    requests: Mapped[int] = mapped_column(Integer, nullable=False)
