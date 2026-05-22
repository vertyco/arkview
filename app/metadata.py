"""Shared response-envelope metadata.

Every data-bearing endpoint splices these keys into its response so AVClient
can read `version`, `day`, `time`, `last_export`, etc. from the top level.
Matches the legacy v3 / v2 envelope shape the cog has been parsing.
"""

import time
import typing as t

from app.config import AppConfig
from app.constants import DATASET_NAMES, VERSION
from app.db import aconnect, ameta_get

START_TIME: t.Final[float] = time.time()


async def _cached_keys(db_path) -> list[str]:
    """Return dataset names whose table currently has rows."""
    keys: list[str] = []
    async with aconnect(db_path) as conn:
        for name in DATASET_NAMES:
            async with conn.execute(f"SELECT 1 FROM {name} LIMIT 1") as cur:
                row = await cur.fetchone()
            if row is not None:
                keys.append(name)
    return keys


async def get_metadata(cfg: AppConfig) -> dict[str, t.Any]:
    """Build the AVClient-compatible meta envelope.

    Returned keys: version, last_export, port, map_name, map_path,
    cluster_dir, cached_keys, day, time, uptime, stale.
    """
    assert isinstance(cfg, AppConfig)
    last_parse_raw = await ameta_get(cfg.db_path, "last_parse_at")
    last_export = int(last_parse_raw) if last_parse_raw else 0
    day_raw = await ameta_get(cfg.db_path, "day")
    time_text = (await ameta_get(cfg.db_path, "time")) or ""
    cached = await _cached_keys(cfg.db_path)
    return {
        "version": VERSION,
        "last_export": last_export,
        "port": cfg.port,
        "map_name": cfg.map_file.stem if cfg.map_file else "",
        "map_path": str(cfg.map_file) if cfg.map_file else "",
        "cluster_dir": str(cfg.cluster_dir) if cfg.cluster_dir else "",
        "cached_keys": cached,
        "day": int(day_raw or 0),
        "time": time_text,
        "uptime": round(time.time() - START_TIME, 1),
        "stale": last_export == 0,
    }
