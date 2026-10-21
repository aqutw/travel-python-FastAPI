"""Typed FastAPI write routes and protected workspace reads."""
from datetime import date, timedelta
import re
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from . import domain
from .errors import APIError, invalid_body
from .models import QuoteInput, ReviewInput, TripInput, WishlistInput
from .query import Query
from .routes import DB, reply
from .workspace import Workspace

router = APIRouter()


def workspace(request: Request):
    return request.app.state.workspace


Store = Annotated[Workspace, Depends(workspace)]


def body_dates(request, start, end):
    start, end = date.fromisoformat(start), date.fromisoformat(end)
    if not request.app.state.today <= start <= request.app.state.today + timedelta(days=365):
        invalid_body('start_date', 'booking_horizon')
    if not 1 <= (end - start).days <= 30:
        invalid_body('end_date', 'range_1_to_30_days')
    return start, end


def mutate(request, store, payload, command):
    Query(request)
    key = ''
    if request.method == 'POST':
        keys = request.headers.getlist('Idempotency-Key')
        if len(keys) != 1 or not re.fullmatch(r'[A-Za-z0-9._-]{8,128}', keys[0]):
            raise APIError(400, 'invalid_idempotency_key', 'Idempotency-Key must occur once and contain 8 to 128 letters, digits, dots, underscores or hyphens')
        key = keys[0]
    status, data, location, replayed = store.apply(request.method + ' ' + request.url.path, key, payload, command)
    headers = {'Location': location}
    if replayed:
        headers['Idempotency-Replayed'] = 'true'
    return JSONResponse(reply(request, data), status_code=status, headers=headers)


def check_version(request, version):
    if request.method == 'PUT' and version < 1:
        invalid_body('version', 'required')
    if request.method == 'POST' and version != 0:
        invalid_body('version', 'omit_on_create')


def save_document(request, store, input, kind, id=''):
    Query(request)
    check_version(request, input.version)
    content = input.model_dump(exclude={'version'})
    if kind == 'trip':
        start, end = body_dates(request, input.start_date, input.end_date)
        seen = set()
        ids = []
        for item in input.items:
            if item.day > (end - start).days:
                invalid_body('items.day', 'within_trip_dates')
            pair = (item.day, item.product_id)
            if pair in seen:
                invalid_body('items', 'unique_product_per_day')
            seen.add(pair)
            ids.append(item.product_id)
    else:
        ids = input.product_ids

    def command(db):
        document = store.save_document(db, kind, id, content, ids, input.version)
        return document, '/api/v1/' + kind + 's/' + document['id']
    return mutate(request, store, {'content': content, 'version': input.version}, command)


@router.get('/api/v1/wishlists')
@router.get('/api/v1/trips')
def documents(request: Request, store: Store):
    q = Query(request, 'page', 'per_page')
    page, size = q.pagination()
    kind = 'wishlist' if request.url.path.endswith('/wishlists') else 'trip'
    return reply(request, store.documents(kind), page, size)


@router.get('/api/v1/wishlists/{id}')
def wishlist(id: str, request: Request, store: Store):
    Query(request)
    return reply(request, store.document('wishlist', id))


@router.get('/api/v1/trips/{id}')
def trip(id: str, request: Request, store: Store):
    Query(request)
    return reply(request, store.document('trip', id))


@router.post('/api/v1/wishlists', status_code=201)
def create_wishlist(input: WishlistInput, request: Request, store: Store):
    return save_document(request, store, input, 'wishlist')


@router.put('/api/v1/wishlists/{id}')
def update_wishlist(id: str, input: WishlistInput, request: Request, store: Store):
    return save_document(request, store, input, 'wishlist', id)


@router.post('/api/v1/trips', status_code=201)
def create_trip(input: TripInput, request: Request, store: Store):
    return save_document(request, store, input, 'trip')


@router.put('/api/v1/trips/{id}')
def update_trip(id: str, input: TripInput, request: Request, store: Store):
    return save_document(request, store, input, 'trip', id)


def save_review(request, store, db, input, product_id, id=''):
    Query(request)
    check_version(request, input.version)
    if input.travel_date > request.app.state.today.isoformat():
        invalid_body('travel_date', 'not_in_future')
    db.one('products', product_id)
    content = input.model_dump(exclude={'version'})

    def command(connection):
        value = store.save_review(connection, product_id, id, content, input.version)
        return value, f'/api/v1/products/{product_id}/reviews/{value["id"]}'
    # Match the normalized request shape used by the shared idempotency table.
    payload = {**content, **({'version': input.version} if input.version else {})}
    return mutate(request, store, payload, command)


@router.post('/api/v1/products/{id}/reviews', status_code=201)
def create_review(id: str, input: ReviewInput, request: Request, store: Store, db: DB):
    return save_review(request, store, db, input, id)


@router.put('/api/v1/products/{id}/reviews/{review_id}')
def update_review(id: str, review_id: str, input: ReviewInput, request: Request, store: Store, db: DB):
    return save_review(request, store, db, input, id, review_id)


@router.get('/api/v1/products/{id}/reviews/{review_id}')
def review(id: str, review_id: str, request: Request, store: Store):
    Query(request)
    return reply(request, store.review(id, review_id))


@router.post('/api/v1/products/{id}/quotes')
def quote(id: str, input: QuoteInput, request: Request, db: DB):
    Query(request)
    product = db.one('products', id)
    dates = body_dates(request, input.start_date, input.end_date)
    if product['type'] != 'stay' and (dates[1] - dates[0]).days != product['duration_days']:
        invalid_body('end_date', 'fixed_product_duration')
    if product['price']['unit'] == 'person' and input.quantity != 1:
        invalid_body('quantity', 'one_for_person_pricing')
    return reply(request, domain.availability(product, dates, input.adults, input.children, input.quantity))
