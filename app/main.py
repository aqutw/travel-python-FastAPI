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
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException

from .database import Catalog
from .errors import APIError
from .routes import router

ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger(__name__)


class TransportPolicy:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        method = scope["method"]

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"access-control-allow-origin", b"*"),
                    (b"access-control-allow-methods", b"GET, HEAD, OPTIONS"),
                    (b"access-control-allow-headers", b"Content-Type"),
                    (b"x-content-type-options", b"nosniff"), (b"cache-control", b"no-store"),
                ]
            if message["type"] == "http.response.body" and method == "HEAD":
                message["body"] = b""
            await send(message)

        if method == "OPTIONS":
            response = JSONResponse(None, status_code=204)
            response.body = b""
            return await response(scope, receive, send_with_headers)
        if method not in ("GET", "HEAD"):
            response = JSONResponse({"error": {"code": "method_not_allowed", "message":
                "only GET, HEAD and OPTIONS are supported"}}, status_code=405,
                headers={"Allow": "GET, HEAD, OPTIONS"})
            return await response(scope, receive, send_with_headers)
        # Route HEAD through the GET handler, preserving headers and suppressing body.
        routed_scope = {**scope, "method": "GET"} if method == "HEAD" else scope
        await self.app(routed_scope, receive, send_with_headers)


def create_app(db_path: str | Path | None = None, reference_date: str | None = None) -> FastAPI:
    path = Path(db_path or os.environ.get("DB_PATH", ROOT / "data/travel.sqlite3")).resolve()
    fixed_date = reference_date if reference_date is not None else os.environ.get("REFERENCE_DATE")

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
        application.state.today = today
        yield

    application = FastAPI(title="Travel Python FastAPI", version="1.0.0", lifespan=lifespan,
                          redoc_url=None, swagger_ui_parameters={"displayRequestDuration": True})
    application.state.db_path = path
    application.include_router(router)
    application.add_middleware(TransportPolicy)

    @application.exception_handler(APIError)
    async def api_error(request: Request, error: APIError):
        return JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=error.status)

    async def database_error(request: Request, error: Exception):
        logger.error("catalog query failed", exc_info=error)
        return JSONResponse({"error": {"code": "database_error", "message": "unable to read catalog"}}, status_code=500)

    application.add_exception_handler(sqlite3.Error, database_error)
    application.add_exception_handler(json.JSONDecodeError, database_error)

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        return JSONResponse({"error": {"code": "not_found", "message": "route not found"}}, status_code=404)

    @application.get("/", include_in_schema=False, response_class=HTMLResponse)
    def explorer():
        return (ROOT / "app/index.html").read_text()

    # Keep the published contract authoritative, including product variants and
    # strict query constraints which cannot be inferred from Request dependencies.
    contract = json.loads((ROOT / "app/openapi.json").read_text())
    implemented = {r.path for r in application.routes if getattr(r, "include_in_schema", False)}
    if implemented != set(contract["paths"]):
        raise RuntimeError("OpenAPI paths do not match implemented routes")
    application.openapi = lambda: contract
    return application


app = create_app()
