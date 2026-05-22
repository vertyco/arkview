"""FTS5-ranked search endpoints.

Exists to push the heavy `findtame` / `findstructure` / `findplayer` workload
off the Discord-bot consumer. Bot was parsing 16k-row JSON responses with
pydantic per command; here we do the search in SQLite and return at most
`limit` rows (default 200). Returned shape matches the existing `/data/*`
endpoints so consumers parse with the same `Response.tamed` etc. — no
model changes required on the cog side.
"""

import typing as t

from fastapi import APIRouter, Depends, Query

from app.auth import RequireBearer
from app.config import AppConfig
from app.db import asearch
from app.metadata import get_metadata


def build_router(cfg: AppConfig) -> APIRouter:
    auth = RequireBearer(cfg.api_key)
    router = APIRouter(dependencies=[Depends(auth)])

    @router.get("/data/search/tamed")
    async def search_tamed(
        q: str = Query(
            "", description="Search across name, tamer, imprinter, tribe, dinoid"
        ),
        tribe_id: int | None = Query(None),
        class_name: str | None = Query(None, description="Exact creature classname"),
        include_cryo: bool = Query(True),
        include_uploaded: bool = Query(True),
        limit: int = Query(200, ge=1, le=1000),
    ) -> dict[str, t.Any]:
        filters: list[tuple[str, t.Any]] = []
        if tribe_id is not None:
            filters.append(("tribeid", tribe_id))
        if class_name is not None:
            filters.append(("creature", class_name))
        if not include_cryo:
            filters.append(("cryo", 0))
        if not include_uploaded:
            filters.append(("uploaded", 0))
        rows = await asearch(cfg.db_path, "tamed", q, filters, limit)
        return {"tamed": rows, **await get_metadata(cfg)}

    @router.get("/data/search/structures")
    async def search_structures(
        q: str = Query("", description="Search across name, tribe, struct"),
        tribe_id: int | None = Query(None),
        class_name: str | None = Query(None, description="Exact struct classname"),
        limit: int = Query(200, ge=1, le=1000),
    ) -> dict[str, t.Any]:
        filters: list[tuple[str, t.Any]] = []
        if tribe_id is not None:
            filters.append(("tribeid", tribe_id))
        if class_name is not None:
            filters.append(("struct", class_name))
        rows = await asearch(cfg.db_path, "structures", q, filters, limit)
        return {"structures": rows, **await get_metadata(cfg)}

    @router.get("/data/search/players")
    async def search_players(
        q: str = Query("", description="Search across name, steam, tribe, steamid"),
        tribe_id: int | None = Query(None),
        limit: int = Query(200, ge=1, le=1000),
    ) -> dict[str, t.Any]:
        filters: list[tuple[str, t.Any]] = []
        if tribe_id is not None:
            filters.append(("tribeid", tribe_id))
        rows = await asearch(cfg.db_path, "players", q, filters, limit)
        return {"players": rows, **await get_metadata(cfg)}

    @router.get("/data/search/tribes")
    async def search_tribes(
        q: str = Query("", description="Search across tribe name"),
        tribe_id: int | None = Query(None),
        limit: int = Query(200, ge=1, le=1000),
    ) -> dict[str, t.Any]:
        filters: list[tuple[str, t.Any]] = []
        if tribe_id is not None:
            filters.append(("tribeid", tribe_id))
        rows = await asearch(cfg.db_path, "tribes", q, filters, limit)
        return {"tribes": rows, **await get_metadata(cfg)}

    return router
