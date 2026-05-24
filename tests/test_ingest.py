import json
import sqlite3  # noqa: F401
import typing as t  # noqa: F401
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest  # noqa: F401

from app.config import AppConfig
from app.db import connect, init_schema, meta_get
from app.ingest import (
    IngestResult,
    _load_cluster_invs,
    ingest_full,
    ingest_profile,
    ingest_tribe,
)


def _make_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        config_path=tmp_path / "config.ini",
        port=8000,
        map_file=tmp_path / "TheIsland.ark",
        cluster_dir=None,
        banlist_file=None,
        debug=False,
        dsn="",
        api_key="",
        db_path=tmp_path / "av.db",
    )


FAKE_TAMED = [
    {
        "tribeid": 1,
        "dinoid": "100",
        "creature": "Rex_Character_BP_C",
        "lvl": 200,
        "cryo": False,
        "ccc": "0 0 0",
    },
    {
        "tribeid": 2,
        "dinoid": "200",
        "creature": "Wyvern_Character_BP_C",
        "lvl": 150,
        "cryo": True,
        "ccc": "0 0 0",
    },
]
FAKE_WILD = [
    {"creature": "Rex_Character_BP_C", "lvl": 30, "tameable": True, "ccc": "1 2 3"},
]
FAKE_PLAYERS = [
    {"playerid": 99, "steamid": "76561198000000000", "tribeid": 1, "name": "Bob"},
]
FAKE_TRIBES = [{"tribeid": 1, "tribe": "Alpha"}]
FAKE_STRUCTURES = [{"tribeid": 1, "struct": "MetalWall_C"}]
FAKE_TRIBELOGS = [{"tribeid": 1, "logs": ["Day 1"]}]
FAKE_MAPSTRUCTURES = [{"struct": "ASV_Terminal", "ccc": "0 0 0"}]


def _patch_ingest_full(
    tamed: list[t.Any] | None = None,
    wild: list[t.Any] | None = None,
    players: list[t.Any] | None = None,
    tribes: list[t.Any] | None = None,
    structures: list[t.Any] | None = None,
    tribelogs: list[t.Any] | None = None,
    mapstructures: list[t.Any] | None = None,
    day: int = 5,
    time_text: str = "12:00",
) -> list[t.Any]:
    """Return a list of patch CMs covering _load_world, _load_cluster_invs, and arkparser exporters.

    ingest_full uses inline `from arkparser import ...` so we patch the functions
    at their canonical location in `arkparser` (the module ingest_full imports from).
    _load_world and _load_cluster_invs are module-level helpers so we patch them
    on app.ingest directly.
    """
    fake_save = MagicMock()
    fake_mc = MagicMock()
    return [
        patch(
            "app.ingest._load_world", return_value=(fake_save, fake_mc, day, time_text)
        ),
        patch("app.ingest._load_cluster_invs", return_value=[]),
        patch(
            "arkparser.export_tamed",
            return_value=tamed if tamed is not None else FAKE_TAMED,
        ),
        patch(
            "arkparser.export_wild",
            return_value=wild if wild is not None else FAKE_WILD,
        ),
        patch(
            "arkparser.export_players",
            return_value=players if players is not None else FAKE_PLAYERS,
        ),
        patch(
            "arkparser.export_tribes",
            return_value=tribes if tribes is not None else FAKE_TRIBES,
        ),
        patch(
            "arkparser.export_structures",
            return_value=structures if structures is not None else FAKE_STRUCTURES,
        ),
        patch(
            "arkparser.export_tribe_logs",
            return_value=tribelogs if tribelogs is not None else FAKE_TRIBELOGS,
        ),
        patch(
            "arkparser.export_map_structures",
            return_value=mapstructures
            if mapstructures is not None
            else FAKE_MAPSTRUCTURES,
        ),
        patch("arkparser.export_cluster_uploads", return_value=[]),
    ]


