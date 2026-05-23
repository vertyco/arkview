"""Console / banner / colored-logging helpers for the frozen exe.

Layered helpers (call order in main.py):
  1. enable_console_vt()  - ANSI escape rendering on Win10+ cmd.exe
  2. disable_quickedit()  - stops cmd.exe selection from freezing event loop
  3. init_logging()       - root logger + colored stream + rotating file
  4. print_banner()       - one-shot ASCII logo + version/host info
  5. title_loop()         - animates console title bar (async task)

Windows-specific helpers no-op on non-Windows.
"""

import asyncio
import ctypes
import logging
import os
import platform
import sys
import typing as t
from itertools import cycle
from logging.handlers import RotatingFileHandler
from pathlib import Path

import psutil

from app.constants import ARKPARSER_VERSION, VERSION

IS_WINDOWS: t.Final[bool] = sys.platform.startswith("win")
IS_FROZEN: t.Final[bool] = getattr(sys, "frozen", False)


LOGO: t.Final[
    str
] = r"""
                _  __      ___
     /\        | | \ \    / (_)
    /  \   _ __| | _\ \  / / _  _____      _____ _ __
   / /\ \ | '__| |/ /\ \/ / | |/ _ \ \ /\ / / _ \ '__|
  / ____ \| |  |   <  \  /  | |  __/\ V  V /  __/ |
 /_/    \_\_|  |_|\_\  \/   |_|\___| \_/\_/ \___|_|
"""


LEVEL_COLORS: t.Final[dict[str, str]] = {
    "DEBUG": "\x1b[36m",
    "INFO": "\x1b[32m",
    "WARNING": "\x1b[33m",
    "ERROR": "\x1b[31m",
    "CRITICAL": "\x1b[1;31m",
}
ANSI_RESET: t.Final[str] = "\x1b[0m"
NAME_WIDTH: t.Final[int] = 8
LEVEL_WIDTH: t.Final[int] = 7

ARKVIEWER_LOGGERS: t.Final[tuple[str, ...]] = (
    "arkviewer",
    "arkviewer.watcher",
    "arkviewer.ingest",
    "arkviewer.main",
    "arkviewer.config",
    "arkviewer.banlist",
    "arkviewer.db",
)
UVICORN_LOGGERS: t.Final[tuple[str, ...]] = (
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
)

TITLE_FRAMES: t.Final[tuple[str, ...]] = (
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
TITLE_TICK_S: t.Final[float] = 0.15

log = logging.getLogger("arkviewer.console")


def disable_quickedit() -> None:
    """Turn off QuickEdit on the Windows console.

    QuickEdit pauses every stdout write while a user click holds selection
    mode — that pause blocks the asyncio loop. No-op on non-Windows.
    """
    if not IS_WINDOWS:
        return
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
    except (OSError, AttributeError) as exc:
        log.debug("disable_quickedit failed: %s", exc)


def enable_console_vt() -> None:
    """Enable ANSI VT escape processing on the Windows console.

    Win10+ supports VT but the flag is off by default for fresh consoles
    (PyInstaller exes); without it color codes render as literal text.
    No-op on non-Windows.
    """
    if not IS_WINDOWS:
        return
    try:
        kernel32 = ctypes.windll.kernel32
        STD_OUTPUT_HANDLE = ctypes.c_ulong(-11)
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        ENABLE_PROCESSED_OUTPUT = 0x0001
        for handle_id in (STD_OUTPUT_HANDLE, ctypes.c_ulong(-12)):  # stdout + stderr
            h = kernel32.GetStdHandle(handle_id)
            mode = ctypes.c_ulong()
            if not kernel32.GetConsoleMode(h, ctypes.byref(mode)):
                continue
            kernel32.SetConsoleMode(
                h,
                mode.value
                | ENABLE_VIRTUAL_TERMINAL_PROCESSING
                | ENABLE_PROCESSED_OUTPUT,
            )
    except (OSError, AttributeError) as exc:
        log.debug("enable_console_vt failed: %s", exc)


def short_logger_name(name: str) -> str:
    if name == "arkviewer":
        name = "app"
    elif name.startswith("arkviewer."):
        name = name[len("arkviewer.") :]
    elif name.startswith("uvicorn"):
        name = "uvicorn"
    return name.ljust(NAME_WIDTH)[:NAME_WIDTH]


class CompactFormatter(logging.Formatter):
    """Console + file formatter that does NOT mutate the shared LogRecord.

    Multiple handlers share each record; mutating attributes (e.g.
    `levelname`, `exc_text`) leaks formatting decisions across handlers.
    Build everything from locals.
    """

    def __init__(self, *, use_color: bool, datefmt: str | None = None) -> None:
        super().__init__(datefmt=datefmt)
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, self.datefmt)
        name = short_logger_name(record.name)
        level_text = record.levelname
        if self.use_color and (color := LEVEL_COLORS.get(level_text)):
            padding = " " * max(0, LEVEL_WIDTH - len(level_text))
            level_display = f"{color}{level_text}{ANSI_RESET}{padding}"
        else:
            level_display = level_text.ljust(LEVEL_WIDTH)
        msg = record.getMessage()
        if record.exc_info:
            exc_text = record.exc_text or self.formatException(record.exc_info)
            msg = f"{msg}\n{exc_text}"
        if record.stack_info:
            msg = f"{msg}\n{self.formatStack(record.stack_info)}"
        return f"{ts} {level_display} {name} | {msg}"


