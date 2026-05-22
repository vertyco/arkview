import json  # noqa: F401
import typing as t  # noqa: F401
from pathlib import Path

import pytest  # noqa: F401
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.db import batch_insert, init_schema, meta_set
from app.routers.data import build_router


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
    meta_set(cfg.db_path, "day", "10")
    meta_set(cfg.db_path, "time", "12:34")
    batch_insert(
        cfg.db_path,
        "tamed",
        [
            {
                "tribeid": 1,
                "creature": "Rex_C",
                "lvl": 200,
                "cryo": False,
                "raw": {"tribeid": 1, "creature": "Rex_C", "lvl": 200, "cryo": False},
            },
            {
                "tribeid": 2,
                "creature": "Wyvern_C",
                "lvl": 150,
                "cryo": True,
                "raw": {"tribeid": 2, "creature": "Wyvern_C", "lvl": 150, "cryo": True},
            },
        ],
    )
    batch_insert(
        cfg.db_path,
        "wild",
        [
            {
                "creature": "Rex_C",
                "lvl": 30,
                "tameable": True,
                "raw": {"creature": "Rex_C", "lvl": 30, "tameable": True},
            },
        ],
    )


def _client(cfg: AppConfig) -> TestClient:
    app = FastAPI()
    app.include_router(build_router(cfg))
    return TestClient(app)


def test_get_data_tamed_envelope(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _seed(cfg)
    r = _client(cfg).get("/data/tamed")
    assert r.status_code == 200
    body = r.json()
    assert body["day"] == 10
    assert body["time"] == "12:34"
    assert isinstance(body["tamed"], list)
    assert {row["creature"] for row in body["tamed"]} == {"Rex_C", "Wyvern_C"}


def test_get_data_unknown_404(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _seed(cfg)
    r = _client(cfg).get("/data/nope")
    assert r.status_code == 404


def test_post_datas_returns_requested_only(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _seed(cfg)
    r = _client(cfg).post("/datas", json={"dtypes": ["tamed", "wild"]})
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) >= {"tamed", "wild", "day", "time"}
    assert len(body["tamed"]) == 2
    assert len(body["wild"]) == 1


def test_filter_tamed_by_tribe(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _seed(cfg)
    r = _client(cfg).get("/data/filter/tamed?tribe_id=2")
    assert r.status_code == 200
    body = r.json()
    assert len(body["tamed"]) == 1
    assert body["tamed"][0]["creature"] == "Wyvern_C"


def test_filter_tamed_by_cryo(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _seed(cfg)
    r = _client(cfg).get("/data/filter/tamed?is_cryo=true")
    assert r.status_code == 200
    body = r.json()
    assert [row["creature"] for row in body["tamed"]] == ["Wyvern_C"]


def test_filter_wild_by_class(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _seed(cfg)
    r = _client(cfg).get("/data/filter/wild?class_name=Rex_C")
    assert r.status_code == 200
    assert len(r.json()["wild"]) == 1
