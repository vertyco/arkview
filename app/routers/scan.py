import json
import typing as t

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth import RequireBearer
from app.config import AppConfig
from app.db import aconnect
from app.metadata import get_metadata


class ScanRequest(BaseModel):
    servernames: list[str]


def build_router(cfg: AppConfig) -> APIRouter:
    auth = RequireBearer(cfg.api_key)
    router = APIRouter(dependencies=[Depends(auth)])

    @router.post("/foreigntamescan")
    async def scan(req: ScanRequest) -> dict[str, t.Any]:
        """Return tames whose `tamedServer` is NOT in the supplied list.

        Response shape: `{tamed: [...], tribes: [...], <meta>}` matching
        legacy v3 so AVClient's `get_foreign_tames` parses both lists.
        `tamedServer` lives only inside `raw` JSON; no index — full scan.
        """
        wanted = set(req.servernames)
        async with aconnect(cfg.db_path) as conn:
            async with conn.execute("SELECT raw FROM tamed") as cur:
                tame_rows = await cur.fetchall()
        foreign: list[dict[str, t.Any]] = []
        tribe_ids: set[int] = set()
        for r in tame_rows:
            data = json.loads(r["raw"])
            srv = data.get("tamedServer") or data.get("tamed_on_server")
            if srv and srv not in wanted:
                foreign.append(data)
                tid = data.get("tribeid") or data.get("tribe_id")
                if tid:
                    tribe_ids.add(int(tid))

        tribes: list[dict[str, t.Any]] = []
        if tribe_ids:
            placeholders = ",".join("?" for _ in tribe_ids)
            async with aconnect(cfg.db_path) as conn:
                async with conn.execute(
                    f"SELECT raw FROM tribes WHERE tribeid IN ({placeholders})",
                    list(tribe_ids),
                ) as cur:
                    tribe_rows = await cur.fetchall()
            tribes = [json.loads(r["raw"]) for r in tribe_rows]

        return {"tamed": foreign, "tribes": tribes, **await get_metadata(cfg)}

    return router