def resolve_log_dir() -> Path:
    """Log lives next to the exe (frozen) or in repo root (dev)."""
    if IS_FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def build_file_handler(log_path: Path) -> logging.Handler | None:
    formatter = CompactFormatter(use_color=False, datefmt="%Y-%m-%d %H:%M:%S")
    try:
        handler = RotatingFileHandler(
            log_path,
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
    except OSError:
        return None
    handler.setFormatter(formatter)
    return handler


def init_logging() -> None:
    """Configure root logger with compact + colored stream handler.

    Stream colored on a TTY, plain otherwise (CI capture / pipe redirect).
    File handler rotates at 2 MB × 3 backups. Idempotent.
    """
    enable_console_vt()

    is_tty = sys.stdout.isatty() if hasattr(sys.stdout, "isatty") else False
    stream_formatter = CompactFormatter(use_color=is_tty, datefmt="%H:%M:%S")
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(stream_formatter)

    log_path = resolve_log_dir() / "arkviewer.log"
    file_handler = build_file_handler(log_path)

    root = logging.getLogger()
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
            "Could not open log file %s for writing — logs will only go to stdout.",
            log_path,
        )

    logging.getLogger("watchdog").setLevel(logging.WARNING)
    for name in UVICORN_LOGGERS:
        logging.getLogger(name).setLevel(logging.INFO)


def reconfigure_logging(debug: bool) -> None:
    """Bump arkviewer.* + uvicorn loggers to DEBUG when Debug=True in config."""
    target = logging.DEBUG if debug else logging.INFO
    for name in ARKVIEWER_LOGGERS:
        logging.getLogger(name).setLevel(target)
    if debug:
        for name in UVICORN_LOGGERS:
            logging.getLogger(name).setLevel(logging.DEBUG)


def print_banner(map_file: Path | None) -> None:
    """One-shot ASCII logo + version/host info to stdout."""
    py = sys.version_info
    vm = psutil.virtual_memory()
    cpu_logical = psutil.cpu_count(logical=True) or 0
    cpu_physical = psutil.cpu_count(logical=False) or 0
    map_status = "<not configured>"
    if map_file is not None:
        marker = "OK" if map_file.exists() else "MISSING"
        map_status = f"{map_file}  [{marker}]"
    rows = (
        ("ArkViewer", VERSION),
        ("arkparser", ARKPARSER_VERSION),
        (
            "Python",
            f"{py.major}.{py.minor}.{py.micro} ({platform.python_implementation()})",
        ),
        ("Platform", platform.platform()),
        ("CPU", f"{cpu_logical} logical / {cpu_physical} physical"),
        (
            "RAM",
            f"{vm.total / (1024**3):.1f} GB total, {vm.available / (1024**3):.1f} GB available",
        ),
        ("PID", str(os.getpid())),
        ("CWD", os.getcwd()),
        ("MapFile", map_status),
    )
    sys.stdout.write(LOGO)
    for key, value in rows:
        sys.stdout.write(f"  {key:<10}: {value}\n")
    sys.stdout.write("\n")
    sys.stdout.flush()


def format_process_ram(rss_bytes: int) -> str:
    mb = rss_bytes / (1024 * 1024)
    if mb < 1024:
        return f"{mb:.0f}MB"
    return f"{mb / 1024:.1f}GB"


async def title_loop(
    map_file: Path | None,
    is_syncing: t.Callable[[], bool],
) -> None:
    """Animate console title with version + map + RAM + CPU% + spinner.

    `is_syncing` is a zero-arg callback so coupling to caller state is one
    explicit callable, not a shared module flag.
    """
    if not IS_WINDOWS:
        return
    try:
        set_title = ctypes.windll.kernel32.SetConsoleTitleW
    except (OSError, AttributeError) as exc:  # pragma: no cover - Windows-only
        log.warning("Title loop disabled: %s", exc)
        return

    log.info("Title loop started (frozen=%s)", IS_FROZEN)
    frames = cycle(TITLE_FRAMES)
    proc = psutil.Process(os.getpid())
    proc.cpu_percent(interval=None)
    cpu_count = psutil.cpu_count(logical=True) or 1
    await asyncio.sleep(1)

    failed = False
    while True:
        try:
            map_name = map_file.stem if map_file else "no map"
            ram = format_process_ram(proc.memory_info().rss)
            cpu = proc.cpu_percent(interval=None) / cpu_count
            frame = next(frames)
            title = f"ArkViewer {VERSION} - {map_name} [{ram} | {cpu:.0f}%] {frame}"
            if is_syncing():
                title += " [Parsing]"
            set_title(title)
        except OSError as exc:
            if not failed:
                log.debug("Title update failed: %s", exc)
                failed = True
        await asyncio.sleep(TITLE_TICK_S)
