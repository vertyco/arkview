import os
import sys
from pathlib import Path

from uvicorn.config import LOGGING_CONFIG

DEFAULT_CONF = """
[Settings]
# Port for the API to listen on (TCP)
# Make sure to forward this port in your router and allow it as TCP in your firewall
Port = 8000

# Direct path to the .ark map file
MapFilePath =

# (Optional): Direct path to the solecluster folder
ClusterFolderPath =

# (Optional): Direct path to BanList.txt file
BanListFile =

# Process priority(Windows-only): LOW, BELOWNORMAL, NORMAL, ABOVENORMAL, HIGH
Priority = LOW

# Number of threads to use for processing (if higher than CPU threads, it will be set to CPU threads)
Threads = 2

# If true, api will only be accessible locally (If running as python, this will cause the client to fail)
Debug = False

# (Optional): Set a sentry DSN for error tracking
DSN =

# (Optional): API Key for authentication
APIKey =
"""

IS_WINDOWS: bool = sys.platform.startswith("win")
IS_EXE = True if (getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")) else False
if IS_EXE and IS_WINDOWS:
    ROOT_DIR = Path(os.path.dirname(os.path.abspath(sys.executable)))
else:
    ROOT_DIR = Path(os.path.dirname(os.path.abspath(__file__))).parent

META_PATH = Path(os.path.abspath(os.path.dirname(__file__))).parent

OUTPUT_DIR = ROOT_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)
FILE_NAME = "ASVExport.exe" if IS_WINDOWS else "ASVExport.dll"
EXE_FILE = (
    Path(os.path.abspath(os.path.dirname(__file__))).parent / "exporter" / FILE_NAME
)

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
    "backupCount": 1,
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
