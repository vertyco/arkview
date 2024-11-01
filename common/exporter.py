import asyncio
import logging
import os
import subprocess
from datetime import datetime
from hashlib import md5
from pathlib import Path

import orjson

from common.constants import IS_WINDOWS
from common.models import cache  # noqa
from common.utils import get_affinity_mask, wait_for_process

log = logging.getLogger("arkview.exporter")


async def export_loop():
    global cache
    if isinstance(cache.map_file, str):
        cache.map_file = Path(cache.map_file)
    while True:
        try:
            await process_export()
            await asyncio.sleep(5)
        except Exception as e:
            log.error("Export failed", exc_info=e)
            await asyncio.sleep(15)


async def process_export():
    global cache
    now = datetime.now().timestamp()
    if not cache.map_file.exists():
        return
    if not cache.exe_file.exists():
        return
    if cache.cluster_dir and not cache.cluster_dir.exists():
        return
    cache.output_dir.mkdir(exist_ok=True)

    map_file_modified = cache.map_file.stat().st_mtime

    if cache.last_export:
        # Run a couple checks to see if we don't need to export
        delta = now - cache.last_export
        if cache.last_export >= map_file_modified and delta < 1800:
            # If the map file hasn't been modified and it's been less than 30 minutes, don't export
            await asyncio.sleep(5)
            return

        log.info("Map file has been updated, re-exporting")

    # Run exporter
    cache.last_export = map_file_modified

    # Threads should be equal to half of the total CPU threads
    available_cores = os.cpu_count() or 1
    threads = min(available_cores, cache.threads)
    priority = cache.priority  # LOW, BELOWNORMAL, NORMAL, ABOVENORMAL, HIGH

    # ASVExport.exe all "path/to/map/file" "path/to/cluster" "path/to/output/folder"
    # ASVExport.exe all "C:\Users\Vert\Documents\Projects-Local\arkviewer\testdata\map_ase\Ragnarok.ark" "C:\Users\Vert\Documents\Projects-Local\arkviewer\testdata\solecluster_ase\" "C:\Users\Vert\Desktop\output\"
    # ASVExport.exe all "C:\Users\Vert\Documents\Projects-Local\arkviewer\testdata\map_asa\TheIsland_WP.ark" "C:\Users\Vert\Documents\Projects-Local\arkviewer\testdata\solecluster_asa\" "C:\Users\Vert\Desktop\output\"
    if IS_WINDOWS:
        mask = get_affinity_mask(threads)
        command = [
            "start",
            f"/{priority}",
            "/MIN",
            "/AFFINITY",
            mask,
            str(cache.exe_file),
            "all",
            f'"{cache.map_file}"',
        ]
        if cdir := cache.cluster_dir:
            command.append(f'"{cdir}\\"')
        command.append(f'"{cache.output_dir}\\"')
    else:
        cpu_range = f"0-{threads - 1}" if threads > 1 else "0"
        command = [
            "taskset",
            "-c",
            cpu_range,
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
            os.system(" ".join(command))
        else:
            # Ensure all the paths have r/w and execute permissions
            cache.exe_file.chmod(0o777)
            cache.map_file.chmod(0o777)
            cache.output_dir.chmod(0o777)
            result = subprocess.run(
                command,
                check=True,
                text=True,
                capture_output=True,
            )
            if stdout := result.stdout:
                log.info(stdout)
            if stderr := result.stderr:
                log.info(stderr)
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

        def _precache(data):
            first_run = not cache.tribelog_buffer
            if first_run:
                log.info("Pre-caching tribe logs")
            new_tribelog_payload = []
            for i in data:
                if "logs" not in i:
                    continue
                tribe_id = i.get("tribeid")
                if not tribe_id:
                    continue
                new_logs = []
                for entry in i["logs"]:
                    key = md5(f"{tribe_id}{entry}".encode()).hexdigest()
                    if key in cache.tribelog_buffer:
                        continue
                    cache.tribelog_buffer.add(key)
                    if not first_run:
                        new_logs.append(entry)
                if new_logs:
                    i["logs"] = new_logs
                    new_tribelog_payload.append(i)
            data = new_tribelog_payload
            return data

        if key == "tribelogs":
            data = await asyncio.to_thread(_precache, data)

        try:
            cache.exports[key] = data
            log.info(f"Cached {export_file.name}")
        except Exception as e:
            log.error(f"Failed to cache export: {type(data)}", exc_info=e)

    cache.last_output = int(datetime.now().timestamp())
