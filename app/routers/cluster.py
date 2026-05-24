import asyncio
import json
import typing as t

from fastapi import APIRouter, Depends, HTTPException

from app.auth import RequireBearer
from app.config import AppConfig
from app.db import aconnect
from app.metadata import get_metadata


def build_router(cfg: AppConfig) -> APIRouter:
    auth = RequireBearer(cfg.api_key)
    router = APIRouter(dependencies=[Depends(auth)])

    @router.get("/data/cluster")
    async def list_cluster() -> dict[str, t.Any]:
        async with aconnect(cfg.db_path) as conn:
            async with conn.execute(
                "SELECT file_id, raw FROM cluster_inventory"
            ) as cur:
                rows = await cur.fetchall()
        # Decode off the event loop; cluster blobs can be large.
        cluster = await asyncio.to_thread(
            lambda: {r["file_id"]: json.loads(r["raw"]) for r in rows}
        )
        return {"cluster": cluster, **await get_metadata(cfg)}

    @router.get("/data/cluster/{file_id}")
    async def get_cluster(file_id: str) -> dict[str, t.Any]:
        async with aconnect(cfg.db_path) as conn:
            async with conn.execute(
                "SELECT raw FROM cluster_inventory WHERE file_id=?", (file_id,)
            ) as cur:
                row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="cluster file not found")
        return {"cluster": json.loads(row["raw"]), **await get_metadata(cfg)}

    return router
