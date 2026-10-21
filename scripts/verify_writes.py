#!/usr/bin/env python3
"""Exercise persistent POST/PUT workflows against their generated OpenAPI schemas."""
import argparse
from datetime import date, timedelta
import json
import os
import re
import uuid

from jsonschema import Draft4Validator
from verify_api import request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://localhost:8000')
    args = parser.parse_args()
    base = args.base_url.rstrip('/')
    key = os.environ.get('API_WRITE_KEY')
    if not key:
        parser.error('set API_WRITE_KEY for workspace operations')
    spec = json.loads(request(base, '/openapi.json')[2])
    checked = 0

    def operation(method, path):
        for route, item in spec['paths'].items():
            if re.fullmatch(re.sub(r'\{[^}]+\}', '[^/]+', route), path.split('?')[0]) and method.lower() in item:
                return item[method.lower()]
        raise AssertionError((method, path))

    def call(method, path, body=None, expected=200, replay_key=None, authenticated=True):
        nonlocal checked
        op = operation(method, path)
        if body is not None and expected in (200, 201):
            shape = op['requestBody']['content']['application/json']['schema']
            Draft4Validator({**spec, **shape}, format_checker=Draft4Validator.FORMAT_CHECKER).validate(body)
        headers = {'Content-Type': 'application/json'}
        if authenticated:
            headers['X-API-Key'] = key
        if replay_key:
            headers['Idempotency-Key'] = replay_key
        status, returned_headers, raw = request(base, path, method, None if body is None else json.dumps(body).encode(), headers)
        assert status == expected, (method, path, status, raw[:500])
        result = json.loads(raw)
        shape = op['responses'][str(status)]['content']['application/json']['schema']
        Draft4Validator({**spec, **shape}, format_checker=Draft4Validator.FORMAT_CHECKER).validate(result)
        assert returned_headers['X-Request-ID']
        checked += 1
        return result, returned_headers

    reference = json.loads(request(base, '/healthz')[2])['meta']['reference_date']
    start = date.fromisoformat(reference) + timedelta(days=12)
    dates = {'start_date': str(start), 'end_date': str(start + timedelta(days=2))}
    quote, _ = call('POST', '/api/v1/products/stay-001/quotes', {**dates, 'adults': 2, 'children': 0, 'quantity': 1}, authenticated=False)
    get_path = '/api/v1/products/stay-001/availability?start_date='+dates['start_date']+'&end_date='+dates['end_date']
    assert quote == json.loads(request(base, get_path)[2])
    wishlist = {'title': '週末收藏', 'description': '住宿候選', 'product_ids': ['stay-001', 'stay-002']}
    replay_key = uuid.uuid4().hex
    created, headers = call('POST', '/api/v1/wishlists', wishlist, 201, replay_key)
    repeated, replay_headers = call('POST', '/api/v1/wishlists', wishlist, 201, replay_key)
    assert created == repeated and replay_headers['Idempotency-Replayed'] == 'true'
    location = headers['Location']
    got, _ = call('GET', location)
    assert got == created
    put = {**wishlist, 'title': '更新收藏', 'product_ids': ['stay-003'], 'version': 1}
    updated, _ = call('PUT', location, put)
    retry, _ = call('PUT', location, put)
    assert updated == retry and updated['data']['version'] == 2
    call('PUT', location, {**put, 'title': '舊版本'}, 409)
    call('POST', '/api/v1/wishlists', {**wishlist, 'title': '不同內容'}, 409, replay_key)
    call('GET', '/api/v1/wishlists')

    trip = {'title': '台北兩日', **dates, 'adults': 2, 'children': 0, 'notes': '',
            'items': [{'product_id': 'stay-001', 'day': 1, 'notes': '入住'}]}
    _, headers = call('POST', '/api/v1/trips', trip, 201, uuid.uuid4().hex)
    updated, _ = call('PUT', headers['Location'], {**trip, 'notes': '安排早餐', 'version': 1})
    assert updated['data']['version'] == 2
    call('GET', headers['Location'])
    call('GET', '/api/v1/trips')
    review = {'author': '測試旅人', 'rating': 5, 'content': '交通便利', 'travel_date': reference}
    review_path = '/api/v1/products/stay-001/reviews'
    _, headers = call('POST', review_path, review, 201, uuid.uuid4().hex)
    updated, _ = call('PUT', headers['Location'], {**review, 'rating': 4, 'version': 1})
    assert updated['data']['version'] == 2
    call('GET', headers['Location'])
    call('GET', '/api/v1/wishlists', expected=401, authenticated=False)
    call('POST', '/api/v1/wishlists', {'title': ' ', 'product_ids': []}, 400, uuid.uuid4().hex)
    call('POST', '/api/v1/trips', {**trip, 'items': [{'product_id': 'stay-001', 'day': 3}]}, 400, uuid.uuid4().hex)
    print(f'PASS: {checked} authenticated/read/write/error responses; all 7 POST/PUT operations and all 5 private GET operations validated.')


if __name__ == '__main__':
    main()
