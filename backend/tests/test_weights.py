"""T13 体重记录、趋势与骤变提示（FRD F08-1/2，F02-2）。"""
from datetime import timedelta
from unittest import TestCase

from sqlalchemy.orm import Session

from test_auth_api import AuthAPIFixture
from app.core.security import utcnow
from app.models import WeightRecord
from app.services.weights import detect_sudden_change


def pt(kg, days_ago=0):
    return {'weight_kg': kg, 'measured_at': utcnow() - timedelta(days=days_ago)}


class WeightLogicTests(TestCase):
    def test_empty_or_single_returns_none(self):
        self.assertIsNone(detect_sudden_change([]))
        self.assertIsNone(detect_sudden_change([pt(4.0)]))

    def test_stable_returns_none(self):
        self.assertIsNone(detect_sudden_change([pt(4.0, 5), pt(4.05, 2), pt(4.02)]))

    def test_loss_exactly_10pct_within_7_days_triggers(self):
        alert = detect_sudden_change([pt(4.0, 6), pt(3.6)])
        self.assertIsNotNone(alert)
        self.assertEqual(alert['direction'], 'loss')
        self.assertEqual(alert['pct'], -10.0)
        self.assertEqual(alert['suggestion'], 'screening')
        self.assertEqual(alert['baseline']['weight_kg'], 4.0)
        self.assertEqual(alert['latest']['weight_kg'], 3.6)

    def test_gain_10pct_triggers(self):
        alert = detect_sudden_change([pt(4.0, 3), pt(4.4)])
        self.assertEqual(alert['direction'], 'gain')
        self.assertEqual(alert['pct'], 10.0)

    def test_change_older_than_7_days_ignored(self):
        self.assertIsNone(detect_sudden_change([pt(4.0, 8), pt(3.0)]))

    def test_below_threshold_none(self):
        self.assertIsNone(detect_sudden_change([pt(4.0, 3), pt(3.64)]))

    def test_picks_largest_magnitude(self):
        alert = detect_sudden_change([pt(4.0, 5), pt(3.4, 3), pt(3.5)])
        self.assertEqual(alert['direction'], 'loss')
        self.assertEqual(alert['pct'], -12.5)
        self.assertEqual(alert['baseline']['weight_kg'], 4.0)


class WeightFixture(AuthAPIFixture):
    def setUp(self):
        super().setUp()
        self.headers = {'Authorization': 'Bearer ' + self.login().json()['access_token']}
        self.client.post('/api/v1/consents', headers=self.headers, json={'privacy': 'p1', 'terms': 't1'}).raise_for_status()
        self.pet = self.client.post('/api/v1/pets', headers=self.headers, json={'name': '团团'}).raise_for_status().json()['id']

    def post_weight(self, body, pet=None, headers=None):
        return self.client.post(f'/api/v1/pets/{pet or self.pet}/weights',
                                headers=self.headers if headers is None else headers, json=body)

    def get_weights(self, pet=None, headers=None, **params):
        return self.client.get(f'/api/v1/pets/{pet or self.pet}/weights',
                               headers=self.headers if headers is None else headers, params=params)

    def make_weight(self, kg, measured_at=None, pet=None):
        with Session(self.engine) as db:
            record = WeightRecord(pet_id=pet or self.pet, weight_kg=kg, measured_at=measured_at or utcnow())
            db.add(record)
            db.commit()
            return record.id

    def other_headers(self, consent=True):
        from app.services.identity import create_email_account, verify_email
        with Session(self.engine) as db:
            token = create_email_account(db, 'other@example.com', self.password, self.now)
            verify_email(db, token, self.now)
            db.commit()
        headers = {'Authorization': 'Bearer ' + self.login(email='other@example.com').json()['access_token']}
        if consent:
            self.client.post('/api/v1/consents', headers=headers, json={'privacy': 'p1', 'terms': 't1'}).raise_for_status()
        return headers


