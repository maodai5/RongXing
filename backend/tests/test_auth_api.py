import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from smtplib import SMTPException
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import main
from app.services.identity import create_email_account, verify_email
from app.services.mail_worker import deliver_due
from app.models import Base
from app.services.sessions import create_session
from app.services.tasks import claim_task, enqueue_task, finish_task


class AuthAPIFixture:
    email = 'cat@example.com'
    password = 'Strong123'
    key = 'test-signing-key-32-bytes-or-more'
    smtp_settings = {
        'SMTP_HOST': 'smtp.example.com', 'SMTP_PORT': '465', 'SMTP_USER': 'test',
        'SMTP_PASSWORD': 'secret', 'SMTP_FROM': 'service@example.com',
        'VERIFICATION_BASE_URL': 'https://testserver/api/v1/auth/verify-email',
    }

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        url = 'sqlite:///' + str(Path(directory.name) / 'test.db')
        self.engine = create_engine(url)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.now = datetime.now(timezone.utc).replace(tzinfo=None)
        with Session(self.engine) as db, db.begin():
            token = create_email_account(db, self.email, self.password, self.now)
            verify_email(db, token, self.now)
            create_email_account(db, 'unverified@example.com', self.password, self.now)
        with patch.dict(os.environ, {
            'DATABASE_URL': url, 'JWT_SECRET': self.key,
            'PRIVACY_VERSION': 'p1', 'TERMS_VERSION': 't1',
            'PRIVACY_URL': 'https://example.com/privacy', 'TERMS_URL': 'https://example.com/terms',
        }):
            self.client = TestClient(main.create_dev_app())
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def login(self, email=None, password=None):
        return self.client.post('/api/v1/auth/login', json={
            'email': email or self.email, 'password': password or self.password,
        })

    def refresh(self, token):
        return self.client.post('/api/v1/auth/refresh', json={'refresh_token': token})

    def me(self, token):
        return self.client.get('/api/v1/me', headers={'Authorization': f'Bearer {token}'})

