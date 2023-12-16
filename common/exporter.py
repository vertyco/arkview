import asyncio
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path

import orjson

from common.constants import IS_WINDOWS
from common.models import cache  # noqa
from common.utils import wait_for_process

log = logging.getLogger("arkview.exporter")


async def export():
    global cache
    if isinstance(cache.map_file, str):
        cache.map_file = Path(cache.map_file)
    while True:
        map_file_modified = int(cache.map_file.stat().st_mtime)
        if cache.last_export == map_file_modified and not cache.startup:
            await asyncio.sleep(2)
            continue
        cache.startup = False
        # Run exporter
        cache.last_export = map_file_modified

        # ASVExport.exe all "path/to/map/file" "path/to/cluster" "path/to/output/folder"
        if IS_WINDOWS:
            command = f'start /LOW /MIN /AFFINITY 0x800 {cache.exe_file} all "{cache.map_file}"'
            # command = f'start {cache.exe_file} all "{cache.map_file}"'
            if cdir := cache.cluster_dir:
                command += f' "{cdir}\\"'
            command += f' "{cache.output_dir}\\"'

        else:
            command = [
                "taskset",
                "-c",
                "0",
                "dotnet",
                str(cache.exe_file),
                "all",
                str(cache.map_file),
            ]
            if cdir := cache.cluster_dir:
                command.append(str(cdir) + "/")
            command.append(str(cache.output_dir) + "/")

        if cache.debug:
            log.info(f"Running: {command}")
        else:
            log.debug(f"Running: {command}")

        try:
            cache.syncing = True
            if IS_WINDOWS:
                os.system(command)
            else:
                result = subprocess.run(
                    command,
                    check=True,
                    text=True,
                    # shell=True,
                    capture_output=True,
                    # stdout=subprocess.PIPE,
                    # stderr=subprocess.PIPE,
                )
                log.info(f"STDOUT: {result.stdout}")
                log.info(f"STDERR: {result.stderr}")
            await asyncio.sleep(5)
            await wait_for_process("ASVExport")
            await asyncio.sleep(5)
        except subprocess.CalledProcessError as e:
            log.error("Export failed", exc_info=e)
            log.error(f"Standard Output: {e.stdout}")
            log.error(f"Standard Error: {e.stderr}")
        except Exception as e:
            log.error("Export failed", exc_info=e)
        finally:
            cache.syncing = False

        try:
            cache.reading = True
            await load_outputs()
        except Exception as e:
            log.error("Failed to load outputs", exc_info=e)
        finally:
            cache.reading = False


async def load_outputs(target: str = ""):
    global cache
    for export_file in cache.output_dir.iterdir():
        key = export_file.name.replace("ASV_", "").replace(".json", "").lower().strip()
        if target and target.lower() != key:
            continue

        # last_modified = int(export_file.stat().st_mtime)
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
            cache.exports[key] = data
            log.info(f"Cached {export_file.name}")
        except Exception as e:
            log.error(f"Failed to cache export: {type(data)}", exc_info=e)

    cache.last_output = int(datetime.now().timestamp())
