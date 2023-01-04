import json
import logging
import os
import subprocess
import webbrowser
from pathlib import Path
from typing import Union

import colorama
import psutil
from colorama import Back, Fore, Style
from pydantic import BaseModel

log = logging.getLogger("arkparser")


class BanList(BaseModel):
    bans: str


class PrettyFormatter(logging.Formatter):
    colorama.init(autoreset=True)
    fmt = "%(asctime)s - %(levelname)s - %(message)s"
    formats = {
        logging.DEBUG: Fore.LIGHTGREEN_EX + Style.BRIGHT + fmt,
        logging.INFO: Fore.LIGHTWHITE_EX + Style.BRIGHT + fmt,
        logging.WARNING: Fore.YELLOW + Style.BRIGHT + fmt,
        logging.ERROR: Fore.LIGHTMAGENTA_EX + Style.BRIGHT + fmt,
        logging.CRITICAL: Fore.LIGHTYELLOW_EX + Back.RED + Style.BRIGHT + fmt,
    }

    def format(self, record):
        log_fmt = self.formats.get(record.levelno)
        formatter = logging.Formatter(fmt=log_fmt, datefmt="%m/%d %I:%M:%S %p")
        return formatter.format(record)


class StandardFormatter(logging.Formatter):
    def format(self, record):
        formatter = logging.Formatter(
            fmt="%(asctime)s - %(levelname)s - %(message)s", datefmt="%m/%d %I:%M:%S %p"
        )
        return formatter.format(record)


class Tools:
    @staticmethod
    def is_running(process: str) -> bool:
        """Check if a process is running"""
        running = [p.name() for p in psutil.process_iter()]
        return process in running

    @staticmethod
    def read_data(path: Union[str, Path]) -> dict:
        with open(path, "rb") as f:
            return json.loads(f.read())

    @staticmethod
    def dotnet_installed() -> bool:
        cmd = r"dotnet --list-sdks"
        is_installed = True
        res = (
            subprocess.run(["powershell", cmd], stdout=subprocess.PIPE)
            .stdout.decode("utf-8")
            .strip()
        )
        log.debug(res)
        if "not recognized as the name of a cmdlet" in res:
            is_installed = False
        else:
            version = res.split(" ")[0].strip()
            log.info(f"Current .NET version: {version}")
            if version < "6.0.0":
                is_installed = False

        if not is_installed:
            log.error(".NET V6.0 framework is REQUIRED!")
            webbrowser.open(Const().dotnet)
        return is_installed

    @staticmethod
    def get_affinity_mask(threads: int) -> int:
        cpus = os.cpu_count()
        if threads > cpus:
            threads = cpus
        options = []
        num = 1
        for i in range(cpus):
            if not options:
                options.append(num)
            else:
                num = num * 2
                options.append(num)
        options = options[:threads]
        mask = sum(options) if options else 1
        return mask


class Const:
    # Ark paths
    defaults = {
        "Port": 8000,
        "MapFilePath": "",
        "ClusterFolderPath": "",
        "Debug": False,
    }
    dotnet = "https://dotnet.microsoft.com/en-us/download"
