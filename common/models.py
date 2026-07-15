from pathlib import Path

from pydantic import BaseModel, Field

from .constants import CONFIG, OUTPUT_DIR, ROOT_DIR


class Banlist(BaseModel):
    bans: list[str]


class Dtypes(BaseModel):
    dtypes: list[str]


class ServerNames(BaseModel):
    servernames: list[str]


class Cache(BaseModel):
    config: Path
    root_dir: Path
    output_dir: Path

    # Settings
    api_key: str = ""
    priority: str = "LOW"  # LOW, BELOWNORMAL, NORMAL, ABOVENORMAL, HIGH
    threads: int = 2
    debug: bool = False
    port: int = 8000
    map_file: Path | None = None
    cluster_dir: Path | None = None
    ban_file: Path | None = None
    asatest: bool = True
    reprocess_on_arkdata_update: bool = False

    # States/Cache
    exports: dict[str, list[dict]] = Field(default_factory=dict)
    syncing: bool = False
    parse_pid: int | None = None  # PID of the running parse child (for RAM readout)
    tribelog_buffer: set[str] = Field(default_factory=set)
    last_export: float = 0.0
    map_last_modified: float = 0.0

    # Sidecar counts from the last successful parse. Written by the parent from
    # the child's _parse_stats.json (a child write would not cross the process
    # boundary), and surfaced on /stats so a skipped player is visible without a
    # shell: a skipped profile leaves the player as a blank-id tribe stub.
    profiles_loaded: int = 0
    profiles_skipped: int = 0
    tribes_loaded: int = 0
    tribes_skipped: int = 0


cache = Cache(
    config=CONFIG,
    root_dir=ROOT_DIR,
    output_dir=OUTPUT_DIR,
)
