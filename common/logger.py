import logging
import sys
from logging.handlers import RotatingFileHandler

import colorama
import sentry_sdk
from colorama import Back, Fore, Style
from sentry_sdk.integrations.aiohttp import AioHttpIntegration
from sentry_sdk.integrations.logging import LoggingIntegration

datefmt = "%m-%d-%Y %I:%M:%S %p"
log_format = logging.Formatter(
    "%(asctime)s - %(levelname)s - %(message)s", datefmt=datefmt
)

# Debug log
debug_file_handler = RotatingFileHandler(
    "debug-logs.log",
    mode="a",
    maxBytes=5 * 1024 * 1024,
    backupCount=5,
)
debug_file_handler.setFormatter(log_format)
debug_file_handler.setLevel(logging.DEBUG)

# Info log
info_file_handler = RotatingFileHandler(
    "logs.log",
    mode="a",
    maxBytes=5 * 1024 * 1024,
    backupCount=5,
)
info_file_handler.setFormatter(log_format)
info_file_handler.setLevel(logging.INFO)


class PrettyFormatter(logging.Formatter):
    colorama.init(autoreset=True)
    fmt = "%(asctime)s - %(levelname)s - %(message)s"
    formats = {
        logging.DEBUG: Fore.LIGHTGREEN_EX + Style.BRIGHT + fmt,
        logging.INFO: Fore.LIGHTWHITE_EX + Style.BRIGHT + fmt,
        logging.WARNING: Fore.YELLOW + Style.BRIGHT + fmt,
        logging.ERROR: Fore.RED + Style.BRIGHT + fmt,
        logging.CRITICAL: Fore.LIGHTYELLOW_EX + Back.RED + Style.BRIGHT + fmt,
    }

    def format(self, record):
        log_fmt = self.formats.get(record.levelno)
        formatter = logging.Formatter(fmt=log_fmt, datefmt="%m/%d %I:%M:%S %p")
        return formatter.format(record)


def init_logging():
    print("Initializing logger")
    applogger = logging.getLogger("apscheduler")
    applogger.setLevel(logging.ERROR)

    # Console Log
    stdout_handler = logging.StreamHandler()
    stdout_handler.setFormatter(PrettyFormatter())
    stdout_handler.setLevel(logging.INFO)

    handlers = [stdout_handler]

    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        # Running as EXE so use file log
        pass
    handlers.append(info_file_handler)
    handlers.append(debug_file_handler)

    logging.basicConfig(
        level=logging.DEBUG,
        datefmt=datefmt,
        handlers=handlers,
    )


def init_sentry(dsn: str, version: str) -> None:
    """Initializes Sentry SDK.

    Parameters
    ----------
    dsn: str
        The Sentry DSN to use.
    version: str
        The version of the application.
    """
    sentry_sdk.init(
        dsn=dsn,
        integrations=[
            AioHttpIntegration(),
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
        release=version,
        environment="windows",
        ignore_errors=[KeyboardInterrupt],
    )
