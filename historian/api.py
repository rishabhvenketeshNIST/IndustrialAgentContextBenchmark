"""Read-only HTTP API over the Historian (docs/HISTORIAN_QUERY_API.md).

A thin layer over ``HistorianReader``: every query semantic (scopes, access policy, half-open ranges,
limits, ordering, keyset continuation, no interpolation) is the reader's; this module only maps HTTP
parameters to reader calls, encodes continuation keys as opaque cursors, and maps reader errors to
stable JSON errors. It serves GET routes only, never the database file, and executes no caller-supplied
SQL.

The access policy is fixed when the app is created (``access="current"`` by default; ``"evaluator"``
only when the service is started for the evaluator) and no query parameter can change it.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import threading
from typing import Optional

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import SCHEMA, HistorianNotFoundError, HistorianQueryError, HistorianSchemaError
from .reader import DEFAULT_LIMIT, MAX_ROWS, MAX_SPAN_S, HistorianAccessError, HistorianReader

API_VERSION = "acme-historian-api/1"


class _Error(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


# ---------------------------------------------------------------------------- cursors
def _fingerprint(endpoint: str, params: dict) -> str:
    return hashlib.sha256(json.dumps([endpoint, params], sort_keys=True).encode()).hexdigest()[:16]


def encode_cursor(endpoint: str, params: dict, after) -> Optional[str]:
    """An opaque continuation token: the reader's key of the last row returned, bound to the query."""
    if after is None:
        return None
    token = json.dumps({"q": _fingerprint(endpoint, params), "a": after}, separators=(",", ":"))
    return base64.urlsafe_b64encode(token.encode()).decode().rstrip("=")


def decode_cursor(endpoint: str, params: dict, cursor: Optional[str], pair: bool):
    if cursor is None:
        return None
    try:
        raw = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode())
        after = raw["a"]
        ok = raw["q"] == _fingerprint(endpoint, params) and (
            (pair and isinstance(after, list) and len(after) == 2 and all(isinstance(x, int) for x in after))
            or (not pair and isinstance(after, int)))
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        ok = False
    if not ok:
        raise _Error(400, "invalid_cursor", "the cursor is malformed or belongs to another query")
    return tuple(after) if pair else after


