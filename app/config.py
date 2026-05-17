import logging
import os
import sys
from configparser import ConfigParser
from pathlib import Path

from pydantic import BaseModel


def _resolve_root_dir() -> Path:
    """Return the directory the user thinks of as "where the app lives".

    - When running from source: the repo root (parent of the ``app/`` package).
    - When frozen by PyInstaller: the directory containing the .exe itself -
      NOT the temporary ``_MEI*`` extraction folder where ``__file__`` points.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _resolve_config_path() -> Path:
    """Locate ``config.ini`` for the running instance.

    Purpose: one arkviewer install often serves multiple ARK maps from a
    single host (one process per map). Allow each instance to point at its
    own config file via the ``ARKVIEWER_CONFIG`` env var so we don't need to
    duplicate the install. Falls back to ``<root>/config.ini`` for the
    single-instance / dev / Windows-exe case.
    Preconditions: ``ARKVIEWER_CONFIG`` (if set) is a readable / creatable path.
    Postconditions: returns an absolute ``Path``; does not check existence.
    Side effects: reads the environment.
    """
    if env_path := os.environ.get("ARKVIEWER_CONFIG"):
        return Path(env_path).expanduser().resolve()
    return _resolve_root_dir() / "config.ini"


ROOT_DIR = _resolve_root_dir()
CONFIG_PATH = _resolve_config_path()

DEFAULT_CONFIG = """[Settings]
# Port for the API to listen on (TCP)
Port = 8000

# Direct path to the .ark map file
# ASE: path to TheIsland.ark
# ASA: path to TheIsland_WP.ark
# Profiles and tribe files are discovered from the same directory for both formats
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

log = logging.getLogger("arkviewer.config")


class AppConfig(BaseModel):
    """Loaded from config.ini at startup."""

    port: int = 8000
    api_key: str = ""
    map_file: Path | None = None
    cluster_dir: Path | None = None
    ban_file: Path | None = None
    debug: bool = False
    dsn: str = ""


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_int(value: str, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def resolve_path(raw: str, base_dir: Path) -> Path | None:
    raw = raw.strip()
    # Windows users (and the Explorer "Copy as path" command) commonly paste
    # paths surrounded by double quotes. Strip a matched pair of surrounding
    # quotes so the path resolves cleanly instead of being treated as relative.
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ('"', "'"):
        raw = raw[1:-1].strip()
    if not raw:
        return None

    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def ensure_config_exists(config_path: Path = CONFIG_PATH) -> None:
    if config_path.exists():
        return

    config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
    log.warning("No config.ini found, created default at %s", config_path)


def validate_paths(config: AppConfig) -> None:
    if config.map_file and not config.map_file.exists():
        log.warning("MapFilePath does not exist: %s", config.map_file)

    if config.cluster_dir and not config.cluster_dir.is_dir():
        log.warning(
            "ClusterFolderPath does not exist or is not a directory: %s",
            config.cluster_dir,
        )

    if config.ban_file and not config.ban_file.exists():
        log.warning("BanListFile does not exist: %s", config.ban_file)


def load_config(config_path: Path = CONFIG_PATH) -> AppConfig:
    ensure_config_exists(config_path)

    parser = ConfigParser()
    parser.read(config_path, encoding="utf-8")
    settings = parser["Settings"] if parser.has_section("Settings") else {}
    base_dir = config_path.parent

    config = AppConfig(
        port=parse_int(settings.get("Port", "8000"), 8000),
        api_key=str(settings.get("APIKey", "")).strip(),
        map_file=resolve_path(str(settings.get("MapFilePath", "")), base_dir),
        cluster_dir=resolve_path(str(settings.get("ClusterFolderPath", "")), base_dir),
        ban_file=resolve_path(str(settings.get("BanListFile", "")), base_dir),
        debug=parse_bool(str(settings.get("Debug", "False"))),
        dsn=str(settings.get("DSN", "")).strip(),
    )

    validate_paths(config)
    log.info(
        "Config loaded - port=%d, map=%s, cluster=%s, debug=%s",
        config.port,
        config.map_file,
        config.cluster_dir,
        config.debug,
    )
    return config
