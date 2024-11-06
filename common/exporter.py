import asyncio
import logging
import os
import subprocess
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
    if cache.syncing:
        return
    try:
        cache.syncing = True
        await _process_export()
    finally:
        cache.syncing = False


async def _process_export():
    global cache
    if not cache.map_file.exists():
        return
    if not cache.exe_file.exists():
        return
    if cache.cluster_dir and not cache.cluster_dir.exists():
        return
    cache.output_dir.mkdir(exist_ok=True)

    map_file_modified = cache.map_file.stat().st_mtime

    if cache.last_export:
        if int(cache.last_export) == int(map_file_modified):
            # Map file hasnt updated yet
            return
        log.info("Map file has been updated, re-exporting")

    # last export is the last modified time of the first json file in the output directory
    for path in cache.output_dir.glob("*.json"):
        if path.stat().st_mtime > cache.last_export:
            cache.last_export = path.stat().st_mtime
            break

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

    try:
        await load_outputs()
    except Exception as e:
        log.error("Failed to load outputs", exc_info=e)


async def load_outputs(target: str = ""):
    global cache

    files = list(cache.output_dir.glob("*.json"))
    for export_file in files:
        key = export_file.stem.replace("ASV_", "").lower().strip()
        if target and target.lower() != key:
            continue

        # Before reading the file, make sure it is not being accessed by another process
        waiting = 0
        while export_file.stat().st_size == 0:
            await asyncio.sleep(6)
            waiting += 1
            if waiting > 10:
                break

        if waiting > 10:
            log.error(
                f"Failed to load {export_file.name}, waited too long for it to be written"
            )
            continue

        raw_file = export_file.read_bytes()

        log.debug(f"Loading {export_file.name}")
        try:
            dump = orjson.loads(raw_file)
        except Exception as e:
            log.error(f"Failed to load {export_file.name}", exc_info=e)
            continue

        if not dump:
            log.error(f"No data found in {export_file.name}")
            continue

        def _precache(data: dict):
            first_run = not cache.tribelog_buffer
            new_tribelog_payload = []
            for i in data["data"]:
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
            if first_run:
                log.info(
                    f"First run, pre-cached {len(cache.tribelog_buffer)} tribe logs"
                )
            data["data"] = new_tribelog_payload
            return data

        if key == "tribelogs":
            dump = await asyncio.to_thread(_precache, dump)

        try:
            cache.exports[key] = dump
        except Exception as e:
            log.error(f"Failed to cache export: {type(dump)}", exc_info=e)
