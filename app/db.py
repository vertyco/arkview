import contextlib
import json
import sqlite3
import typing as t
from pathlib import Path

import aiosqlite

META_TABLE: t.Final[str] = "meta"

# Each row stores the indexable columns we filter on, plus the full ASV-legacy
# dict in `raw` JSON. Routers select `raw` and json.loads on the way out, so
# the wire shape stays bit-for-bit identical to arkparser's export.
DATASET_TABLES: t.Final[tuple[str, ...]] = (
    "tamed",
    "wild",
    "players",
    "tribes",
    "structures",
    "tribelogs",
    "mapstructures",
    "cluster_inventory",
)


_SCHEMA_DDL: t.Final[
    str
] = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tamed (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    tribeid INTEGER NOT NULL DEFAULT 0,
    creature TEXT NOT NULL DEFAULT '',
    lvl INTEGER NOT NULL DEFAULT 0,
    cryo INTEGER NOT NULL DEFAULT 0,
    raw TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tamed_tribe ON tamed(tribeid);
CREATE INDEX IF NOT EXISTS tamed_class ON tamed(creature);
CREATE INDEX IF NOT EXISTS tamed_cryo ON tamed(cryo);

CREATE TABLE IF NOT EXISTS wild (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    creature TEXT NOT NULL DEFAULT '',
    lvl INTEGER NOT NULL DEFAULT 0,
    tameable INTEGER NOT NULL DEFAULT 0,
    raw TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS wild_class ON wild(creature);
CREATE INDEX IF NOT EXISTS wild_tameable ON wild(tameable);

CREATE TABLE IF NOT EXISTS players (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    playerid INTEGER NOT NULL DEFAULT 0,
    steamid TEXT NOT NULL DEFAULT '',
    tribeid INTEGER NOT NULL DEFAULT 0,
    raw TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS players_tribe ON players(tribeid);
CREATE INDEX IF NOT EXISTS players_steam ON players(steamid);
CREATE INDEX IF NOT EXISTS players_pid ON players(playerid);

CREATE TABLE IF NOT EXISTS tribes (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    tribeid INTEGER NOT NULL DEFAULT 0,
    raw TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tribes_tribe ON tribes(tribeid);

CREATE TABLE IF NOT EXISTS structures (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    tribeid INTEGER NOT NULL DEFAULT 0,
    struct TEXT NOT NULL DEFAULT '',
    raw TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS struct_tribe ON structures(tribeid);
CREATE INDEX IF NOT EXISTS struct_class ON structures(struct);

CREATE TABLE IF NOT EXISTS tribelogs (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    tribeid INTEGER NOT NULL DEFAULT 0,
    raw TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tribelog_tribe ON tribelogs(tribeid);

CREATE TABLE IF NOT EXISTS mapstructures (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    struct TEXT NOT NULL DEFAULT '',
    raw TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS mapstruct_type ON mapstructures(struct);

CREATE TABLE IF NOT EXISTS cluster_inventory (
    file_id TEXT PRIMARY KEY,
    raw TEXT NOT NULL
);
"""


_INDEX_COLS: t.Final[dict[str, tuple[str, ...]]] = {
    "tamed": ("tribeid", "creature", "lvl", "cryo"),
    "wild": ("creature", "lvl", "tameable"),
    "players": ("playerid", "steamid", "tribeid"),
    "tribes": ("tribeid",),
    "structures": ("tribeid", "struct"),
    "tribelogs": ("tribeid",),
    "mapstructures": ("struct",),
    "cluster_inventory": ("file_id",),
}


@contextlib.contextmanager
def connect(db_path: Path) -> t.Iterator[sqlite3.Connection]:
    assert isinstance(db_path, Path)
    conn = sqlite3.connect(
        db_path, isolation_level=None
    )  # autocommit; we use BEGIN explicitly
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
    finally:
        conn.close()


def init_schema(db_path: Path) -> None:
    assert isinstance(db_path, Path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.executescript(_SCHEMA_DDL)


def _row_to_params(table: str, row: dict[str, t.Any]) -> dict[str, t.Any]:
    cols = _INDEX_COLS[table]
    out: dict[str, t.Any] = {}
    for c in cols:
        v = row.get(
            c, 0 if c not in ("creature", "struct", "steamid", "file_id") else ""
        )
        if c == "cryo" or c == "tameable":
            v = 1 if v else 0
        out[c] = v
    raw = row.get("raw", {})
    out["raw"] = json.dumps(raw, separators=(",", ":"), default=str)
    return out


def batch_insert(db_path: Path, table: str, rows: t.Iterable[dict[str, t.Any]]) -> int:
    assert table in DATASET_TABLES, f"unknown table {table}"
    cols = _INDEX_COLS[table]
    placeholders = ", ".join(f":{c}" for c in cols) + ", :raw"
    column_list = ", ".join(cols) + ", raw"
    sql = f"INSERT INTO {table} ({column_list}) VALUES ({placeholders})"
    count = 0
    with connect(db_path) as conn:
        conn.execute("BEGIN")
        try:
            for chunk in _chunked(rows, 500):
                params = [_row_to_params(table, r) for r in chunk]
                conn.executemany(sql, params)
                count += len(params)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return count


def swap_staging(db_path: Path, table: str, rows: t.Iterable[dict[str, t.Any]]) -> int:
    """Write `rows` to a staging table, then atomically replace `table`.

    Precondition: `table` is a known DATASET_TABLES entry.
    Postcondition: only the new rows are visible; old rows are gone.
    """
    assert table in DATASET_TABLES, f"unknown table {table}"
    cols = _INDEX_COLS[table]
    placeholders = ", ".join(f":{c}" for c in cols) + ", :raw"
    column_list = ", ".join(cols) + ", raw"
    count = 0
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(f"DROP TABLE IF EXISTS {table}_new")
            conn.execute(f"CREATE TABLE {table}_new AS SELECT * FROM {table} WHERE 0")
            sql = f"INSERT INTO {table}_new ({column_list}) VALUES ({placeholders})"
            for chunk in _chunked(rows, 500):
                params = [_row_to_params(table, r) for r in chunk]
                conn.executemany(sql, params)
                count += len(params)
            conn.execute(f"DROP TABLE {table}")
            conn.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
            # Indexes were dropped with the table; recreate from DDL. We
            # split on ';' and run each statement via execute() because
            # executescript() implicitly commits the open transaction.
            for stmt in _SCHEMA_DDL.split(";"):
                if stmt.strip():
                    conn.execute(stmt)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return count


def meta_set(db_path: Path, key: str, value: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def meta_get(db_path: Path, key: str) -> str | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def _chunked(
    items: t.Iterable[dict[str, t.Any]], size: int
) -> t.Iterator[list[dict[str, t.Any]]]:
    assert size > 0
    buf: list[dict[str, t.Any]] = []
    for item in items:
        buf.append(item)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf


# ---------------------------------------------------------------------------
# Async readers via aiosqlite. Routers run on FastAPI's event loop and must
# not block on sync sqlite3. Writers stay sync (called from ingest in
# asyncio.to_thread); these are read-only helpers.
# ---------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def aconnect(db_path: Path) -> t.AsyncIterator[aiosqlite.Connection]:
    """Async sqlite connection with the same PRAGMAs as the sync `connect()`.

    Pre: `db_path` is a Path; schema initialised by sync init_schema().
    Post: yields an aiosqlite.Connection with row_factory set to sqlite3.Row.
    """
    assert isinstance(db_path, Path)
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        yield conn
    finally:
        await conn.close()


async def ameta_get(db_path: Path, key: str) -> str | None:
    """Async equivalent of `meta_get`."""
    assert isinstance(db_path, Path)
    assert isinstance(key, str) and key
    async with aconnect(db_path) as conn:
        async with conn.execute("SELECT value FROM meta WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
    return row["value"] if row else None


async def aselect_all(db_path: Path, table: str) -> list[dict[str, t.Any]]:
    """Return every row's `raw` JSON deserialised from `table`.

    Pre: `table` in DATASET_TABLES.
    Post: list of decoded dicts; empty list if table is empty.
    """
    assert table in DATASET_TABLES, f"unknown table {table}"
    async with aconnect(db_path) as conn:
        async with conn.execute(f"SELECT raw FROM {table}") as cur:
            rows = await cur.fetchall()
    return [json.loads(r["raw"]) for r in rows]


async def aselect_where(
    db_path: Path,
    table: str,
    clauses: list[tuple[str, t.Any]],
) -> list[dict[str, t.Any]]:
    """Return rows matching every `(col, value)` clause (AND-joined).

    Pre: `table` in DATASET_TABLES; each clause is `(indexed_col, value)`.
    Empty clauses returns every row (same as aselect_all).
    Post: list of decoded `raw` JSON dicts.
    """
    assert table in DATASET_TABLES, f"unknown table {table}"
    assert all(isinstance(c, tuple) and len(c) == 2 for c in clauses)
    if not clauses:
        return await aselect_all(db_path, table)
    where = " AND ".join(f"{col}=?" for col, _ in clauses)
    params = tuple(v for _, v in clauses)
    async with aconnect(db_path) as conn:
        async with conn.execute(
            f"SELECT raw FROM {table} WHERE {where}", params
        ) as cur:
            rows = await cur.fetchall()
    return [json.loads(r["raw"]) for r in rows]
