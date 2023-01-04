import asyncio
import functools
import json
import logging
import multiprocessing
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from configparser import ConfigParser
from datetime import datetime
from logging import handlers
from pathlib import Path
from typing import Optional

import uvicorn.config
from fastapi import FastAPI, HTTPException
from fastapi_utils.cbv import cbv
from fastapi_utils.inferring_router import InferringRouter
from uvicorn import Config, Server

from utils import BanList, Const, PrettyFormatter, StandardFormatter, Tools

# Config setup
parser = ConfigParser()
# API setup
api = FastAPI()
router = InferringRouter()
# Log setup
log = logging.getLogger("arkparser")
# Console logs
console = logging.StreamHandler()
console.setFormatter(PrettyFormatter())
# File logs
logfile = handlers.RotatingFileHandler(
    "logs.log", mode="a", maxBytes=5 * 1024 * 1024, backupCount=3
)
logfile.setFormatter(StandardFormatter())
# Set log level
log.setLevel(logging.DEBUG)
console.setLevel(logging.DEBUG)
logfile.setLevel(logging.DEBUG)
# Add handlers
log.addHandler(console)
log.addHandler(logfile)

# Global cache
CACHE: dict = {}
mapfile: Optional[str] = ""
cluster: Optional[str] = ""
threads: Optional[int] = round(os.cpu_count() / 2)


