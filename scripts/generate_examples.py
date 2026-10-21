#!/usr/bin/env python3
"""Run HTTP example recipes through FastAPI using an isolated database copy."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient
from app.main import create_app

REFERENCE_DATE = '2026-10-20'
EXAMPLE_KEY = 'documentation-example-key-not-for-deployment-0001'
EXAMPLES = ROOT / 'docs/examples'


def stem(method, route):
    return method.lower() + '-' + route.strip('/').replace('/', '-').replace('{', '').replace('}', '')


def normalize(value, ids):
    if isinstance(value, str):
        for id, variable in ids.items():
            value = value.replace(id, '{{' + variable + '}}')
        return value
    if isinstance(value, list):
        return [normalize(item, ids) for item in value]
    if isinstance(value, dict):
        if value.get('id') in ids:
            if 'kind' in value:
                value['created_at'] = REFERENCE_DATE + 'T00:00:00Z'
                value['updated_at'] = REFERENCE_DATE + ('T00:05:00Z' if value['version'] > 1 else 'T00:00:00Z')
            else:
                value['created_at'] = REFERENCE_DATE
        return {key: normalize(item, ids) for key, item in value.items()}
    return value


def generate():
    spec = json.loads((ROOT / 'app/openapi.json').read_text())
    operations = [(method.upper(), route, op) for route, methods in spec['paths'].items() for method, op in methods.items()]
    table_rows, pairs = {}, []
    for method, route, op in operations:
        name = stem(method, route)
        req = EXAMPLES / (name + '.request.http')
        assert req.is_file(), f'add a request recipe for {method} {route}'
        pairs.append((method, route, op, req, False))
        invalid = EXAMPLES / (name + '.invalid.request.http')
        if invalid.exists():
            pairs.append((method, route, op, invalid, True))
        table_rows[(method, route)] = (name, invalid.exists())
    # Public seed reads -> creates -> protected reads -> updates/quotes -> errors.
    def order(entry):
        method, route, op, _, invalid = entry
        if invalid:
            return 4, route
        if method == 'GET' and not op.get('security'):
            return 0, route
        if method == 'POST' and op.get('security'):
            return 1, route
        if method == 'GET':
            return 2, route
        return 3, route
    ids = {}
    with tempfile.TemporaryDirectory(prefix='travel-examples-') as folder:
        db_path = Path(folder) / 'travel.sqlite3'
        shutil.copyfile(ROOT / 'data/travel.sqlite3', db_path)
        with TestClient(create_app(db_path, REFERENCE_DATE, api_write_key=EXAMPLE_KEY, burst=100)) as client:
            for method, route, op, req_file, invalid in sorted(pairs, key=order):
                raw = req_file.read_text()
                head, _, body = raw.partition('\n\n')
                lines = head.splitlines()
                sent_method, target, _ = lines[0].split()
                assert sent_method == method
                for id, variable in ids.items():
                    target = target.replace('{{' + variable + '}}', id)
                headers = dict(line.split(': ', 1) for line in lines[1:])
                headers.pop('Host', None)
                if 'X-API-Key' in headers:
                    assert headers['X-API-Key'] == '{{API_WRITE_KEY}}'
                    headers['X-API-Key'] = EXAMPLE_KEY
                response = client.request(method, target, headers=headers, content=body.encode() if body.strip() else None)
                expected = 400 if invalid else 201 if method == 'POST' and op.get('security') else 200
                assert response.status_code == expected, (method, route, response.status_code, response.text)
                result = response.json()
                if expected == 201:
                    variable = 'review_id' if route.endswith('/reviews') else 'wishlist_id' if route.endswith('/wishlists') else 'trip_id'
                    ids[result['data']['id']] = variable
                result = normalize(result, ids)
                rendered = [f'HTTP/1.1 {response.status_code} {response.reason_phrase}']
                for header in ('Content-Type', 'Location', 'X-Request-ID', 'Cache-Control', 'X-Content-Type-Options'):
                    if header in response.headers:
                        rendered.append(header + ': ' + normalize(response.headers[header], ids))
                output = '\n'.join(rendered) + '\n\n' + json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
                req_file.with_name(req_file.name.replace('.request.http', '.response.http')).write_text(output)
    readme = ROOT / 'README.md'
    prefix, marker, _ = readme.read_text().partition('## Request / response examples')
    assert marker
    counts = {method: sum(entry[0] == method for entry in operations) for method in ('GET', 'POST', 'PUT')}
    table = [marker, '', f"共 {len(operations)} 個 API：{counts['GET']} GET、{counts['POST']} POST、{counts['PUT']} PUT。每一列對應一組 method 與 endpoint，範例包含完整 JSON 回應。", '',
             '範例日期為 `REFERENCE_DATE=2026-10-20`。`{{API_WRITE_KEY}}` 請替換為服務金鑰；`{{wishlist_id}}`、`{{trip_id}}`、`{{review_id}}` 請使用對應 POST 回傳的 ID。回應中的時間為固定示例值，PUT 範例使用建立後的 `version=1`。', '',
             '| Method | Endpoint | 說明 | Request 範例 | Response 範例 |', '| --- | --- | --- | --- | --- |']
    for method, route, op in sorted(operations, key=lambda item: (item[1], ('GET', 'POST', 'PUT').index(item[0]))):
        name, invalid = table_rows[(method, route)]
        request = f'[Request](docs/examples/{name}.request.http)'
        response = f'[Response](docs/examples/{name}.response.http)'
        if invalid:
            request += f' · [400](docs/examples/{name}.invalid.request.http)'
            response += f' · [400](docs/examples/{name}.invalid.response.http)'
        summary = op['summary'].replace('|', r'\|')
        table.append(f'| `{method}` | `{route}` | {summary} | {request} | {response} |')
    readme.write_text(prefix + '\n'.join(table) + '\n')
    print(f'Generated {len(operations)} API rows, {len(pairs)} pairs and {len(pairs)*2} HTTP example files.')


if __name__ == '__main__':
    generate()
