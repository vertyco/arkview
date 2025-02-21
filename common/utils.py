import asyncio
import logging
import os
import subprocess
import webbrowser
from datetime import datetime
from time import perf_counter

import cpuinfo
import psutil

log = logging.getLogger("arkview.common.utils")


async def get_process_pid(process: str) -> int | None:
    """
    Get the PID of a process by name

    Returns:
        int | None: The PID of the process or None if the process does not exist
    """

    def _exe():
        for proc in psutil.process_iter():
            if process in proc.name():
                return proc.pid
        return None

    return await asyncio.to_thread(_exe)


async def wait_for_process_to_exist(process: str, wait_time: int = 6) -> int | None:
    """
    Wait for a process to exist by name

    Returns:
        int | None: The PID of the process or None if the process does not exist
    """
    start = perf_counter()
    while True:
        pid = await get_process_pid(process)
        if pid:
            return pid
        if perf_counter() - start >= wait_time:
            return None
        await asyncio.sleep(0.01)


async def wait_for_pid_to_stop(pid: int, wait_time: int = 900) -> bool:
    """
    Wait for a process to stop by PID

    Returns:
        bool: True if the process has stopped
    """
    start = perf_counter()
    while True:
        if not psutil.pid_exists(pid):
            return True
        if perf_counter() - start >= wait_time:
            return False
        await asyncio.sleep(0.1)


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
        # Use version parsing for accurate comparison
        from packaging.version import parse as parse_version

        try:
            if parse_version(version) < parse_version("6.0.0") or parse_version(
                version
            ) > parse_version("6.9.9"):
                is_installed = False
            else:
                log.info(f"Current .NET version: {version}")
        except Exception as e:
            log.error("Failed to parse .NET version", exc_info=e)
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
    cpu_perc = psutil.cpu_percent(interval=0.1, percpu=True)  # List of floats
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
