import asyncio
import logging
import os
import sys

from common.constants import IS_WINDOWS
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
            log.critical("Something went wrong during startup!")
            self.loop.stop()

    async def shutdown(self) -> None:
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        [task.cancel() for task in tasks]

        log.info("Cancelling outstanding tasks")
        await asyncio.gather(*tasks, return_exceptions=True)

        log.info("Shutting down asyncgens...")
        await self.loop.shutdown_asyncgens()
        await asyncio.sleep(1)
        self.loop.stop()

    @classmethod
    def run(cls) -> None:
        log.info(f"Starting ArkViewer with PID {os.getpid()}")

        loop = asyncio.ProactorEventLoop() if IS_WINDOWS else asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        arkview = cls(loop)

        try:
            loop.create_task(arkview.start())
            loop.run_forever()
        except KeyboardInterrupt:
            print("CTRL+C received, shutting down...")
            loop.run_until_complete(arkview.shutdown())
        except Exception as e:
            log.critical("Fatal error!", exc_info=e)
            loop.run_until_complete(arkview.shutdown())
        finally:
            log.info("Shutting down...")
            if not loop.is_closed():
                loop.close()

            # loop.run_until_complete(loop.shutdown_asyncgens())
            # loop.run_until_complete(asyncio.sleep(1))
            # asyncio.set_event_loop(None)
            # loop.stop()

            log.info("Goodbye.")
            sys.exit()


if __name__ == "__main__":
    Manager.run()
