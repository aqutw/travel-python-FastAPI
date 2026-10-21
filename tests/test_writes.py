from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import logging
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

from fastapi.testclient import TestClient

from app.main import ROOT, create_app
from app.models import WishlistInput
from app.workspace import Workspace, encode

KEY = 'test-key-at-least-32-bytes-long-123456'


class WriteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'travel.sqlite3'
        shutil.copyfile(ROOT / 'data/travel.sqlite3', self.path)
        self.application = create_app(self.path, '2026-10-20', api_write_key=KEY, burst=1000)
        self.client = TestClient(self.application)
        self.client.__enter__()
        self.headers = {'X-API-Key': KEY, 'Idempotency-Key': 'create-example-001'}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def call(self, method, path, body=None, status=200, **kwargs):
        response = self.client.request(method, path, json=body, headers=self.headers, **kwargs)
        self.assertEqual(response.status_code, status, response.text)
        return response

    def test_all_seven_write_operations_and_five_private_reads(self):
        wishlist = {'title': '收藏', 'product_ids': ['stay-001']}
        trip = {'title': '行程', 'start_date': '2026-11-01', 'end_date': '2026-11-03', 'adults': 2,
                'items': [{'product_id': 'stay-001', 'day': 1}]}
        review = {'author': '旅人', 'rating': 5, 'content': '交通便利', 'travel_date': '2026-10-01'}
        for path, body in [('/api/v1/wishlists', wishlist), ('/api/v1/trips', trip),
                           ('/api/v1/products/stay-001/reviews', review)]:
            with self.subTest(path=path):
                created = self.call('POST', path, body, 201)
                location = created.headers['Location']
                self.assertEqual(self.call('GET', location).json(), created.json())
                put = {**body, 'version': 1}
                put['content' if path.endswith('/reviews') else 'title'] = '更新內容'
                updated = self.call('PUT', location, put)
                self.assertEqual(updated.json()['data']['version'], 2)
                self.assertEqual(self.call('PUT', location, put).json(), updated.json())
        for path in ('/api/v1/wishlists', '/api/v1/trips'):
            self.assertEqual(len(self.call('GET', path).json()['data']), 1)
        quote = self.call('POST', '/api/v1/products/stay-001/quotes', {
            'start_date': '2026-11-01', 'end_date': '2026-11-03', 'adults': 2, 'children': 0, 'quantity': 1}).json()
        expected = self.client.get('/api/v1/products/stay-001/availability?start_date=2026-11-01&end_date=2026-11-03').json()
        self.assertEqual(quote, expected)

    def test_idempotency_conflicts_versions_and_restart(self):
        body = {'title': 'one', 'product_ids': ['stay-001']}
        first = self.call('POST', '/api/v1/wishlists', body, 201)
        second = self.call('POST', '/api/v1/wishlists', body, 201)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(second.headers['Idempotency-Replayed'], 'true')
        self.call('POST', '/api/v1/wishlists', {**body, 'title': 'different'}, 409)
        path = first.headers['Location']
        self.call('PUT', path, {**body, 'title': 'new', 'version': 1})
        self.call('PUT', path, {**body, 'title': 'stale', 'version': 1}, 409)
        with TestClient(create_app(self.path, '2026-10-20', api_write_key=KEY)) as restarted:
            replay = restarted.post('/api/v1/wishlists', json=body, headers=self.headers)
            self.assertEqual(replay.status_code, 201)
            self.assertEqual(replay.json(), first.json())
            self.assertEqual(restarted.get(path, headers=self.headers).json()['data']['version'], 2)

    def test_reviews_update_public_rating_and_cannot_edit_seed(self):
        body = {'author': '旅人', 'rating': 1, 'content': '心得', 'travel_date': '2026-10-01'}
        r = self.call('POST', '/api/v1/products/stay-001/reviews', body, 201)
        p = self.client.get('/api/v1/products/stay-001').json()['data']
        self.assertEqual(p['rating'], {'count': 6, 'average': 4.0})
        self.call('PUT', r.headers['Location'], {**body, 'rating': 5, 'version': 1})
        self.assertEqual(self.client.get('/api/v1/products/stay-001').json()['data']['rating']['average'], 4.7)
        self.call('PUT', '/api/v1/products/stay-001/reviews/stay-001-review-1', {**body, 'version': 1}, 404)
        self.call('POST', '/api/v1/products/missing/reviews', body, 404)

    def test_strict_models_and_semantic_validation(self):
        bodies = [{}, {'title': ' ', 'product_ids': []}, {'title': 1, 'product_ids': []},
                  {'title': 'a', 'product_ids': [], 'extra': True},
                  {'title': 'a', 'product_ids': ['stay-001', 'stay-001']},
                  {'title': 'a', 'product_ids': [], 'version': True},
                  {'title': 'a', 'product_ids': [], 'version': '1'},
                  {'title': 'a', 'product_ids': ['missing']}]
        for body in bodies:
            with self.subTest(body=body):
                error = self.call('POST', '/api/v1/wishlists', body, 400).json()['error']
                self.assertEqual(error['code'], 'invalid_body')
                self.assertTrue(error['fields'])
        trip = {'title': '旅遊', 'start_date': '2026-11-01', 'end_date': '2026-11-03', 'adults': 2,
                'items': [{'product_id': 'stay-001', 'day': 0}]}
        r = self.call('POST', '/api/v1/trips', trip, 400)
        self.assertEqual(r.json()['error']['fields'][0], {'field': 'items[0].day', 'rule': 'required'})
        for changes in [{'start_date': '2026-02-30'}, {'start_date': '2026-1-01'}, {'end_date': '2026-11-01'},
                        {'items': [{'product_id': 'missing', 'day': 1}]},
                        {'items': [{'product_id': 'stay-001', 'day': 3}]}]:
            self.call('POST', '/api/v1/trips', {**trip, **changes}, 400)
        quote = {'start_date': '2026-11-01', 'end_date': '2026-11-03', 'adults': 2, 'quantity': 1}
        self.call('POST', '/api/v1/products/stay-001/quotes', {**quote, 'adults': '2'}, 400)
        self.call('POST', '/api/v1/products/cruise-001/quotes', {**quote, 'quantity': 2}, 400)

    def test_authentication_disabled_mode_and_private_reads(self):
        methods = self.client.patch('/api/v1/wishlists').headers['Allow']
        self.assertEqual(set(methods.split(', ')), {'GET', 'HEAD', 'POST', 'OPTIONS'})
        for method, path in [('GET', '/api/v1/wishlists'), ('GET', '/api/v1/trips/x'),
                             ('GET', '/api/v1/products/stay-001/reviews/x'),
                             ('POST', '/api/v1/wishlists'), ('PUT', '/api/v1/trips/x')]:
            self.assertEqual(self.client.request(method, path, headers={'X-API-Key': 'wrong'}).status_code, 401)
            self.assertEqual(self.client.request(method, path).status_code, 401)
        with TestClient(create_app(self.path, '2026-10-20', api_write_key='')) as disabled:
            self.assertEqual(disabled.get('/api/v1/wishlists').status_code, 503)
            self.assertEqual(disabled.get('/api/v1/products').status_code, 200)
        self.assertEqual(self.client.post('/api/v1/wishlists', json={'title': 'a', 'product_ids': []},
                                         headers={'X-API-Key': KEY}).json()['error']['code'], 'invalid_idempotency_key')

    def test_atomic_rollback_and_foreign_keys(self):
        self.call('POST', '/api/v1/wishlists', {'title': 'one', 'product_ids': ['stay-001', 'missing']}, 400)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM documents').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM idempotency_keys').fetchone()[0], 0)
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())
        self.call('POST', '/api/v1/wishlists', {'title': 'one', 'product_ids': ['stay-001']}, 201)

    def test_parallel_retries_and_parallel_version_updates(self):
        body = {'title': 'one', 'product_ids': ['stay-001']}
        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(lambda _: self.client.post('/api/v1/wishlists', json=body, headers=self.headers), range(12)))
        self.assertTrue(all(r.status_code == 201 for r in responses))
        self.assertTrue(all(r.json() == responses[0].json() for r in responses))
        path = responses[0].headers['Location']
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda title: self.client.put(path, json={**body, 'title': title, 'version': 1},
                                                                  headers=self.headers), ('update-a', 'update-b')))
        self.assertEqual(sorted(r.status_code for r in results), [200, 409])
        self.assertEqual(len(self.call('GET', '/api/v1/wishlists').json()['data']), 1)
        self.assertEqual(sorted(p.name for p in Path(self.temp.name).iterdir()), ['travel.sqlite3'])

    def test_body_limit_content_type_and_json_documents(self):
        for body in ['null', '[]', '{} {}', '{bad-json}']:
            r = self.client.post('/api/v1/wishlists', content=body,
                                 headers={**self.headers, 'Content-Type': 'application/json'})
            self.assertEqual(r.status_code, 400, r.text)
            self.assertEqual(r.json()['error']['code'], 'invalid_body')
        self.assertEqual(self.client.post('/api/v1/wishlists', content='{}', headers=self.headers).status_code, 415)
        large = b' ' * 65537
        r = self.client.post('/api/v1/wishlists', content=large,
                             headers={**self.headers, 'Content-Type': 'application/json'})
        self.assertEqual(r.status_code, 413)
        r = self.client.post('/api/v1/wishlists', content=iter([large[:40000], large[40000:]]),
                             headers={**self.headers, 'Content-Type': 'application/json'})
        self.assertEqual(r.status_code, 413)

    def test_cors_request_ids_logs_and_recovery(self):
        app = create_app(self.path, '2026-10-20', api_write_key=KEY, allowed_origins=['http://localhost:5173'])
        @app.get('/panic', include_in_schema=False)
        def panic():
            raise RuntimeError('secret error text')
        with TestClient(app) as client:
            with self.assertLogs('app.access', logging.INFO) as logs:
                response = client.get('/healthz?token=do-not-log', headers={'Origin': 'http://localhost:5173',
                                                                          'X-Request-ID': 'request-test-1234'})
            self.assertEqual(response.headers['X-Request-ID'], 'request-test-1234')
            self.assertNotIn('do-not-log', str(logs.output))
            self.assertNotIn(KEY, str(logs.output))
            self.assertEqual(client.get('/healthz', headers={'Origin': 'https://blocked.example'}).status_code, 403)
            preflight = client.options('/api/v1/wishlists', headers={'Origin': 'http://localhost:5173'})
            self.assertEqual(preflight.status_code, 204)
            self.assertIn('PUT', preflight.headers['Access-Control-Allow-Methods'])
            self.assertEqual(preflight.headers['Vary'], 'Origin')
            with self.assertLogs('app.main', logging.ERROR):
                r = client.get('/panic')
            self.assertEqual(r.status_code, 500)
            self.assertNotIn('secret', r.text)

    def test_rate_limit_ignores_forwarded_client_addresses(self):
        app = create_app(self.path, '2026-10-20', api_write_key=KEY, rate_per_minute=1, burst=1)
        body = {'start_date': '2026-11-01', 'end_date': '2026-11-03', 'adults': 2, 'quantity': 1}
        with TestClient(app) as client:
            self.assertEqual(client.post('/api/v1/products/stay-001/quotes', json=body).status_code, 200)
            r = client.post('/api/v1/products/stay-001/quotes', json=body, headers={'X-Forwarded-For': '192.0.2.1'})
            self.assertEqual(r.status_code, 429)
            self.assertTrue(r.headers['Retry-After'])
            self.assertEqual(client.get('/healthz').status_code, 200)

    def test_shared_request_hash_format(self):
        content = WishlistInput(title='a < b', product_ids=[]).model_dump(exclude={'version'})
        payload = {'content': content, 'version': 0}
        self.assertEqual(encode(payload), '{"content":{"title":"a \\u003c b","description":"","product_ids":[]},"version":0}')


class MigrationTests(unittest.TestCase):
    def test_v1_upgrade_reopen_and_missing_database(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'travel.sqlite3'
            shutil.copyfile(ROOT / 'data/travel.sqlite3', path)
            with closing(sqlite3.connect(path)) as db, db:
                for table in ('document_products', 'documents', 'managed_reviews', 'idempotency_keys'):
                    db.execute('DROP TABLE IF EXISTS ' + table)
                db.execute('PRAGMA user_version=1')
            Workspace(path).initialize()
            Workspace(path).initialize()
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 2)
                self.assertEqual(db.execute('SELECT count(*) FROM products').fetchone()[0], 96)
            missing = Path(folder) / 'missing.sqlite3'
            with self.assertRaises(sqlite3.OperationalError):
                Workspace(missing).initialize()
            self.assertFalse(missing.exists())
        with self.assertRaises(ValueError):
            create_app(api_write_key='short')
        for value in [['*', 'http://localhost:5173'], ['https://example.com/path']]:
            with self.assertRaises(ValueError):
                create_app(allowed_origins=value)
