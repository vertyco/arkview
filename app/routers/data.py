"""
Data routes - exposes parsed ARK game data from the in-memory state cache.

All endpoints return data from the last completed reparse cycle with metadata.

Spec routes:
    GET  /data/{datatype}         - single data type (or "all")
    POST /datas                   - multiple data types at once
    GET  /tribetames/{gameid}     - tribe tames by player game ID
    GET  /overlimit/{limit}       - tribes exceeding tame count limit
    POST /foreigntamescan         - tames from a different server

Extra filtered routes (superset of spec):
    GET /data/filter/tamed         - with tribe_id, class_name, is_cryo query params
    GET /data/filter/wild          - with class_name, tameable query params
    GET /data/filter/players       - with tribe_id, steam_id query params
    GET /data/filter/players/{player_id}
    GET /data/filter/tribes        - with tribe_id query param
    GET /data/filter/tribes/{tribe_id}
    GET /data/filter/structures    - with tribe_id, class_name query params
    GET /data/filter/tribelogs     - with tribe_id, day query params
    GET /data/filter/mapstructures - with type query param
    GET /data/filter/cloud_inventory
    GET /data/filter/cloud_inventory/{file_id}
"""

import math
import typing as t

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.auth import verify_api_key
from app.constants import IGNORED_DINO_PATHS, VALID_DATATYPES
from app.metadata import get_metadata
from app.models import Tamed
from app.state import state

router = APIRouter(tags=["data"], dependencies=[Depends(verify_api_key)])

DATA_KEYS = [dt for dt in VALID_DATATYPES if dt != "all"]


def _sanitize(obj: t.Any) -> t.Any:
    """Replace non-finite floats (inf/nan) with 0.0 so json.dumps never fails.

    ARK saves can produce infinite durability values for items that don't
    degrade (e.g. ammo). Python's json module raises ValueError on inf/nan,
    so every serialization path goes through this sanitizer.
    """
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else 0.0
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


def serialize(items: list[t.Any]) -> list[dict[str, t.Any]]:
    """Dump a list of Pydantic models to plain dicts for JSONResponse."""
    return [_sanitize(item.model_dump()) for item in items]


def get_data_dict(datatype: str) -> dict[str, t.Any]:
    """Return {datatype: [...serialized...]} for a single valid key."""
    data = getattr(state.data, datatype, None)
    if data is None:
        return {datatype: []}
    if isinstance(data, dict):
        return {datatype: [_sanitize(v.model_dump()) for v in data.values()]}
    return {datatype: serialize(data)}


@router.get("/data/{datatype}")
async def get_data(datatype: str) -> JSONResponse:
    """Return a single data type (or all) with metadata."""
    if datatype not in VALID_DATATYPES:
        raise HTTPException(status_code=422, detail=f"Invalid datatype: {datatype}")

    meta = get_metadata()

    if datatype == "all":
        payload: dict[str, t.Any] = {}
        for key in DATA_KEYS:
            payload.update(get_data_dict(key))
        payload.update(meta)
        return JSONResponse(content=payload)

    return JSONResponse(content={**get_data_dict(datatype), **meta})


class DatasRequest(BaseModel):
    dtypes: list[str]


@router.post("/datas")
async def get_datas(body: DatasRequest) -> JSONResponse:
    """Return multiple data types in one response with metadata."""
    invalid = [dt for dt in body.dtypes if dt not in VALID_DATATYPES]
    if invalid:
        raise HTTPException(status_code=422, detail=f"Invalid datatypes: {invalid}")

    meta = get_metadata()
    payload: dict[str, t.Any] = {}

    for dt in body.dtypes:
        if dt == "all":
            for key in DATA_KEYS:
                payload.update(get_data_dict(key))
        else:
            payload.update(get_data_dict(dt))

    payload.update(meta)
    return JSONResponse(content=payload)


@router.get("/tribetames/{gameid}")
async def get_tribetames(gameid: str) -> JSONResponse:
    """Find player by steam/platform ID, return their tribe's tames + tribe info."""
    player = None
    for p in state.data.players:
        if p.steam_id == gameid:
            player = p
            break

    if player is None:
        raise HTTPException(
            status_code=404, detail=f"Player with game ID {gameid} not found"
        )

    tribe_id = player.tribe_id
    tamed = [tame for tame in state.data.tamed if tame.tribe_id == tribe_id]
    tribes = [tribe for tribe in state.data.tribes if tribe.tribe_id == tribe_id]

    meta = get_metadata()
    return JSONResponse(
        content={
            "tamed": serialize(tamed),
            "tribes": serialize(tribes),
            **meta,
        }
    )


