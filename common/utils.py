import os
import re
from datetime import datetime
from pathlib import Path

import cpuinfo
import psutil


def validate_path(path: Path) -> bool:
    """
    Validate that a path contains only alphanumeric characters, underscores,
    and standard path separators (no spaces or special characters).

    Args:
        path: The path to validate

    Returns:
        bool: True if the path is valid, False otherwise
    """
    # Convert to string for validation
    path_str = str(path)

    # Check for spaces
    if " " in path_str:
        return False

    # Build regex pattern allowing only alphanumeric, underscore, dot, colon, slash, backslash
    # This allows standard path components like C:\ and path separators / and \
    pattern = r"^[a-zA-Z0-9_\.:/\\]+"

    return bool(re.match(pattern, path_str))


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
