import asyncio
import os

from .constants import BAR
from .models import cache  # noqa
from .version import VERSION


async def status_bar():
    await asyncio.sleep(5)
    global cache
    index = 0
    while True:
        cmd = f"title ArkViewer {VERSION} {BAR[index]}"
        if cache.syncing:
            cmd += " [Syncing...]"
        os.system(cmd)
        index += 1
        index %= len(BAR)
        await asyncio.sleep(0.15)
