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

from common.constants import (
    DEFAULT_CONF,
    EXPORTER_LOGS,
    IGNORED_DINO_PATHS,
    IS_EXE,
    IS_WINDOWS,
    VALID_DATATYPES,
)
from common.exporter import export_loop, load_outputs, process_export
from common.logger import init_sentry
from common.models import Banlist, Dtypes, ServerNames, cache  # noqa
from common.scheduler import scheduler
from common.statusbar import status_bar
from common.utils import dotnet_installed, follow_logs, format_sys_info, validate_path
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
                cache.map_file = None
                return False

            cache.map_file = Path(map_file)

            # Validate map file path
            if not validate_path(cache.map_file):
                log.error(
                    "Map file path contains invalid characters! Path must only contain letters, numbers, and underscores (no spaces or special characters): %s",
                    cache.map_file,
                )
                cache.map_file = None
                return False

            # Make sure cache.config and cache.map_file are on the same physical drive
            if cache.config.resolve().drive != cache.map_file.resolve().drive:
                log.warning(
                    "Config file and map file should be on the same drive! %s %s",
                    cache.config,
                    cache.map_file,
                )
            if not Path(cache.map_file).exists():
                log.error("Map file does not exist! %s", cache.map_file)
                cache.map_file = None
                return False
            if not Path(cache.map_file).is_file():
                log.error(
                    "Map path must be a file, not a directory! %s", cache.map_file
                )
                cache.map_file = None
                return False
            else:
                cache.map_file = Path(cache.map_file)

            cluster_dir = settings.get("ClusterFolderPath", fallback="").replace(
                '"', ""
            )
            cache.cluster_dir = None
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

                # Validate cluster directory path
                if not validate_path(cache.cluster_dir):
                    log.error(
                        "Cluster directory path contains invalid characters! Path must only contain letters, numbers, and underscores (no spaces or special characters): %s",
                        cache.cluster_dir,
                    )
                    cache.cluster_dir = None
                    return False

            ban_file = settings.get("BanListFile", fallback="").replace('"', "")
            if ban_file:
                path = Path(ban_file)
                if not path.exists():
                    log.error("Banlist file %s specified but does not exist!", path)
                    cache.ban_file = None
                    return False
                if not path.is_file():
                    log.error("Banlist path %s is not a file!", path)
                    cache.ban_file = None
                    return False
                # Ensure it's a .txt file
                if not path.name.lower().endswith(".txt"):
                    log.error("Banlist file %s is not a .txt file!", path)
                    cache.ban_file = None
                    return False
                cache.ban_file = path
            else:
                log.info("Banlist file not set!")
                cache.ban_file = None

        txt = (
            f"\nRunning as EXE: {cache.root_dir}\n"
            f"Exporter: {cache.exe_file}\n"
            f"Map File: {cache.map_file if cache.map_file else 'None'}\n"
            f"Cluster Dir: {cache.cluster_dir if cache.cluster_dir else 'None'}\n"
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

        if not cache.map_file or not cache.map_file.exists():
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

        # Start the exporter log tailing task
        if EXPORTER_LOGS.exists():
            asyncio.create_task(self.tail_exporter_logs(), name="exporter_logs")
        else:
            log.warning(
                f"Exporter log file not found at {EXPORTER_LOGS}, log tailing will be disabled"
            )

        asyncio.create_task(self.server(), name="arkview_server")
        asyncio.create_task(load_outputs(), name="load_outputs")
        if IS_WINDOWS and IS_EXE:
            asyncio.create_task(status_bar(), name="status_bar")
        return True

    async def tail_exporter_logs(self):
        """Follow the exporter logs and add them to the application logs."""
        try:
            log.info(f"Starting to tail exporter logs at {EXPORTER_LOGS}")
            async for line in follow_logs(EXPORTER_LOGS):
                # Filter out empty lines
                if not line.strip():
                    continue

                # Parse the log format: timestamp|LEVEL|message
                parts = line.strip().split("|", 2)
                if len(parts) >= 3:
                    _, level, message = parts
                    level = level.upper()

                    # Use appropriate logging level based on parsed level
                    if level == "ERROR":
                        log.error(f"[Exporter] {message}")
                    elif level == "WARNING" or level == "WARN":
                        log.warning(f"[Exporter] {message}")
                    elif level == "INFO":
                        log.info(f"[Exporter] {message}")
                    elif level == "DEBUG":
                        log.debug(f"[Exporter] {message}")
                    else:
                        log.info(f"[Exporter] {line}")
                else:
                    # Fallback for lines that don't match expected format
                    log.info(f"[Exporter] {line}")

        except FileNotFoundError:
            log.error(f"Exporter log file not found at {EXPORTER_LOGS}")
        except asyncio.CancelledError:
            log.info("Exporter log tailing task cancelled")
        except Exception as e:
            log.error("Error tailing exporter logs", exc_info=e)

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
        if cache.api_key:
            # Extract API key from Authorization header with proper handling
            # Support both "Bearer <token>" format and direct token
            auth_header = request.headers.get(
                "Authorization", request.headers.get("authorization", "")
            )
            token = auth_header

            if auth_header.lower().startswith("bearer "):
                token = auth_header[7:]

            if not token:
                raise HTTPException(
                    status_code=401,
                    detail="No API key provided!",
                    headers=self.info(stringify=True),
                )

            if token.strip() != cache.api_key:
                # Use constant-time comparison to prevent timing attacks
                from hmac import compare_digest

                if not compare_digest(token.strip(), cache.api_key):
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
        map_name = cache.map_file.name if cache.map_file else ""
        map_path = str(cache.map_file) if cache.map_file else ""
        cluster_dir = str(cache.cluster_dir) if cache.cluster_dir else ""
        return {
            "last_export": str(int(cache.last_export))
            if stringify
            else int(cache.last_export),
            "port": str(cache.port) if stringify else cache.port,
            "map_name": map_name if stringify else map_name,
            "map_path": map_path if stringify else map_path,
            "cluster_dir": cluster_dir if stringify else cluster_dir,
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
        if cache.ban_file and not cache.ban_file.exists():
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

    @router.get("/tribetames/{gameid}")
    async def get_tribe_tames(self, request: Request, gameid: str):
        """Get the tames belonging to the tribe of the player with the given game ID"""
        await self.check_keys(request)
        global cache
        tribes: list[dict] | None = cache.exports.get("tribes")
        tamed: list[dict] | None = cache.exports.get("tamed")
        if not tribes or not tamed:
            raise HTTPException(
                status_code=404,
                detail="Tribes or Tamed data not cached yet!",
                headers=self.info(stringify=True),
            )
        for tribe in tribes["data"]:
            if not tribe.get("members"):
                continue
            if not isinstance(tribe["members"], list):
                continue
            if any(
                str(i.get("steamid")).lower() == gameid.lower()
                for i in tribe["members"]
            ):
                break
        else:
            raise HTTPException(
                status_code=404,
                detail=f"No tribe found for player with game ID {gameid}",
                headers=self.info(stringify=True),
            )
        tribe_tames = []
        for tame in tamed["data"]:
            if int(tame.get("tribeid", -1)) == tribe["tribeid"]:
                tribe_tames.append(tame)
        return JSONResponse(
            content={"tamed": tribe_tames, "tribes": [tribe], **self.info()}
        )

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

    @router.post("/foreigntamescan")
    async def foreign_tame_scan(self, request: Request, servers: ServerNames):
        """Scan all tames and ensure their 'tamedServer' field matches one of the provided server names"""
        await self.check_keys(request)
        global cache
        valid_servers = [s.lower() for s in servers.servernames if s.strip()]

        if not valid_servers:
            raise HTTPException(
                status_code=400,
                detail="No valid server names provided!",
                headers=self.info(stringify=True),
            )

        tamed = cache.exports.get("tamed")
        tribes = cache.exports.get("tribes")
        if not tamed or not tribes:
            raise HTTPException(
                status_code=404,
                detail="Tamed or Tribes data not cached yet!",
                headers=self.info(stringify=True),
            )

        flagged_tames = []
        flagged_tribes = []
        found_tribes = set()
        for tame in tamed["data"]:
            if tame.get("tamedServer") is None:
                continue
            if not tame.get("tamedServer", "").strip():
                continue
            server = tame["tamedServer"].lower()
            if server in valid_servers:
                continue
            if tame["creature"] in IGNORED_DINO_PATHS:
                continue
            tribe_id = tame["tribeid"]
            if tribe_id not in found_tribes:
                found_tribes.add(tribe_id)

            flagged_tames.append(tame)

        for tribe in tribes["data"]:
            if tribe["tribeid"] in found_tribes:
                flagged_tribes.append(tribe)

        return JSONResponse(
            content={"tamed": flagged_tames, "tribes": flagged_tribes, **self.info()}
        )

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
