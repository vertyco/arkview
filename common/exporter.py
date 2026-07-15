import asyncio
import logging
import multiprocessing
import os
from hashlib import md5
from time import perf_counter

import orjson
import psutil

from common.constants import IS_WINDOWS
from common.models import cache  # noqa
from common.parse_worker import run_export

log = logging.getLogger("arkview.exporter")

# Hard ceiling on a single parse (matches the old 15-minute wait budget).
EXPORT_TIMEOUT: int = 900


def apply_child_limits(pid: int, threads: int, priority: str) -> None:
    """Best-effort cap on the parse child so it can't starve the game server.

    Mirrors the old ``start /<priority> /AFFINITY`` (Windows) and ``taskset``
    (Linux) behaviour using psutil. Silently skips on any error -- the child
    may exit before we attach, and throttling is an optimisation, not a
    correctness requirement.
    """
    try:
        proc = psutil.Process(pid)
        cores = list(range(max(1, min(threads, os.cpu_count() or 1))))
        try:
            proc.cpu_affinity(cores)
        except (psutil.AccessDenied, NotImplementedError, AttributeError):
            pass
        if IS_WINDOWS:
            classes = {
                "LOW": psutil.IDLE_PRIORITY_CLASS,
                "BELOWNORMAL": psutil.BELOW_NORMAL_PRIORITY_CLASS,
                "NORMAL": psutil.NORMAL_PRIORITY_CLASS,
                "ABOVENORMAL": psutil.ABOVE_NORMAL_PRIORITY_CLASS,
                "HIGH": psutil.HIGH_PRIORITY_CLASS,
            }
            proc.nice(classes.get(priority, psutil.BELOW_NORMAL_PRIORITY_CLASS))
        else:
            proc.nice(10)
    except psutil.Error as e:
        log.debug("Could not apply child limits to pid %s: %s", pid, e)


# {file_name: last_modified}
last_file_states: dict[str, int] | None = None


async def scan_cluster_dir() -> bool:
    """Scan all ark data files in the cluster directory (they have no suffix)
    Returns:
        bool: True if any file in the cluster directory has been modified since the last scan
    """
    global cache
    global last_file_states
    if not cache.cluster_dir:
        return False
    if not cache.cluster_dir.exists():
        return False
    if last_file_states is None:
        # Initialize the last_file_states
        last_file_states = {}
        for file in cache.cluster_dir.glob("*"):
            if file.suffix:
                continue
            last_file_states[file.name] = int(file.stat().st_mtime)
        log.info(f"Initialized {len(last_file_states)} files in cluster directory")
        return False

    # Check if any file has been modified
    modified = False
    current_files = set()
    for file in cache.cluster_dir.glob("*"):
        if file.suffix:
            continue
        current_files.add(file.name)
        if file.name not in last_file_states:
            last_file_states[file.name] = int(file.stat().st_mtime)
            modified = True
            continue
        if last_file_states[file.name] != int(file.stat().st_mtime):
            modified = True
            break

    # Check if any file has been deleted
    for file in list(last_file_states.keys()):
        if file not in current_files:
            del last_file_states[file]
            modified = True

    return modified


async def export_loop():
    global cache
    global last_file_states
    if (
        cache.reprocess_on_arkdata_update
        and cache.cluster_dir
        and cache.cluster_dir.exists()
    ):
        # Initialize the last_file_states
        await scan_cluster_dir()

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


async def wipe_output():
    global cache
    to_delete = list(cache.output_dir.glob("*.json"))
    if to_delete:
        log.info(f"Wiping {len(to_delete)} files from output directory")
    for file in to_delete:
        try:
            file.unlink(missing_ok=True)
        except Exception as e:
            log.error(f"Failed to delete {file.name}", exc_info=e)
    if cache.exports:
        cache.exports.clear()
        log.info("Cleared exports")


async def _process_export():
    global cache
    map_file = cache.map_file
    if not map_file or not map_file.exists():
        log.warning("No map file found")
        await wipe_output()
        return
    cluster_dir = cache.cluster_dir
    if cluster_dir and not cluster_dir.exists():
        log.warning("Cluster is set but the specified path does not exist")
    cache.output_dir.mkdir(exist_ok=True)

    map_file_modified = map_file.stat().st_mtime

    if cache.map_last_modified:
        if int(cache.map_last_modified) == int(map_file_modified):
            # Map file hasnt updated yet, check if any of the cluster files have been updated
            if (
                cache.reprocess_on_arkdata_update
                and cache.cluster_dir
                and cache.cluster_dir.exists()
            ):
                updated = await scan_cluster_dir()
                if not updated:
                    return
                log.info("Cluster files have been updated, re-exporting")
            else:
                return
        else:
            log.info("Map file has been updated, re-exporting")

    cache.map_last_modified = map_file_modified

    # Threads + priority throttle the parse child so it can't starve the game server.
    available_cores = os.cpu_count() or 2
    threads = max(min(available_cores, cache.threads), 2)
    priority = cache.priority  # LOW, BELOWNORMAL, NORMAL, ABOVENORMAL, HIGH

    # Parse out-of-process: a spawn child writes ASV_*.json then exits, so the
    # multi-GB arkparser object graph is reclaimed by the OS and the long-lived
    # server process stays lean.
    cluster_arg = str(cluster_dir) if cluster_dir else None
    ctx = multiprocessing.get_context("spawn")
    proc = ctx.Process(
        target=run_export,
        args=(str(map_file), str(cache.output_dir), cluster_arg, priority),
        name="arkview-parser",
        daemon=False,
    )
    start = perf_counter()
    proc.start()
    cache.parse_pid = proc.pid
    apply_child_limits(proc.pid, threads, priority)
    log.info("Parse worker started with PID %s", proc.pid)
    try:
        await asyncio.to_thread(proc.join, EXPORT_TIMEOUT)
        if proc.is_alive():
            proc.terminate()
            await asyncio.to_thread(proc.join, 5)
            log.warning(
                "Parse timed out after %ss; worker terminated",
                int(perf_counter() - start),
            )
        elif proc.exitcode != 0:
            log.error("Parse worker exited with code %s", proc.exitcode)
        else:
            log.info("Export completed in %s seconds", int(perf_counter() - start))
    except Exception as e:
        log.error("Parse worker failed", exc_info=e)
    finally:
        cache.parse_pid = None

    try:
        await load_outputs()
    except Exception as e:
        log.error("Failed to load outputs", exc_info=e)


async def load_outputs(target: str = ""):
    global cache

    asv_players = cache.output_dir / "ASV_Players.json"
    if asv_players.exists():
        cache.last_export = asv_players.stat().st_mtime

    files = list(cache.output_dir.glob("*.json"))
    for export_file in files:
        key = export_file.stem.replace("ASV_", "").lower().strip()
        if target and target.lower() != key:
            continue

        # Before reading the file, make sure it is not being accessed by another process
        waiting = 0
        while export_file.stat().st_size == 0:
            await asyncio.sleep(1)
            waiting += 1
            if waiting > 10:
                break

        if waiting > 10:
            log.error(
                f"Failed to load {export_file.name}, file remained empty after 10 seconds"
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
