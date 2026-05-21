import typing as t

import psutil
from fastapi import APIRouter

from app.config import AppConfig
from app.constants import VERSION
from app.db import ameta_get


def build_router(cfg: AppConfig) -> APIRouter:
    router = APIRouter()  # no auth: health always reachable

    @router.get("/")
    async def root() -> dict[str, t.Any]:
        return {
            "version": VERSION,
            "map": cfg.map_file.name if cfg.map_file else None,
            "day": int((await ameta_get(cfg.db_path, "day")) or 0),
            "time": (await ameta_get(cfg.db_path, "time")) or "",
            "last_parse_at": await ameta_get(cfg.db_path, "last_parse_at"),
        }

    @router.get("/stats")
    async def stats() -> dict[str, t.Any]:
        return {
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memory_percent": psutil.virtual_memory().percent,
            "disk_percent": psutil.disk_usage(".").percent,
        }

    return router