class WeightAPITests(WeightFixture, TestCase):
    def test_post_valid_returns_record(self):
        data = self.post_weight({'weight_kg': 4.25}).raise_for_status().json()
        self.assertEqual(data['weight_kg'], 4.25)
        self.assertIsInstance(data['id'], int)
        from datetime import datetime
        measured = datetime.fromisoformat(data['measured_at'])
        self.assertLess(abs((utcnow() - measured).total_seconds()), 120)

    def test_boundaries_accepted(self):
        self.assertEqual(self.post_weight({'weight_kg': 0.1}).raise_for_status().json()['weight_kg'], 0.1)
        self.assertEqual(self.post_weight({'weight_kg': 30}).raise_for_status().json()['weight_kg'], 30.0)

    def test_out_of_range_locates_field(self):
        for kg in (0.09, 30.01, 0, -1):
            resp = self.post_weight({'weight_kg': kg})
            self.assertEqual(resp.status_code, 400, kg)
            body = resp.json()
            self.assertEqual(body['code'], 'VALIDATION_ERROR')
            self.assertIn('weight_kg', [f['field'] for f in body['detail']['fields']])

    def test_precision_over_2_decimals_rejected(self):
        resp = self.post_weight({'weight_kg': 4.255})
        self.assertEqual(resp.status_code, 400)
        self.assertIn('weight_kg', [f['field'] for f in resp.json()['detail']['fields']])

    def test_extreme_value_rejected_as_validation_error(self):
        resp = self.post_weight({'weight_kg': 1e30})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['code'], 'VALIDATION_ERROR')
        self.assertIn('weight_kg', [f['field'] for f in resp.json()['detail']['fields']])

    def test_extra_field_rejected(self):
        self.assertEqual(self.post_weight({'weight_kg': 4.0, 'note': 'x'}).status_code, 400)

    def test_backdate_any_past_allowed(self):
        past = (utcnow() - timedelta(days=30)).isoformat()
        data = self.post_weight({'weight_kg': 4.0, 'measured_at': past}).raise_for_status().json()
        self.assertEqual(data['measured_at'], past)

    def test_future_rejected_skew_tolerated(self):
        resp = self.post_weight({'weight_kg': 4.0, 'measured_at': (utcnow() + timedelta(minutes=5)).isoformat()})
        self.assertEqual(resp.status_code, 400)
        self.assertIn('measured_at', [f['field'] for f in resp.json()['detail']['fields']])
        self.post_weight({'weight_kg': 4.0, 'measured_at': (utcnow() + timedelta(seconds=30)).isoformat()}).raise_for_status()

    def test_same_day_multiple_all_kept(self):
        for kg in (4.0, 4.1, 4.2):
            self.post_weight({'weight_kg': kg}).raise_for_status()
        data = self.get_weights().raise_for_status().json()
        self.assertEqual(data['count'], 3)
        self.assertEqual([r['weight_kg'] for r in data['records']], [4.2, 4.1, 4.0])

    def test_recent_desc_and_limit(self):
        self.make_weight(3.0, utcnow() - timedelta(days=3))
        self.make_weight(4.0, utcnow() - timedelta(days=1))
        self.make_weight(4.5)
        data = self.get_weights(limit=2).raise_for_status().json()
        self.assertEqual([r['weight_kg'] for r in data['records']], [4.5, 4.0])
        self.assertEqual(self.get_weights(limit=201).status_code, 400)

    def test_trend_points_ascending_90_day_window(self):
        self.make_weight(3.0, utcnow() - timedelta(days=100))
        self.make_weight(4.0, utcnow() - timedelta(days=10))
        self.make_weight(4.2)
        trend = self.get_weights().raise_for_status().json()['trend']
        self.assertEqual(trend['window_days'], 90)
        self.assertEqual([p['weight_kg'] for p in trend['points']], [4.0, 4.2])
        self.assertIsNone(trend['alert'])

    def test_ac_08_1_profile_and_feeding_update_immediately(self):
        self.assertIsNone(self.client.get(f'/api/v1/pets/{self.pet}', headers=self.headers).raise_for_status().json()['latest_weight'])
        self.post_weight({'weight_kg': 4.25}).raise_for_status()
        profile = self.client.get(f'/api/v1/pets/{self.pet}', headers=self.headers).raise_for_status().json()
        self.assertEqual(profile['latest_weight']['weight_kg'], 4.25)
        listing = self.client.get('/api/v1/pets', headers=self.headers).raise_for_status().json()
        self.assertEqual(listing[0]['latest_weight']['weight_kg'], 4.25)
        health = self.client.get(f'/api/v1/pets/{self.pet}/health', headers=self.headers).raise_for_status().json()
        self.assertEqual(health['latest_weight']['weight_kg'], 4.25)
        reference = self.client.get(f'/api/v1/pets/{self.pet}/feeding-reference', headers=self.headers).raise_for_status().json()
        self.assertTrue(reference['has_weight'])
        self.assertEqual(reference['weight_kg'], 4.25)
        self.assertGreater(reference['der_kcal'], 0)

    def test_patch_preserves_latest_weight(self):
        self.post_weight({'weight_kg': 4.25}).raise_for_status()
        profile = self.client.patch(f'/api/v1/pets/{self.pet}', headers=self.headers, json={'name': '圆圆'}).raise_for_status().json()
        self.assertEqual(profile['latest_weight']['weight_kg'], 4.25)

    def test_ac_08_2_loss_alert_suggests_screening(self):
        self.post_weight({'weight_kg': 4.0, 'measured_at': (utcnow() - timedelta(days=6)).isoformat()}).raise_for_status()
        self.post_weight({'weight_kg': 3.5}).raise_for_status()
        alert = self.get_weights().raise_for_status().json()['trend']['alert']
        self.assertEqual(alert['direction'], 'loss')
        self.assertEqual(alert['pct'], -12.5)
        self.assertEqual(alert['suggestion'], 'screening')

    def test_empty_get(self):
        data = self.get_weights().raise_for_status().json()
        self.assertEqual(data['records'], [])
        self.assertEqual(data['count'], 0)
        self.assertEqual(data['trend']['points'], [])
        self.assertIsNone(data['trend']['alert'])

    def test_ownership_and_auth(self):
        self.assertEqual(self.get_weights(headers={}).status_code, 401)
        other = self.other_headers()
        self.assertEqual(self.get_weights(headers=other).status_code, 404)
        self.assertEqual(self.post_weight({'weight_kg': 4.0}, headers=other).status_code, 404)
        self.assertEqual(self.get_weights(pet=9999).status_code, 404)

    def test_no_consent_403(self):
        headers = self.other_headers(consent=False)
        self.assertEqual(self.get_weights(headers=headers).status_code, 403)
