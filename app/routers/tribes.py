import json
import typing as t

from fastapi import APIRouter, Depends, HTTPException, Path

from app.auth import RequireBearer
from app.config import AppConfig
from app.db import aconnect
from app.metadata import get_metadata


def build_router(cfg: AppConfig) -> APIRouter:
    auth = RequireBearer(cfg.api_key)
    router = APIRouter(dependencies=[Depends(auth)])

    @router.get("/tribetames/{gameid}")
    async def tribetames(gameid: str) -> dict[str, t.Any]:
        """Resolve player by steam/platform ID, return tribe + its tames.

        Response shape matches legacy v3: `{tamed: [...], tribes: [...], <meta>}`.
        """
        async with aconnect(cfg.db_path) as conn:
            async with conn.execute(
                "SELECT tribeid FROM players WHERE steamid=?", (gameid,)
            ) as cur:
                player = await cur.fetchone()
            if player is None:
                raise HTTPException(status_code=404, detail="player not found")
            tribe_id = player["tribeid"]
            # tribeid 0 == tribeless; querying `WHERE tribeid=0` would return
            # every orphan/default-tribe tame on the map. Return an empty
            # roster instead of leaking unrelated tames.
            if not tribe_id:
                return {"tamed": [], "tribes": [], **await get_metadata(cfg)}
            async with conn.execute(
                "SELECT raw FROM tamed WHERE tribeid=?", (tribe_id,)
            ) as cur:
                tame_rows = await cur.fetchall()
            async with conn.execute(
                "SELECT raw FROM tribes WHERE tribeid=?", (tribe_id,)
            ) as cur:
                tribe_rows = await cur.fetchall()
        return {
            "tamed": [json.loads(r["raw"]) for r in tame_rows],
            "tribes": [json.loads(r["raw"]) for r in tribe_rows],
            **await get_metadata(cfg),
        }

    @router.get("/overlimit/{limit}")
    async def overlimit(limit: int = Path(ge=0)) -> dict[str, t.Any]:
        """Return tames grouped by player steamid for tribes exceeding limit.

        Response shape: `{overlimit: {steamid: [tame, ...]}, <meta>}`.
        Cryoed and uploaded tames are excluded so the count reflects
        on-map roster pressure only. Both filters are indexed columns now,
        so the bucket query and the fetch run entirely in SQL — no JSON
        scan of uploaded flag. `limit` is validated `>= 0` by FastAPI (422
        on a negative path value rather than a 500).
        """
        async with aconnect(cfg.db_path) as conn:
            async with conn.execute(
                "SELECT tribeid, COUNT(*) AS c "
                "FROM tamed WHERE cryo=0 AND uploaded=0 "
                "GROUP BY tribeid HAVING c > ?",
                (limit,),
            ) as cur:
                bucket_rows = await cur.fetchall()
            over_tribes = {r["tribeid"] for r in bucket_rows}
            if not over_tribes:
                return {"overlimit": {}, **await get_metadata(cfg)}

            placeholders = ",".join("?" for _ in over_tribes)
            tribe_ids = list(over_tribes)

            async with conn.execute(
                f"SELECT tribeid, raw FROM tamed "
                f"WHERE cryo=0 AND uploaded=0 AND tribeid IN ({placeholders})",
                tribe_ids,
            ) as cur:
                tame_rows = await cur.fetchall()

            async with conn.execute(
                f"SELECT tribeid, steamid FROM players "
                f"WHERE tribeid IN ({placeholders}) AND steamid <> ''",
                tribe_ids,
            ) as cur:
                player_rows = await cur.fetchall()

        by_tribe: dict[int, list[dict[str, t.Any]]] = {}
        for r in tame_rows:
            by_tribe.setdefault(int(r["tribeid"]), []).append(json.loads(r["raw"]))

        overlimit_map: dict[str, list[dict[str, t.Any]]] = {}
        for r in player_rows:
            tid = int(r["tribeid"])
            sid = r["steamid"]
            if tid in by_tribe and sid:
                overlimit_map[sid] = by_tribe[tid]

        return {"overlimit": overlimit_map, **await get_metadata(cfg)}

    return router
