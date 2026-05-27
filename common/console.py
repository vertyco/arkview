"""Windows console helpers: QuickEdit/VT toggles + startup banner.

All Windows-specific helpers no-op on non-Windows.
"""

import ctypes
import logging
import os
import platform
import sys
from importlib.metadata import PackageNotFoundError, version as pkg_version
from pathlib import Path

import psutil

from common.constants import IS_WINDOWS, LOGO
from common.version import VERSION

log = logging.getLogger("arkview.console")


def disable_quickedit() -> None:
    """Turn off QuickEdit on the Windows console.

    QuickEdit pauses every stdout write while a user click holds the console
    in selection mode -- that pause blocks the asyncio loop and freezes the
    whole server until the selection is cleared. No-op on non-Windows.
    """
    if not IS_WINDOWS:
        return
    try:
        kernel32 = ctypes.windll.kernel32
        std_input_handle = ctypes.c_ulong(-10)
        enable_quick_edit = 0x0040
        enable_extended_flags = 0x0080
        handle = kernel32.GetStdHandle(std_input_handle)
        mode = ctypes.c_ulong()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        mode.value &= ~enable_quick_edit
        mode.value |= enable_extended_flags
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
        enable_vt = 0x0004
        enable_processed = 0x0001
        for handle_id in (ctypes.c_ulong(-11), ctypes.c_ulong(-12)):  # stdout, stderr
            h = kernel32.GetStdHandle(handle_id)
            mode = ctypes.c_ulong()
            if not kernel32.GetConsoleMode(h, ctypes.byref(mode)):
                continue
            kernel32.SetConsoleMode(h, mode.value | enable_vt | enable_processed)
    except (OSError, AttributeError) as exc:
        log.debug("enable_console_vt failed: %s", exc)


def _arkparser_version() -> str:
    try:
        return pkg_version("arkparser")
    except PackageNotFoundError:
        return "unknown"


def print_banner(map_file: Path | None = None) -> None:
    """One-shot ASCII logo + version/host specs to stdout."""
    py = sys.version_info
    vm = psutil.virtual_memory()
    rows = [
        ("ArkViewer", VERSION),
        ("arkparser", _arkparser_version()),
        (
            "Python",
            f"{py.major}.{py.minor}.{py.micro} ({platform.python_implementation()})",
        ),
        ("Platform", platform.platform()),
        (
            "CPU",
            f"{psutil.cpu_count(logical=True) or 0} logical / {psutil.cpu_count(logical=False) or 0} physical",
        ),
        (
            "RAM",
            f"{vm.total / (1024**3):.1f} GB total, {vm.available / (1024**3):.1f} GB available",
        ),
        ("PID", str(os.getpid())),
    ]
    if map_file is not None:
        marker = "OK" if map_file.exists() else "MISSING"
        rows.append(("MapFile", f"{map_file}  [{marker}]"))
    sys.stdout.write(LOGO)
    for key, value in rows:
        sys.stdout.write(f"  {key:<10}: {value}\n")
    sys.stdout.write("\n")
    sys.stdout.flush()
