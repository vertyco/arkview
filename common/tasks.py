import asyncio
import json
import logging
import multiprocessing
import os
import sys
from collections import defaultdict
from configparser import ConfigParser
from datetime import datetime, timedelta
from pathlib import Path

import psutil
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi_utils.cbv import cbv
from fastapi_utils.inferring_router import InferringRouter
from uvicorn import Config, Server

from common.constants import DEFAULT_CONF, IS_EXE, IS_WINDOWS, VALID_DATATYPES
from common.exporter import export_loop, load_outputs, process_export
from common.logger import init_sentry
from common.models import Banlist, Dtypes, cache  # noqa
from common.scheduler import scheduler
from common.statusbar import status_bar
from common.utils import dotnet_installed, format_sys_info
from common.version import VERSION

api = FastAPI()
router = InferringRouter()
parser = ConfigParser()

log = logging.getLogger("arkview")


# Add helper to extract default block (comments + key) from DEFAULT_CONF
def get_default_block(key: str) -> str:
    lines = DEFAULT_CONF.splitlines()
    block_lines = []
    for i, line in enumerate(lines):
        if line.lower().strip().startswith((key.lower() + " =", key.lower() + "=")):
            # Include preceding comment lines if any
            j = i - 1
            while j >= 0 and lines[j].strip().startswith("#"):
                block_lines.insert(0, lines[j])
                j -= 1
            block_lines.append(line)
            break
    return "\n".join(block_lines)


