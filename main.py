import asyncio
import ctypes
import logging
import os
import platform
import sys
import time as time_module
import typing as t
from importlib import metadata as importlib_metadata
from itertools import cycle
from pathlib import Path

import psutil
import uvicorn

from app.api import create_app
from app.config import load_config
from app.constants import VERSION
from app.enrichment import enrich_tribes
from app.loader import (
    apply_player_pawn_state,
    parse_all_data,
    parse_cluster_data,
    parse_cluster_inventory_file,
    parse_profile_data,
    parse_tribe_data,
    parse_world_data,
)
from app.state import state
from app.watcher import ReparseScope, watcher

log = logging.getLogger("arkviewer")

IS_WINDOWS = sys.platform.startswith("win")
IS_FROZEN = getattr(sys, "frozen", False)

# Animated progress bar used by ``title_loop``.
TITLE_BAR_FRAMES = (
    "▱▱▱▱▱▱▱",
    "▰▱▱▱▱▱▱",
    "▰▰▱▱▱▱▱",
    "▰▰▰▱▱▱▱",
    "▰▰▰▰▱▱▱",
    "▰▰▰▰▰▱▱",
    "▰▰▰▰▰▰▱",
    "▰▰▰▰▰▰▰",
    "▱▰▰▰▰▰▰",
    "▱▱▰▰▰▰▰",
    "▱▱▱▰▰▰▰",
    "▱▱▱▱▰▰▰",
    "▱▱▱▱▱▰▰",
    "▱▱▱▱▱▱▰",
)


LOGO = r"""
                _  __      ___
     /\        | | \ \    / (_)
    /  \   _ __| | _\ \  / / _  _____      _____ _ __
   / /\ \ | '__| |/ /\ \/ / | |/ _ \ \ /\ / / _ \ '__|
  / ____ \| |  |   <  \  /  | |  __/\ V  V /  __/ |
 /_/    \_\_|  |_|\_\  \/   |_|\___| \_/\_/ \___|_|
"""


def _arkparser_version() -> str:
    """Return the installed arkparser package version (or 'unknown').

    Primary source is ``importlib.metadata.version`` which reads the package's
    dist-info. The PyInstaller hook (extra-hooks/hook-arkparser.py) copies
    that metadata into the frozen exe. If for any reason the dist-info isn't
    available, fall back to ``arkparser.__version__`` (note: that constant
    can lag behind the installed version when upstream forgets to bump it).
    """
    try:
        return importlib_metadata.version("arkparser")
    except importlib_metadata.PackageNotFoundError:
        try:
            import arkparser

            return f"{getattr(arkparser, '__version__', 'unknown')} (from __version__)"
        except Exception:
            return "unknown"


def disable_quickedit() -> None:
    """Disable QuickEdit mode on the Windows console.

    When QuickEdit is enabled, clicking inside the console window enters
    selection mode and blocks all stdout writes, freezing the entire
    asyncio event loop until the user presses Enter.
    """
    try:
        kernel32 = ctypes.windll.kernel32
        STD_INPUT_HANDLE = ctypes.c_ulong(-10)
        ENABLE_QUICK_EDIT = 0x0040
        ENABLE_EXTENDED_FLAGS = 0x0080
        handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
        mode = ctypes.c_ulong()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        mode.value &= ~ENABLE_QUICK_EDIT
        mode.value |= ENABLE_EXTENDED_FLAGS
        kernel32.SetConsoleMode(handle, mode)
    except Exception:
        pass


