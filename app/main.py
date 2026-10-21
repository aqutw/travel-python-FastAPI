"""Application factory and ASGI transport policy."""
import json
import logging
import os
import re
import sqlite3
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException

from .database import Catalog
from .errors import APIError
from .routes import router
from .writes import router as write_router
from .workspace import Workspace
from .middleware import TransportPolicy, validate_origins

ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger(__name__)


def create_app(db_path: str | Path | None = None, reference_date: str | None = None,
               api_write_key: str | None = None, allowed_origins: list[str] | None = None,
               rate_per_minute: int = 60, burst: int = 20) -> FastAPI:
    path = Path(db_path or os.environ.get("DB_PATH", ROOT / "data/travel.sqlite3")).resolve()
    fixed_date = reference_date if reference_date is not None else os.environ.get("REFERENCE_DATE")

    key = api_write_key if api_write_key is not None else os.environ.get('API_WRITE_KEY', '')
    if key and len(key.encode()) < 32:
        raise ValueError('API_WRITE_KEY must contain at least 32 bytes')
    origins = validate_origins(allowed_origins if allowed_origins is not None else
                               [o.strip() for o in os.environ.get('CORS_ORIGINS', '*').split(',')])
    if rate_per_minute <= 0 or burst <= 0:
        raise ValueError('rate and burst must be positive')

    @asynccontextmanager
    async def lifespan(application):
        if fixed_date is not None:
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", fixed_date):
                raise ValueError("REFERENCE_DATE must use YYYY-MM-DD")
            today = date.fromisoformat(fixed_date)
        else:
            today = datetime.now(ZoneInfo("Asia/Taipei")).date()
        if today.year > 9997:
            raise ValueError("REFERENCE_DATE must leave room for the booking horizon")
        catalog = Catalog(path)
        try:
            catalog.validate()
        finally:
            catalog.close()
        if key:
            application.state.workspace.initialize()
        application.state.today = today
        yield

    application = FastAPI(title="Travel Python FastAPI", version="2.0.0", lifespan=lifespan,
                          redoc_url=None, redirect_slashes=False, swagger_ui_parameters={"displayRequestDuration": True})
    application.state.db_path = path
    application.state.workspace = Workspace(path)
    application.include_router(router)
    application.include_router(write_router)
    application.add_middleware(TransportPolicy, api_key=key, origins=origins,
                               rate_per_minute=rate_per_minute, burst=burst)

    @application.exception_handler(APIError)
    async def api_error(request: Request, error: APIError):
        body = {'code': error.code, 'message': error.message}
        if error.fields:
            body['fields'] = error.fields
        return JSONResponse({'error': body}, status_code=error.status)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        fields = []
        for entry in error.errors():
            parts = list(entry['loc'])
            if parts and parts[0] == 'body':
                parts.pop(0)
            field = ''
            for part in parts:
                field += f'[{part}]' if isinstance(part, int) else ('.' if field else '') + str(part)
            field = field or 'body'
            kind = entry['type']
            rule = {'missing': 'required', 'greater_than_equal': 'min', 'less_than_equal': 'max',
                    'string_too_short': 'min', 'string_too_long': 'max',
                    'too_long': 'max', 'json_invalid': 'single_json_object'}.get(kind, kind)
            if kind in ('int_type', 'string_type', 'list_type', 'extra_forbidden'):
                field, rule = 'body', 'known_fields_and_types'
            if kind in ('model_type', 'model_attributes_type'):
                field, rule = 'body', 'json_object'
            if entry.get('input') == 0 and field.rsplit('.', 1)[-1] in ('day', 'adults', 'rating', 'quantity'):
                rule = 'required'
            if entry.get('input') == '' and kind == 'string_too_short':
                rule = 'required'
            violation = {'field': field, 'rule': rule}
            if violation not in fields:
                fields.append(violation)
        return JSONResponse({'error': {'code': 'invalid_body', 'message': 'JSON body validation failed',
                                      'fields': fields}}, status_code=400)

    async def database_error(request: Request, error: Exception):
        logger.error("catalog query failed", exc_info=error)
        return JSONResponse({"error": {"code": "database_error", "message": "unable to read catalog"}}, status_code=500)

    application.add_exception_handler(sqlite3.Error, database_error)
    application.add_exception_handler(json.JSONDecodeError, database_error)

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        if error.status_code == 405:
            methods = {method for route in application.routes
                       if route.path_regex.fullmatch(request.scope['path'])
                       for method in route.methods}
            if 'GET' in methods:
                methods.add('HEAD')
            methods.add('OPTIONS')
            return JSONResponse({'error': {'code': 'method_not_allowed',
                'message': 'method is not supported for this route'}}, status_code=405,
                headers={'Allow': ', '.join(method for method in ('GET', 'HEAD', 'POST', 'PUT', 'OPTIONS')
                                           if method in methods)})
        return JSONResponse({"error": {"code": "not_found", "message": "route not found"}}, status_code=404)

    @application.get("/", include_in_schema=False, response_class=HTMLResponse)
    def explorer():
        return (ROOT / "app/index.html").read_text()

    # Keep the published contract authoritative, including product variants and
    # strict query constraints which cannot be inferred from Request dependencies.
    contract = json.loads((ROOT / "app/openapi.json").read_text())
    implemented = {(method.lower(), r.path) for r in application.routes
                   if getattr(r, 'include_in_schema', False) for method in r.methods}
    documented = {(method, path) for path, methods in contract['paths'].items() for method in methods}
    if implemented != documented:
        raise RuntimeError("OpenAPI paths do not match implemented routes")
    application.openapi = lambda: contract
    return application


app = create_app()
