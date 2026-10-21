#!/usr/bin/env python3
"""Exercise a running server and validate its real responses against OpenAPI."""
import argparse
import json
import urllib.error
import urllib.parse
import urllib.request

from jsonschema import Draft4Validator
from openapi_spec_validator import validate


def request(base, path, method='GET', data=None, headers=None):
    path = urllib.parse.quote(path, safe='/?=&,%:-')
    req = urllib.request.Request(base + path, method=method, data=data,
                                 headers={'Origin': 'http://localhost:5173', **(headers or {})})
    try:
        response = urllib.request.urlopen(req, timeout=15)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read()
        return response.status, response.headers, raw


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://localhost:8000')
    args = parser.parse_args()
    base = args.base_url.rstrip('/')
    status, _, raw = request(base, '/openapi.json')
    assert status == 200
    spec = json.loads(raw)
    validate(spec)
    checked = 0

    def check(path, schema):
        nonlocal checked
        status, headers, raw = request(base, path)
        assert status == 200, (path, status, raw[:300])
        assert headers['Access-Control-Allow-Origin'] == '*'
        assert 'application/json' in headers['Content-Type']
        body = json.loads(raw)
        # Keep local component references rooted at the complete OpenAPI document.
        Draft4Validator({**spec, **schema}, format_checker=Draft4Validator.FORMAT_CHECKER).validate(body)
        checked += 1
        return body

    for route, item in spec['paths'].items():
        operation = item.get('get')
        if not operation or operation.get('security'):
            continue
        schema = operation['responses']['200']['content']['application/json']['schema']
        check(operation['x-smoke-path'], schema)
        print('PASS', route)

    detail_schema = spec['paths']['/api/v1/products/{id}']['get']['responses']['200']['content']['application/json']['schema']
    list_schema = spec['paths']['/api/v1/products']['get']['responses']['200']['content']['application/json']['schema']
    cards = check('/api/v1/products?per_page=100', list_schema)['data']
    assert len(cards) == 96
    images = set()
    for card in cards:
        product = check(card['detail_url'], detail_schema)['data']
        assert product['id'] == card['id'] and product['price'] == card['price']
        images.update(image['url'] for image in product['images'])
    for image in images:
        status, headers, raw = request(base, image)
        assert status == 200 and b'<svg' in raw
        assert 'image/svg+xml' in headers['Content-Type']
    for method, expected in [('HEAD', 200), ('OPTIONS', 204), ('POST', 405)]:
        status, _, raw = request(base, '/api/v1/products', method)
        assert status == expected, (method, status)
        if method in ('HEAD', 'OPTIONS'):
            assert not raw
    status, _, raw = request(base, '/api/v1/products?min_rating=NaN')
    assert status == 400 and json.loads(raw)['error']['code'] == 'invalid_query'
    print(f'Validated OpenAPI and {checked} live JSON responses; {len(images)} local images; CORS and method handling.')


if __name__ == '__main__':
    main()