def print_banner() -> None:
    """Print the ASCII logo + versions + host info to stdout once at startup.

    Purpose: give the operator an at-a-glance summary when arkviewer launches:
    which build is running, which arkparser it's linked against, what Python
    interpreter, and what host resources are available. Not a log line - just
    a print so it isn't prefixed with a timestamp and goes out as a single
    visual block.
    Preconditions: psutil installed (it's a hard runtime dependency anyway).
    Postconditions: writes the banner to stdout and returns. No side effects
    beyond stdout writes.
    """
    py = sys.version_info
    vm = psutil.virtual_memory()
    cpu_logical = psutil.cpu_count(logical=True) or 0
    cpu_physical = psutil.cpu_count(logical=False) or 0
    rows = (
        ("ArkViewer", VERSION),
        ("arkparser", _arkparser_version()),
        (
            "Python",
            f"{py.major}.{py.minor}.{py.micro} ({platform.python_implementation()})",
        ),
        ("Platform", platform.platform()),
        ("CPU", f"{cpu_logical} logical / {cpu_physical} physical"),
        (
            "RAM",
            f"{vm.total / (1024 ** 3):.1f} GB total, {vm.available / (1024 ** 3):.1f} GB available",
        ),
        ("PID", str(os.getpid())),
        ("CWD", os.getcwd()),
    )
    sys.stdout.write(LOGO)
    for key, value in rows:
        sys.stdout.write(f"  {key:<10}: {value}\n")
    sys.stdout.write("\n")
    sys.stdout.flush()


class _CompactFormatter(logging.Formatter):
    """Strips the ``arkviewer.`` namespace prefix from logger names so each line
    stays narrow enough to read without console wrapping."""

    def format(self, record: logging.LogRecord) -> str:
        if record.name == "arkviewer":
            record.name = "app"
        elif record.name.startswith("arkviewer."):
            record.name = record.name[len("arkviewer.") :]
        return super().format(record)


def _resolve_log_dir() -> Path:
    """Pick the directory the log file lives in.

    - Frozen exe: same directory as the .exe (where ``config.ini`` already lives).
    - From source: the repo root.
    Matches ``app.config._resolve_root_dir`` so logs and config are siblings.
    """
    if IS_FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def init_logging() -> None:
    """Initial logger setup - INFO by default. Bumped to DEBUG later if config has Debug=True.

    Writes to ``arkviewer.log`` in the same directory as the exe (or repo
    root in dev mode). The log rotates at 2 MB with 3 backups kept so the
    file never grows unbounded on long-running servers.
    """
    formatter = _CompactFormatter(
        fmt="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)

    log_path = _resolve_log_dir() / "arkviewer.log"
    file_handler: logging.Handler | None
    try:
        from logging.handlers import RotatingFileHandler

        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
    except OSError:
        # Best-effort: if the exe's directory is read-only or otherwise
        # un-writable, fall back to stdout-only so the app still runs.
        file_handler = None

    root = logging.getLogger()
    # Don't double-up handlers if init_logging is called twice (e.g. tests).
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(stream_handler)
    if file_handler is not None:
        root.addHandler(file_handler)
    root.setLevel(logging.INFO)

    if file_handler is not None:
        logging.getLogger("arkviewer").info("Log file: %s", log_path)
    else:
        logging.getLogger("arkviewer").warning(
            "Could not open log file %s for writing - logs will only go to stdout.",
            log_path,
        )

    # Watchdog spams every file-system event at DEBUG; keep it muted.
    logging.getLogger("watchdog").setLevel(logging.WARNING)
    # uvicorn.access stays at INFO so incoming HTTP requests show up.
    logging.getLogger("uvicorn").setLevel(logging.INFO)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)


def reconfigure_logging(debug: bool) -> None:
    """Bump arkviewer.* loggers to DEBUG when Debug=True in config.

    Leaves watchdog on WARNING (otherwise every internal poll spams the console);
    bumps uvicorn loggers to DEBUG so request/response timing also surfaces.
    """
    target = logging.DEBUG if debug else logging.INFO
    for name in (
        "arkviewer",
        "arkviewer.watcher",
        "arkviewer.loader",
        "arkviewer.main",
        "arkviewer.config",
        "arkviewer.auth",
        "arkviewer.banlist",
    ):
        logging.getLogger(name).setLevel(target)
    if debug:
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            logging.getLogger(name).setLevel(logging.DEBUG)
        log.debug(
            "Debug logging enabled - verbose watcher + loader output will follow."
        )


