import typing as t
from importlib import metadata as importlib_metadata

VERSION: t.Final[str] = "3.2.1"
DEFAULT_PORT: t.Final[int] = 8000
DEFAULT_DB_FILENAME: t.Final[str] = "arkviewer.db"
STALE_AFTER_SECONDS: t.Final[int] = 6 * 60 * 60  # 6 hours

DATASET_NAMES: t.Final[tuple[str, ...]] = (
    "tamed",
    "wild",
    "players",
    "tribes",
    "structures",
    "tribelogs",
    "mapstructures",
)


def _detect_arkparser_version() -> str:
    try:
        return importlib_metadata.version("arkparser")
    except importlib_metadata.PackageNotFoundError:
        try:
            import arkparser

            return getattr(arkparser, "__version__", "unknown")
        except ImportError:
            return "unknown"


# Cached at import so meta-equality compares (db.py cache invalidation) stay
# stable across the process lifetime; arkparser is never reloaded.
ARKPARSER_VERSION: t.Final[str] = _detect_arkparser_version()
