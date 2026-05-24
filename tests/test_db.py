import json
import sqlite3  # noqa: F401
import typing as t  # noqa: F401
from pathlib import Path

import pytest  # noqa: F401

from app.db import (
    DATASET_TABLES,
    META_TABLE,
    batch_insert,
    connect,
    init_schema,
    meta_get,
    meta_set,
    swap_staging,
)


def test_init_schema_creates_all_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "av.db"
    init_schema(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    names = {r["name"] for r in rows}
    for tbl in DATASET_TABLES:
        assert tbl in names, f"missing table {tbl}"
    assert META_TABLE in names


def test_wal_mode_enabled(tmp_path: Path) -> None:
    db_path = tmp_path / "av.db"
    init_schema(db_path)
    with connect(db_path) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_batch_insert_then_select(tmp_path: Path) -> None:
    db_path = tmp_path / "av.db"
    init_schema(db_path)
    rows = [
        {"tribeid": 1, "creature": "Rex", "lvl": 200, "cryo": False, "raw": {"k": "v"}},
        {"tribeid": 1, "creature": "Wyvern", "lvl": 150, "cryo": True, "raw": {}},
    ]
    batch_insert(db_path, "tamed", rows)
    with connect(db_path) as conn:
        out = conn.execute(
            "SELECT creature, lvl, cryo FROM tamed ORDER BY lvl DESC"
        ).fetchall()
    assert [dict(r) for r in out] == [
        {"creature": "Rex", "lvl": 200, "cryo": 0},
        {"creature": "Wyvern", "lvl": 150, "cryo": 1},
    ]


def test_swap_staging_is_atomic(tmp_path: Path) -> None:
    db_path = tmp_path / "av.db"
    init_schema(db_path)
    batch_insert(
        db_path,
        "tamed",
        [{"tribeid": 1, "creature": "Old", "lvl": 1, "cryo": False, "raw": {}}],
    )
    swap_staging(
        db_path,
        "tamed",
        [{"tribeid": 2, "creature": "New", "lvl": 2, "cryo": False, "raw": {}}],
    )
    with connect(db_path) as conn:
        rows = conn.execute("SELECT creature FROM tamed").fetchall()
    assert [r["creature"] for r in rows] == ["New"]


def test_init_schema_stamps_arkparser_version(tmp_path: Path) -> None:
    from app.constants import ARKPARSER_VERSION

    db_path = tmp_path / "av.db"
    init_schema(db_path)
    # Stamped at init time (not only after the first full ingest) so an
    # arkparser upgrade still invalidates the cache even when the process
    # crashed before any ingest completed.
    assert meta_get(db_path, "arkparser_version") == ARKPARSER_VERSION


def test_meta_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "av.db"
    init_schema(db_path)
    meta_set(db_path, "last_parse_at", "1234567890")
    assert meta_get(db_path, "last_parse_at") == "1234567890"
    assert meta_get(db_path, "missing") is None


def test_raw_json_column_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "av.db"
    init_schema(db_path)
    payload = {"a": 1, "b": [1, 2, 3], "c": {"d": True}}
    batch_insert(
        db_path,
        "tamed",
        [{"tribeid": 0, "creature": "X", "lvl": 0, "cryo": False, "raw": payload}],
    )
    with connect(db_path) as conn:
        row = conn.execute("SELECT raw FROM tamed").fetchone()
    assert json.loads(row["raw"]) == payload


# ---------------------------------------------------------------------------
# Version-mismatch behaviour tests
# ---------------------------------------------------------------------------


from app.constants import ARKPARSER_VERSION  # noqa: E402
from app.db import SCHEMA_VERSION  # noqa: E402


def _seed_db(db_path: Path, *, user_version: int, arkparser_version: str) -> None:
    """Create a schema DB then force its stored versions + a sentinel row."""
    init_schema(db_path)
    meta_set(db_path, "arkparser_version", arkparser_version)
    meta_set(db_path, "last_parse_at", "1000")
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO tamed (tribeid, creature, lvl, cryo, uploaded, raw) "
            "VALUES (1, 'Rex', 100, 0, 0, '{}')"
        )
    raw = sqlite3.connect(db_path)
    try:
        raw.execute(f"PRAGMA user_version = {user_version}")
    finally:
        raw.commit()
        raw.close()


def test_arkparser_bump_keeps_data_and_flags_reparse(tmp_path: Path) -> None:
    db = tmp_path / "v.db"
    _seed_db(db, user_version=SCHEMA_VERSION, arkparser_version="0.0.1-old")

    init_schema(db)  # boot after an arkparser upgrade

    with connect(db) as conn:
        n = conn.execute("SELECT COUNT(*) AS c FROM tamed").fetchone()["c"]
    assert n == 1, "arkparser bump must NOT wipe cached rows"
    assert meta_get(db, "last_parse_at") == "1000", "must keep serving (no 503)"
    assert meta_get(db, "reparse_pending") == "1"
    assert meta_get(db, "arkparser_version") == ARKPARSER_VERSION


def test_schema_bump_wipes(tmp_path: Path) -> None:
    db = tmp_path / "v.db"
    _seed_db(db, user_version=SCHEMA_VERSION - 1, arkparser_version=ARKPARSER_VERSION)

    init_schema(db)  # boot after a schema change

    with connect(db) as conn:
        n = conn.execute("SELECT COUNT(*) AS c FROM tamed").fetchone()["c"]
    assert n == 0, "schema mismatch must wipe"
    assert meta_get(db, "last_parse_at") is None
    # A wipe deletes the DB file entirely, so no stale reparse flag survives.
    assert meta_get(db, "reparse_pending") is None


def test_same_versions_no_wipe_no_flag(tmp_path: Path) -> None:
    db = tmp_path / "v.db"
    _seed_db(db, user_version=SCHEMA_VERSION, arkparser_version=ARKPARSER_VERSION)

    init_schema(db)

    with connect(db) as conn:
        n = conn.execute("SELECT COUNT(*) AS c FROM tamed").fetchone()["c"]
    assert n == 1
    assert meta_get(db, "reparse_pending") in (None, "0")
