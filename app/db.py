import contextlib
import json
import logging
import sqlite3
import typing as t
from pathlib import Path

import aiosqlite

log = logging.getLogger("arkviewer.db")

META_TABLE: t.Final[str] = "meta"

# Bump on any schema change. Mismatch at boot → wipe + recreate. The DB is a
# cache (source of truth is the save file); watcher reparses immediately. No
# migration scripts. PRAGMA user_version is free + atomic + lives in the
# header, so no `meta` table dependency needed for the version check.
SCHEMA_VERSION: t.Final[int] = 5

# Tables that get an FTS5 search sidecar. Each gets a `<table>_search` virtual
# table populated at the end of every ingest swap. The sidecar's rowid mirrors
# the source table's rowid so JOIN-back recovers the full raw row.
FTS_TABLES: t.Final[tuple[str, ...]] = ("tamed", "structures", "players", "tribes")

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

-- NB: no explicit `rowid` column. SQLite's implicit rowid is used as the
-- JOIN key against the FTS5 sidecar. Naming a column `rowid INTEGER
-- PRIMARY KEY AUTOINCREMENT` would shadow the implicit rowid, and the
-- `CREATE TABLE foo_new AS SELECT * FROM foo WHERE 0` pattern used by
-- swap_staging drops PRIMARY KEY constraints, so the shadow column would
-- end up NULL — silently breaking JOINs against the FTS table.
CREATE TABLE IF NOT EXISTS tamed (
    tribeid INTEGER NOT NULL DEFAULT 0,
    creature TEXT NOT NULL DEFAULT '',
    lvl INTEGER NOT NULL DEFAULT 0,
    cryo INTEGER NOT NULL DEFAULT 0,
    uploaded INTEGER NOT NULL DEFAULT 0,
    raw TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tamed_tribe ON tamed(tribeid);
CREATE INDEX IF NOT EXISTS tamed_class ON tamed(creature);
CREATE INDEX IF NOT EXISTS tamed_cryo ON tamed(cryo);
CREATE INDEX IF NOT EXISTS tamed_uploaded ON tamed(uploaded);
CREATE INDEX IF NOT EXISTS tamed_cryo_uploaded ON tamed(cryo, uploaded);

CREATE VIRTUAL TABLE IF NOT EXISTS tamed_search USING fts5(
    name, tamer, imprinter, tribe, dinoid,
    tokenize='unicode61 remove_diacritics 2'
);

CREATE VIRTUAL TABLE IF NOT EXISTS structures_search USING fts5(
    name, tribe, struct,
    tokenize='unicode61 remove_diacritics 2'
);

CREATE VIRTUAL TABLE IF NOT EXISTS players_search USING fts5(
    name, steam, tribe, steamid,
    tokenize='unicode61 remove_diacritics 2'
);

CREATE VIRTUAL TABLE IF NOT EXISTS tribes_search USING fts5(
    tribe,
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS wild (
    creature TEXT NOT NULL DEFAULT '',
    lvl INTEGER NOT NULL DEFAULT 0,
    tameable INTEGER NOT NULL DEFAULT 0,
    raw TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS wild_class ON wild(creature);
CREATE INDEX IF NOT EXISTS wild_tameable ON wild(tameable);

CREATE TABLE IF NOT EXISTS players (
    playerid INTEGER NOT NULL DEFAULT 0,
    steamid TEXT NOT NULL DEFAULT '',
    tribeid INTEGER NOT NULL DEFAULT 0,
    raw TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS players_tribe ON players(tribeid);
CREATE INDEX IF NOT EXISTS players_steam ON players(steamid);
CREATE INDEX IF NOT EXISTS players_pid ON players(playerid);

CREATE TABLE IF NOT EXISTS tribes (
    tribeid INTEGER NOT NULL DEFAULT 0,
    raw TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tribes_tribe ON tribes(tribeid);

CREATE TABLE IF NOT EXISTS structures (
    tribeid INTEGER NOT NULL DEFAULT 0,
    struct TEXT NOT NULL DEFAULT '',
    raw TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS struct_tribe ON structures(tribeid);
CREATE INDEX IF NOT EXISTS struct_class ON structures(struct);

CREATE TABLE IF NOT EXISTS tribelogs (
    tribeid INTEGER NOT NULL DEFAULT 0,
    raw TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tribelog_tribe ON tribelogs(tribeid);

CREATE TABLE IF NOT EXISTS mapstructures (
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
    "tamed": ("tribeid", "creature", "lvl", "cryo", "uploaded"),
    "wild": ("creature", "lvl", "tameable"),
    "players": ("playerid", "steamid", "tribeid"),
    "tribes": ("tribeid",),
    "structures": ("tribeid", "struct"),
    "tribelogs": ("tribeid",),
    "mapstructures": ("struct",),
    "cluster_inventory": ("file_id",),
}


def _extract_fts_fields(table: str, raw: dict[str, t.Any]) -> tuple[str, ...]:
    """Pull text fields out of raw JSON for FTS5 indexing.

    Returns values in the order the corresponding `<table>_search` virtual
    table declares columns. Empty string for missing fields — FTS5 tokenizer
    ignores them. Order matches CREATE VIRTUAL TABLE column order in
    `_SCHEMA_DDL` above.
    """
    if table == "tamed":
        return (
            str(raw.get("name") or ""),
            str(raw.get("tamer") or ""),
            str(raw.get("imprinter") or ""),
            str(raw.get("tribe") or ""),
            str(raw.get("dinoid") or ""),
        )
    if table == "structures":
        return (
            str(raw.get("name") or ""),
            str(raw.get("tribe") or ""),
            str(raw.get("struct") or ""),
        )
    if table == "players":
        return (
            str(raw.get("name") or ""),
            str(raw.get("steam") or ""),
            str(raw.get("tribe") or ""),
            str(raw.get("steamid") or ""),
        )
    if table == "tribes":
        return (str(raw.get("tribe") or ""),)
    raise ValueError(f"no FTS extractor for {table}")


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


def _read_user_version(db_path: Path) -> int:
    """Read `PRAGMA user_version` from a possibly-empty DB. Returns 0 if absent."""
    if not db_path.exists():
        return 0
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("PRAGMA user_version").fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def _wipe_db(db_path: Path) -> None:
    """Delete the DB + WAL/SHM siblings. Caller must hold no open connections."""
    for path in (
        db_path,
        db_path.with_name(db_path.name + "-wal"),
        db_path.with_name(db_path.name + "-shm"),
    ):
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("Failed to unlink %s: %s", path, exc)


def init_schema(db_path: Path) -> None:
    """Create or recreate the schema, wiping on SCHEMA_VERSION mismatch.

    DB is a cache, not a system of record. Schema mismatches → wipe + rebuild
    rather than risk migration bugs. Cold start serves 503 until next reparse.
    """
    assert isinstance(db_path, Path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    current = _read_user_version(db_path)
    if current != 0 and current != SCHEMA_VERSION:
        log.warning(
            "Schema version mismatch (db=%d, code=%d). Wiping %s.",
            current,
            SCHEMA_VERSION,
            db_path,
        )
        _wipe_db(db_path)
    with connect(db_path) as conn:
        conn.executescript(_SCHEMA_DDL)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


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
    Postcondition: only the new rows are visible; old rows are gone. For FTS
    tables the `<table>_search` sidecar is rebuilt in the same transaction.
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
            # Skip CREATE VIRTUAL TABLE statements (FTS5) — those persist
            # across the swap and we rebuild their contents separately below.
            for stmt in _SCHEMA_DDL.split(";"):
                s = stmt.strip()
                if not s:
                    continue
                if "VIRTUAL TABLE" in s.upper():
                    continue
                conn.execute(s)
            if table in FTS_TABLES:
                _rebuild_fts_inside_txn(conn, table)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return count


def _rebuild_fts_inside_txn(conn: sqlite3.Connection, table: str) -> None:
    """Rebuild `<table>_search` from `<table>.raw`. Caller owns the txn.

    Wipes the sidecar then re-inserts one row per source row, keeping FTS5
    rowid == source rowid so the search router can JOIN back to recover the
    full row. Runs CPU on the arkviewer side; deliberate trade-off — bot is
    the constrained party.
    """
    fts = f"{table}_search"
    conn.execute(f"DELETE FROM {fts}")
    cur = conn.execute(f"SELECT rowid, raw FROM {table}")
    col_count = len(_extract_fts_fields(table, {}))
    placeholders = ", ".join("?" for _ in range(col_count))
    # Insert with explicit rowid so we can JOIN back: tamed_search.rowid = tamed.rowid.
    insert_sql = f"INSERT INTO {fts}(rowid, {_fts_column_list(table)}) VALUES (?, {placeholders})"
    batch: list[tuple[t.Any, ...]] = []
    for row in cur:
        try:
            data = json.loads(row["raw"])
        except (json.JSONDecodeError, TypeError):
            continue
        fields = _extract_fts_fields(table, data)
        batch.append((row["rowid"], *fields))
        if len(batch) >= 1000:
            conn.executemany(insert_sql, batch)
            batch.clear()
    if batch:
        conn.executemany(insert_sql, batch)


def _fts_column_list(table: str) -> str:
    """Comma-separated FTS5 column names for INSERT statements."""
    if table == "tamed":
        return "name, tamer, imprinter, tribe, dinoid"
    if table == "structures":
        return "name, tribe, struct"
    if table == "players":
        return "name, steam, tribe, steamid"
    if table == "tribes":
        return "tribe"
    raise ValueError(f"no FTS columns for {table}")


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


def _escape_fts_query(q: str) -> str:
    """Sanitize a user-typed string into a safe FTS5 MATCH expression.

    FTS5 has a small query language (AND, OR, NOT, NEAR, column filters,
    phrase quoting). Surfacing raw input would let callers crash the query
    with stray quotes, colons, or unmatched parens. Strategy: tokenize on
    whitespace, strip FTS5 operator chars, wrap each token in double-quotes
    (which makes it a phrase literal), append `*` for prefix matching.
    """
    # Drop FTS5 operator chars so they can't be interpreted as syntax.
    forbidden = '"():^-'
    tokens: list[str] = []
    for raw_tok in q.split():
        cleaned = "".join(c for c in raw_tok if c not in forbidden).strip()
        if not cleaned:
            continue
        # Quoted phrase + prefix wildcard. e.g. `rex` → `"rex"*` which
        # matches `rex`, `rexx`, `rextoothbrush`. Prefix gives substring-ish
        # behavior at the head of words; full substring needs trigram and
        # isn't worth the complexity here.
        tokens.append(f'"{cleaned}"*')
    return " ".join(tokens)


async def asearch(
    db_path: Path,
    table: str,
    q: str,
    exact_filters: list[tuple[str, t.Any]] | None = None,
    limit: int = 200,
) -> list[dict[str, t.Any]]:
    """FTS5-ranked search against `<table>_search` joined back to `<table>`.

    Pre: `table` in FTS_TABLES; `limit` in [1, 1000].
    Post: list of decoded raw JSON dicts, ordered by FTS5 bm25 rank when `q`
    is non-empty, else by rowid (insertion order). Returns empty list when
    `q` is non-empty but tokenizes to nothing.
    """
    assert table in FTS_TABLES, f"FTS not enabled for {table}"
    assert 1 <= limit <= 1000
    exact_filters = exact_filters or []
    assert all(isinstance(c, tuple) and len(c) == 2 for c in exact_filters)

    where_parts: list[str] = []
    params: list[t.Any] = []
    if q:
        match_expr = _escape_fts_query(q)
        if not match_expr:
            return []
        where_parts.append(f"{table}_search MATCH ?")
        params.append(match_expr)
    for col, val in exact_filters:
        where_parts.append(f"t.{col} = ?")
        params.append(val)
    where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
    order_sql = " ORDER BY rank" if q else " ORDER BY t.rowid"
    sql = (
        f"SELECT t.raw FROM {table} t "
        f"JOIN {table}_search ON {table}_search.rowid = t.rowid"
        f"{where_sql}{order_sql} LIMIT ?"
    )
    params.append(limit)
    async with aconnect(db_path) as conn:
        async with conn.execute(sql, params) as cur:
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
