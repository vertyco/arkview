import typing as t  # noqa: F401
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.db import init_schema, meta_set
from app.routers.banlist import build_router


def _cfg(tmp_path: Path) -> AppConfig:
    bl = tmp_path / "banlist.txt"
    bl.write_text("76561198000000000\n76561198000000001\n", encoding="utf-8")
    return AppConfig(
        config_path=tmp_path / "config.ini",
        port=8000,
        map_file=None,
        cluster_dir=None,
        banlist_file=bl,
        debug=False,
        dsn="",
        api_key="",
        db_path=tmp_path / "av.db",
    )


def test_banlist_returns_lines(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    init_schema(cfg.db_path)
    meta_set(cfg.db_path, "last_parse_at", "9999999999")
    app = FastAPI()
    app.include_router(build_router(cfg))
    r = TestClient(app).get("/banlist")
    assert r.status_code == 200
    assert r.json()["banlist"] == ["76561198000000000", "76561198000000001"]


def test_updatebanlist_overwrites_file(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    init_schema(cfg.db_path)
    meta_set(cfg.db_path, "last_parse_at", "9999999999")
    app = FastAPI()
    app.include_router(build_router(cfg))
    client = TestClient(app)
    r = client.put("/updatebanlist", json={"banlist": ["999"]})
    assert r.status_code == 200
    assert cfg.banlist_file is not None
    assert cfg.banlist_file.read_text(encoding="utf-8").strip().split("\n") == ["999"]
