import asyncio
import os
from itertools import cycle
from pathlib import Path

from .constants import BAR
from .models import cache  # noqa
from .version import VERSION


async def status_bar():
    await asyncio.sleep(5)
    global cache
    bar_cycle = cycle(BAR)
    path = Path(str(cache.map_file))
    while True:
        cmd = f"title ArkViewer {VERSION} - {path.stem} {next(bar_cycle)}"
        if cache.syncing:
            cmd += " [Syncing...]"
        os.system(cmd)
        await asyncio.sleep(0.15)
