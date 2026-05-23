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
        foreign: list[dict[str, t.Any]] = []
        tribe_ids: set[int] = set()
        tribes: list[dict[str, t.Any]] = []
        async with aconnect(cfg.db_path) as conn:
            # Stream rows; tamed can be 100k+ entries on busy servers and
            # `fetchall()` would materialize every raw-JSON blob at once.
            async with conn.execute("SELECT raw FROM tamed") as cur:
                async for r in cur:
                    data = json.loads(r["raw"])
                    srv = data.get("tamedServer")
                    if srv and srv not in wanted:
                        foreign.append(data)
                        tid = data.get("tribeid")
                        if tid:
                            tribe_ids.add(int(tid))
            if tribe_ids:
                placeholders = ",".join("?" for _ in tribe_ids)
                async with conn.execute(
                    f"SELECT raw FROM tribes WHERE tribeid IN ({placeholders})",
                    list(tribe_ids),
                ) as cur:
                    tribe_rows = await cur.fetchall()
                tribes = [json.loads(r["raw"]) for r in tribe_rows]

        return {"tamed": foreign, "tribes": tribes, **await get_metadata(cfg)}

    return router
