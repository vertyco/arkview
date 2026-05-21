import sqlite3  # noqa: F401
import typing as t  # noqa: F401
from pathlib import Path

import pytest

from app.db import (
    aconnect,
    ameta_get,
    aselect_all,
    aselect_where,
    batch_insert,
    init_schema,
    meta_set,
)

pytestmark = pytest.mark.asyncio


async def test_aconnect_context_manager_yields_connection(tmp_path: Path) -> None:
    db_path = tmp_path / "av.db"
    init_schema(db_path)
    async with aconnect(db_path) as conn:
        cursor = await conn.execute("SELECT 1")
        row = await cursor.fetchone()
    assert row[0] == 1


async def test_ameta_get_returns_value(tmp_path: Path) -> None:
    db_path = tmp_path / "av.db"
    init_schema(db_path)
    meta_set(db_path, "day", "42")
    val = await ameta_get(db_path, "day")
    assert val == "42"


async def test_ameta_get_missing_returns_none(tmp_path: Path) -> None:
    db_path = tmp_path / "av.db"
    init_schema(db_path)
    assert await ameta_get(db_path, "nope") is None


async def test_aselect_all_returns_dicts(tmp_path: Path) -> None:
    db_path = tmp_path / "av.db"
    init_schema(db_path)
    batch_insert(
        db_path,
        "tamed",
        [
            {
                "tribeid": 1,
                "creature": "Rex",
                "lvl": 200,
                "cryo": False,
                "raw": {"k": "v"},
            },
            {
                "tribeid": 2,
                "creature": "Wyvern",
                "lvl": 150,
                "cryo": True,
                "raw": {"x": 1},
            },
        ],
    )
    rows = await aselect_all(db_path, "tamed")
    assert len(rows) == 2
    assert {r["k"] for r in rows if "k" in r} == {"v"}
    assert {r["x"] for r in rows if "x" in r} == {1}


async def test_aselect_where_filters_on_indexed_column(tmp_path: Path) -> None:
    db_path = tmp_path / "av.db"
    init_schema(db_path)
    batch_insert(
        db_path,
        "tamed",
        [
            {
                "tribeid": 1,
                "creature": "Rex",
                "lvl": 1,
                "cryo": False,
                "raw": {"tribeid": 1},
            },
            {
                "tribeid": 2,
                "creature": "Wyvern",
                "lvl": 1,
                "cryo": False,
                "raw": {"tribeid": 2},
            },
        ],
    )
    rows = await aselect_where(db_path, "tamed", [("tribeid", 2)])
    assert len(rows) == 1
    assert rows[0]["tribeid"] == 2


async def test_aselect_where_multi_clause_and(tmp_path: Path) -> None:
    db_path = tmp_path / "av.db"
    init_schema(db_path)
    batch_insert(
        db_path,
        "tamed",
        [
            {
                "tribeid": 1,
                "creature": "Rex",
                "lvl": 1,
                "cryo": False,
                "raw": {"id": 1},
            },
            {
                "tribeid": 1,
                "creature": "Wyvern",
                "lvl": 1,
                "cryo": True,
                "raw": {"id": 2},
            },
        ],
    )
    rows = await aselect_where(db_path, "tamed", [("tribeid", 1), ("cryo", 1)])
    assert [r["id"] for r in rows] == [2]


async def test_aselect_where_unknown_table_raises(tmp_path: Path) -> None:
    db_path = tmp_path / "av.db"
    init_schema(db_path)
    with pytest.raises(AssertionError):
        await aselect_where(db_path, "bogus", [])
