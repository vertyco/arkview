import asyncio
import os
from itertools import cycle
from pathlib import Path

import psutil

from .constants import BAR
from .models import cache  # noqa
from .version import VERSION


def _format_bytes(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0:
            return f"{num:.1f}{unit}"
        num /= 1024.0
    return f"{num:.1f}PB"


def total_rss(self_proc: psutil.Process) -> int:
    """Server RSS plus the parse child's whole process tree while it is alive.

    During a parse the heavy memory lives in the spawn child (``cache.parse_pid``),
    not the server, so the title bar would otherwise under-report total usage.
    Best-effort: a child can exit between reads, so every lookup is guarded.
    """
    rss = self_proc.memory_info().rss
    pid = cache.parse_pid
    if not pid:
        return rss
    try:
        child = psutil.Process(pid)
        rss += child.memory_info().rss
        for sub in child.children(recursive=True):
            try:
                rss += sub.memory_info().rss
            except psutil.Error:
                continue
    except psutil.Error:
        pass
    return rss


async def status_bar():
    await asyncio.sleep(5)
    global cache
    self_proc = psutil.Process()
    bar_cycle = cycle(BAR)
    while True:
        current_map = cache.map_file
        current_path = Path(str(current_map)) if current_map else None
        title = f"title ArkViewer {VERSION}"
        if current_path:
            title += f" - {current_path.stem}"
        # Brackets (not a "|" separator) so cmd's `title` builtin doesn't treat
        # it as a pipe.
        title += f" [RAM {_format_bytes(total_rss(self_proc))}]"
        cmd = f"{title} {next(bar_cycle)}"
        if cache.syncing:
            cmd += " [Syncing...]"
        os.system(cmd)
        await asyncio.sleep(0.15)
