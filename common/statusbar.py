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
    while True:
        current_map = cache.map_file
        current_path = Path(str(current_map)) if current_map else None
        title = f"title ArkViewer {VERSION}"
        if current_path:
            title += f" - {current_path.stem}"
        cmd = f"{title} {next(bar_cycle)}"
        if cache.syncing:
            cmd += " [Syncing...]"
        os.system(cmd)
        await asyncio.sleep(0.15)
