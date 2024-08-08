import asyncio
import logging
import os
import subprocess
import webbrowser
from datetime import datetime

import cpuinfo
import psutil

log = logging.getLogger("arkview.common.utils")


async def wait_for_process(process: str):
    def _is_running():
        try:
            running = [p.name() for p in psutil.process_iter()]
            return any(process in procname for procname in running)
        except psutil.NoSuchProcess:
            return True

    while await asyncio.to_thread(_is_running):
        log.debug("Waiting for ASV to export")
        await asyncio.sleep(5)


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
    windows = True if "C:\\Users" in os.environ.get("USERPROFILE", "") else False
    if not is_installed:
        log.critical(".NET V6.0 framework is REQUIRED!")
        if windows:
            webbrowser.open("https://dotnet.microsoft.com/en-us/download/dotnet/6.0")
    return is_installed


def get_affinity_mask(threads: int) -> str:
    # https://poweradm.com/set-cpu-affinity-powershell/
    cpus = os.cpu_count() or 1
    if threads > cpus:
        threads = cpus

    options = []
    num = 1
    for _ in range(cpus):
        if not options:
            options.append(num)
        else:
            num = num * 2
            options.append(num)

    # Reverse and use last core first
    options = options[-threads:]
    mask = sum(options) if options else 1
    return hex(mask)


def format_sys_info() -> dict:
    def get_size(num: float) -> str:
        for unit in ["B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB"]:
            if abs(num) < 1024.0:
                return "{0:.1f}{1}".format(num, unit)
            num /= 1024.0
        return "{0:.1f}{1}".format(num, "YB")

    def get_bar(perc: float, width: int = 18) -> str:
        fill = "▰"
        space = "▱"
        ratio = perc / 100
        bar = fill * round(ratio * width) + space * round(width - (ratio * width))
        return f"{bar} {round(100 * ratio, 1)}%"

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