@cbv(router)
class ArkViewer:
    """
    Compile with 'pyinstaller.exe --clean app.spec'

    Requirements (.NET V6.0 framework)
    https://dotnet.microsoft.com/en-us/download
    """

    async def initialize(self) -> bool:
        global cache
        if not cache.config.exists():
            log.warning("No config file exists! Creating one...")
            cache.config.write_text(DEFAULT_CONF.strip())
            return False

        log.info(f"Reading from {cache.config}")
        parser.read(str(cache.config))
        settings = parser["Settings"]

        # Make sure all settings are present, adding missing keys to existing config
        required = [
            "Port",
            "BanListFile",
            "MapFilePath",
            "ClusterFolderPath",
            "Priority",
            "Threads",
            "ReprocessOnArkDataUpdate",
            "Debug",
            "DSN",
            "APIKey",
        ]
        missing = [key for key in required if key not in settings]
        if missing:
            log.warning(
                "Missing settings in config file: %s. Updating config with defaults.",
                ", ".join(missing),
            )
            with cache.config.open("a", encoding="utf-8") as cf:
                for key in missing:
                    block = get_default_block(key)
                    cf.write("\n" + block + "\n")
            parser.read(str(cache.config))
            settings = parser["Settings"]

        parsed = [f"{k}: {v}\n" for k, v in settings.items()]
        log.info(f"Parsed settings\n{''.join(parsed)}")

        cache.debug = settings.getboolean("Debug", fallback=False)
        if cache.debug:
            log.setLevel(logging.DEBUG)
        else:
            log.setLevel(logging.INFO)
        cache.asatest = settings.getboolean("ASATest", fallback=False)
        cache.port = settings.getint("Port", fallback=8000)
        cache.reprocess_on_arkdata_update = settings.getboolean(
            "ReprocessOnArkDataUpdate", fallback=False
        )

        priority = settings.get("Priority", fallback="NORMAL").upper()
        if priority not in ["LOW", "BELOWNORMAL", "NORMAL", "ABOVENORMAL", "HIGH"]:
            log.error("Invalid priority setting! Using LOW")
            priority = "LOW"
        cache.priority = priority

        cpus = os.cpu_count() or 1
        cache.threads = settings.getint("Threads", fallback=2)
        if cache.threads > cpus:
            log.warning(
                f"Threads set to {cache.threads} but only {cpus} available, defaulting to {cpus}"
            )
            cache.threads = cpus

        cache.api_key = settings.get("APIKey", fallback="").replace('"', "")
        if not cache.api_key:
            log.warning("API key is not set! Running with reduced security!")

        if cache.debug and not IS_EXE:
            testdata = cache.root_dir / "testdata"
            if cache.asatest:
                log.info("Using test files (ASA)")
                cache.map_file = testdata / "map_asa" / "TheIsland_WP.ark"
                cache.cluster_dir = testdata / "solecluster_asa"
            else:
                log.info("Using test files (ASE)")
                cache.map_file = testdata / "map_ase" / "LostIsland.ark"
                cache.cluster_dir = testdata / "solecluster_ase"
        else:
            if dsn := settings.get(
                "DSN",
                fallback="https://ab80bb7b88b00008400a4c63dbf85dac@sentry.vertyco.net/4",
            ).replace('"', ""):
                log.info("Initializing Sentry")
                if dsn.strip():
                    init_sentry(dsn=dsn.strip(), version=VERSION)

            map_file = settings.get("MapFilePath", fallback="").replace('"', "")
            if not map_file:
                log.error("Map file path cannot be empty!")
                return False

            cache.map_file = Path(map_file)
            # Make sure cache.config and cache.map_file are on the same physical drive
            if cache.config.resolve().drive != cache.map_file.resolve().drive:
                log.warning(
                    "Config file and map file should be on the same drive! %s %s",
                    cache.config,
                    cache.map_file,
                )
            if not Path(cache.map_file).exists():
                log.error("Map file does not exist! %s", cache.map_file)
                return False
            if not Path(cache.map_file).is_file():
                log.error(
                    "Map path must be a file, not a directory! %s", cache.map_file
                )
                return False
            else:
                cache.map_file = Path(cache.map_file)

            cluster_dir = settings.get("ClusterFolderPath", fallback="").replace(
                '"', ""
            )
            if not cluster_dir:
                log.warning(
                    "Cluster dir has not been set, some features will be unavailable!"
                )
            elif not Path(cluster_dir).exists():
                log.error("Cluster dir was set but does not exist! %s", cluster_dir)
                return False
            elif not Path(cluster_dir).is_dir():
                log.error("Cluster path is not a directory! %s", cluster_dir)
                return False
            else:
                cache.cluster_dir = Path(cluster_dir)

            ban_file = settings.get("BanListFile", fallback="").replace('"', "")
            if ban_file:
                path = Path(ban_file)
                if not path.exists():
                    log.error("Banlist file %s specified but does not exist!", path)
                    return False
                if not path.is_file():
                    log.error("Banlist path %s is not a file!", path)
                    return False
                # Ensure it's a .txt file
                if not path.name.lower().endswith(".txt"):
                    log.error("Banlist file %s is not a .txt file!", path)
                    return False
                cache.ban_file = path
            else:
                log.info("Banlist file not set!")

        txt = (
            f"\nRunning as EXE: {cache.root_dir}\n"
            f"Exporter: {cache.exe_file}\n"
            f"Map File: {cache.map_file}\n"
            f"Cluster Dir: {cache.cluster_dir}\n"
            f"Output Dir: {cache.output_dir}\n"
            f"Working Dir: {os.getcwd()}\n"
            f"Debug: {cache.debug}\n"
            f"Using Cores: {cache.threads}/{cpus}\n"
            f"Priority: {cache.priority}\n"
            f"OS: {'Windows' if IS_WINDOWS else 'Linux'}\n"
            f"LD Lib: {os.environ.get('LD_LIBRARY_PATH')}\n"
        )
        log.info(txt)
        try:
            if IS_WINDOWS and not dotnet_installed():
                log.info("Dotnet not installed!")
                return False
        except FileNotFoundError:
            log.error("Failed to check .NET version!")

        if not cache.map_file.exists():
            log.error("Map file does not exist!")
            return False
        if cache.cluster_dir and not cache.cluster_dir.exists():
            log.error("Cluster dir does not exist!")
            return False
        if not cache.exe_file.exists():
            log.error("Exporter does not exist!")
            return False

        if cpus < 4:
            log.warning("Server has less than 4 cores, performance may be impacted!")

        if IS_WINDOWS:
            scheduler.add_job(
                process_export,
                trigger="interval",
                seconds=5,
                next_run_time=datetime.now() + timedelta(seconds=5),
                id="Handler.exporter",
                max_instances=1,
            )
        else:
            asyncio.create_task(export_loop(), name="export_loop")

        asyncio.create_task(self.server(), name="arkview_server")
        asyncio.create_task(load_outputs(), name="load_outputs")
        if IS_WINDOWS and IS_EXE:
            asyncio.create_task(status_bar(), name="status_bar")
        return True

    async def server(self):
        global cache
        api.include_router(router)
        host = "127.0.0.1" if (cache.debug or not IS_EXE) else "0.0.0.0"
        # Check if user provided arguments for host and port
        if len(sys.argv) > 1:
            host = sys.argv[1]
        config = Config(
            app=api,
            host=host,
            port=cache.port,
            log_level="debug" if cache.debug else "info",
            # log_config=API_CONF,
            log_config=None,
            workers=1,
        )
        server = Server(config)
        multiprocessing.freeze_support()
        try:
            await server.serve()
        except (KeyboardInterrupt, RuntimeError):
            pass

    async def check_keys(self, request: Request):
        global cache
        if cache.api_key and not request.headers.get(
            "Authorization", request.headers.get("authorization")
        ):
            raise HTTPException(
                status_code=405,
                detail="No API key provided!",
                headers=self.info(stringify=True),
            )
        if cache.api_key and cache.api_key != request.headers.get("Authorization"):
            raise HTTPException(
                status_code=401,
                detail="Invalid API key!",
                headers=self.info(stringify=True),
            )

    def info(self, stringify: bool = False) -> dict:
        global cache
        day = 0
        time = "00:00"
        for v in cache.exports.values():
            if "day" in v:
                day = v["day"]
                time = v["time"]
        uptime = (
            datetime.now() - datetime.fromtimestamp(psutil.boot_time())
        ).total_seconds()
        return {
            "last_export": str(int(cache.last_export))
            if stringify
            else int(cache.last_export),
            "port": str(cache.port) if stringify else cache.port,
            "map_name": str(cache.map_file.name),
            "map_path": str(cache.map_file),
            "cluster_dir": str(cache.cluster_dir),
            "version": VERSION,
            "cached_keys": ", ".join(cache.exports.keys())
            if stringify
            else list(cache.exports.keys()),
            "day": str(day) if stringify else day,
            "time": time,
            "uptime": str(uptime) if stringify else uptime,
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
            raise HTTPException(
                status_code=400,
                detail="Banlist file not set!",
                headers=self.info(stringify=True),
            )
        if isinstance(cache.ban_file, Path) and not cache.ban_file.exists():
            raise HTTPException(
                status_code=400,
                detail="Banlist file does not exist!",
                headers=self.info(stringify=True),
            )
        try:
            banlist_raw = cache.ban_file.read_text()
            content = {
                "banlist": [i.strip() for i in banlist_raw.split("\n") if i.strip()],
                **self.info(),
            }
            return JSONResponse(content=content)
        except Exception as e:
            log.exception("Failed to read banlist file %s", cache.ban_file)
            raise HTTPException(
                status_code=500,
                detail=str(e),
                headers=self.info(stringify=True),
            )

    @router.put("/updatebanlist")
    async def update_banlist(self, request: Request, banlist: Banlist):
        await self.check_keys(request)
        global cache
        if not cache.ban_file:
            raise HTTPException(
                status_code=400,
                detail="Banlist file not set!",
                headers=self.info(stringify=True),
            )
        if not cache.ban_file.exists():
            raise HTTPException(
                status_code=400,
                detail="Banlist file does not exist!",
                headers=self.info(stringify=True),
            )
        if not banlist.bans:
            raise HTTPException(
                status_code=400,
                detail="Banlist is empty!",
                headers=self.info(stringify=True),
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
                status_code=422,
                detail=f"Invalid datatype, valid types are: {joined}",
                headers=self.info(stringify=True),
            )
        if datatype.lower() == "all":
            data = cache.exports
        else:
            target_data = cache.exports.get(datatype)
            if not target_data:
                raise HTTPException(
                    status_code=404,
                    detail=f"Datatype {datatype} not cached yet!",
                    headers=self.info(stringify=True),
                )
            data = {datatype: target_data}

        return JSONResponse(content={**data, **self.info()})

    @router.get("/overlimit/{limit}")
    async def get_over_limit(self, request: Request, limit: int):
        """Get all players who's tribe has uncryod tames over the limit"""
        await self.check_keys(request)
        global cache
        tamed = cache.exports.get("tamed")
        tribes = cache.exports.get("tribes")
        if not tamed:
            raise HTTPException(
                status_code=404,
                detail="Tamed data not cached yet!",
                headers=self.info(stringify=True),
            )
        if not tribes:
            raise HTTPException(
                status_code=404,
                detail="Tribes data not cached yet!",
                headers=self.info(stringify=True),
            )

        def _exe():
            # First map all tames to tribes
            found = set()
            tribe_tames: dict[int, list[dict]] = defaultdict(list)
            for tame in tamed["data"]:
                if tame.get("uploadedTime") or tame["cryo"]:
                    continue
                key = f"{tame['id']}-{tame['dinoid']}"
                if key in found:
                    continue
                found.add(key)
                tribeid = int(tame["tribeid"])
                tribe_tames[tribeid].append(tame)

            over_limit: dict[str, list[dict]] = {}
            for tribe in tribes["data"]:
                if not tribe.get("members"):
                    continue
                uncryod: list[dict] = tribe_tames.get(tribe["tribeid"], [])
                if len(uncryod) <= limit:
                    continue
                for member in tribe["members"]:
                    over_limit[member["steamid"]] = uncryod
            return over_limit

        over_limit: dict[str, list[dict]] = await asyncio.to_thread(_exe)
        return JSONResponse(content={"overlimit": over_limit, **self.info()})

    @router.post("/datas")
    async def get_datas(self, request: Request, datatypes: Dtypes):
        await self.check_keys(request)
        global cache
        invalid_types = [
            datatype for datatype in datatypes.dtypes if datatype not in VALID_DATATYPES
        ]

        if invalid_types:
            joined_valid = ", ".join(VALID_DATATYPES)
            joined_invalid = ", ".join(invalid_types)
            raise HTTPException(
                status_code=400,
                detail=f"Invalid data types {joined_invalid}, valid types are: {joined_valid}",
                headers=self.info(stringify=True),
            )

        data = {}

        for datatype in datatypes.dtypes:
            target_data = cache.exports.get(datatype)
            if not target_data:
                raise HTTPException(
                    status_code=404,
                    detail=f"Datatype {datatype} not cached yet!",
                    headers=self.info(stringify=True),
                )
            data[datatype] = target_data

        return JSONResponse(content={**data, **self.info()})

    @router.get("/stats")
    async def get_system_info(self, request: Request):
        await self.check_keys(request)
        base = self.info()
        try:
            stats = await asyncio.to_thread(format_sys_info)
            return JSONResponse(content={**base, **stats})
        except Exception as e:
            log.exception("Failed to get system info!")
            raise HTTPException(
                status_code=500, detail=str(e), headers=self.info(stringify=True)
            )