# ---------------------------------------------------------------------------- the app
def create_app(database, access: str = "current") -> FastAPI:
    """The API over one Historian file. Raises HistorianSchemaError for an incompatible file and
    FileNotFoundError if it does not exist."""
    reader = HistorianReader(database, access=access)
    lock = threading.Lock()                          # one SQLite connection, used by one request at a time
    app = FastAPI(title="Historian API", version=API_VERSION, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.reader = reader

    @app.exception_handler(_Error)
    async def _own(_: Request, exc: _Error):
        return _error(exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        name = ".".join(str(x) for x in first.get("loc", ())[1:]) or "parameter"
        missing = first.get("type") == "missing"
        return _error(400, "missing_parameter" if missing else "invalid_parameter",
                      f"{'missing' if missing else 'invalid'} query parameter '{name}'")

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return _error(exc.status_code, code, "no such endpoint" if exc.status_code == 404 else
                      "the Historian API is read-only" if exc.status_code == 405 else "request failed")

    def call(fn, *args, **kwargs):
        """Run a reader query and map its errors to stable HTTP errors (no paths, no SQL)."""
        try:
            with lock:
                return fn(*args, **kwargs)
        except HistorianAccessError:
            raise _Error(403, "scope_not_permitted",
                         "this service serves the current scope only; other scopes need evaluator access")
        except HistorianNotFoundError as exc:
            raise _Error(404, f"unknown_{exc.kind}", str(exc))
        except HistorianQueryError as exc:
            raise _Error(400, exc.code, str(exc))
        except HistorianSchemaError:
            raise _Error(503, "incompatible_schema", f"the database is not a {SCHEMA} Historian")
        except sqlite3.Error:
            raise _Error(503, "database_unavailable", "the Historian database is unavailable")

    def covered_range(cov):
        return {"from_t": cov[0]["from_t"], "to_t": cov[-1]["to_t"]} if cov else None

    # ------------------------------------------------------------------ routes (GET only)
    @app.get("/status")
    def status():
        scopes = call(reader.scopes)
        current = call(reader.current_scope)
        cov = call(reader.coverage, current) if current else []
        return {"api": API_VERSION, "schema": SCHEMA, "access": reader.access, "current_scope": current,
                "scopes_visible": len(scopes), "coverage": cov, "covered_range": covered_range(cov),
                "time": "simulation seconds; ranges are half-open [start, end)",
                "limits": {"default_limit": DEFAULT_LIMIT, "max_limit": MAX_ROWS, "max_span_s": MAX_SPAN_S}}

    @app.get("/scopes")
    def scopes():
        out = []
        for s in call(reader.scopes):
            cov = call(reader.coverage, s["operational_scope_id"])
            out.append(dict(s, coverage=cov, covered_range=covered_range(cov)))
        return {"scopes": out}

    @app.get("/entities")
    def entities(scope: str = Query(...), type: Optional[str] = None, parent: Optional[str] = None,
                 limit: int = DEFAULT_LIMIT):
        r = call(reader.entities, scope, entity_type=type, parent_id=parent, limit=limit)
        return {"scope": scope, "rows": r["rows"], "truncated": r["truncated"]}

    @app.get("/series")
    def series(scope: str = Query(...), entity: str = Query(...), variable: Optional[str] = None):
        return {"scope": scope, "rows": call(reader.series, scope, entity, variable)}

    @app.get("/samples")
    def samples(scope: str = Query(...), entity: str = Query(...), variable: str = Query(...),
                start: int = Query(...), end: int = Query(...), limit: int = DEFAULT_LIMIT,
                cursor: Optional[str] = None):
        params = {"scope": scope, "entity": entity, "variable": variable, "start": start, "end": end}
        after = decode_cursor("samples", params, cursor, pair=False)
        r = call(reader.samples, scope, entity, variable, start, end, limit=limit, after=after)
        return {"scope": scope, "series": r["series"], "rows": r["rows"], "truncated": r["truncated"],
                "next_cursor": encode_cursor("samples", params, r["next_after"])}

    @app.get("/value_at")
    def value_at(scope: str = Query(...), entity: str = Query(...), variable: str = Query(...),
                 t: int = Query(...)):
        v = call(reader.value_at, scope, entity, variable, t)
        return dict(v, scope=scope, series=call(reader.series, scope, entity, variable)[0])

    @app.get("/events")
    def events(scope: str = Query(...), start: int = Query(...), end: int = Query(...),
               entity: Optional[str] = None, type: Optional[str] = None, limit: int = DEFAULT_LIMIT,
               cursor: Optional[str] = None):
        params = {"scope": scope, "start": start, "end": end, "entity": entity, "type": type}
        after = decode_cursor("events", params, cursor, pair=True)
        r = call(reader.events, scope, start, end, entity_id=entity, event_type=type, limit=limit, after=after)
        return {"scope": scope, "rows": r["rows"], "truncated": r["truncated"],
                "next_cursor": encode_cursor("events", params, list(r["next_after"]) if r["next_after"] else None)}

    @app.get("/states")
    def states(scope: str = Query(...), entity: str = Query(...), start: int = Query(...), end: int = Query(...),
               property: Optional[str] = None, limit: int = DEFAULT_LIMIT, cursor: Optional[str] = None):
        params = {"scope": scope, "entity": entity, "property": property, "start": start, "end": end}
        after = decode_cursor("states", params, cursor, pair=True)
        r = call(reader.state_changes, scope, start, end, entity_id=entity, prop=property, limit=limit, after=after)
        return {"scope": scope, "rows": r["rows"], "truncated": r["truncated"],
                "next_cursor": encode_cursor("states", params, list(r["next_after"]) if r["next_after"] else None)}

    @app.get("/state_at")
    def state_at(scope: str = Query(...), entity: str = Query(...), t: int = Query(...)):
        return dict(call(reader.state_at, scope, entity, t), scope=scope)

    return app
