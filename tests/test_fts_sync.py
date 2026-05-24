"""FTS sidecar stays in sync after incremental profile/tribe reparses.

Regression guard for the bug where `ingest_profile` / `ingest_tribe` did
DELETE + batch_insert on the base table but never updated `<table>_search`,
so `/data/search/{players,tribes}` returned stale, missing, or — on rowid
reuse — the WRONG row between full world reparses.
"""

import typing as t  # noqa: F401
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

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


def _ingest_patches(
    player_name: str, tribe_name: str, day: int = 1, time_text: str = "00:00"
) -> list[t.Any]:
    """Return patches for _load_world + all arkparser export functions.

    ingest_full uses inline `from arkparser import ...` so we patch at
    `arkparser.<func>` (the module ingest_full imports from at call time).
    """
    fake_save = MagicMock()
    fake_mc = MagicMock()
    return [
        patch(
            "app.ingest._load_world", return_value=(fake_save, fake_mc, day, time_text)
        ),
        patch("app.ingest._load_cluster_invs", return_value=[]),
        patch("arkparser.export_tamed", return_value=[]),
        patch(
            "arkparser.export_players",
            return_value=[
                {"playerid": 7, "steamid": "abc", "tribeid": 3, "name": player_name}
            ],
        ),
        patch(
            "arkparser.export_tribes",
            return_value=[{"tribeid": 3, "tribe": tribe_name}],
        ),
        patch("arkparser.export_wild", return_value=[]),
        patch("arkparser.export_structures", return_value=[]),
        patch("arkparser.export_tribe_logs", return_value=[]),
        patch("arkparser.export_map_structures", return_value=[]),
        patch("arkparser.export_cluster_uploads", return_value=[]),
    ]


async def test_profile_reparse_updates_player_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _cfg(tmp_path)
    init_schema(cfg.db_path)
    with ExitStack() as stack:
        for p in _ingest_patches("OldName", "Alpha"):
            stack.enter_context(p)
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
    with ExitStack() as stack:
        for p in _ingest_patches("Bob", "OldTribe"):
            stack.enter_context(p)
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