@router.get("/overlimit/{limit}")
async def get_overlimit(limit: int) -> JSONResponse:
    """
    Return tamed creatures grouped by steam_id for tribes exceeding the tame limit.

    - Deduplicates tames by {id}-{dino_id}
    - Excludes cryoed and uploaded creatures
    - Groups remaining tames by tribe
    - Returns {steam_id: [tames]} for every player in tribes over the limit
    """
    seen: set[str] = set()
    unique: list[Tamed] = []
    for tame in state.data.tamed:
        key = f"{tame.id}-{tame.dino_id}"
        if key not in seen:
            seen.add(key)
            unique.append(tame)

    active = [
        tame for tame in unique if not tame.is_cryo and not tame.uploaded_from_server
    ]

    tames_by_tribe: dict[int, list[Tamed]] = {}
    for tame in active:
        tames_by_tribe.setdefault(tame.tribe_id, []).append(tame)

    overlimit: dict[str, list[dict[str, t.Any]]] = {}
    for tid, tribe_tames in tames_by_tribe.items():
        if len(tribe_tames) <= limit:
            continue
        serialized = serialize(tribe_tames)
        for player in state.data.players:
            if player.tribe_id == tid:
                overlimit[player.steam_id] = serialized

    meta = get_metadata()
    return JSONResponse(content={"overlimit": overlimit, **meta})


class ForeignTameScanRequest(BaseModel):
    servernames: list[str]


@router.post("/foreigntamescan")
async def foreign_tame_scan(body: ForeignTameScanRequest) -> JSONResponse:
    """Return tamed creatures whose tamed_on_server is not in the given list."""
    server_set = set(body.servernames)
    foreign = [
        tame
        for tame in state.data.tamed
        if tame.tamed_on_server
        and tame.tamed_on_server not in server_set
        and tame.class_name not in IGNORED_DINO_PATHS
    ]

    tribe_ids = {tame.tribe_id for tame in foreign}
    tribes = [tribe for tribe in state.data.tribes if tribe.tribe_id in tribe_ids]

    meta = get_metadata()
    return JSONResponse(
        content={
            "tamed": serialize(foreign),
            "tribes": serialize(tribes),
            **meta,
        }
    )


filter_router = APIRouter(
    prefix="/data/filter",
    tags=["data-filter"],
    dependencies=[Depends(verify_api_key)],
)


@filter_router.get("/tamed")
async def filter_tamed(
    tribe_id: t.Annotated[int | None, Query(description="Filter by tribe ID")] = None,
    class_name: t.Annotated[
        str | None,
        Query(description="Case-insensitive substring match on creature class"),
    ] = None,
    is_cryo: t.Annotated[
        bool | None, Query(description="If set, filter by cryo state")
    ] = None,
) -> JSONResponse:
    results = state.data.tamed
    if tribe_id is not None:
        results = [tame for tame in results if tame.tribe_id == tribe_id]
    if class_name is not None:
        name_lower = class_name.lower()
        results = [tame for tame in results if name_lower in tame.class_name.lower()]
    if is_cryo is not None:
        results = [tame for tame in results if tame.is_cryo == is_cryo]
    return JSONResponse(content={"tamed": serialize(results), **get_metadata()})


@filter_router.get("/wild")
async def filter_wild(
    class_name: t.Annotated[
        str | None,
        Query(description="Case-insensitive substring match on creature class"),
    ] = None,
    tameable: t.Annotated[
        bool | None, Query(description="If set, filter by tameable state")
    ] = None,
) -> JSONResponse:
    results = state.data.wild
    if class_name is not None:
        name_lower = class_name.lower()
        results = [w for w in results if name_lower in w.class_name.lower()]
    if tameable is not None:
        results = [w for w in results if w.tameable == tameable]
    return JSONResponse(content={"wild": serialize(results), **get_metadata()})


@filter_router.get("/players")
async def filter_players(
    tribe_id: t.Annotated[int | None, Query(description="Filter by tribe ID")] = None,
    steam_id: t.Annotated[
        str | None, Query(description="Exact match on steam ID")
    ] = None,
) -> JSONResponse:
    results = state.data.players
    if tribe_id is not None:
        results = [p for p in results if p.tribe_id == tribe_id]
    if steam_id is not None:
        results = [p for p in results if p.steam_id == steam_id]
    return JSONResponse(content={"players": serialize(results), **get_metadata()})


