import datetime as dt
import time
import typing as t
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.constants import STALE_AFTER_SECONDS
from app.db import ameta_get

_BYPASS_PATHS: t.Final[frozenset[str]] = frozenset(
    {"/", "/stats", "/banlist", "/updatebanlist"}
)


class StalenessMiddleware(BaseHTTPMiddleware):
    """Inject 503 / X-Arkviewer-Stale based on `meta.last_parse_at`.

    Uses the async ameta_get so the event loop is never blocked. Health and
    banlist endpoints are exempt so they answer during cold start.

    Pre: `db_path` points at an initialised schema (meta table exists).
    Post: data routes return 503 when no parse on disk; otherwise 200 with
    `X-Arkviewer-Stale: true` when last_parse_at > STALE_AFTER_SECONDS.
    """

    def __init__(self, app: t.Any, db_path: Path) -> None:
        super().__init__(app)
        assert isinstance(db_path, Path)
        self.db_path = db_path

    async def dispatch(self, request: Request, call_next: t.Any) -> Response:
        assert request is not None
        # Normalize a trailing slash before the bypass check: the documented
        # AVClient consumer calls `stats/`, `banlist/`, `updatebanlist/` with
        # trailing slashes, and an exact match would 503 those during cold
        # start even though they must answer without parsed data.
        normalized = request.url.path.rstrip("/") or "/"
        is_bypass = normalized in _BYPASS_PATHS
        last = await ameta_get(self.db_path, "last_parse_at")

        if last is None and not is_bypass:
            return JSONResponse(
                {"detail": "No parse yet; data unavailable"},
                status_code=503,
                headers={"Retry-After": "30"},
            )

        response = await call_next(request)

        if last is not None:
            try:
                last_int = int(last)
            except ValueError:
                last_int = 0
            if int(time.time()) - last_int > STALE_AFTER_SECONDS:
                response.headers["X-Arkviewer-Stale"] = "true"
                response.headers["X-Arkviewer-Last-Parse"] = dt.datetime.fromtimestamp(
                    last_int, tz=dt.timezone.utc
                ).isoformat()

        assert response is not None
        return response
