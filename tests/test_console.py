"""Log line format mirrors the arkhandler look: `[ts] LEVEL [name]: message`."""

import logging
import typing as t  # noqa: F401

from app.console import CompactFormatter, short_logger_name


def _record(name: str, level: int, msg: str) -> logging.LogRecord:
    return logging.LogRecord(name, level, "f.py", 1, msg, None, None)


def test_short_logger_name_maps_and_is_unpadded() -> None:
    # Names appear inside [brackets] now, so they must not carry pad spaces.
    assert short_logger_name("arkviewer") == "app"
    assert short_logger_name("arkviewer.db") == "db"
    assert short_logger_name("arkviewer.watcher") == "watcher"
    assert short_logger_name("uvicorn.access") == "uvicorn"


def test_plain_format_is_arkhandler_style() -> None:
    fmt = CompactFormatter(use_color=False, datefmt="%Y-%m-%d %I:%M:%S %p")
    out = fmt.format(_record("arkviewer.db", logging.INFO, "hello"))
    assert out.startswith("[")  # bracketed timestamp
    assert "] INFO" in out  # level after the timestamp bracket
    assert out.endswith("[db]: hello")  # bracketed short name, colon, message


def test_colored_format_wraps_level_and_brackets_name() -> None:
    fmt = CompactFormatter(use_color=True, datefmt="%I:%M:%S %p")
    out = fmt.format(_record("arkviewer.watcher", logging.WARNING, "x"))
    assert "\x1b[" in out  # ANSI colour present
    assert "[watcher]" in out
    assert "WARNING" in out