@filter_router.get("/players/{player_id}")
async def filter_player(player_id: int) -> JSONResponse:
    for player in state.data.players:
        if player.player_id == player_id:
            return JSONResponse(
                content={"players": serialize([player]), **get_metadata()}
            )
    raise HTTPException(status_code=404, detail=f"Player {player_id} not found")


@filter_router.get("/tribes")
async def filter_tribes(
    tribe_id: t.Annotated[
        int | None, Query(description="Filter to a single tribe by ID")
    ] = None,
) -> JSONResponse:
    results = state.data.tribes
    if tribe_id is not None:
        results = [tribe for tribe in results if tribe.tribe_id == tribe_id]
    return JSONResponse(content={"tribes": serialize(results), **get_metadata()})


@filter_router.get("/tribes/{tribe_id}")
async def filter_tribe(tribe_id: int) -> JSONResponse:
    for tribe in state.data.tribes:
        if tribe.tribe_id == tribe_id:
            return JSONResponse(
                content={"tribes": serialize([tribe]), **get_metadata()}
            )
    raise HTTPException(status_code=404, detail=f"Tribe {tribe_id} not found")


@filter_router.get("/structures")
async def filter_structures(
    tribe_id: t.Annotated[int | None, Query(description="Filter by tribe ID")] = None,
    class_name: t.Annotated[
        str | None, Query(description="Case-insensitive substring match")
    ] = None,
) -> JSONResponse:
    results = state.data.structures
    if tribe_id is not None:
        results = [s for s in results if s.tribe_id == tribe_id]
    if class_name is not None:
        cls_lower = class_name.lower()
        results = [s for s in results if cls_lower in s.class_name.lower()]
    return JSONResponse(content={"structures": serialize(results), **get_metadata()})


@filter_router.get("/tribelogs")
async def filter_tribelogs(
    tribe_id: t.Annotated[int | None, Query(description="Filter by tribe ID")] = None,
    day: t.Annotated[
        int | None,
        Query(description="Filter inner log entries to a specific in-game day"),
    ] = None,
) -> JSONResponse:
    results = state.data.tribelogs
    if tribe_id is not None:
        results = [tlog for tlog in results if tlog.tribe_id == tribe_id]
    if day is not None:
        narrowed = []
        for tlog in results:
            matching = [entry for entry in tlog.logs if entry.day == day]
            if matching:
                narrowed.append(tlog.model_copy(update={"logs": matching}))
        results = narrowed
    return JSONResponse(content={"tribelogs": serialize(results), **get_metadata()})


@filter_router.get("/mapstructures")
async def filter_mapstructures(
    struct_type: t.Annotated[
        str | None,
        Query(alias="type", description="Filter by type key e.g. 'oil_vein'"),
    ] = None,
) -> JSONResponse:
    results = state.data.mapstructures
    if struct_type is not None:
        results = [m for m in results if m.type == struct_type]
    return JSONResponse(content={"mapstructures": serialize(results), **get_metadata()})


@filter_router.get("/cloud_inventory")
async def filter_cloud_inventory(
    file_id: t.Annotated[
        str | None, Query(description="Filter by player file ID (filename stem)")
    ] = None,
) -> JSONResponse:
    """Return cloud inventories, optionally filtered by player file ID."""
    inventories = state.data.cloud_inventory
    if file_id is not None:
        inv = inventories.get(file_id)
        items = [_sanitize(inv.model_dump())] if inv else []
    else:
        items = [_sanitize(inv.model_dump()) for inv in inventories.values()]
    return JSONResponse(content={"cloud_inventory": items, **get_metadata()})


@filter_router.get("/cloud_inventory/{file_id}")
async def filter_cloud_inventory_by_id(file_id: str) -> JSONResponse:
    """Return a single player's cloud inventory by file ID."""
    inv = state.data.cloud_inventory.get(file_id)
    if inv is None:
        raise HTTPException(
            status_code=404, detail=f"Cloud inventory {file_id} not found"
        )
    return JSONResponse(
        content={"cloud_inventory": [_sanitize(inv.model_dump())], **get_metadata()}
    )
