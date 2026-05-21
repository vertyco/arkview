import typing as t  # noqa: F401
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.db import init_schema, meta_set
from app.routers.health import build_router


def test_root_returns_metadata(tmp_path: Path) -> None:
    cfg = AppConfig(
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
    init_schema(cfg.db_path)
    meta_set(cfg.db_path, "last_parse_at", "12345")
    app = FastAPI()
    app.include_router(build_router(cfg))
    body = TestClient(app).get("/").json()
    assert body["map"] == "TheIsland.ark"
    assert "version" in body
    assert body["last_parse_at"] == "12345"


def test_stats_returns_system_metrics(tmp_path: Path) -> None:
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
    app = FastAPI()
    app.include_router(build_router(cfg))
    body = TestClient(app).get("/stats").json()
    assert "cpu_percent" in body
    assert "memory_percent" in body
