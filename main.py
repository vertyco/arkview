import asyncio
import logging
import multiprocessing
import os
import sys

log = logging.getLogger("arkview.main")

# NOTE: keep this module's top level minimal and side-effect free. The parser
# runs in a `multiprocessing` spawn child, and spawn re-imports this module
# (as __mp_main__) in every child. Heavy imports (tasks/scheduler/logger) are
# therefore deferred into the methods below, and init_logging() is called only
# from the __main__ guard -- so a parse child never drags in FastAPI/uvicorn or
# rotates the server's log file.


class Manager:
    """Owns the runtime: config, scheduler, watcher loop, and the API server.

    Build the Windows exe with PyInstaller (see README).
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        from common.tasks import ArkViewer

        self.loop: asyncio.AbstractEventLoop = loop
        self.handler = ArkViewer()

    async def start(self) -> None:
        from common.scheduler import scheduler
        from common.version import VERSION

        log.info(f"Version: {VERSION}")
        scheduler.start()
        scheduler.remove_all_jobs()
        success = await self.handler.initialize()
        if not success:
            input("Initialization failed. Press any key to exit...")
            self.loop.stop()

    async def shutdown(self) -> None:
        from common.scheduler import scheduler

        scheduler.remove_all_jobs()
        scheduler.shutdown(wait=False)

        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        [task.cancel() for task in tasks]

        log.info("Cancelling outstanding tasks")
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception) and not isinstance(
                result, asyncio.CancelledError
            ):
                log.error("Task exited with %s during shutdown", type(result).__name__)

        log.info("Shutting down asyncgens...")
        try:
            await self.loop.shutdown_asyncgens()
            await asyncio.sleep(1)
            self.loop.stop()
        except RuntimeError:
            pass

    @classmethod
    def run(cls) -> None:
        from common.constants import IS_WINDOWS

        log.info(f"Starting ArkViewer with PID {os.getpid()}")

        loop = asyncio.ProactorEventLoop() if IS_WINDOWS else asyncio.new_event_loop()
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
            if not loop.is_closed():
                loop.run_until_complete(arkview.shutdown())
                loop.run_until_complete(loop.shutdown_asyncgens())
                asyncio.set_event_loop(None)
                loop.stop()
                loop.close()

                log.info("Goodbye.")
                sys.exit()


if __name__ == "__main__":
    # freeze_support() MUST run first: in a frozen exe the same binary is
    # re-launched as the parse child, and this is what hands control to the
    # multiprocessing bootstrap instead of starting a second server.
    multiprocessing.freeze_support()
    multiprocessing.set_start_method("spawn", force=True)

    from common.console import disable_quickedit, enable_console_vt, print_banner
    from common.logger import init_logging

    disable_quickedit()  # stop console click-to-select from freezing the loop
    enable_console_vt()
    init_logging()
    print_banner()
    Manager.run()
