import logging
import sys
from logging.handlers import RotatingFileHandler

import colorama
import sentry_sdk
from colorama import Back, Fore, Style
from sentry_sdk.integrations.aiohttp import AioHttpIntegration
from sentry_sdk.integrations.logging import LoggingIntegration

green = Fore.LIGHTGREEN_EX + Style.BRIGHT
blue = Fore.LIGHTBLUE_EX + Style.BRIGHT
yellow = Fore.YELLOW + Style.BRIGHT
red = Fore.LIGHTRED_EX + Style.BRIGHT
critical = Fore.LIGHTYELLOW_EX + Back.RED + Style.BRIGHT
reset = Style.RESET_ALL + Fore.RESET + Back.RESET
timestamp = Fore.WHITE + "[%(asctime)s]" + reset
module = Fore.LIGHTBLACK_EX + Style.BRIGHT + "[%(name)s]" + reset
message = Fore.WHITE + Style.BRIGHT + "%(message)s" + reset
formats = {
    logging.DEBUG: f"{timestamp} {green}%(levelname)s{reset}    {module}: {message}",
    logging.INFO: f"{timestamp} {blue}%(levelname)s{reset}     {module}: {message}",
    logging.WARNING: f"{timestamp} {yellow}%(levelname)s{reset}  {module}: {message}",
    logging.ERROR: f"{timestamp} {red}%(levelname)s{reset}    {module}: {message}",
    logging.CRITICAL: f"{timestamp} {critical}%(levelname)s{reset} {module}: {message}",
}
dt_fmt = "%Y-%m-%d %I:%M:%S %p"
colorama.init(autoreset=True)


class PrettyFormatter(logging.Formatter):
    def format(self, record):
        log_fmt = formats[record.levelno]
        formatter = logging.Formatter(fmt=log_fmt, datefmt="%I:%M:%S %p")
        return formatter.format(record)


def init_logging():
    print("Initializing logger")
    applogger = logging.getLogger("apscheduler")
    applogger.setLevel(logging.ERROR)

    # Console Log
    stdout_handler = logging.StreamHandler()
    stdout_handler.setFormatter(PrettyFormatter())
    stdout_handler.setLevel(logging.DEBUG)

    file_handler = RotatingFileHandler(
        "logs.log",
        mode="a",
        maxBytes=1 * 1024 * 1024,
        backupCount=0,
    )
    file_formatter = logging.Formatter(
        fmt="{asctime} [{levelname:<8}] {name}: {message}",
        datefmt=dt_fmt,
        style="{",
    )
    file_handler.setFormatter(file_formatter)

    logging.basicConfig(
        level=logging.DEBUG,
        datefmt=dt_fmt,
        handlers=[stdout_handler, file_handler],
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
    try:
        sentry_sdk.init(
            dsn=dsn,
            integrations=[
                AioHttpIntegration(),
                LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
            ],
            release=version,
            environment=sys.platform,
            ignore_errors=[KeyboardInterrupt, RuntimeError],
        )
        log.info("Sentry initialized successfully")
    except Exception as e:
        log.error("Failed to initialize Sentry: %s", e)


if __name__ == "__main__":
    init_logging()
    log = logging.getLogger("test.module")
    log.debug("This is a debug message")
    log.info("This is an info message")
    log.warning("This is a warning message")
    log.error("This is an error message")
    log.critical("This is a critical message")
