import typing as t  # noqa: F401
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.db import batch_insert, init_schema, meta_set
from app.routers.tribes import build_router


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


def _seed(cfg: AppConfig) -> None:
    init_schema(cfg.db_path)
    meta_set(cfg.db_path, "last_parse_at", "9999999999")
    meta_set(cfg.db_path, "day", "1")
    meta_set(cfg.db_path, "time", "00:00")
    batch_insert(
        cfg.db_path,
        "players",
        [
            {
                "playerid": 1,
                "steamid": "76561198000000001",
                "tribeid": 100,
                "raw": {"playerid": 1, "steamid": "76561198000000001", "tribeid": 100},
            },
        ],
    )
    batch_insert(
        cfg.db_path,
        "tamed",
        [
            {
                "tribeid": 100,
                "creature": "Rex_C",
                "lvl": 1,
                "cryo": False,
                "raw": {"tribeid": 100, "creature": "Rex_C"},
            },
            {
                "tribeid": 100,
                "creature": "Rex_C",
                "lvl": 1,
                "cryo": False,
                "raw": {"tribeid": 100, "creature": "Rex_C"},
            },
            {
                "tribeid": 200,
                "creature": "Argy_C",
                "lvl": 1,
                "cryo": False,
                "raw": {"tribeid": 200, "creature": "Argy_C"},
            },
        ],
    )


def _client(cfg: AppConfig) -> TestClient:
    app = FastAPI()
    app.include_router(build_router(cfg))
    return TestClient(app)


def test_tribetames_returns_tribe_creatures(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _seed(cfg)
    # Seed a matching tribe so the response can echo it back.
    batch_insert(
        cfg.db_path,
        "tribes",
        [{"tribeid": 100, "raw": {"tribeid": 100, "name": "TribeOne"}}],
    )
    r = _client(cfg).get("/tribetames/76561198000000001")
    assert r.status_code == 200
    body = r.json()
    assert len(body["tamed"]) == 2
    assert len(body["tribes"]) == 1
    assert body["tribes"][0]["tribeid"] == 100


def test_tribetames_unknown_player_404(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _seed(cfg)
    r = _client(cfg).get("/tribetames/0000")
    assert r.status_code == 404


def test_overlimit_lists_tribes_over_threshold(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _seed(cfg)
    r = _client(cfg).get("/overlimit/1")
    assert r.status_code == 200
    body = r.json()
    # New shape: {steamid: [tame, ...]} for every player in over-limit tribes.
    over = body["overlimit"]
    assert "76561198000000001" in over
    assert len(over["76561198000000001"]) == 2
