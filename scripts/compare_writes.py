#!/usr/bin/env python3
"""Verify writes and replay records across two services sharing a TEST database."""
import argparse
from datetime import date, timedelta
import json
import os
import uuid

from verify_api import request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://localhost:8000')
    parser.add_argument('--reference-url', default='http://localhost:8080')
    args = parser.parse_args()
    key = os.environ.get('API_WRITE_KEY')
    if not key:
        parser.error('set API_WRITE_KEY; both services must share one TEST database')
    services = (args.base_url.rstrip('/'), args.reference_url.rstrip('/'))
    checked = 0

    def call(base, method, path, body=None, replay_key=None, expected=200):
        nonlocal checked
        headers = {'X-API-Key': key, 'Content-Type': 'application/json'}
        if replay_key:
            headers['Idempotency-Key'] = replay_key
        status, returned, raw = request(base, path, method,
            None if body is None else json.dumps(body, ensure_ascii=False).encode(), headers)
        assert status == expected, (base, method, path, status, raw[:500])
        checked += 1
        return json.loads(raw), returned

    references = [json.loads(request(base, '/healthz')[2])['meta']['reference_date'] for base in services]
    assert references[0] == references[1], 'services need the same REFERENCE_DATE'
    start = date.fromisoformat(references[0]) + timedelta(days=12)
    dates = {'start_date': str(start), 'end_date': str(start + timedelta(days=2))}
    cases = [
        ('/api/v1/wishlists', {'title': '旅遊 <>&\u2028\u2029', 'product_ids': ['stay-001']}, {'title': '新版收藏'}),
        ('/api/v1/trips', {'title': '兩日行程', **dates, 'adults': 2,
                           'items': [{'product_id': 'stay-001', 'day': 1}]}, {'notes': '安排早餐'}),
        ('/api/v1/products/stay-001/reviews', {'author': '跨服務旅人', 'rating': 5,
             'content': '房間 <>&\u2028\u2029', 'travel_date': references[0]}, {'rating': 4}),
    ]
    for index, (path, body, change) in enumerate(cases):
        first, second = services[::(-1 if index % 2 else 1)]
        replay_key = uuid.uuid4().hex
        created, headers = call(first, 'POST', path, body, replay_key, 201)
        replayed, replay_headers = call(second, 'POST', path, body, replay_key, 201)
        assert created == replayed and headers['Location'] == replay_headers['Location']
        assert replay_headers['Idempotency-Replayed'] == 'true'
        location = headers['Location']
        assert call(first, 'GET', location)[0] == call(second, 'GET', location)[0] == created
        updated = {**body, **change, 'version': 1}
        saved, _ = call(second, 'PUT', location, updated)
        assert call(first, 'PUT', location, updated)[0] == saved
        assert saved['data']['version'] == 2
        stale = {**updated, 'version': 1, **({'rating': 3} if path.endswith('/reviews') else {'title': '舊版本'})}
        assert call(first, 'PUT', location, stale, expected=409)[0] == call(second, 'PUT', location, stale, expected=409)[0]
        changed = {**body, **({'rating': 2} if path.endswith('/reviews') else {'title': '不同內容'})}
        assert call(first, 'POST', path, changed, replay_key, 409)[0] == call(second, 'POST', path, changed, replay_key, 409)[0]
    for path in ('/api/v1/wishlists', '/api/v1/trips'):
        assert call(services[0], 'GET', path)[0] == call(services[1], 'GET', path)[0]
    quote = {**dates, 'adults': 2, 'quantity': 1}
    path = '/api/v1/products/stay-001/quotes'
    assert call(services[0], 'POST', path, quote)[0] == call(services[1], 'POST', path, quote)[0]
    print(f'PASS: {checked} cross-service responses; all 7 writes, 5 private reads, replay hashes and versions match.')


if __name__ == '__main__':
    main()
