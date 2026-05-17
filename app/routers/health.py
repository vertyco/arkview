import asyncio
import os
import sys
import typing as t

import psutil
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.auth import verify_api_key
from app.metadata import get_metadata
from app.state import state

router = APIRouter(tags=["health"])


@router.get("/")
async def get_info(_: None = Depends(verify_api_key)) -> JSONResponse:
    """Basic server info and metadata."""
    return JSONResponse(content=get_metadata())


def get_size(num: float) -> str:
    """Format a byte count to a human-readable string (e.g. '4.2GB')."""
    for unit in ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB"):
        if abs(num) < 1024.0:
            return f"{num:.1f}{unit}"
        num /= 1024.0
    return f"{num:.1f}YB"


def get_bar(perc: float, width: int = 18) -> str:
    """Build an ASCII progress bar string."""
    fill = "▰"
    space = "▱"
    ratio = perc / 100
    bar = fill * round(ratio * width) + space * round(width - (ratio * width))
    return f"{bar} {round(100 * ratio, 1)}%"


def collect_stats() -> dict[str, t.Any]:
    """Gather system stats synchronously (blocking psutil calls)."""
    cpu_count = psutil.cpu_count(logical=True)
    cpu_perc = psutil.cpu_percent(interval=0.1, percpu=True)
    cpu_freq = psutil.cpu_freq(percpu=True) or []

    ram = psutil.virtual_memory()

    disk_root = "C:\\" if sys.platform.startswith("win") else "/"
    disk = psutil.disk_usage(disk_root)

    disk_load = 0.0
    try:
        proc_io = psutil.Process(os.getpid()).io_counters()
        proc_bytes = proc_io.read_bytes + proc_io.write_bytes
        sys_io = psutil.disk_io_counters()
        if sys_io:
            sys_bytes = sys_io.read_bytes + sys_io.write_bytes
            if sys_bytes > 0:
                disk_load = (proc_bytes / sys_bytes) * 100
    except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
        pass

    net = psutil.net_io_counters()

    return {
        "cpu": {
            "cores": cpu_count,
            "percent": sum(cpu_perc) / len(cpu_perc) if cpu_perc else 0.0,
            "percents": cpu_perc if isinstance(cpu_perc, list) else None,
            "freq": [(f.current, f.max) for f in cpu_freq] if cpu_freq else [],
            "bars": [get_bar(p) for p in cpu_perc] if cpu_perc else None,
        },
        "mem": {
            "total": ram.total,
            "available": ram.available,
            "percent": ram.percent,
            "used": get_size(ram.used),
            "total_h": get_size(ram.total),
            "bar": get_bar(ram.percent),
        },
        "disk": {
            "total": disk.total,
            "free": disk.free,
            "percent": disk.percent,
            "used": get_size(disk.used),
            "total_h": get_size(disk.total),
            "bar": get_bar(disk.percent),
            "load": disk_load,
            "loadbar": get_bar(disk_load),
        },
        "net": {
            "bytes_sent": net.bytes_sent,
            "bytes_recv": net.bytes_recv,
            "sent": get_size(net.bytes_sent),
            "received": get_size(net.bytes_recv),
        },
    }


@router.get("/stats")
async def get_stats(_: None = Depends(verify_api_key)) -> JSONResponse:
    """System resource stats."""
    stats = await asyncio.to_thread(collect_stats)
    return JSONResponse(content={**get_metadata(), **stats})


def _compute_summary() -> dict[str, int]:
    data = state.data
    tames_cryo = sum(1 for t in data.tamed if t.is_cryo)
    players_alive = sum(1 for p in data.players if (p.lat or p.lon))
    active_tribes = {t.tribe_id for t in data.tamed if t.tribe_id} | {
        s.tribe_id for s in data.structures if s.tribe_id
    }
    # Tamed/Wild pydantic models don't currently expose `inventory`; only players,
    # structures, and mapstructures do. Cog mapstats counts these only for display -
    # the missing tame-inventory count is acceptable for now.
    items = (
        sum(len(p.inventory) for p in data.players)
        + sum(len(s.inventory) for s in data.structures)
        + sum(len(m.inventory) for m in data.mapstructures)
    )
    return {
        "tamed": len(data.tamed),
        "tames_cryo": tames_cryo,
        "wild": len(data.wild),
        "players": len(data.players),
        "players_alive": players_alive,
        "tribes": len(data.tribes),
        "active_tribes": len(active_tribes),
        "structures": len(data.structures),
        "tribelogs": len(data.tribelogs),
        "mapstructures": len(data.mapstructures),
        "cloud_inventory": len(data.cloud_inventory),
        "items": items,
    }


@router.get("/stats/summary")
async def get_summary(_: None = Depends(verify_api_key)) -> JSONResponse:
    """Metadata envelope + scalar counts (incl. cryo'd tames, alive players, items, active tribes).

    Designed for the cog's `mapstats` command - pre-aggregates everything that previously
    required pulling the full ~140 MB `/data/all` payload.
    """
    counts = await asyncio.to_thread(_compute_summary)
    return JSONResponse(content={"counts": counts, **get_metadata()})
