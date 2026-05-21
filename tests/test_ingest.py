import json
import sqlite3  # noqa: F401
import typing as t  # noqa: F401
from pathlib import Path
from unittest.mock import patch

import pytest  # noqa: F401

from app.config import AppConfig
from app.db import connect, init_schema, meta_get
from app.ingest import IngestResult, ingest_full, ingest_profile, ingest_tribe


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


FAKE_EXPORT = {
    "ASV_Tamed": [
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
    ],
    "ASV_Wild": [
        {"creature": "Rex_Character_BP_C", "lvl": 30, "tameable": True, "ccc": "1 2 3"},
    ],
    "ASV_Players": [
        {"playerid": 99, "steamid": "76561198000000000", "tribeid": 1, "name": "Bob"},
    ],
    "ASV_Tribes": [{"tribeid": 1, "tribe": "Alpha"}],
    "ASV_Structures": [{"tribeid": 1, "struct": "MetalWall_C"}],
    "ASV_TribeLogs": [{"tribeid": 1, "logs": ["Day 1"]}],
    "ASV_MapStructures": [{"struct": "ASV_Terminal", "ccc": "0 0 0"}],
}


def test_ingest_full_populates_every_table(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    init_schema(cfg.db_path)

    with patch(
        "app.ingest._load_world_and_export", return_value=(FAKE_EXPORT, 5, "12:00")
    ):
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
    with patch(
        "app.ingest._load_world_and_export", return_value=(FAKE_EXPORT, 5, "12:00")
    ):
        ingest_full(cfg)
    assert meta_get(cfg.db_path, "day") == "5"
    assert meta_get(cfg.db_path, "time") == "12:00"
    assert meta_get(cfg.db_path, "last_parse_at") is not None
    int(meta_get(cfg.db_path, "last_parse_at"))  # parseable epoch seconds


def test_ingest_full_swap_replaces_old(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    init_schema(cfg.db_path)
    first = {
        **FAKE_EXPORT,
        "ASV_Tamed": [{"tribeid": 99, "creature": "OldRex_C", "lvl": 1, "cryo": False}],
    }
    with patch("app.ingest._load_world_and_export", return_value=(first, 1, "01:00")):
        ingest_full(cfg)
    with patch(
        "app.ingest._load_world_and_export", return_value=(FAKE_EXPORT, 2, "02:00")
    ):
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
