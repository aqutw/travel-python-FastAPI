"""ASGI request limits, workspace authentication, CORS and request tracing."""
import hashlib
import hmac
import json
import logging
import math
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse, Response

MAX_BODY_BYTES = 64 << 10
REQUEST_ID = re.compile(r'^[A-Za-z0-9_-]{8,64}$')
logger = logging.getLogger('app.access')
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
logger.propagate = False


def private_operation(method, path):
    if re.fullmatch(r'/api/v1/(wishlists|trips)', path):
        return method in ('GET', 'HEAD', 'POST')
    if re.fullmatch(r'/api/v1/(wishlists|trips)/[^/]+', path):
        return method in ('GET', 'HEAD', 'PUT')
    if re.fullmatch(r'/api/v1/products/[^/]+/reviews', path):
        return method == 'POST'
    return bool(re.fullmatch(r'/api/v1/products/[^/]+/reviews/[^/]+', path)) and method in ('GET', 'HEAD', 'PUT')


def validate_origins(origins):
    if origins == ['*']:
        return origins
    for origin in origins:
        url = urlsplit(origin)
        if url.scheme not in ('http', 'https') or not url.netloc or url.path or url.query or url.fragment or url.username:
            raise ValueError('CORS_ORIGINS must contain full http/https origins, or a single wildcard')
    return origins


class TransportPolicy:
    def __init__(self, app, api_key='', origins=None, rate_per_minute=60, burst=20):
        self.app = app
        self.api_key = api_key
        self.digest = hashlib.sha256(api_key.encode()).digest()
        self.origins = origins or ['*']
        self.rate = rate_per_minute
        self.burst = burst
        self.buckets = {}
        self.lock = threading.Lock()

    def permit(self, peer):
        now = time.monotonic()
        with self.lock:
            if peer not in self.buckets:
                self.buckets = {key: value for key, value in self.buckets.items() if now - value[1] <= 600}
                if len(self.buckets) >= 1024:
                    return False, 60
            tokens, updated = self.buckets.get(peer, (self.burst, now))
            tokens = min(self.burst, tokens + (now - updated) * self.rate / 60)
            permitted = tokens >= 1
            self.buckets[peer] = (tokens - 1 if permitted else tokens, now)
            return permitted, max(1, math.ceil((1 - tokens) * 60 / self.rate))

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        method, path = scope['method'], scope['path']
        headers = Headers(scope=scope)
        request_id = headers.get('X-Request-ID', '')
        if not REQUEST_ID.fullmatch(request_id):
            request_id = secrets.token_hex(16)
        scope['request_id'] = request_id
        origin = headers.get('Origin', '')
        cors = '*' if '*' in self.origins else origin if origin in self.origins else ''
        started, sent, status = time.monotonic(), False, 500

        async def traced_send(message):
            nonlocal sent, status
            if message['type'] == 'http.response.start':
                sent, status = True, message['status']
                added = [(b'x-request-id', request_id.encode()), (b'cache-control', b'no-store'),
                         (b'x-content-type-options', b'nosniff'),
                         (b'access-control-allow-methods', b'GET, HEAD, POST, PUT, OPTIONS'),
                         (b'access-control-allow-headers', b'Content-Type, X-API-Key, Idempotency-Key, X-Request-ID'),
                         (b'access-control-expose-headers', b'X-Request-ID, Location, Idempotency-Replayed, Retry-After')]
                if cors:
                    added.append((b'access-control-allow-origin', cors.encode()))
                if self.origins != ['*']:
                    added.append((b'vary', b'Origin'))
                message['headers'] = list(message.get('headers', [])) + added
            if message['type'] == 'http.response.body' and method == 'HEAD':
                message['body'] = b''
            await send(message)

        async def failure(code, name, message, extra=None):
            await JSONResponse({'error': {'code': name, 'message': message}}, status_code=code, headers=extra)(scope, receive, traced_send)

        try:
            if origin and not cors:
                return await failure(403, 'origin_not_allowed', 'origin is not allowed')
            if method == 'OPTIONS':
                return await Response(status_code=204)(scope, receive, traced_send)
            if method in ('POST', 'PUT'):
                try:
                    length = int(headers.get('Content-Length', '0'))
                except ValueError:
                    length = 0
                if length > MAX_BODY_BYTES:
                    return await failure(413, 'body_too_large', 'JSON body must be at most 65536 bytes')
            protected = private_operation(method, path)
            if protected:
                if not self.api_key:
                    return await failure(503, 'writes_disabled', 'configure API_WRITE_KEY to enable workspace access')
                supplied = hashlib.sha256(headers.get('X-API-Key', '').encode()).digest()
                if not hmac.compare_digest(self.digest, supplied):
                    return await failure(401, 'unauthorized', 'a valid X-API-Key header is required')
            quote = method == 'POST' and bool(re.fullmatch(r'/api/v1/products/[^/]+/quotes', path))
            if method in ('POST', 'PUT') and (protected or quote):
                permitted, retry = self.permit((scope.get('client') or ('unknown',))[0])
                if not permitted:
                    return await failure(429, 'rate_limited', 'write request limit exceeded', {'Retry-After': str(retry)})
                if headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json':
                    return await failure(415, 'unsupported_media_type', 'Content-Type must be application/json')
            if method in ('POST', 'PUT'):
                body = bytearray()
                while True:
                    chunk = await receive()
                    if chunk['type'] == 'http.disconnect':
                        return
                    payload = chunk.get('body', b'')
                    if len(body) + len(payload) > MAX_BODY_BYTES:
                        return await failure(413, 'body_too_large', 'JSON body must be at most 65536 bytes')
                    body.extend(payload)
                    if not chunk.get('more_body'):
                        break
                original_receive, delivered = receive, False

                async def buffered_receive():
                    nonlocal delivered
                    if not delivered:
                        delivered = True
                        return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
                    return await original_receive()

                downstream_receive = buffered_receive
            else:
                downstream_receive = receive
            routed_scope = {**scope, 'method': 'GET'} if method == 'HEAD' else scope
            await self.app(routed_scope, downstream_receive, traced_send)
            if 'route' in routed_scope:
                scope['route'] = routed_scope['route']
        except Exception:
            logging.getLogger('app.main').error('request panic', extra={'request_id': request_id})
            if not sent:
                await failure(500, 'internal_error', 'internal server error')
        finally:
            route = getattr(scope.get('route'), 'path', 'unmatched')
            logger.info(json.dumps({'event': 'http_request', 'request_id': request_id, 'method': method,
                                   'route': route, 'status': status,
                                   'duration_ms': round((time.monotonic() - started) * 1000)}))
