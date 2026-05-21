import json
import typing as t

from fastapi import APIRouter, Depends, HTTPException

from app.auth import RequireBearer
from app.config import AppConfig
from app.db import aconnect, ameta_get


def build_router(cfg: AppConfig) -> APIRouter:
    auth = RequireBearer(cfg.api_key)
    router = APIRouter(dependencies=[Depends(auth)])

    @router.get("/tribetames/{gameid}")
    async def tribetames(gameid: str) -> dict[str, t.Any]:
        async with aconnect(cfg.db_path) as conn:
            async with conn.execute(
                "SELECT tribeid FROM players WHERE steamid=?", (gameid,)
            ) as cur:
                player = await cur.fetchone()
            if player is None:
                raise HTTPException(status_code=404, detail="player not found")
            async with conn.execute(
                "SELECT raw FROM tamed WHERE tribeid=?", (player["tribeid"],)
            ) as cur:
                rows = await cur.fetchall()
        return {
            "day": int((await ameta_get(cfg.db_path, "day")) or 0),
            "time": (await ameta_get(cfg.db_path, "time")) or "",
            "data": [json.loads(r["raw"]) for r in rows],
        }

    @router.get("/overlimit/{limit}")
    async def overlimit(limit: int) -> dict[str, t.Any]:
        assert limit >= 0
        async with aconnect(cfg.db_path) as conn:
            async with conn.execute(
                "SELECT tribeid, COUNT(*) AS c FROM tamed GROUP BY tribeid HAVING c > ?",
                (limit,),
            ) as cur:
                rows = await cur.fetchall()
        return {"data": [{"tribeid": r["tribeid"], "tame_count": r["c"]} for r in rows]}

    return router
