import asyncio
import logging
import os
import sys

from common.logger import init_logging
from common.tasks import ArkViewer
from common.version import VERSION

init_logging()


log = logging.getLogger("arkview.main")


class Manager:
    """Compile with 'pyinstaller.exe --clean app.spec'"""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop: asyncio.AbstractEventLoop = loop
        self.handler = ArkViewer()

    async def start(self) -> None:
        log.info(f"Version: {VERSION}")
        success = await self.handler.initialize()
        if not success:
            log.warning("Something went wrong during startup!")
            self.loop.stop()

    @classmethod
    def run(cls) -> None:
        log.info(f"Starting ArkViewer with PID {os.getpid()}")

        loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(loop)
        arkview = cls(loop)

        try:
            loop.create_task(arkview.start())
            loop.run_forever()
        except KeyboardInterrupt:
            print("CTRL+C received, shutting down...")
        except Exception as e:
            log.critical("Fatal error!", exc_info=e)
        finally:
            log.info("Shutting down...")
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(asyncio.sleep(1))
            asyncio.set_event_loop(None)
            loop.stop()
            loop.close()

            log.info("Goodbye.")
            sys.exit()


if __name__ == "__main__":
    Manager.run()