@cbv(router)
class ArkViewer:
    """
    Compile with 'pyinstaller.exe --clean app.spec'

    Requirements (.NET V6.0 framework)
    https://dotnet.microsoft.com/en-us/download
    """

    __version__ = "0.1.16"

    def __init__(self):
        self.exe = (
            True
            if (getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))
            else False
        )
        self.root = os.path.abspath(os.path.dirname(__file__))
        self.output = os.path.join(self.root, "output")

        self.threadpool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="parser")
        self.syncing = False
        self.debug = None
        self.port = 8000

        self.valid = [
            "players",
            "structures",
            "tamed",
            "tribelogs",
            "tribes",
            "wild",
            "mapstructures",
        ]

    async def initialize(self):
        global mapfile
        global cluster
        global threads
        if not os.path.exists(self.output):
            os.mkdir(self.output)

        config = Path(os.path.join(os.getcwd(), "config.ini"))
        if not config.exists():
            parser["Settings"] = Const().defaults
            with open(config, "w") as f:
                parser.write(f)

        parser.read(config)
        settings = parser["Settings"]
        for k, v in dict(settings).items():
            if not str(v).strip():
                del settings[k]

        self.port = settings.getint("Port", fallback=8000)
        self.debug = settings.getboolean("Debug", fallback=False)

        mapfile = settings.get("MapFilePath", fallback="").replace('"', "")
        cluster = settings.get("ClusterFolderPath", fallback="").replace('"', "")
        threads = settings.getint("Threads", fallback=round(os.cpu_count() / 2))
        if not mapfile:
            log.warning("No map file path has been set!")
        if not cluster:
            log.warning("No cluster path has been set")

        if self.debug:
            if not self.exe:
                mapfile = os.path.join(self.root, "testdata/mapdata/Ragnarok.ark")
                cluster = os.path.join(self.root, "testdata/clusterdata")
            else:
                log.debug(f"Running as EXE - {self.root}")
        else:
            log.setLevel(logging.INFO)
            console.setLevel(logging.INFO)
            logfile.setLevel(logging.INFO)

        log.debug(
            f"\nConfig loaded\n"
            f"map file: {mapfile}\n"
            f"cluster folder: {cluster}\n"
            f"working dir: {os.getcwd()}\n"
            f"debug: {self.debug}"
        )
        log.debug(f"Python version {sys.version}")

        # Make sure .NET is installed
        log.debug(f"OS: {os.name}")
        windows = True if "C:\\Users" in os.environ.get("USERPROFILE", "") else False
        if windows:
            log.debug("Windows detected")
            if not Tools().dotnet_installed():
                return
        else:
            log.info("Linux detected")

        log.info(
            f"Using {threads} {'thread' if threads == 1 else 'threads'} for parsing"
        )
        tasks = [self.exporter(), self.server()]
        if os.name == "nt":
            tasks.append(self.window())
        await asyncio.gather(*tasks)

    async def exporter(self):
        global cluster
        last_modified = None
        while True:
            if not mapfile:
                log.error("Map file is not set")
                return
            if not mapfile.lower().endswith(".ark"):
                log.error(f"Invalid Map File: {mapfile}")
                return
            file = Path(mapfile)
            if not file.exists():
                log.info("Can't find map file. Waiting 60 seconds to check again")
                await asyncio.sleep(60)
                continue
            if file.is_dir():
                log.error("Map file path only specifies a directory!")
                return

            lastm = file.stat().st_mtime
            if last_modified == lastm:
                await asyncio.sleep(2)
                continue

            # Update last modified time
            last_modified = lastm

            exe = os.path.join(self.root, "exporter/ASVExport.exe")
            affinity = Tools().get_affinity_mask(threads)

            if not Path(cluster).exists():
                log.warning("Cluster path is set but doesn't exist!")
                cluster = None

            cmd = rf'start /LOW /MIN /AFFINITY {affinity} {exe} all "{mapfile}" "{self.output}\"'
            if cluster:
                if Path(cluster).is_dir():
                    cmd = rf'start /LOW /MIN /AFFINITY {affinity} {exe} all "{mapfile}" "{cluster}\" "{self.output}\"'
                    log.debug("Running parser with cluster included")

            self.syncing = True
            os.system(cmd)
            await asyncio.sleep(3)
            while Tools().is_running("ASVExport.exe"):
                await asyncio.sleep(3)
            await asyncio.sleep(5)
            loop = asyncio.get_event_loop()
            try:
                await loop.run_in_executor(
                    self.threadpool, functools.partial(self.update_cache)
                )
                log.info("Map files synced")
            finally:
                self.syncing = False
            log.debug(f"Exported to {self.output}")
            await asyncio.sleep(5)
            if self.debug and not self.exe:
                await asyncio.sleep(100)

    def update_cache(self, target: Optional[str] = ""):
        global CACHE
        for file in Path(self.output).iterdir():
            key = file.name.replace("ASV_", "").replace(".json", "").lower().strip()
            if target and target.lower() != key:
                continue
            ts = datetime.fromtimestamp(file.stat().st_mtime).astimezone(
                tz=datetime.now().astimezone().tzinfo
            )
            tries = 0
            data = None
            while tries < 3:
                tries += 1
                try:
                    data = Tools().read_data(file.resolve())
                    break
                except (
                    OSError,
                    WindowsError,
                    json.JSONDecodeError,
                    UnicodeDecodeError,
                ):
                    time.sleep(3)
                    continue
            if not data:
                continue
            CACHE[key] = {"data": data, "last_modified": ts.isoformat()}
            CACHE["last_modified"] = ts.isoformat()

    async def window(self):
        # Keep the window title animated, so we know it isn't frozen
        barlength = 7
        fbox = "▰"  # Filled box
        f = 0
        ebox = "▱"  # Empty box
        e = barlength
        stage = 1  # There should be twice as many stages as bar length
        while True:
            if stage > barlength * 2:
                stage = 1
                f = 0
                e = barlength

            if stage <= barlength:
                bar = (f * fbox) + (e * ebox)
                f += 1
                e -= 1
            else:
                bar = (e * ebox) + (f * fbox)
                f -= 1
                e += 1

            stage += 1

            cmd = f"title ArkViewer {self.__version__}  {bar}"
            if self.syncing:
                cmd += "  [Syncing...]"
            os.system(cmd)
            await asyncio.sleep(0.1)

    async def server(self):
        api.include_router(router)
        level = "debug" if self.debug else "info"
        log_config = uvicorn.config.LOGGING_CONFIG
        log_config["formatters"]["access"][
            "fmt"
        ] = "%(asctime)s - %(levelname)s - %(message)s"
        log_config["formatters"]["default"][
            "fmt"
        ] = "%(asctime)s - %(levelname)s - %(message)s"
        log_config["formatters"]["access"]["datefmt"] = "%m/%d %I:%M:%S %p"
        log_config["formatters"]["default"]["datefmt"] = "%m/%d %I:%M:%S %p"
        config = Config(
            app=api,
            host="0.0.0.0",
            port=self.port,
            log_level=level,
            log_config=log_config,
            workers=4,
        )
        server = Server(config)
        multiprocessing.freeze_support()
        if not self.debug:
            log.info(f"API bound to http://0.0.0.0:{self.port}")
        await server.serve()

    # Players, Structures, Tamed, TribeLogs, Tribes, Wild, MapStructure
    @router.get("/data/{datatype}")
    async def get_data(self, datatype: str):
        """
        Get parsed map data including last sync time
        defaults = {"data": dict, "last_modified": str}
        :param datatype:
        :return:
        """
        keys = list(CACHE.keys())
        log.debug(f"Incoming API call for {datatype}\n{keys}")
        dtype = datatype.lower().strip()
        if dtype == "all":
            return CACHE

        headers = {
            "threads": str(threads),
            "version": self.__version__,
            "keys": str(keys),
        }
        if dtype not in self.valid:
            raise HTTPException(
                status_code=400, detail="Not a valid data type", headers=headers
            )
        res = CACHE.get(dtype, None)
        if not res:
            self.update_cache(dtype)
            res = CACHE.get(dtype, None)
            if not res:
                raise HTTPException(
                    status_code=404,
                    detail=f"The {datatype} data has not been cached yet",
                    headers=headers,
                )
        for k, v in headers.items():
            res[k] = v
        return res

    @router.get("/")
    async def get_info(self):
        """
        Get API info like map file name and last sync time
        :return:
        """
        map_path = str(mapfile)
        map_filename = map_path.replace("\\", "|").replace("/", "|").split("|")[-1]
        res = {
            "path": map_path,
            "map": map_filename,
            "last_modified": None,
            "threads": threads,
            "version": self.__version__,
        }
        keys = list(CACHE.keys())
        if keys:
            item = CACHE.get(keys[0], None)
            if item:
                res["last_modified"] = item["last_modified"]
        return res

    @router.get("/banlist")
    async def get_banlist(self):
        """Returns a list of banned XUIDs"""
        banlistfile = Path(f"{Path(mapfile).parent}/BanList.txt")
        if not banlistfile.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Could not find BanList.txt in {banlistfile.parent}",
            )
        xuids = [i.strip() for i in banlistfile.read_text().split("\n") if i.strip()]
        return {"data": xuids}

    @router.post("/updatebanlist")
    async def update_banlist(self, banlist: BanList):
        with open(f"{Path(mapfile).parent}/BanList.txt", "w") as f:
            f.write(banlist.bans)
        return {"success": True}

    @staticmethod
    @api.on_event("startup")
    async def startup_event():
        pass


if __name__ == "__main__":
    try:
        while True:
            ark = ArkViewer()
            try:
                asyncio.run(ark.initialize())
            except KeyboardInterrupt:
                break
            except Exception:
                log.critical(f"ArkViewer failed to start!!!\n{traceback.format_exc()}")
                log.info("Sleeping for 60 seconds and trying again")
                time.sleep(60)
    finally:
        log.info("ArkParser has shut down")
        input()
