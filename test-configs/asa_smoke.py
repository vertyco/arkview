"""ASA-flavor smoke: ingest_full against a real ASA save (TheIsland_WP)."""

import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ["ARKVIEWER_CONFIG"] = str(REPO / "test-configs" / "asa.ini")
os.environ["ARKVIEWER_DB"] = str(REPO / "test-configs" / "asa.db")

import psutil  # noqa: E402

from app.config import load_config  # noqa: E402
from app.db import init_schema  # noqa: E402
from app.ingest import ingest_full  # noqa: E402


def rss_mb() -> float:
    return psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024


def main() -> int:
    cfg = load_config(Path(os.environ["ARKVIEWER_CONFIG"]))
    print(f"map={cfg.map_file} db={cfg.db_path}")
    if cfg.map_file is None or not cfg.map_file.exists():
        print(f"ERROR: missing {cfg.map_file}")
        return 1
    for sidecar in (
        cfg.db_path,
        cfg.db_path.with_suffix(".db-wal"),
        cfg.db_path.with_suffix(".db-shm"),
    ):
        if sidecar.exists():
            sidecar.unlink()
    init_schema(cfg.db_path)
    print(f"start RSS={rss_mb():.1f} MB")

    t0 = time.perf_counter()
    result = ingest_full(cfg)
    elapsed = time.perf_counter() - t0
    print(
        f"\nINGEST OK in {elapsed:.1f}s. RSS={rss_mb():.1f} MB\n"
        f"  tamed={result.tamed} wild={result.wild} players={result.players} "
        f"tribes={result.tribes} structures={result.structures} "
        f"tribelogs={result.tribelogs} mapstructures={result.mapstructures} "
        f"cluster={result.cluster_files}"
    )

    # Sanity: at least the map loaded; some categories may be 0 on a small/fresh ASA save.
    if result.players + result.tribes + result.tamed + result.wild == 0:
        print(
            "WARN: ALL counts are zero — something is wrong with arkparser ASA parsing"
        )
        return 2

    print("DONE.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
