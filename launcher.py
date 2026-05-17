"""Frozen-exe entrypoint that catches import-time and startup crashes.

main.py's top-level imports can fail (e.g. missing bundled module), in which
case the exe window closes instantly with no log file. This launcher wraps
the import + run in a try/except, writes the traceback to
``arkviewer-crash.log`` next to the executable, and pauses on Windows so the
operator can read the error before the window closes.
"""

import datetime as _dt
import os
import sys
import traceback
from pathlib import Path


def _crash_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def _write_crash(tb: str) -> None:
    try:
        path = _crash_dir() / "arkviewer-crash.log"
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n=== {_dt.datetime.now().isoformat()} ===\n{tb}\n")
    except Exception:
        pass


def _hold_window() -> None:
    if not (getattr(sys, "frozen", False) and sys.platform.startswith("win")):
        return
    try:
        input("\nPress Enter to exit...")
    except (EOFError, KeyboardInterrupt):
        pass


def main() -> None:
    if getattr(sys, "frozen", False):
        os.chdir(Path(sys.executable).parent)
    from main import Manager

    Manager.run()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        tb = traceback.format_exc()
        sys.stderr.write(tb)
        _write_crash(tb)
        _hold_window()
        sys.exit(1)
