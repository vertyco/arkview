import asyncio
import logging
import multiprocessing
import os
from configparser import ConfigParser
from datetime import datetime
from pathlib import Path

import orjson
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi_utils.cbv import cbv
from fastapi_utils.inferring_router import InferringRouter
from uvicorn import Config, Server

from common.constants import (
    API_CONF,
    BAR,
    CONFIG,
    EXE_FILE,
    IS_EXE,
    IS_WINDOWS,
    OUTPUT_DIR,
    ROOT_DIR,
    VALID_DATATYPES,
)
from common.logger import init_sentry
from common.models import Banlist, Cache, FilePath, FileUpload
from common.utils import dotnet_installed, format_sys_info, wait_for_process
from common.version import VERSION

api = FastAPI()
router = InferringRouter()
parser = ConfigParser()

log = logging.getLogger("arkview")

cache = Cache(
    config=CONFIG,
    root_dir=ROOT_DIR,
    output_dir=OUTPUT_DIR,
    exe_file=EXE_FILE,
)


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
                log.error("Map file path has not been set!")
                return False
            if not cache.map_file.lower().endswith(".ark"):
                log.error("Map file must have the .ark extension!")
                return False
            if not Path(cache.map_file).exists():
                log.error("Map file does not exist!")
                return False
            cache.map_file = Path(cache.map_file)

            cache.cluster_dir = settings.get("ClusterFolderPath", fallback="").replace(
                '"', ""
            )
            if not cache.cluster_dir:
                log.warning(
                    "Cluster dir has not been set, some features will be unavailable!"
                )
            elif not Path(cache.cluster_dir).exists():
                log.warning(
                    "Cluster dir does not exist, some features will be unavailable"
                )
                cache.cluster_dir = ""
            elif not Path(cache.cluster_dir).is_dir():
                log.warning(
                    "Cluster path is not a directory! Some features will be unavailable"
                )
                cache.cluster_dir = ""
            else:
                cache.cluster_dir = Path(cache.cluster_dir)

            cache.ban_file = settings.get("BanListFile", fallback="").replace('"', "")
            if not Path(cache.ban_file).exists():
                log.warning("Banlist file does not exist!")
                cache.ban_file = ""
            elif cache.ban_file and not cache.ban_file.lower().endswith(".txt"):
                log.warning("Invalid Banlist file!")
                cache.ban_file = ""

        txt = (
            f"\nRunning as EXE: {cache.root_dir}\n"
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
        if IS_WINDOWS and IS_EXE:
            asyncio.create_task(self.status_bar(), name="status_bar")
        asyncio.create_task(self.server(), name="arkview_server")
        asyncio.create_task(self.export(), name="arkview_export")
        asyncio.create_task(self.load_outputs(), name="load_outputs")
        return True

    async def status_bar(self):
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

    async def export(self):
        global cache
        while True:
            map_file_modified = int(cache.map_file.stat().st_mtime)
            if cache.last_export == map_file_modified:
                await asyncio.sleep(2)
                continue
            # Run exporter
            cache.last_export = map_file_modified

            # ASVExport.exe all "path/to/map/file" "path/to/cluster" "path/to/output/folder"
            sep = "\\" if IS_WINDOWS else "/"
            pre = f"start /LOW /MIN /AFFINITY 0x800 {cache.exe_file} all"
            ext = f' "{cache.map_file}" "{cache.output_dir}{sep}"'
            if cache.cluster_dir:
                ext = f' "{cache.map_file}" "{cache.cluster_dir}{sep}" "{cache.output_dir}{sep}"'
            command = pre + ext

            try:
                cache.syncing = True
                os.system(command)
                await asyncio.sleep(5)
                await wait_for_process("ASVExport.exe")
                await asyncio.sleep(5)
            except Exception as e:
                log.error("Export failed", exc_info=e)
            finally:
                cache.syncing = False

            try:
                cache.reading = True
                await self.load_outputs()
            except Exception as e:
                log.error("Failed to load outputs", exc_info=e)
            finally:
                cache.reading = False

    async def load_outputs(self, target: str = ""):
        global cache
        for export_file in cache.output_dir.iterdir():
            key = (
                export_file.name.replace("ASV_", "")
                .replace(".json", "")
                .lower()
                .strip()
            )
            if target and target.lower() != key:
                continue

            last_modified = int(export_file.stat().st_mtime)
            tries = 0
            data = None

            while tries < 3:
                tries += 1
                try:
                    raw_file = export_file.read_bytes()
                    data = await asyncio.to_thread(orjson.loads, raw_file)
                    break
                except (orjson.JSONDecodeError, UnicodeDecodeError):
                    await asyncio.sleep(3)
                except Exception as e:
                    log.warning(f"Failed to load {export_file.name}", exc_info=e)
                    break

            if not data:
                continue

            try:
                cache.exports[key] = {"data": data, "last_modified": last_modified}
                log.info(f"Cached {export_file.name}")
            except Exception as e:
                log.error(f"Failed to cache export: {type(data)}", exc_info=e)

        cache.last_output = int(datetime.now().timestamp())

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
            raise HTTPException(status_code=405, detail="No API key provided!")
        if cache.api_key and cache.api_key != request.headers.get("Authorization"):
            raise HTTPException(status_code=401, detail="Invalid API key!")

    def info(self) -> dict:
        global cache
        return {
            "last_export": cache.last_export,
            "last_output": cache.last_output,
            "port": cache.port,
            "map_file": str(cache.map_file),
            "cluster_dir": str(cache.cluster_dir),
            "version": VERSION,
            "cached_keys": list(cache.exports.keys()),
        }

    @router.get("/")
    async def get_info(self, request: Request):
        await self.check_keys(request)
        return JSONResponse(content=self.info())

    @router.get("/banlist")
    async def get_banlist(self, request: Request):
        await self.check_keys(request)
        global cache
        if not cache.ban_file:
            raise HTTPException(status_code=400, detail="Banlist file not set!")
        if not cache.ban_file.exists():
            raise HTTPException(status_code=400, detail="Banlist file does not exist!")
        try:
            banlist_raw = cache.ban_file.read_text()
            content = {
                "banlist": [i.strip() for i in banlist_raw.split("\n") if i.strip()],
                **self.info(),
            }
            return JSONResponse(content=content)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.put("/updatebanlist")
    async def update_banlist(self, request: Request, banlist: Banlist):
        await self.check_keys(request)
        global cache
        if not cache.ban_file:
            raise HTTPException(status_code=400, detail="Banlist file not set!")
        if not cache.ban_file.exists():
            raise HTTPException(status_code=400, detail="Banlist file does not exist!")
        if not banlist.bans:
            raise HTTPException(status_code=400, detail="Banlist is empty!")
        if not all([i.isdigit() for i in banlist.bans]):
            raise HTTPException(
                status_code=400, detail="Banlist must contain User IDs only"
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
                status_code=400,
                detail=f"Invalid data type, valid types are: {joined}",
            )

        info = self.info()

        if datatype.lower() == "all":
            data = cache.exports
        else:
            data = cache.exports.get(datatype)

        if not data:
            raise HTTPException(
                status_code=400,
                detail=f"Datatype {datatype} not cached yet!",
                headers=info,
            )

        return JSONResponse(content={**data, **info})

    @router.get("/sysinfo")
    async def get_system_info(self, request: Request):
        await self.check_keys(request)
        base = self.info()
        try:
            stats = await asyncio.to_thread(format_sys_info)
            return JSONResponse(content={**base, **stats})
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/file/exists")
    async def check_file_exists(self, request: Request, filepath: FilePath):
        await self.check_keys(request)
        payload = {"exists": Path(filepath.path).exists(), **self.info()}
        return JSONResponse(content=payload)

    @router.post("/file/info")
    async def get_file_info(self, request: Request, filepath: FilePath):
        await self.check_keys(request)
        stat = Path(filepath.path).stat()
        payload = {"st_mtime": stat.st_mtime, "size": stat.st_size, **self.info()}
        return JSONResponse(content=payload)

    @router.post("/file/get")
    async def get_file(self, request: Request, filepath: FilePath):
        await self.check_keys(request)
        try:
            payload = {"file": Path(filepath.path).read_bytes(), **self.info()}
            return JSONResponse(content=payload)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete("/file/delete")
    async def delete_file(self, request: Request, filepath: FilePath):
        await self.check_keys(request)
        if not Path(filepath.path).exists():
            raise HTTPException(status_code=400, detail="File does not exist!")
        try:
            Path(filepath.path).unlink()
            return JSONResponse(content={"success": True, **self.info()})
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/file/listdir")
    async def list_dir(self, request: Request, filepath: FilePath):
        await self.check_keys(request)
        if not Path(filepath.path).exists():
            raise HTTPException(
                status_code=400,
                detail=f"Directory '{filepath.path}' does not exist!",
            )
        if not Path(filepath.path).is_dir():
            raise HTTPException(
                status_code=401,
                detail=f"'{filepath.path}' is not a valid directory!",
            )
        try:
            contents = [str(file) for file in Path(filepath.path).iterdir()]
            return JSONResponse(content={"contents": contents, **self.info()})
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.put("/file/put")
    async def put_file(self, request: Request, upload: FileUpload):
        await self.check_keys(request)
        try:
            Path(upload.path).write_bytes(upload.file)
            return JSONResponse(content={"success": True, **self.info()})
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