def test_ingest_full_populates_every_table(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    init_schema(cfg.db_path)
    from contextlib import ExitStack

    with ExitStack() as stack:
        for p in _patch_ingest_full():
            stack.enter_context(p)
        result = ingest_full(cfg)

    assert isinstance(result, IngestResult)
    assert result.tamed == 2
    assert result.wild == 1
    assert result.players == 1
    assert result.tribes == 1
    assert result.structures == 1
    assert result.tribelogs == 1
    assert result.mapstructures == 1

    with connect(cfg.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tamed").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM wild").fetchone()[0] == 1


def test_ingest_full_updates_meta(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    init_schema(cfg.db_path)
    from contextlib import ExitStack

    with ExitStack() as stack:
        for p in _patch_ingest_full(day=5, time_text="12:00"):
            stack.enter_context(p)
        ingest_full(cfg)
    assert meta_get(cfg.db_path, "day") == "5"
    assert meta_get(cfg.db_path, "time") == "12:00"
    assert meta_get(cfg.db_path, "last_parse_at") is not None
    int(meta_get(cfg.db_path, "last_parse_at"))  # parseable epoch seconds


def test_ingest_full_swap_replaces_old(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    init_schema(cfg.db_path)
    first_tamed = [{"tribeid": 99, "creature": "OldRex_C", "lvl": 1, "cryo": False}]
    from contextlib import ExitStack

    with ExitStack() as stack:
        for p in _patch_ingest_full(tamed=first_tamed, day=1, time_text="01:00"):
            stack.enter_context(p)
        ingest_full(cfg)
    with ExitStack() as stack:
        for p in _patch_ingest_full(day=2, time_text="02:00"):
            stack.enter_context(p)
        ingest_full(cfg)
    with connect(cfg.db_path) as conn:
        rows = conn.execute("SELECT creature FROM tamed ORDER BY creature").fetchall()
    assert [r["creature"] for r in rows] == [
        "Rex_Character_BP_C",
        "Wyvern_Character_BP_C",
    ]


def test_ingest_profile_single_record(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    init_schema(cfg.db_path)
    fake_player = {"playerid": 7, "steamid": "abc", "tribeid": 3, "name": "Solo"}
    profile_file = tmp_path / "abc.arkprofile"
    profile_file.touch()
    with patch("app.ingest._load_one_profile", return_value=fake_player):
        n = ingest_profile(cfg, profile_file)
    assert n == 1
    with connect(cfg.db_path) as conn:
        row = conn.execute("SELECT raw FROM players WHERE playerid=7").fetchone()
    assert json.loads(row["raw"])["name"] == "Solo"


def test_ingest_tribe_single_record(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    init_schema(cfg.db_path)
    tribe_file = tmp_path / "42.arktribe"
    tribe_file.touch()
    fake_tribe = {"tribeid": 42, "tribe": "Bravo"}
    fake_log = {"tribeid": 42, "logs": ["Day 7"]}
    with patch("app.ingest._load_one_tribe", return_value=(fake_tribe, fake_log)):
        n = ingest_tribe(cfg, tribe_file)
    assert n == 1
    with connect(cfg.db_path) as conn:
        t_row = conn.execute("SELECT raw FROM tribes WHERE tribeid=42").fetchone()
        l_row = conn.execute("SELECT raw FROM tribelogs WHERE tribeid=42").fetchone()
    assert json.loads(t_row["raw"])["tribe"] == "Bravo"
    assert json.loads(l_row["raw"])["logs"] == ["Day 7"]


def _cfg(tmp_path: Path, cluster_dir: Path | None) -> AppConfig:
    # AppConfig is a slots dataclass with no field defaults — supply all 9.
    return AppConfig(
        config_path=tmp_path / "config.ini",
        port=8000,
        map_file=tmp_path / "Map.ark",
        cluster_dir=cluster_dir,
        banlist_file=None,
        debug=False,
        dsn="",
        api_key="",
        db_path=tmp_path / "v.db",
    )


def test_load_cluster_invs_skips_sub16_byte_stubs(tmp_path: Path) -> None:
    cdir = tmp_path / "clusters"
    cdir.mkdir()
    (cdir / "tiny").write_bytes(b"\x00\x01\x02")  # < 16 bytes → skipped silently
    (cdir / "empty").write_bytes(b"")  # 0 bytes → skipped
    result = _load_cluster_invs(_cfg(tmp_path, cdir))
    assert result == []


def test_load_cluster_invs_none_dir_returns_empty(tmp_path: Path) -> None:
    assert _load_cluster_invs(_cfg(tmp_path, None)) == []


def test_load_cluster_invs_skips_unparsable_file(tmp_path: Path) -> None:
    # A >=16-byte file passes the stub gate but fails CloudInventory.load — it
    # must be skipped (warning logged), not raised, so one corrupt cluster file
    # can't abort the whole reparse. This is the path that emits the production
    # "Attempted to read N bytes" warnings.
    cdir = tmp_path / "clusters"
    cdir.mkdir()
    (cdir / "corrupt").write_bytes(b"\x00" * 64)  # >=16 bytes → reaches .load
    with patch("arkparser.CloudInventory.load", side_effect=ValueError("boom")):
        result = _load_cluster_invs(_cfg(tmp_path, cdir))
    assert result == []
