import unittest
from datetime import datetime

from argon2.exceptions import VerifyMismatchError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.deps import admin_user
from app.core.errors import APIError
from app.models import Base, User, UserCredential
from app.services.identity import login_email, password_hasher
from scripts.create_admin import ADMIN_EMAIL, ADMIN_PASSWORD, create_admin


class AdminAccountTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.now = datetime(2026, 11, 24, 8, 0, 0)

    def test_new_accounts_default_to_user(self):
        with Session(self.engine) as db:
            user = User(nickname='猫咪主人')
            db.add(user)
            db.commit()
            db.refresh(user)
            self.assertEqual(user.role, 'user')

    def test_create_admin_is_idempotent_and_login_ready(self):
        with Session(self.engine) as db:
            first = create_admin(db, ADMIN_EMAIL, ADMIN_PASSWORD, self.now)
            db.commit()
            credential = db.scalar(select(UserCredential).where(
                UserCredential.identifier == ADMIN_EMAIL,
            ))
            self.assertTrue(first['created'])
            self.assertIsNotNone(credential.verified_at)
            self.assertEqual(login_email(db, ADMIN_EMAIL, ADMIN_PASSWORD).role, 'admin')

        with Session(self.engine) as db:
            second = create_admin(db, ADMIN_EMAIL, 'Changed123', self.now)
            db.commit()
            credential = db.scalar(select(UserCredential).where(
                UserCredential.identifier == ADMIN_EMAIL,
            ))
            self.assertFalse(second['created'])
            self.assertEqual(second['user_id'], first['user_id'])
            self.assertEqual(db.get(User, first['user_id']).nickname, '管理员')
            password_hasher.verify(credential.secret_hash, 'Changed123')
            with self.assertRaises(VerifyMismatchError):
                password_hasher.verify(credential.secret_hash, ADMIN_PASSWORD)

    def test_admin_dependency_rejects_regular_user(self):
        with self.assertRaises(APIError) as raised:
            admin_user(User(nickname='猫咪主人', role='user'))
        self.assertEqual(raised.exception.code, 'ADMIN_REQUIRED')
        self.assertEqual(admin_user(User(nickname='管理员', role='admin')).role, 'admin')
