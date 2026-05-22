import typing as t

VERSION: t.Final[str] = "3.1.0"
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
