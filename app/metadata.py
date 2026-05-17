"""Shared metadata helper used by all routers to build the standard response envelope."""

import time
import typing as t

from app.constants import VERSION
from app.state import state

start_time = time.time()


def get_metadata() -> dict[str, t.Any]:
    """Return the metadata dict included in every API response."""
    cached_keys: list[str] = []
    if state.data.tamed:
        cached_keys.append("tamed")
    if state.data.wild:
        cached_keys.append("wild")
    if state.data.players:
        cached_keys.append("players")
    if state.data.tribes:
        cached_keys.append("tribes")
    if state.data.structures:
        cached_keys.append("structures")
    if state.data.tribelogs:
        cached_keys.append("tribelogs")
    if state.data.mapstructures:
        cached_keys.append("mapstructures")
    if state.data.cloud_inventory:
        cached_keys.append("cloud_inventory")

    return {
        "version": VERSION,
        "last_export": int(state.last_export),
        "port": state.config.port,
        "map_name": state.config.map_file.stem if state.config.map_file else "",
        "map_path": str(state.config.map_file) if state.config.map_file else "",
        "cluster_dir": str(state.config.cluster_dir)
        if state.config.cluster_dir
        else "",
        "cached_keys": cached_keys,
        "day": state.day,
        "time": state.time,
        "uptime": round(time.time() - start_time, 1),
    }
