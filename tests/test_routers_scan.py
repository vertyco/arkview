import typing as t  # noqa: F401
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.db import batch_insert, init_schema, meta_set
from app.routers.scan import build_router


def test_foreigntamescan_matches_server_name(tmp_path: Path) -> None:
    cfg = AppConfig(
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
    init_schema(cfg.db_path)
    meta_set(cfg.db_path, "last_parse_at", "9999999999")
    batch_insert(
        cfg.db_path,
        "tamed",
        [
            {
                "tribeid": 1,
                "creature": "Rex_C",
                "lvl": 1,
                "cryo": True,
                "raw": {
                    "tribeid": 1,
                    "creature": "Rex_C",
                    "tamedServer": "TheIsland-PvE",
                },
            },
            {
                "tribeid": 1,
                "creature": "Argy_C",
                "lvl": 1,
                "cryo": True,
                "raw": {
                    "tribeid": 1,
                    "creature": "Argy_C",
                    "tamedServer": "Ragnarok-PvE",
                },
            },
        ],
    )
    app = FastAPI()
    app.include_router(build_router(cfg))
    client = TestClient(app)
    batch_insert(
        cfg.db_path,
        "tribes",
        [{"tribeid": 1, "raw": {"tribeid": 1, "name": "Loner"}}],
    )
    r = client.post("/foreigntamescan", json={"servernames": ["TheIsland-PvE"]})
    assert r.status_code == 200
    body = r.json()
    assert len(body["tamed"]) == 1
    assert body["tamed"][0]["creature"] == "Argy_C"
    assert len(body["tribes"]) == 1
    assert body["tribes"][0]["tribeid"] == 1
