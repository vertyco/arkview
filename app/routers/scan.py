import asyncio
import json
import typing as t

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth import RequireBearer
from app.config import AppConfig
from app.db import connect
from app.metadata import get_metadata


class ScanRequest(BaseModel):
    servernames: list[str]


def build_router(cfg: AppConfig) -> APIRouter:
    auth = RequireBearer(cfg.api_key)
    router = APIRouter(dependencies=[Depends(auth)])

    def scan_db(
        wanted: set[str],
    ) -> tuple[list[dict[str, t.Any]], list[dict[str, t.Any]]]:
        """Full `tamed` scan + tribe join. CPU-bound (`json.loads` per row),
        so this runs in a worker thread to keep the event loop responsive on
        big maps (100k+ tames). Sync `connect` streams the cursor — no
        `fetchall()` on `tamed` — keeping peak memory low.
        """
        foreign: list[dict[str, t.Any]] = []
        tribe_ids: set[int] = set()
        tribes: list[dict[str, t.Any]] = []
        with connect(cfg.db_path) as conn:
            for r in conn.execute("SELECT raw FROM tamed"):
                data = json.loads(r["raw"])
                srv = data.get("tamedServer")
                if srv and srv not in wanted:
                    foreign.append(data)
                    tid = data.get("tribeid")
                    if tid:
                        tribe_ids.add(int(tid))
            if tribe_ids:
                placeholders = ",".join("?" for _ in tribe_ids)
                tribe_rows = conn.execute(
                    f"SELECT raw FROM tribes WHERE tribeid IN ({placeholders})",
                    list(tribe_ids),
                ).fetchall()
                tribes = [json.loads(r["raw"]) for r in tribe_rows]
        return foreign, tribes

    @router.post("/foreigntamescan")
    async def scan(req: ScanRequest) -> dict[str, t.Any]:
        """Return tames whose `tamedServer` is NOT in the supplied list.

        Response shape: `{tamed: [...], tribes: [...], <meta>}` matching
        legacy v3 so AVClient's `get_foreign_tames` parses both lists.
        `tamedServer` lives only inside `raw` JSON; no index — full scan,
        offloaded to a thread (see `scan_db`).
        """
        wanted = set(req.servernames)
        foreign, tribes = await asyncio.to_thread(scan_db, wanted)
        return {"tamed": foreign, "tribes": tribes, **await get_metadata(cfg)}

    return router
