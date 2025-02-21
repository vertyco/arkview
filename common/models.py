from pathlib import Path

from pydantic import BaseModel

from .constants import CONFIG, EXE_FILE, OUTPUT_DIR, ROOT_DIR


class Banlist(BaseModel):
    bans: list[str]


class Dtypes(BaseModel):
    dtypes: list[str]


class Cache(BaseModel):
    config: Path
    root_dir: Path
    output_dir: Path
    exe_file: Path

    # Settings
    api_key: str = ""
    priority: str = "LOW"  # LOW, BELOWNORMAL, NORMAL, ABOVENORMAL, HIGH
    threads: int = 2
    debug: bool = False
    port: int = 8000
    map_file: Path = ""
    cluster_dir: Path = ""
    ban_file: Path = ""
    asatest: bool = True
    reprocess_on_arkdata_update: bool = False

    # States/Cache
    exports: dict[str, list[dict]] = {}
    syncing: bool = False
    tribelog_buffer: set[str] = set()
    last_export: float = 0.0
    map_last_modified: float = 0.0


cache = Cache(
    config=CONFIG,
    root_dir=ROOT_DIR,
    output_dir=OUTPUT_DIR,
    exe_file=EXE_FILE,
)