def _format_process_ram(rss_bytes: int) -> str:
    """Compact RAM display for the title bar: '823MB' below 1 GB, '1.4GB' above."""
    mb = rss_bytes / (1024 * 1024)
    if mb < 1024:
        return f"{mb:.0f}MB"
    return f"{mb / 1024:.1f}GB"


async def title_loop() -> None:
    """Animate the console window title with map name + RAM + CPU% + progress bar.

    Only runs when the app is frozen on Windows (i.e. running as the released
    .exe) - there's no terminal title to update when running from a Python
    interpreter on Linux/macOS, and updating it while developing is noise.
    """
    if not (IS_FROZEN and IS_WINDOWS):
        return

    set_title = ctypes.windll.kernel32.SetConsoleTitleW
    frames = cycle(TITLE_BAR_FRAMES)
    # Cache the Process handle once; psutil.Process() with no args binds to
    # the current PID and reuses an internal handle on subsequent calls.
    proc = psutil.Process(os.getpid())
    # Seed cpu_percent so the first reading isn't 0.0. ``cpu_percent(None)``
    # uses the wall-clock interval since the previous call; we measure it
    # every 150 ms in the loop below, normalized to "% of one CPU" by
    # dividing by the logical-core count so a fully-pegged process never
    # shows >100% (Windows Task Manager convention).
    proc.cpu_percent(interval=None)
    cpu_count = psutil.cpu_count(logical=True) or 1
    # Give the rest of startup a head start so config + initial parse trigger
    # before the first frame paints.
    await asyncio.sleep(1)

    while True:
        try:
            cfg = state.config
            map_name = cfg.map_file.stem if cfg.map_file else "no map"
            ram = _format_process_ram(proc.memory_info().rss)
            cpu = proc.cpu_percent(interval=None) / cpu_count
            frame = next(frames)
            title = f"ArkViewer {VERSION} - {map_name} [{ram} | {cpu:.0f}%] {frame}"
            if state.syncing:
                title += " [Parsing]"
            set_title(title)
        except Exception:
            # Title updates are cosmetic; never let a transient error tear the loop down.
            pass
        await asyncio.sleep(0.15)


def replace_by_data_file(items: list[t.Any], item: t.Any) -> list[t.Any]:
    updated: list[t.Any] = []
    replaced = False

    for existing in items:
        if getattr(existing, "data_file", "") == getattr(item, "data_file", ""):
            updated.append(item)
            replaced = True
            continue
        updated.append(existing)

    if not replaced:
        updated.append(item)

    return updated


def remove_by_data_file(items: list[t.Any], data_file: str) -> list[t.Any]:
    return [item for item in items if getattr(item, "data_file", "") != data_file]


