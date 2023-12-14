import asyncio
import json
import logging
import multiprocessing
import os
from configparser import ConfigParser
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi_utils.cbv import cbv
from fastapi_utils.inferring_router import InferringRouter
from uvicorn import Config, Server

from common.constants import API_CONF, IS_EXE, IS_WINDOWS, VALID_DATATYPES
from common.exporter import export, load_outputs
from common.logger import init_sentry
from common.models import Banlist, cache  # noqa
from common.statusbar import status_bar
from common.utils import dotnet_installed, format_sys_info
from common.version import VERSION

api = FastAPI()
router = InferringRouter()
parser = ConfigParser()

log = logging.getLogger("arkview")


@cbv(router)
class ArkViewer:
    """
    Compile with 'pyinstaller.exe --clean app.spec'

    Requirements (.NET V6.0 framework)
    https://dotnet.microsoft.com/en-us/download
    """

    async def initialize(self) -> bool:
        global cache
        parser.read(str(cache.config))
        settings = parser["Settings"]

        cache.debug = settings.getboolean("Debug", fallback=False)
        cache.port = settings.getint("Port", fallback=8000)

        cache.api_key = settings.get("APIKey", fallback="").replace('"', "")
        if not cache.api_key:
            log.warning("API key is not set! Running with reduced security!")

        if cache.debug and not IS_EXE:
            log.info("Using test files")
            cache.map_file = cache.root_dir / "testdata" / "mapdata" / "Ragnarok.ark"
            cache.cluster_dir = cache.root_dir / "testdata" / "clusterdata"
            cache.ban_file = cache.root_dir / "testdata" / "mapdata" / "BanList.txt"
        else:
            if dsn := settings.get("DSN", fallback="").replace('"', ""):
                log.info("Initializing Sentry")
                init_sentry(dsn=dsn, version=VERSION)

            cache.map_file = settings.get("MapFilePath", fallback="").replace('"', "")
            if not cache.map_file:
                log.error("Map file path cannot be empty!")
                return False
            if not Path(cache.map_file).exists():
                log.error("Map file does not exist!")
                return False
            else:
                cache.map_file = Path(cache.map_file)

            cache.cluster_dir = settings.get("ClusterFolderPath", fallback="").replace(
                '"', ""
            )
            if not cache.cluster_dir:
                log.warning(
                    "Cluster dir has not been set, some features will be unavailable!"
                )
            elif not Path(cache.cluster_dir).exists():
                log.error("Cluster dir does not exist!")
                return False
            elif not Path(cache.cluster_dir).is_dir():
                log.error("Cluster path is not a directory!")
                return False
            else:
                cache.cluster_dir = Path(cache.cluster_dir)

            cache.ban_file = settings.get("BanListFile", fallback="").replace('"', "")
            if cache.ban_file and not Path(cache.ban_file).exists():
                log.warning("Banlist file does not exist, creating a new one!")
                return False
            elif cache.ban_file and not cache.ban_file.lower().endswith(".txt"):
                log.warning("Invalid Banlist file!")
                return False

        txt = (
            f"\nRunning as EXE: {cache.root_dir}\n"
            f"Exporter: {cache.exe_file}\n"
            f"Map File: {cache.map_file}\n"
            f"Cluster Dir: {cache.cluster_dir}\n"
            f"Output Dir: {cache.output_dir}\n"
            f"Working Dir: {os.getcwd()}\n"
            f"Debug: {cache.debug}\n"
            f"Cores: {os.cpu_count()}\n"
            f"OS: {'Windows' if IS_WINDOWS else 'Linux'}\n"
        )
        log.info(txt)
        if IS_WINDOWS and not dotnet_installed():
            return

        asyncio.create_task(self.server(), name="arkview_server")
        asyncio.create_task(export(), name="arkview_export")
        asyncio.create_task(load_outputs(), name="load_outputs")
        if IS_WINDOWS and IS_EXE:
            asyncio.create_task(status_bar(), name="status_bar")
        return True

    async def server(self):
        global cache
        api.include_router(router)
        host = "127.0.0.1" if cache.debug else "0.0.0.0"
        config = Config(
            app=api,
            host=host,
            port=cache.port,
            log_level="debug" if cache.debug else "info",
            log_config=API_CONF,
            workers=1,
        )
        server = Server(config)
        multiprocessing.freeze_support()
        await server.serve()

    async def check_keys(self, request: Request):
        global cache
        if cache.api_key and not request.headers.get("Authorization"):
            raise HTTPException(status_code="405", detail="No API key provided!")
        if cache.api_key and cache.api_key != request.headers.get("Authorization"):
            raise HTTPException(status_code="401", detail="Invalid API key!")

    def info(self) -> dict:
        global cache
        return {
            "last_export": cache.last_export,
            "last_output": cache.last_output,
            "port": cache.port,
            "map_name": cache.map_file.name,
            "map_path": str(cache.map_file),
            "cluster_dir": str(cache.cluster_dir),
            "version": VERSION,
            "cached_keys": list(cache.exports.keys()),
        }

    @router.get("/")
    async def get_info(self, request: Request):
        await self.check_keys(request)
        info = self.info()
        log.info(f"Info requested!\n{json.dumps(info, indent=2)}")
        return JSONResponse(content=info)

    @router.get("/banlist")
    async def get_banlist(self, request: Request):
        await self.check_keys(request)
        global cache
        if not cache.ban_file:
            raise HTTPException(status_code="400", detail="Banlist file not set!")
        if not cache.ban_file.exists():
            raise HTTPException(
                status_code="400", detail="Banlist file does not exist!"
            )
        try:
            banlist_raw = cache.ban_file.read_text()
            content = {
                "banlist": [i.strip() for i in banlist_raw.split("\n") if i.strip()],
                **self.info(),
            }
            return JSONResponse(content=content)
        except Exception as e:
            raise HTTPException(status_code="500", detail=str(e))

    @router.put("/updatebanlist")
    async def update_banlist(self, request: Request, banlist: Banlist):
        await self.check_keys(request)
        global cache
        if not cache.ban_file:
            raise HTTPException(status_code="400", detail="Banlist file not set!")
        if not cache.ban_file.exists():
            raise HTTPException(
                status_code="400", detail="Banlist file does not exist!"
            )
        if not banlist.bans:
            raise HTTPException(status_code="400", detail="Banlist is empty!")
        if not all([i.isdigit() for i in banlist.bans]):
            raise HTTPException(
                status_code="400", detail="Banlist must contain User IDs only"
            )
        formatted = "\n".join(banlist.bans)
        cache.ban_file.write_text(formatted)
        return JSONResponse(content={"success": True, **self.info()})

    # Players, Structures, Tamed, TribeLogs, Tribes, Wild, MapStructure
    @router.get("/data/{datatype}")
    async def get_data(self, request: Request, datatype: str):
        await self.check_keys(request)
        global cache
        if datatype.lower() not in VALID_DATATYPES:
            joined = ", ".join(VALID_DATATYPES)
            raise HTTPException(
                status_code="400",
                detail=f"Invalid data type, valid types are: {joined}",
            )

        info = self.info()

        if datatype.lower() == "all":
            data = cache.exports
        else:
            target_data = cache.exports.get(datatype)
            if not target_data:
                raise HTTPException(
                    status_code="400",
                    detail=f"Datatype {datatype} not cached yet!",
                    headers=info,
                )
            data = {datatype: target_data}

        return JSONResponse(content={**data, **info})

    @router.get("/stats")
    async def get_system_info(self, request: Request):
        await self.check_keys(request)
        base = self.info()
        try:
            stats = await asyncio.to_thread(format_sys_info)
            return JSONResponse(content={**base, **stats})
        except Exception as e:
            raise HTTPException(status_code="500", detail=str(e))
