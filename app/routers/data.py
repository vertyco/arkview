import asyncio
import typing as t
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.auth import RequireBearer
from app.config import AppConfig
from app.constants import DATASET_NAMES
from app.db import aselect_all, aselect_where
from app.metadata import get_metadata


async def _select_all_datasets(db_path: Path) -> dict[str, t.Any]:
    """Load every dataset serially.

    Parallel reads via `asyncio.gather` would multiply peak RAM by N because
    each `aselect_all` materializes its table's JSON into a Python list, and
    the bottleneck is JSON decode + dict growth, not I/O. Serial keeps peak
    at the largest single table.
    """
    return {name: await aselect_all(db_path, name) for name in DATASET_NAMES}


class DatasRequest(BaseModel):
    dtypes: list[str]


def build_router(cfg: AppConfig) -> APIRouter:
    auth = RequireBearer(cfg.api_key)
    router = APIRouter(dependencies=[Depends(auth)])

    @router.post("/datas")
    async def post_datas(req: DatasRequest) -> dict[str, t.Any]:
        if not req.dtypes:
            raise HTTPException(status_code=400, detail="dtypes required")
        # Validate first so a bad request doesn't trigger an "all" fetch.
        wanted_named = [d for d in req.dtypes if d != "all"]
        for d in wanted_named:
            if d not in DATASET_NAMES:
                raise HTTPException(status_code=404, detail=f"unknown dtype: {d}")
        out: dict[str, t.Any] = {}
        if "all" in req.dtypes:
            out.update(await _select_all_datasets(cfg.db_path))
        for d in wanted_named:
            if d not in out:
                out[d] = await aselect_all(cfg.db_path, d)
        out.update(await get_metadata(cfg))
        return out

    @router.get("/data/filter/tamed")
    async def filter_tamed(
        tribe_id: int | None = Query(None),
        class_name: str | None = Query(None),
        is_cryo: bool | None = Query(None),
    ) -> dict[str, t.Any]:
        clauses: list[tuple[str, t.Any]] = []
        if tribe_id is not None:
            clauses.append(("tribeid", tribe_id))
        if class_name is not None:
            clauses.append(("creature", class_name))
        if is_cryo is not None:
            clauses.append(("cryo", 1 if is_cryo else 0))
        rows = await aselect_where(cfg.db_path, "tamed", clauses)
        return {"tamed": rows, **await get_metadata(cfg)}

    @router.get("/data/filter/wild")
    async def filter_wild(
        class_name: str | None = Query(None),
        tameable: bool | None = Query(None),
    ) -> dict[str, t.Any]:
        clauses: list[tuple[str, t.Any]] = []
        if class_name is not None:
            clauses.append(("creature", class_name))
        if tameable is not None:
            clauses.append(("tameable", 1 if tameable else 0))
        rows = await aselect_where(cfg.db_path, "wild", clauses)
        return {"wild": rows, **await get_metadata(cfg)}

    @router.get("/data/filter/players/{player_id}")
    async def get_player(player_id: int) -> dict[str, t.Any]:
        rows = await aselect_where(cfg.db_path, "players", [("playerid", player_id)])
        if not rows:
            raise HTTPException(status_code=404, detail="player not found")
        return {"players": [rows[0]], **await get_metadata(cfg)}

    @router.get("/data/filter/players")
    async def filter_players(
        tribe_id: int | None = Query(None),
        steam_id: str | None = Query(None),
    ) -> dict[str, t.Any]:
        clauses: list[tuple[str, t.Any]] = []
        if tribe_id is not None:
            clauses.append(("tribeid", tribe_id))
        if steam_id is not None:
            clauses.append(("steamid", steam_id))
        rows = await aselect_where(cfg.db_path, "players", clauses)
        return {"players": rows, **await get_metadata(cfg)}

    @router.get("/data/filter/tribes/{tribe_id}")
    async def get_tribe(tribe_id: int) -> dict[str, t.Any]:
        rows = await aselect_where(cfg.db_path, "tribes", [("tribeid", tribe_id)])
        if not rows:
            raise HTTPException(status_code=404, detail="tribe not found")
        return {"tribes": [rows[0]], **await get_metadata(cfg)}

    @router.get("/data/filter/tribes")
    async def filter_tribes(tribe_id: int | None = Query(None)) -> dict[str, t.Any]:
        clauses: list[tuple[str, t.Any]] = []
        if tribe_id is not None:
            clauses.append(("tribeid", tribe_id))
        rows = await aselect_where(cfg.db_path, "tribes", clauses)
        return {"tribes": rows, **await get_metadata(cfg)}

    @router.get("/data/filter/structures")
    async def filter_structures(
        tribe_id: int | None = Query(None),
        class_name: str | None = Query(None),
    ) -> dict[str, t.Any]:
        clauses: list[tuple[str, t.Any]] = []
        if tribe_id is not None:
            clauses.append(("tribeid", tribe_id))
        if class_name is not None:
            clauses.append(("struct", class_name))
        rows = await aselect_where(cfg.db_path, "structures", clauses)
        return {"structures": rows, **await get_metadata(cfg)}

    @router.get("/data/filter/tribelogs")
    async def filter_tribelogs(tribe_id: int | None = Query(None)) -> dict[str, t.Any]:
        clauses: list[tuple[str, t.Any]] = []
        if tribe_id is not None:
            clauses.append(("tribeid", tribe_id))
        rows = await aselect_where(cfg.db_path, "tribelogs", clauses)
        return {"tribelogs": rows, **await get_metadata(cfg)}

    @router.get("/data/filter/mapstructures")
    async def filter_mapstructures(type: str | None = Query(None)) -> dict[str, t.Any]:
        clauses: list[tuple[str, t.Any]] = []
        if type is not None:
            clauses.append(("struct", type))
        rows = await aselect_where(cfg.db_path, "mapstructures", clauses)
        return {"mapstructures": rows, **await get_metadata(cfg)}

    @router.get("/data/{dtype}")
    async def get_dataset(dtype: str) -> dict[str, t.Any]:
        if dtype == "all":
            out = await _select_all_datasets(cfg.db_path)
            out.update(await get_metadata(cfg))
            return out
        if dtype not in DATASET_NAMES:
            raise HTTPException(status_code=404, detail=f"unknown dtype: {dtype}")
        rows, meta = await asyncio.gather(
            aselect_all(cfg.db_path, dtype), get_metadata(cfg)
        )
        return {dtype: rows, **meta}

    return router
