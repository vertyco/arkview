import os
import sys
from pathlib import Path

from uvicorn.config import LOGGING_CONFIG

DEFAULT_CONF = """
[Settings]
APIKey =
Port = 8000
BanListFile =
MapFilePath =
ClusterFolderPath =
Debug = False
DSN =
DataOnly = False
"""

IS_WINDOWS = True if "C:\\Users" in os.environ.get("USERPROFILE", "") else False
IS_EXE = True if (getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")) else False
if IS_EXE:
    ROOT_DIR = Path(os.path.dirname(os.path.abspath(sys.executable)))
else:
    ROOT_DIR = Path(os.path.dirname(os.path.abspath(sys.executable))).parent.parent
OUTPUT_DIR = ROOT_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)
EXE_FILE = ROOT_DIR / "exporter" / "ASVExport.exe"
CONFIG = ROOT_DIR / "config.ini"
if not CONFIG.exists():
    CONFIG.write_text(DEFAULT_CONF.strip())


BAR = [
    "▱▱▱▱▱▱▱",
    "▰▱▱▱▱▱▱",
    "▰▰▱▱▱▱▱",
    "▰▰▰▱▱▱▱",
    "▰▰▰▰▱▱▱",
    "▰▰▰▰▰▱▱",
    "▰▰▰▰▰▰▱",
    "▰▰▰▰▰▰▰",
    "▱▰▰▰▰▰▰",
    "▱▱▰▰▰▰▰",
    "▱▱▱▰▰▰▰",
    "▱▱▱▱▰▰▰",
    "▱▱▱▱▱▰▰",
    "▱▱▱▱▱▱▰",
]

LOGO = r"""
                _  __      ___
     /\        | | \ \    / (_)
    /  \   _ __| | _\ \  / / _  _____      _____ _ __
   / /\ \ | '__| |/ /\ \/ / | |/ _ \ \ /\ / / _ \ '__|
  / ____ \| |  |   <  \  /  | |  __/\ V  V /  __/ |
 /_/    \_\_|  |_|\_\  \/   |_|\___| \_/\_/ \___|_|
"""


API_CONF = LOGGING_CONFIG.copy()
API_CONF["formatters"]["access"]["fmt"] = "%(asctime)s - %(levelname)s - %(message)s"
API_CONF["formatters"]["default"]["fmt"] = "%(asctime)s - %(levelname)s - %(message)s"
API_CONF["formatters"]["access"]["datefmt"] = "%m/%d %I:%M:%S %p"
API_CONF["formatters"]["default"]["datefmt"] = "%m/%d %I:%M:%S %p"
API_CONF["handlers"]["file"] = {
    "formatter": "default",
    "class": "logging.handlers.RotatingFileHandler",
    "filename": str(ROOT_DIR / "uvicorn.log"),
    "mode": "a",
    "maxBytes": 1024 * 1024,
    "backupCount": 3,
}
API_CONF["loggers"]["uvicorn"] = {
    "handlers": ["default", "file"],
    "level": "INFO",
    "propagate": False,
}
API_CONF["loggers"]["uvicorn.access"] = {
    "handlers": ["access", "file"],
    "level": "INFO",
    "propagate": False,
}

VALID_DATATYPES = [
    "mapstructures",
    "players",
    "structures",
    "tamed",
    "tribelogs",
    "tribes",
    "wild",
    "all",
]
