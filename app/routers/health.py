import asyncio
import os
import sys
import typing as t

import psutil
from fastapi import APIRouter

from app.config import AppConfig
from app.metadata import get_metadata


def _fmt_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(num) < 1024.0:
            return f"{num:.1f}{unit}"
        num /= 1024.0
    return f"{num:.1f}EB"


def _fmt_bar(perc: float, width: int = 18) -> str:
    fill = "▰"
    space = "▱"
    ratio = max(0.0, min(perc / 100.0, 1.0))
    filled = round(ratio * width)
    return f"{fill * filled}{space * (width - filled)} {round(perc, 1)}%"


def _collect_stats() -> dict[str, t.Any]:
    """Gather system stats synchronously; called via to_thread."""
    cpu_count = psutil.cpu_count(logical=True)
    cpu_perc = psutil.cpu_percent(interval=0.1, percpu=True)
    try:
        cpu_freq = psutil.cpu_freq(percpu=True) or []
    except (NotImplementedError, OSError):
        cpu_freq = []
    ram = psutil.virtual_memory()
    disk_root = "C:\\" if sys.platform.startswith("win") else "/"
    disk = psutil.disk_usage(disk_root)
    disk_load = 0.0
    try:
        proc_io = psutil.Process(os.getpid()).io_counters()
        proc_bytes = proc_io.read_bytes + proc_io.write_bytes
        sys_io = psutil.disk_io_counters()
        if sys_io and (sys_io.read_bytes + sys_io.write_bytes) > 0:
            disk_load = (proc_bytes / (sys_io.read_bytes + sys_io.write_bytes)) * 100
    except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
        pass
    net = psutil.net_io_counters()
    return {
        "cpu": {
            "cores": cpu_count,
            "percent": sum(cpu_perc) / len(cpu_perc) if cpu_perc else 0.0,
            "percents": cpu_perc if isinstance(cpu_perc, list) else None,
            "freq": [(f.current, f.max) for f in cpu_freq] if cpu_freq else [],
            "bars": [_fmt_bar(p) for p in cpu_perc] if cpu_perc else None,
        },
        "mem": {
            "total": ram.total,
            "available": ram.available,
            "percent": ram.percent,
            "used": _fmt_size(ram.used),
            "total_h": _fmt_size(ram.total),
            "bar": _fmt_bar(ram.percent),
        },
        "disk": {
            "total": disk.total,
            "free": disk.free,
            "percent": disk.percent,
            "used": _fmt_size(disk.used),
            "total_h": _fmt_size(disk.total),
            "bar": _fmt_bar(disk.percent),
            "load": disk_load,
            "loadbar": _fmt_bar(disk_load),
        },
        "net": {
            "bytes_sent": net.bytes_sent,
            "bytes_recv": net.bytes_recv,
            "sent": _fmt_size(net.bytes_sent),
            "received": _fmt_size(net.bytes_recv),
        },
    }


def build_router(cfg: AppConfig) -> APIRouter:
    router = APIRouter()  # no auth: health/stats reachable for monitoring

    @router.get("/")
    async def root() -> dict[str, t.Any]:
        return await get_metadata(cfg)

    @router.get("/stats")
    async def stats() -> dict[str, t.Any]:
        sys_stats = await asyncio.to_thread(_collect_stats)
        return {**await get_metadata(cfg), **sys_stats}

    return router
