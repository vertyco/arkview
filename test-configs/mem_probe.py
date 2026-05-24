"""Peak-RAM probe for a full ArkViewer ingest.

Usage (from repo root, venv active):

    & .venv\\Scripts\\Activate.ps1
    python test-configs\\mem_probe.py --config test-configs\\ase.ini
    # or point straight at a save:
    python test-configs\\mem_probe.py --map "C:\\...\\Ragnarok.ark"

Prints tracemalloc peak (Python allocations), RSS peak (psutil), wall time, and
per-table row counts. Run it on the current code to record a baseline, then on
the streaming branch to compare. The object graph is the residual floor; the
win shows up as a lower tracemalloc peak because only one dataset list is held
at a time instead of all seven.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import tracemalloc
import typing as t
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from app.config import AppConfig, load_config  # noqa: E402
from app.db import init_schema  # noqa: E402
from app.ingest import ingest_full  # noqa: E402


def _build_config(args: argparse.Namespace) -> AppConfig:
    cfg_path = args.config or os.environ.get("ARKVIEWER_CONFIG")
    if cfg_path:
        return load_config(Path(cfg_path))
    assert args.map, "pass --config, set ARKVIEWER_CONFIG, or pass --map"
    db = Path(args.db or "test-configs/mem_probe.db")
    # AppConfig is a slots dataclass with NO field defaults — every field must
    # be supplied (config_path/banlist_file/dsn included) or construction fails.
    return AppConfig(
        config_path=db.with_suffix(".ini"),
        port=8000,
        map_file=Path(args.map),
        cluster_dir=Path(args.cluster) if args.cluster else None,
        banlist_file=None,
        debug=False,
        dsn="",
        api_key="",
        db_path=db,
    )


class _RSSSampler(threading.Thread):
    """Background RSS peak sampler (psutil)."""

    def __init__(self, interval_s: float = 0.2) -> None:
        super().__init__(daemon=True)
        self.interval_s = interval_s
        self.peak_bytes = 0
        self._stop = threading.Event()
        import psutil  # noqa: PLC0415

        self._proc: t.Any = psutil.Process()

    def run(self) -> None:
        while not self._stop.is_set():
            rss = int(self._proc.memory_info().rss)
            if rss > self.peak_bytes:
                self.peak_bytes = rss
            self._stop.wait(self.interval_s)

    def stop(self) -> None:
        self._stop.set()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--map")
    ap.add_argument("--cluster")
    ap.add_argument("--db")
    args = ap.parse_args()

    cfg = _build_config(args)
    assert (
        cfg.map_file is not None and cfg.map_file.exists()
    ), f"missing map: {cfg.map_file}"
    init_schema(cfg.db_path)
    print(f"map           : {cfg.map_file}")

    sampler = _RSSSampler()
    sampler.start()
    tracemalloc.start()
    t0 = time.perf_counter()
    result = ingest_full(cfg)
    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    sampler.stop()
    sampler.join(timeout=1.0)

    mib = 1024 * 1024
    print(
        f"elapsed       : {elapsed:.1f}s (ingest_full reported {result.elapsed_s:.1f}s)"
    )
    print(f"tracemalloc   : peak {peak / mib:,.0f} MiB (Python allocations)")
    print(f"rss peak      : {sampler.peak_bytes / mib:,.0f} MiB (psutil)")
    print(
        "rows          : "
        f"tamed={result.tamed} wild={result.wild} players={result.players} "
        f"tribes={result.tribes} structures={result.structures} "
        f"tribelogs={result.tribelogs} mapstructures={result.mapstructures} "
        f"cluster={result.cluster_files}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