class AuthAPITests(AuthAPIFixture, unittest.TestCase):
    def test_verified_email_can_login_and_read_own_profile(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        tokens = response.json()
        self.assertEqual(tokens['token_type'], 'bearer')
        claims = jwt.decode(tokens['access_token'], self.key, algorithms=['HS256'], issuer='rongxing')
        self.assertEqual(claims['exp'] - claims['iat'], 1800)
        profile = self.me(tokens['access_token'])
        self.assertEqual(profile.status_code, 200)
        self.assertEqual(profile.json(), {'id': int(claims['sub']), 'nickname': '猫咪主人', 'role': 'user'})

    def test_email_verification_hint_requires_correct_password(self):
        for email, password, code in (
            ('unverified@example.com', self.password, 'EMAIL_UNVERIFIED'),
            ('unverified@example.com', 'Wrong123', 'AUTH_REQUIRED'),
            ('missing@example.com', self.password, 'AUTH_REQUIRED'),
        ):
            with self.subTest(email=email, code=code):
                response = self.login(email, password)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json()['code'], code)

    def test_refresh_replay_revokes_access_and_refresh_but_not_other_login(self):
        first = self.login().json()
        other = self.login().json()
        response = self.refresh(first['refresh_token'])
        self.assertEqual(response.status_code, 200)
        rotated = response.json()
        self.assertNotEqual(rotated['refresh_token'], first['refresh_token'])
        self.assertEqual(self.me(rotated['access_token']).status_code, 200)
        self.assertEqual(self.refresh(first['refresh_token']).status_code, 401)
        self.assertEqual(self.refresh(rotated['refresh_token']).status_code, 401)
        for entry in (first, rotated):
            self.assertEqual(self.me(entry['access_token']).status_code, 401)
        self.assertEqual(self.me(other['access_token']).status_code, 200)
        self.assertEqual(self.refresh(other['refresh_token']).status_code, 200)

    def test_expired_or_invalid_tokens_are_rejected(self):
        tokens = self.login().json()
        claims = jwt.decode(tokens['access_token'], self.key, algorithms=['HS256'])
        expired = jwt.encode({**claims, 'exp': claims['iat'] - 1}, self.key, algorithm='HS256')
        self.assertEqual(self.me(expired).json()['code'], 'TOKEN_EXPIRED')
        self.assertEqual(self.me('not-a-jwt').status_code, 401)
        self.assertEqual(self.me(tokens['refresh_token']).status_code, 401)
        with Session(self.engine) as db, db.begin():
            old_refresh = create_session(db, int(claims['sub']), self.now - timedelta(days=31))
        self.assertEqual(self.refresh(old_refresh).status_code, 401)

    def register(self, email, password=None):
        return self.client.post('/api/v1/auth/register', json={
            'email': email, 'password': self.password if password is None else password,
        })

    def test_registration_requires_confirmation_before_login(self):
        with patch.dict(os.environ, self.smtp_settings), patch('app.services.mail.smtplib.SMTP_SSL') as smtp:
            response = self.register('New@Example.com')
            self.assertEqual(response.status_code, 202)
            client = smtp.return_value.__enter__.return_value
            client.send_message.assert_not_called()
            self.assertEqual(deliver_due(self.engine, self.key, self.now + timedelta(seconds=1)), 1)
            message = client.send_message.call_args.args[0]
            self.assertEqual(message['To'], 'new@example.com')
            link = urlsplit(message.get_content().splitlines()[2])
            self.assertEqual(link.query, '')
            token = parse_qs(link.fragment)['token'][0]
            self.assertNotIn(token, response.text)
            self.assertEqual(self.register('new@example.com').json(), response.json())
            self.assertEqual(client.send_message.call_count, 1)
        preview = self.client.get(link.path)
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.headers['cache-control'], 'no-store')
        self.assertIn('确认验证', preview.text)
        self.assertEqual(self.login('new@example.com').json()['code'], 'EMAIL_UNVERIFIED')
        self.assertEqual(self.client.post(link.path, json={'token': token}).status_code, 200)
        self.assertEqual(self.login('new@example.com').status_code, 200)
        self.assertEqual(self.client.post(link.path, json={'token': token}).status_code, 400)

    def test_mail_failure_is_retried_with_same_link(self):
        email = 'retry@example.com'
        self.assertEqual(self.register(email).status_code, 202)
        self.assertEqual(self.login(email).json()['code'], 'EMAIL_UNVERIFIED')
        with patch.dict(os.environ, self.smtp_settings), patch('app.services.mail.smtplib.SMTP_SSL') as smtp:
            deliver = smtp.return_value.__enter__.return_value.send_message
            deliver.side_effect = SMTPException('internal failure')
            self.assertEqual(deliver_due(self.engine, self.key, self.now + timedelta(seconds=1)), 0)
            self.assertEqual(deliver_due(self.engine, self.key, self.now + timedelta(seconds=2)), 0)
            deliver.side_effect = None
            self.assertEqual(deliver_due(self.engine, self.key, self.now + timedelta(seconds=32)), 1)
            self.assertEqual(deliver.call_count, 2)
            self.assertEqual(deliver.call_args_list[0].args[0].get_content(), deliver.call_args.args[0].get_content())
            self.assertEqual(deliver_due(self.engine, self.key, self.now + timedelta(seconds=33)), 0)

    def test_resend_requires_password_and_wait_and_invalidates_old_link(self):
        email = 'resend@example.com'
        self.assertEqual(self.register(email).status_code, 202)
        path = '/api/v1/auth/resend-verification'
        with patch.dict(os.environ, self.smtp_settings), patch('app.services.mail.smtplib.SMTP_SSL') as smtp:
            deliver = smtp.return_value.__enter__.return_value.send_message
            deliver_due(self.engine, self.key, self.now + timedelta(seconds=1))
            old = parse_qs(urlsplit(deliver.call_args.args[0].get_content().splitlines()[2]).fragment)['token'][0]
            data = {'email': email, 'password': self.password}
            self.assertEqual(self.client.post(path, json=data).status_code, 202)
            self.assertEqual(deliver_due(self.engine, self.key, self.now + timedelta(seconds=2)), 0)
            with patch('app.routers.auth.utcnow', return_value=self.now + timedelta(seconds=65)):
                self.assertEqual(self.client.post(path, json={**data, 'password': 'Wrong123'}).status_code, 202)
                self.assertEqual(deliver_due(self.engine, self.key, self.now + timedelta(seconds=65)), 0)
                self.assertEqual(self.client.post(path, json=data).status_code, 202)
            self.assertEqual(deliver_due(self.engine, self.key, self.now + timedelta(seconds=66)), 1)
            new = parse_qs(urlsplit(deliver.call_args.args[0].get_content().splitlines()[2]).fragment)['token'][0]
        self.assertEqual(self.client.post('/api/v1/auth/verify-email', json={'token': old}).status_code, 400)
        self.assertEqual(self.client.post('/api/v1/auth/verify-email', json={'token': new}).status_code, 200)

    def test_ip_limit_is_persisted_and_returns_retry_after(self):
        with patch('app.core.middleware.utcnow', return_value=self.now.replace(second=10, microsecond=0)):
            for _ in range(30):
                response = self.client.post('/api/v1/auth/login', json={})
                self.assertEqual(response.status_code, 400)
            response = self.login()
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.json()['code'], 'RATE_LIMITED')
            self.assertEqual(response.headers['retry-after'], '50')
        with patch('app.core.middleware.utcnow', return_value=self.now.replace(second=10, microsecond=0) + timedelta(minutes=1)):
            self.assertEqual(self.login().status_code, 200)

    def test_invalid_registration_is_rejected_before_mail(self):
        for email, password in (('bad-email', self.password), ('new@example.com', '12345678')):
            response = self.register(email, password)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()['code'], 'VALIDATION_ERROR')

    def test_expired_email_verification_is_rejected(self):
        email = 'expired@example.com'
        with Session(self.engine) as db, db.begin():
            token = create_email_account(db, email, self.password, self.now - timedelta(hours=25))
        response = self.client.post('/api/v1/auth/verify-email', json={'token': token})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'VERIFICATION_INVALID')
        self.assertEqual(self.login(email).json()['code'], 'EMAIL_UNVERIFIED')

    def test_pet_crud_requires_current_consents_and_enforces_ownership(self):
        token = self.login().json()['access_token']
        headers = {'Authorization': f'Bearer {token}'}
        path = '/api/v1/pets'
        self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.get(path, headers=headers).json()['code'], 'CONSENT_REQUIRED')
        consent = '/api/v1/consents'
        state = self.client.get(consent, headers=headers).json()
        self.assertEqual(state['required'], {'privacy': 'p1', 'terms': 't1'})
        self.assertFalse(state['granted'])
        self.assertFalse(state['training_granted'])
        self.assertEqual(self.client.post(consent, headers=headers, json={'privacy': 'p0', 'terms': 't1'}).status_code, 409)
        links = self.client.app.state.policy_links
        self.client.app.state.policy_links = {'privacy': '', 'terms': ''}
        self.assertEqual(self.client.post(consent, headers=headers, json=state['required']).status_code, 503)
        self.client.app.state.policy_links = links
        self.assertTrue(self.client.post(consent, headers=headers, json=state['required']).json()['granted'])
        self.assertEqual(self.client.get(path, headers=headers).json(), [])
        pet = self.client.post(path, headers=headers, json={'name': '团团'})
        self.assertEqual(pet.status_code, 201)
        detail = f"{path}/{pet.json()['id']}"
        self.assertEqual(self.client.patch(detail, headers=headers, json={'neutered': True}).json()['name'], '团团')
        self.assertTrue(self.client.get(detail, headers=headers).json()['neutered'])
        with Session(self.engine) as db, db.begin():
            verify = create_email_account(db, 'other@example.com', self.password, self.now)
            verify_email(db, verify, self.now)
        other = {'Authorization': 'Bearer ' + self.login('other@example.com').json()['access_token']}
        self.client.post(consent, headers=other, json=state['required'])
        self.assertEqual(self.client.get(path, headers=other).json(), [])
        self.assertEqual(self.client.patch(detail, headers=other, json={'name': '越权'}).status_code, 404)
        self.assertEqual(self.client.delete(detail, headers=other).status_code, 404)
        self.assertEqual(self.client.delete(detail, headers=headers).status_code, 204)
        self.assertEqual(self.client.get(detail, headers=headers).status_code, 404)
        self.client.app.state.policy_versions = {'privacy': 'p2', 'terms': 't1'}
        self.assertEqual(self.client.get(path, headers=headers).status_code, 403)

    def test_task_polling_is_private_and_requires_current_consent(self):
        headers = {'Authorization': 'Bearer ' + self.login().json()['access_token']}
        versions = {'privacy': 'p1', 'terms': 't1'}
        self.client.post('/api/v1/consents', headers=headers, json=versions).raise_for_status()
        pet_id = self.client.post('/api/v1/pets', headers=headers, json={'name': '团团'}).raise_for_status().json()['id']
        with Session(self.engine) as db, db.begin():
            task_id = enqueue_task(db, pet_id, 'avatar_batch', 'photo-1', {'private': 'input'}).id
        path = '/api/v1/tasks/' + task_id
        self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.get('/api/v1/tasks/' + '0' * 32, headers=headers).status_code, 404)
        self.assertEqual(self.client.get('/api/v1/tasks/invalid', headers=headers).status_code, 400)
        response = self.client.get(path, headers=headers)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.json(), {
            'task_id': task_id, 'type': 'avatar_batch', 'status': 'pending', 'result': None,
        })
        with Session(self.engine) as db, db.begin():
            attempt = claim_task(db, task_id)
            finish_task(db, task_id, attempt, result={'clips': []})
            token = create_email_account(db, 'other@example.com', self.password, self.now)
            verify_email(db, token, self.now)
        complete = self.client.get(path, headers=headers).json()
        self.assertEqual((complete['status'], complete['result']), ('succeeded', {'clips': []}))
        other = {'Authorization': 'Bearer ' + self.login('other@example.com').json()['access_token']}
        self.assertEqual(self.client.get(path, headers=other).status_code, 403)
        self.client.post('/api/v1/consents', headers=other, json=versions).raise_for_status()
        self.assertEqual(self.client.get(path, headers=other).status_code, 404)
        self.client.app.state.policy_versions = {**versions, 'privacy': 'p2'}
        self.assertEqual(self.client.get(path, headers=headers).status_code, 403)
        self.client.app.state.policy_versions = versions
        self.client.delete(f'/api/v1/pets/{pet_id}', headers=headers).raise_for_status()
        self.assertEqual(self.client.get(path, headers=headers).status_code, 404)

    def test_validation_error_does_not_echo_password(self):
        response = self.login(password='Sensitive123' * 20)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'VALIDATION_ERROR')
        self.assertNotIn('Sensitive123', response.text)


class DefaultAppTests(unittest.TestCase):
    def test_default_app_does_not_enable_development_auth(self):
        client = TestClient(main.app)
        for path in ('login', 'register', 'verify-email'):
            self.assertEqual(client.post(f'/api/v1/auth/{path}', json={}).status_code, 404)

    def test_development_app_rejects_weak_signing_key(self):
        with patch.dict(os.environ, {'DATABASE_URL': 'sqlite://', 'JWT_SECRET': 'too-short'}):
            with self.assertRaisesRegex(RuntimeError, 'JWT_SECRET'):
                main.create_dev_app()
