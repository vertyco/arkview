import json
import logging
import os
import subprocess
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Union

import colorama
import cpuinfo
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
        try:
            running = [p.name() for p in psutil.process_iter()]
            return process in running
        except psutil.NoSuchProcess:
            return False

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
            elif version > "6.9.9":
                is_installed = False

        if not is_installed:
            log.error(".NET V6.0 framework is REQUIRED!")
            webbrowser.open(Const().dotnet)
        return is_installed

    @staticmethod
    def get_affinity_mask(threads: int) -> str:
        # https://poweradm.com/set-cpu-affinity-powershell/
        cpus = os.cpu_count()
        if threads > cpus:
            threads = cpus

        options = []
        num = 1
        for c in range(cpus):
            if not options:
                options.append(num)
            else:
                num = num * 2
                options.append(num)

        # options = options[:threads]
        # Reverse and use last core first
        options = options[-threads:]
        mask = sum(options) if options else 1
        return hex(mask)

    @staticmethod
    def get_stats() -> dict:
        def get_size(num: float) -> str:
            for unit in ["B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB"]:
                if abs(num) < 1024.0:
                    return "{0:.1f}{1}".format(num, unit)
                num /= 1024.0
            return "{0:.1f}{1}".format(num, "YB")

        def get_bar(perc: float, width: int = 20) -> str:
            ratio = perc / 100
            bar = "█" * round(ratio * width) + "-" * round(width - (ratio * width))
            return f"|{bar}| {round(100 * ratio, 1)}%"

        # -/-/-/CPU-/-/-/
        cpu_count = psutil.cpu_count()  # Int
        cpu_perc = psutil.cpu_percent(interval=0.5, percpu=True)  # List of floats
        cpu_freq = psutil.cpu_freq(percpu=True)  # List of Objects
        cpu_info = cpuinfo.get_cpu_info()  # Dict
        cpu_type = cpu_info["brand_raw"] if "brand_raw" in cpu_info else "Unknown"

        # -/-/-/MEM-/-/-/
        ram = psutil.virtual_memory()  # Obj
        ram_total = get_size(ram.total)
        ram_used = get_size(ram.used)
        disk = psutil.disk_usage(os.getcwd())
        disk_total = get_size(disk.total)
        disk_used = get_size(disk.used)

        p = psutil.Process()
        io_counters = p.io_counters()
        disk_usage_process = io_counters[2] + io_counters[3]  # read_bytes + write_bytes
        # Disk load
        disk_io_counter = psutil.disk_io_counters()
        if disk_io_counter:
            disk_io_total = (
                disk_io_counter[2] + disk_io_counter[3]
            )  # read_bytes + write_bytes
            disk_usage = (disk_usage_process / disk_io_total) * 100
        else:
            disk_usage = 0

        # -/-/-/NET-/-/-/
        net = psutil.net_io_counters()  # Obj
        sent = get_size(net.bytes_sent)
        recv = get_size(net.bytes_recv)

        uptime = (
            datetime.now() - datetime.fromtimestamp(psutil.boot_time())
        ).total_seconds()

        res = {
            "cpu": {
                "cores": cpu_count,
                "percents": cpu_perc if isinstance(cpu_perc, list) else None,
                "freq": [(i.current, i.max) for i in cpu_freq],
                "bars": [get_bar(i) for i in cpu_perc] if cpu_perc else None,
                "type": cpu_type,
            },
            "mem": {"used": ram_used, "total": ram_total, "bar": get_bar(ram.percent)},
            "disk": {
                "used": disk_used,
                "total": disk_total,
                "bar": get_bar(disk.percent),
                "load": disk_usage,
                "loadbar": get_bar(disk_usage),
            },
            "net": {"sent": sent, "received": recv},
            "uptime": uptime,
        }
        return res


class Const:
    # Ark paths
    defaults = {
        "Port": 8000,
        "MapFilePath": "",
        "ClusterFolderPath": "",
        "Debug": False,
    }
    dotnet = "https://dotnet.microsoft.com/en-us/download"


if __name__ == "__main__":
    for i in range(1, os.cpu_count() + 1):
        affinity_mask = Tools().get_affinity_mask(i)
        print(f"Mask for {i} threads: {affinity_mask}")
