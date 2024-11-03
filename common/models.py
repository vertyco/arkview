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
    map_file: str | Path = ""
    cluster_dir: str | Path = ""
    ban_file: str | Path = ""
    asatest: bool = True

    # States/Cache
    exports: dict[str, list[dict]] = {}
    syncing: bool = False
    tribelog_buffer: set[str] = set()
    last_export: int = 0


cache = Cache(
    config=CONFIG,
    root_dir=ROOT_DIR,
    output_dir=OUTPUT_DIR,
    exe_file=EXE_FILE,
)
