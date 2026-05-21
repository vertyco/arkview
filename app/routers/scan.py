import json
import typing as t

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth import RequireBearer
from app.config import AppConfig
from app.db import aconnect


class ScanRequest(BaseModel):
    servernames: list[str]


def build_router(cfg: AppConfig) -> APIRouter:
    auth = RequireBearer(cfg.api_key)
    router = APIRouter(dependencies=[Depends(auth)])

    @router.post("/foreigntamescan")
    async def scan(req: ScanRequest) -> dict[str, t.Any]:
        # tamedServer lives only inside `raw` JSON; no index for it.
        # Low query frequency makes full-scan acceptable.
        wanted = set(req.servernames)
        async with aconnect(cfg.db_path) as conn:
            async with conn.execute("SELECT raw FROM tamed") as cur:
                rows = await cur.fetchall()
        out: list[dict[str, t.Any]] = []
        for r in rows:
            data = json.loads(r["raw"])
            srv = data.get("tamedServer")
            if srv and srv not in wanted:
                out.append(data)
        return {"data": out}

    return router
