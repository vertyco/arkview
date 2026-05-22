import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.db import SCHEMA_VERSION, asearch, batch_insert, connect, init_schema, meta_set
from app.routers.search import build_router


def _cfg(tmp_path: Path) -> AppConfig:
    return AppConfig(
        config_path=tmp_path / "config.ini",
        port=8000,
        map_file=None,
        cluster_dir=None,
        banlist_file=None,
        debug=False,
        dsn="",
        api_key="",
        db_path=tmp_path / "av.db",
    )


def _seed_tamed(cfg: AppConfig) -> None:
    init_schema(cfg.db_path)
    meta_set(cfg.db_path, "last_parse_at", "9999999999")
    meta_set(cfg.db_path, "day", "1")
    meta_set(cfg.db_path, "time", "00:00")
    rows = [
        {
            "tribeid": 1,
            "creature": "Rex_Character_BP_C",
            "lvl": 200,
            "cryo": False,
            "uploaded": False,
            "raw": {
                "dinoid": "abc-001",
                "tribeid": 1,
                "creature": "Rex_Character_BP_C",
                "name": "Bessie",
                "tamer": "Alice",
                "imprinter": "Alice",
                "tribe": "TribeA",
            },
        },
        {
            "tribeid": 2,
            "creature": "Argentavis_Character_BP_C",
            "lvl": 100,
            "cryo": True,
            "uploaded": False,
            "raw": {
                "dinoid": "abc-002",
                "tribeid": 2,
                "creature": "Argentavis_Character_BP_C",
                "name": "Sky",
                "tamer": "Bob",
                "imprinter": "",
                "tribe": "TribeB",
            },
        },
        {
            "tribeid": 1,
            "creature": "Rex_Character_BP_C",
            "lvl": 150,
            "cryo": False,
            "uploaded": True,
            "raw": {
                "dinoid": "abc-003",
                "tribeid": 1,
                "creature": "Rex_Character_BP_C",
                "name": "UploadedRex",
                "tamer": "Alice",
                "imprinter": "Alice",
                "tribe": "TribeA",
                "uploaded_from_server": True,
            },
        },
    ]
    batch_insert(cfg.db_path, "tamed", rows)


def _rebuild_fts_after_batch(db_path: Path) -> None:
    """batch_insert doesn't rebuild FTS5 (only swap_staging does). Tests use
    batch_insert for simplicity, so call swap_staging-equivalent: snapshot
    rows + run swap_staging which rebuilds the FTS sidecar. Simpler: just
    invoke the private helper directly.
    """
    from app.db import _rebuild_fts_inside_txn

    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for tbl in ("tamed", "structures", "players", "tribes"):
                # Only rebuild if source table has rows; FTS5 DELETE is no-op
                # on empty table.
                _rebuild_fts_inside_txn(conn, tbl)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def test_schema_version_set_on_init(tmp_path: Path) -> None:
    db_path = tmp_path / "av.db"
    init_schema(db_path)
    conn = sqlite3.connect(db_path)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    assert version == SCHEMA_VERSION


def test_schema_mismatch_wipes_db(tmp_path: Path) -> None:
    db_path = tmp_path / "av.db"
    init_schema(db_path)
    # Insert some data, then forge an old schema version.
    batch_insert(
        db_path,
        "tribes",
        [{"tribeid": 99, "raw": {"tribeid": 99, "tribe": "OldData"}}],
    )
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
    finally:
        conn.close()
    # Re-init should wipe and recreate.
    init_schema(db_path)
    with connect(db_path) as conn:
        rows = conn.execute("SELECT COUNT(*) AS c FROM tribes").fetchone()
    assert rows["c"] == 0  # wiped


def test_fts_table_created(tmp_path: Path) -> None:
    db_path = tmp_path / "av.db"
    init_schema(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%_search'"
        ).fetchall()
    names = {r["name"] for r in rows}
    assert {
        "tamed_search",
        "structures_search",
        "players_search",
        "tribes_search",
    } <= names


@pytest.mark.asyncio
async def test_search_tamed_by_name(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _seed_tamed(cfg)
    _rebuild_fts_after_batch(cfg.db_path)
    rows = await asearch(cfg.db_path, "tamed", "bessie")
    assert len(rows) == 1
    assert rows[0]["name"] == "Bessie"


@pytest.mark.asyncio
async def test_search_tamed_prefix_match(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _seed_tamed(cfg)
    _rebuild_fts_after_batch(cfg.db_path)
    # `up` should hit "UploadedRex" via prefix.
    rows = await asearch(cfg.db_path, "tamed", "up")
    names = {r["name"] for r in rows}
    assert "UploadedRex" in names


@pytest.mark.asyncio
async def test_search_tamed_empty_q_returns_all(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _seed_tamed(cfg)
    _rebuild_fts_after_batch(cfg.db_path)
    rows = await asearch(cfg.db_path, "tamed", "")
    assert len(rows) == 3


@pytest.mark.asyncio
async def test_search_tamed_filter_combines_with_q(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _seed_tamed(cfg)
    _rebuild_fts_after_batch(cfg.db_path)
    # `alice` matches the two TribeA tames; tribe_id filter is redundant but
    # exercises the filter path.
    rows = await asearch(cfg.db_path, "tamed", "alice", [("tribeid", 1)])
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_search_tamed_exact_filter_only(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _seed_tamed(cfg)
    _rebuild_fts_after_batch(cfg.db_path)
    rows = await asearch(cfg.db_path, "tamed", "", [("cryo", 1)])
    assert len(rows) == 1
    assert rows[0]["name"] == "Sky"


@pytest.mark.asyncio
async def test_search_strips_fts_operators(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _seed_tamed(cfg)
    _rebuild_fts_after_batch(cfg.db_path)
    # Quotes, colons, parens must not crash the query.
    rows = await asearch(cfg.db_path, "tamed", 'be"s(s)ie:^-')
    assert len(rows) == 1
    assert rows[0]["name"] == "Bessie"


def test_search_router_tamed(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _seed_tamed(cfg)
    _rebuild_fts_after_batch(cfg.db_path)
    app = FastAPI()
    app.include_router(build_router(cfg))
    client = TestClient(app)
    r = client.get("/data/search/tamed?q=bessie")
    assert r.status_code == 200
    body = r.json()
    assert len(body["tamed"]) == 1
    assert body["tamed"][0]["name"] == "Bessie"
    # meta envelope is spliced in
    assert "version" in body
    assert "day" in body


def test_search_router_tamed_include_filters(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _seed_tamed(cfg)
    _rebuild_fts_after_batch(cfg.db_path)
    app = FastAPI()
    app.include_router(build_router(cfg))
    client = TestClient(app)
    # Empty q + drop cryo + drop uploaded → only Bessie (tribeid 1, not cryo, not uploaded).
    r = client.get("/data/search/tamed?include_cryo=false&include_uploaded=false")
    assert r.status_code == 200
    names = [row["name"] for row in r.json()["tamed"]]
    assert names == ["Bessie"]


def test_search_router_limit_clamp(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _seed_tamed(cfg)
    _rebuild_fts_after_batch(cfg.db_path)
    app = FastAPI()
    app.include_router(build_router(cfg))
    client = TestClient(app)
    r = client.get("/data/search/tamed?limit=2")
    assert r.status_code == 200
    assert len(r.json()["tamed"]) == 2
    # Out-of-range rejected
    assert client.get("/data/search/tamed?limit=0").status_code == 422
    assert client.get("/data/search/tamed?limit=10000").status_code == 422
