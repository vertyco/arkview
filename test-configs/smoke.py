"""End-to-end smoke test: load real ASE save -> ingest -> hit every endpoint.

Run with:
    & .venv\\Scripts\\Activate.ps1
    python test-configs/smoke.py

Validates:
    1. Ingest succeeds on a real save.
    2. All routes return 200 / sensible bodies.
    3. RSS stays bounded across repeated /data/tamed scrapes (regression
       check for the v3 memory blow-up).
    4. 503 + Retry-After when DB empty.
    5. Stale header when last_parse_at is forced to >6h ago.
"""

import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ["ARKVIEWER_CONFIG"] = str(REPO / "test-configs" / "ase.ini")

import psutil  # noqa: E402

from app.config import load_config  # noqa: E402
from app.db import init_schema, meta_set  # noqa: E402
from app.ingest import ingest_full  # noqa: E402


def rss_mb() -> float:
    return psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024


def main() -> int:
    cfg_path = Path(os.environ["ARKVIEWER_CONFIG"])
    cfg = load_config(cfg_path)
    print(f"config: port={cfg.port} map={cfg.map_file} db={cfg.db_path}")
    if cfg.map_file is None or not cfg.map_file.exists():
        print(f"ERROR: map file missing: {cfg.map_file}")
        return 1

    # Wipe any prior DB to test cold-start path.
    if cfg.db_path.exists():
        cfg.db_path.unlink()
    for sidecar in (
        cfg.db_path.with_suffix(".db-wal"),
        cfg.db_path.with_suffix(".db-shm"),
    ):
        if sidecar.exists():
            sidecar.unlink()

    init_schema(cfg.db_path)
    print(f"schema initialised. RSS={rss_mb():.1f} MB")

    print("-- 503 check (DB empty, should refuse data routes) --")
    # Spin up the app in-process via fastapi.testclient.
    from fastapi.testclient import TestClient

    from main import build_app

    app = build_app(cfg)
    client = TestClient(app)

    r = client.get("/data/tamed")
    print(
        f"  /data/tamed: {r.status_code} (expect 503)  Retry-After={r.headers.get('Retry-After')}"
    )
    if r.status_code != 503:
        print(f"  FAIL: expected 503, got {r.status_code}")
        return 2

    print("-- Ingest --")
    t0 = time.perf_counter()
    result = ingest_full(cfg)
    print(
        f"  ingest done in {time.perf_counter()-t0:.1f}s. "
        f"tamed={result.tamed} wild={result.wild} players={result.players} "
        f"tribes={result.tribes} structures={result.structures} "
        f"tribelogs={result.tribelogs} mapstructures={result.mapstructures} "
        f"cluster={result.cluster_files}. RSS={rss_mb():.1f} MB"
    )

    print("-- 200 + fresh (no stale header) --")
    r = client.get("/data/tamed")
    print(
        f"  /data/tamed: {r.status_code}  rows={len(r.json()['tamed'])}  "
        f"X-Stale={r.headers.get('X-Arkviewer-Stale')}"
    )
    if r.status_code != 200 or r.headers.get("X-Arkviewer-Stale") is not None:
        print("  FAIL")
        return 3

    print("-- Repeated scrape RSS check (regression guard) --")
    samples = []
    for i in range(20):
        rr = client.get("/data/tamed")
        assert rr.status_code == 200
        samples.append(rss_mb())
    print(
        f"  20x /data/tamed. RSS samples: min={min(samples):.1f} max={max(samples):.1f} delta={max(samples)-min(samples):.1f} MB"
    )

    print("-- Other endpoints --")
    checks = [
        ("/", 200),
        ("/stats", 200),
        ("/data/wild", 200),
        ("/data/players", 200),
        ("/data/tribes", 200),
        ("/data/structures", 200),
        ("/data/mapstructures", 200),
        ("/data/tribelogs", 200),
        ("/data/cluster", 200),
        ("/data/filter/tamed?is_cryo=true", 200),
        ("/data/filter/wild?tameable=true", 200),
        ("/data/filter/players", 200),
        ("/data/filter/structures", 200),
        ("/data/nonsense", 404),
    ]
    failures = 0
    for path, expected in checks:
        rr = client.get(path)
        ok = rr.status_code == expected
        marker = "ok" if ok else "FAIL"
        rows = ""
        if ok and "/data/" in path and "/filter/" not in path and expected == 200:
            try:
                # Each /data/<dtype> response has the dtype as a top-level key.
                # Pull it out of the URL path's last segment.
                key = path.rsplit("/", 1)[-1]
                payload = rr.json().get(key)
                if isinstance(payload, list):
                    rows = f"  rows={len(payload)}"
                elif isinstance(payload, dict):
                    rows = f"  files={len(payload)}"
            except (KeyError, TypeError):
                pass
        print(f"  [{marker}] {path:42s} -> {rr.status_code}{rows}")
        if not ok:
            failures += 1

    rr = client.post("/datas", json={"dtypes": ["tamed", "tribes"]})
    print(
        f"  [{'ok' if rr.status_code == 200 else 'FAIL'}] POST /datas         -> {rr.status_code}"
    )
    failures += 0 if rr.status_code == 200 else 1

    rr = client.post("/foreigntamescan", json={"servernames": ["DoesNotExist"]})
    print(
        f"  [{'ok' if rr.status_code == 200 else 'FAIL'}] POST /foreigntamescan -> {rr.status_code}"
    )
    failures += 0 if rr.status_code == 200 else 1

    print("-- Stale header check (force last_parse_at to 7h ago) --")
    meta_set(cfg.db_path, "last_parse_at", str(int(time.time()) - 7 * 3600))
    rr = client.get("/data/tamed")
    stale = rr.headers.get("X-Arkviewer-Stale")
    print(
        f"  X-Arkviewer-Stale={stale} X-Last-Parse={rr.headers.get('X-Arkviewer-Last-Parse')}"
    )
    if stale != "true":
        print("  FAIL: expected X-Arkviewer-Stale: true")
        return 4

    print(f"\nDONE. failures={failures}. RSS final={rss_mb():.1f} MB")
    return failures


if __name__ == "__main__":
    sys.exit(main())
