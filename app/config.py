import configparser
import os
import typing as t  # noqa: F401
from dataclasses import dataclass
from pathlib import Path

from app.constants import DEFAULT_DB_FILENAME, DEFAULT_PORT

DEFAULT_INI = """[Settings]
# Port for the API to listen on (TCP)
Port = 8000

# Direct path to the .ark map file
MapFilePath =

# (Optional) Direct path to the cluster/solecluster folder
ClusterFolderPath =

# (Optional) Direct path to BanList.txt file
BanListFile =

# If true, API binds to 127.0.0.1 only
Debug = False

# (Optional) Sentry DSN for error tracking
DSN =

# (Optional) API Key for Bearer token authentication
APIKey =
"""


@dataclass(slots=True)
class AppConfig:
    config_path: Path
    port: int
    map_file: Path | None
    cluster_dir: Path | None
    banlist_file: Path | None
    debug: bool
    dsn: str
    api_key: str
    db_path: Path


def _coerce_path(value: str) -> Path | None:
    value = value.strip()
    return Path(value) if value else None


def load_config(path: Path) -> AppConfig:
    assert isinstance(path, Path), "path must be a pathlib.Path"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_INI, encoding="utf-8")

    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    s = parser["Settings"]

    env_db = os.environ.get("ARKVIEWER_DB")
    db_path = Path(env_db) if env_db else path.parent / DEFAULT_DB_FILENAME

    cfg = AppConfig(
        config_path=path,
        port=int(s.get("Port", DEFAULT_PORT)),
        map_file=_coerce_path(s.get("MapFilePath", "")),
        cluster_dir=_coerce_path(s.get("ClusterFolderPath", "")),
        banlist_file=_coerce_path(s.get("BanListFile", "")),
        debug=s.getboolean("Debug", fallback=False),
        dsn=s.get("DSN", "").strip(),
        api_key=s.get("APIKey", "").strip(),
        db_path=db_path,
    )

    assert cfg.port > 0, "port must be positive"
    assert cfg.db_path is not None
    return cfg
