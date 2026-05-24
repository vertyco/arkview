"""FTS sidecar stays in sync after incremental profile/tribe reparses.

Regression guard for the bug where `ingest_profile` / `ingest_tribe` did
DELETE + batch_insert on the base table but never updated `<table>_search`,
so `/data/search/{players,tribes}` returned stale, missing, or — on rowid
reuse — the WRONG row between full world reparses.
"""

import typing as t  # noqa: F401
from pathlib import Path

import pytest

from app.config import AppConfig
from app.db import asearch, init_schema
from app.ingest import ingest_full, ingest_profile, ingest_tribe

pytestmark = pytest.mark.asyncio


def _cfg(tmp_path: Path) -> AppConfig:
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


def _export(player_name: str, tribe_name: str) -> dict[str, t.Any]:
    return {
        "ASV_Players": [
            {"playerid": 7, "steamid": "abc", "tribeid": 3, "name": player_name},
        ],
        "ASV_Tribes": [{"tribeid": 3, "tribe": tribe_name}],
    }


async def test_profile_reparse_updates_player_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _cfg(tmp_path)
    init_schema(cfg.db_path)
    monkeypatch.setattr(
        "app.ingest._load_world_and_export",
        lambda _cfg: (_export("OldName", "Alpha"), 1, "00:00"),
    )
    ingest_full(cfg)
    assert len(await asearch(cfg.db_path, "players", "OldName")) == 1

    # Player renames -> a single .arkprofile write fires ingest_profile.
    monkeypatch.setattr(
        "app.ingest._load_one_profile",
        lambda _path: {
            "playerid": 7,
            "steamid": "abc",
            "tribeid": 3,
            "name": "NewName",
        },
    )
    ingest_profile(cfg, tmp_path / "abc.arkprofile")

    # New name must be searchable; old name must not linger.
    assert len(await asearch(cfg.db_path, "players", "NewName")) == 1
    assert await asearch(cfg.db_path, "players", "OldName") == []


async def test_tribe_reparse_updates_tribe_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _cfg(tmp_path)
    init_schema(cfg.db_path)
    monkeypatch.setattr(
        "app.ingest._load_world_and_export",
        lambda _cfg: (_export("Bob", "OldTribe"), 1, "00:00"),
    )
    ingest_full(cfg)
    assert len(await asearch(cfg.db_path, "tribes", "OldTribe")) == 1

    monkeypatch.setattr(
        "app.ingest._load_one_tribe",
        lambda _path: ({"tribeid": 3, "tribe": "NewTribe"}, {"tribeid": 3, "logs": []}),
    )
    ingest_tribe(cfg, tmp_path / "3.arktribe")

    assert len(await asearch(cfg.db_path, "tribes", "NewTribe")) == 1
    assert await asearch(cfg.db_path, "tribes", "OldTribe") == []


async def test_search_control_byte_query_returns_empty(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    init_schema(cfg.db_path)
    # A query of only control bytes tokenizes to nothing; must return [] not 500.
    assert await asearch(cfg.db_path, "tamed", "\x00") == []
    assert await asearch(cfg.db_path, "tamed", "\x01\x02") == []
