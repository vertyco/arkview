import asyncio
import os
from itertools import cycle

from .constants import BAR
from .models import cache  # noqa
from .version import VERSION


async def status_bar():
    await asyncio.sleep(5)
    global cache
    bar_cycle = cycle(BAR)
    while True:
        cmd = f"title ArkViewer {VERSION} {next(bar_cycle)}"
        if cache.syncing:
            cmd += " [Syncing...]"
        os.system(cmd)
        await asyncio.sleep(0.15)
