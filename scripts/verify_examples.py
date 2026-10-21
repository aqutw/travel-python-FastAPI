#!/usr/bin/env python3
"""Check README coverage, HTTP example links and request/response schemas."""
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

from jsonschema import Draft4Validator

ROOT = Path(__file__).resolve().parents[1]


def message(path):
    text = path.read_text()
    header, separator, body = text.partition('\n\n')
    assert separator, f'{path}: missing header/body separator'
    lines = header.splitlines()
    headers = dict(line.split(': ', 1) for line in lines[1:])
    return lines[0], headers, json.loads(body) if body.strip() else None


def validator(spec, schema):
    return Draft4Validator({**spec, **schema}, format_checker=Draft4Validator.FORMAT_CHECKER)


def resolve(spec, value):
    if '$ref' not in value:
        return value
    result = spec
    for key in value['$ref'].removeprefix('#/').split('/'):
        result = result[key]
    return result


def main():
    spec = json.loads((ROOT / 'app/openapi.json').read_text())
    readme = (ROOT / 'README.md').read_text()
    section = readme.split('## Request / response examples\n', 1)[1]
    assert '```' not in section, 'README examples must link to files, without inline payloads'
    pattern = r'^\| `(GET|POST|PUT)` \| `([^`]+)` \| ([^|]+) \| ([^|]+) \| ([^|]+) \|$'
    rows = re.findall(pattern, section, re.M)
    expected = {(method.upper(), route) for route, methods in spec['paths'].items() for method in methods}
    actual = [(method, route) for method, route, *_ in rows]
    assert len(actual) == len(set(actual)), 'duplicate method/endpoint rows'
    assert set(actual) == expected, (expected - set(actual), set(actual) - expected)
    checked = set()
    pairs = 0
    for method, route, _, request_cell, response_cell in rows:
        operation = spec['paths'][route][method.lower()]
        requests = re.findall(r'\[([^]]+)\]\(([^)]+)\)', request_cell)
        responses = re.findall(r'\[([^]]+)\]\(([^)]+)\)', response_cell)
        assert len(requests) == len(responses) and requests, (method, route)
        for (request_label, request_file), (response_label, response_file) in zip(requests, responses):
            assert (request_label, response_label) == ('Request', 'Response') or request_label == response_label
            req_path, res_path = ROOT / request_file, ROOT / response_file
            for path in (req_path, res_path):
                assert path.resolve().is_relative_to(ROOT / 'docs/examples')
                assert path.is_file(), path
                checked.add(path.resolve())
            request_line, request_headers, request_body = message(req_path)
            sent_method, target, protocol = request_line.split()
            assert sent_method == method and protocol == 'HTTP/1.1', req_path
            parsed = urlsplit(target)
            route_pattern = '/'.join('[^/]+' if part.startswith('{') and part.endswith('}') else re.escape(part)
                                     for part in route.split('/'))
            assert re.fullmatch(route_pattern, parsed.path), (req_path, route)
            assert request_headers['Host'] == 'localhost:8000'
            if operation.get('security'):
                assert request_headers['X-API-Key'] == '{{API_WRITE_KEY}}'
            status_line, response_headers, response_body = message(res_path)
            protocol, status, _ = status_line.split(' ', 2)
            assert protocol == 'HTTP/1.1' and status in operation['responses'], res_path
            if request_label.isdigit():
                assert status == request_label, res_path
            else:
                assert int(status) < 400, res_path
            response_schema = operation['responses'][status]['content']['application/json']['schema']
            validator(spec, response_schema).validate(response_body)
            assert response_headers['Content-Type'].startswith('application/json'), res_path
            assert response_headers['X-Request-ID'] == request_headers['X-Request-ID'], res_path
            request_errors = []
            if 'requestBody' in operation:
                assert request_headers['Content-Type'] == 'application/json'
                body_schema = operation['requestBody']['content']['application/json']['schema']
                request_errors.extend(validator(spec, body_schema).iter_errors(request_body))
            else:
                assert request_body is None, req_path
            query = parse_qs(parsed.query, keep_blank_values=True)
            for entry in operation.get('parameters', []):
                parameter = resolve(spec, entry)
                name = parameter['name']
                if parameter['in'] == 'header':
                    value = request_headers.get(name)
                    if parameter.get('required'):
                        assert value is not None, (req_path, name)
                    if value is not None:
                        request_errors.extend(validator(spec, parameter['schema']).iter_errors(value))
                elif parameter['in'] == 'query' and name in query:
                    assert len(query[name]) == 1, (req_path, name)
                    value = query[name][0]
                    kind = parameter['schema'].get('type')
                    if kind == 'integer':
                        value = int(value)
                    elif kind == 'number':
                        value = float(value)
                    elif kind == 'boolean':
                        assert value in ('true', 'false')
                        value = value == 'true'
                    request_errors.extend(validator(spec, parameter['schema']).iter_errors(value))
            if int(status) < 400:
                assert not request_errors, (req_path, [error.message for error in request_errors])
            else:
                assert request_errors, f'{req_path}: invalid example unexpectedly satisfies the request schema'
            pairs += 1
    files = {p.resolve() for p in (ROOT / 'docs/examples').glob('*.http')}
    assert files == checked, ('unlinked files', files - checked)
    print(f'PASS: {len(actual)} unique API rows; {pairs} request/response pairs; {len(files)} linked HTTP files; all schemas valid.')


if __name__ == '__main__':
    main()
