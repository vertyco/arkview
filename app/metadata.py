"""Shared response-envelope metadata.

Every data-bearing endpoint splices these keys into its response so AVClient
can read `version`, `day`, `time`, `last_export`, etc. from the top level.
Matches the legacy v3 / v2 envelope shape the cog has been parsing.
"""

import time
import typing as t
from pathlib import Path

from app.config import AppConfig
from app.constants import DATASET_NAMES, VERSION
from app.db import aconnect

START_TIME: t.Final[float] = time.time()


async def _load_envelope(db_path: Path) -> tuple[dict[str, str], list[str]]:
    """One connection, one trip: read meta keys + non-empty table list."""
    assert isinstance(db_path, Path)
    meta: dict[str, str] = {}
    keys: list[str] = []
    async with aconnect(db_path) as conn:
        async with conn.execute(
            "SELECT key, value FROM meta WHERE key IN ('last_parse_at','day','time')"
        ) as cur:
            async for row in cur:
                meta[row["key"]] = row["value"]
        for name in DATASET_NAMES:
            async with conn.execute(f"SELECT 1 FROM {name} LIMIT 1") as cur:
                row = await cur.fetchone()
            if row is not None:
                keys.append(name)
    return meta, keys


async def get_metadata(cfg: AppConfig) -> dict[str, t.Any]:
    """Build the AVClient-compatible meta envelope.

    Returned keys: version, last_export, port, map_name, map_path,
    cluster_dir, cached_keys, day, time, uptime, stale.
    """
    assert isinstance(cfg, AppConfig)
    meta, cached = await _load_envelope(cfg.db_path)
    last_export = int(meta.get("last_parse_at") or 0)
    return {
        "version": VERSION,
        "last_export": last_export,
        "port": cfg.port,
        "map_name": cfg.map_file.stem if cfg.map_file else "",
        "map_path": str(cfg.map_file) if cfg.map_file else "",
        "cluster_dir": str(cfg.cluster_dir) if cfg.cluster_dir else "",
        "cached_keys": cached,
        "day": int(meta.get("day") or 0),
        "time": meta.get("time", ""),
        "uptime": round(time.time() - START_TIME, 1),
        "stale": last_export == 0,
    }