class Manager:
    def __init__(self) -> None:
        self.server: uvicorn.Server | None = None
        self.reparse_lock = asyncio.Lock()

    async def refresh_all(self) -> None:
        log.info("Initial full parse starting (this can take 30-60s for a busy map)...")
        t0 = time_module.perf_counter()
        # Flip syncing for the duration of the initial parse so the title-bar
        # animation appends [Parsing] and clears it when we're done. Use
        # try/finally so a parse-time exception still releases the flag.
        state.syncing = True
        try:
            result = await asyncio.to_thread(parse_all_data, state.config)
            state.data = result["data"]
            state.day = result["day"]
            state.time = result["time"]
            state.player_pawn_state = result.get("player_pawn_state") or {}
            enrich_tribes(
                state.data.tribes,
                state.data.tamed,
                state.data.structures,
                state.data.players,
            )
            state.last_export = time_module.time()
        finally:
            state.syncing = False
        log.info(
            "Initial parse complete in %.1fs - tamed=%d wild=%d players=%d tribes=%d "
            "structures=%d tribelogs=%d mapstructures=%d cloud=%d (day=%d, time=%s)",
            time_module.perf_counter() - t0,
            len(state.data.tamed),
            len(state.data.wild),
            len(state.data.players),
            len(state.data.tribes),
            len(state.data.structures),
            len(state.data.tribelogs),
            len(state.data.mapstructures),
            len(state.data.cloud_inventory),
            state.day,
            state.time,
        )

    async def reparse(
        self,
        scopes: frozenset[ReparseScope],
        changed_paths: frozenset[Path] = frozenset(),
    ) -> None:
        async with self.reparse_lock:
            state.syncing = True
            scope_names = ",".join(sorted(s.value for s in scopes))
            log.info(
                "Reparse triggered (scopes=%s, %d paths)",
                scope_names,
                len(changed_paths),
            )
            t0 = time_module.perf_counter()
            counters: dict[str, int] = {}
            try:
                if ReparseScope.SAVE in scopes and state.config.map_file is not None:
                    log.debug(
                        "SAVE: re-parsing world save %s", state.config.map_file.name
                    )
                    save_t0 = time_module.perf_counter()
                    world_result = await asyncio.to_thread(
                        parse_world_data, state.config
                    )
                    if world_result is not None:
                        state.data.tamed = world_result["tamed"]
                        state.data.wild = world_result["wild"]
                        state.data.structures = world_result["structures"]
                        state.data.mapstructures = world_result["mapstructures"]
                        state.day = world_result["day"]
                        state.time = world_result["time"]
                        # Refresh the cached pawn state and re-apply to current
                        # players so positions/levels/stats update on every
                        # SAVE reparse without needing to re-parse profiles.
                        state.player_pawn_state = (
                            world_result.get("player_pawn_state") or {}
                        )
                        apply_player_pawn_state(
                            state.data.players, state.player_pawn_state
                        )
                        log.debug(
                            "SAVE done in %.1fs - tamed=%d wild=%d structures=%d mapstructures=%d (day=%d %s)",
                            time_module.perf_counter() - save_t0,
                            len(state.data.tamed),
                            len(state.data.wild),
                            len(state.data.structures),
                            len(state.data.mapstructures),
                            state.day,
                            state.time,
                        )
                        counters["tamed"] = len(state.data.tamed)
                        counters["wild"] = len(state.data.wild)
                        counters["structures"] = len(state.data.structures)

                if ReparseScope.PROFILE in scopes:
                    profile_paths = sorted(
                        path for path in changed_paths if path.suffix == ".arkprofile"
                    )
                    log.debug("PROFILE: %d file(s) changed", len(profile_paths))
                    for path in profile_paths:
                        if not path.exists():
                            log.debug("PROFILE removed: %s", path.name)
                            state.data.players = remove_by_data_file(
                                state.data.players, path.name
                            )
                            continue

                        player = await asyncio.to_thread(
                            parse_profile_data,
                            path,
                            state.config,
                            state.player_pawn_state,
                        )
                        if player is None:
                            log.debug(
                                "PROFILE %s: parse returned None, skipping", path.name
                            )
                            continue
                        log.debug(
                            "PROFILE updated: %s (steam_id=%s, level=%d)",
                            path.name,
                            getattr(player, "steam_id", "?"),
                            getattr(player, "level", 0),
                        )
                        state.data.players = replace_by_data_file(
                            state.data.players, player
                        )
                    counters["players"] = len(state.data.players)

                if ReparseScope.TRIBE in scopes:
                    tribe_paths = sorted(
                        path
                        for path in changed_paths
                        if path.suffix in {".arktribe", ".arktributetribe"}
                    )
                    log.debug("TRIBE: %d file(s) changed", len(tribe_paths))
                    for path in tribe_paths:
                        if not path.exists():
                            log.debug("TRIBE removed: %s", path.name)
                            state.data.tribes = remove_by_data_file(
                                state.data.tribes, path.name
                            )
                            state.data.tribelogs = remove_by_data_file(
                                state.data.tribelogs, path.name
                            )
                            continue

                        tribe_result = await asyncio.to_thread(
                            parse_tribe_data, path, state.config
                        )
                        tribe = tribe_result["tribe"]
                        tribelog = tribe_result["tribelog"]

                        if tribe is not None:
                            log.debug(
                                "TRIBE updated: %s (tribe_id=%s, name=%r)",
                                path.name,
                                getattr(tribe, "tribe_id", "?"),
                                getattr(tribe, "name", ""),
                            )
                            state.data.tribes = replace_by_data_file(
                                state.data.tribes, tribe
                            )
                        if tribelog is not None:
                            state.data.tribelogs = replace_by_data_file(
                                state.data.tribelogs, tribelog
                            )
                    counters["tribes"] = len(state.data.tribes)

                if ReparseScope.CLUSTER in scopes:
                    cluster_dir = state.config.cluster_dir
                    if cluster_dir is not None and cluster_dir.exists():
                        cluster_paths = sorted(
                            path
                            for path in changed_paths
                            if not path.suffix and path.parent == cluster_dir
                        )

                        if not cluster_paths:
                            log.debug(
                                "CLUSTER: no specific paths given, doing full rescan of %s",
                                cluster_dir,
                            )
                            state.data.cloud_inventory = await asyncio.to_thread(
                                parse_cluster_data, state.config
                            )
                        else:
                            log.debug("CLUSTER: %d file(s) changed", len(cluster_paths))
                            for path in cluster_paths:
                                if not path.exists():
                                    log.debug("CLUSTER removed: %s", path.name)
                                    state.data.cloud_inventory.pop(path.stem, None)
                                    continue

                                inventory = await asyncio.to_thread(
                                    parse_cluster_inventory_file, path, state.config
                                )
                                if inventory is not None:
                                    log.debug("CLUSTER updated: %s", path.name)
                                    state.data.cloud_inventory[path.stem] = inventory
                        counters["cloud_inventory"] = len(state.data.cloud_inventory)

                enrich_tribes(
                    state.data.tribes,
                    state.data.tamed,
                    state.data.structures,
                    state.data.players,
                )
                state.last_export = time_module.time()
                summary = " ".join(f"{k}={v}" for k, v in counters.items())
                log.info(
                    "Reparse complete in %.1fs (scopes=%s)%s",
                    time_module.perf_counter() - t0,
                    scope_names,
                    f" - {summary}" if summary else "",
                )
            except Exception as exc:
                log.error("Reparse failed: %s", exc, exc_info=True)
            finally:
                state.syncing = False

    async def start(self) -> None:
        state.config = load_config()
        reconfigure_logging(state.config.debug)
        log.info("ArkViewer v%s starting (PID %d)", VERSION, os.getpid())

        loop = asyncio.get_running_loop()
        watcher.start(
            loop=loop,
            callback=self.reparse,
            map_file=state.config.map_file,
            cluster_dir=state.config.cluster_dir,
        )

        asyncio.create_task(self.refresh_all())
        # Animate the console title bar (only on frozen Windows exe).
        asyncio.create_task(title_loop())

        uv_config = uvicorn.Config(
            app=create_app(),
            host="0.0.0.0",
            port=state.config.port,
            loop="none",
            # log_config=None tells uvicorn not to install its own handlers; its
            # log records propagate through the root logger we set up in
            # init_logging() and get our compact format.
            log_config=None,
            access_log=True,
        )
        self.server = uvicorn.Server(uv_config)
        log.info("Listening on 0.0.0.0:%d", state.config.port)
        await self.server.serve()

    async def shutdown(self) -> None:
        log.info("Shutting down...")
        watcher.stop()
        if self.server is not None:
            self.server.should_exit = True

    @classmethod
    def run(cls) -> None:
        disable_quickedit()
        # Banner before init_logging so it prints as a clean block without
        # log-line timestamps prefixed to every row.
        print_banner()
        init_logging()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        manager = cls()

        try:
            loop.run_until_complete(manager.start())
        except KeyboardInterrupt:
            print("Interrupted - shutting down...")
        except Exception as e:
            log.critical("Fatal error", exc_info=e)
        finally:
            if not loop.is_closed():
                loop.run_until_complete(manager.shutdown())
                loop.close()


if __name__ == "__main__":
    if getattr(sys, "frozen", False):
        os.chdir(Path(sys.executable).parent)

    Manager.run()
