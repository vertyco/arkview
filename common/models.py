from pathlib import Path

from pydantic import BaseModel

from .constants import CONFIG, EXE_FILE, OUTPUT_DIR, ROOT_DIR


class Banlist(BaseModel):
    bans: list[str]


class Cache(BaseModel):
    api_key: str = ""
    config: Path
    root_dir: Path
    output_dir: Path
    exe_file: Path

    last_export: int = 0
    last_output: int = 0
    syncing: bool = False
    reading: bool = False
    debug: bool = False
    port: int = 8000

    exports: dict[str, dict] = {}
    map_file: str | Path = ""
    cluster_dir: str | Path = ""
    ban_file: str | Path = ""

    asatest: bool = True


cache = Cache(
    config=CONFIG,
    root_dir=ROOT_DIR,
    output_dir=OUTPUT_DIR,
    exe_file=EXE_FILE,
)
