import time
import typing as t  # noqa: F401
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.db import init_schema, meta_set
from app.staleness import StalenessMiddleware


def _make_app(cfg: AppConfig) -> TestClient:
    app = FastAPI()
    app.add_middleware(StalenessMiddleware, db_path=cfg.db_path)

    @app.get("/data/tamed")
    def _route() -> dict[str, str]:
        return {"ok": "true"}

    return TestClient(app)


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


def test_503_when_db_empty(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    init_schema(cfg.db_path)
    client = _make_app(cfg)
    r = client.get("/data/tamed")
    assert r.status_code == 503
    assert r.headers.get("Retry-After") == "30"


def test_200_fresh_no_stale_header(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    init_schema(cfg.db_path)
    meta_set(cfg.db_path, "last_parse_at", str(int(time.time())))
    client = _make_app(cfg)
    r = client.get("/data/tamed")
    assert r.status_code == 200
    assert r.headers.get("X-Arkviewer-Stale") is None


def test_200_stale_header_when_over_6h(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    init_schema(cfg.db_path)
    seven_hours_ago = int(time.time()) - (7 * 3600)
    meta_set(cfg.db_path, "last_parse_at", str(seven_hours_ago))
    client = _make_app(cfg)
    r = client.get("/data/tamed")
    assert r.status_code == 200
    assert r.headers.get("X-Arkviewer-Stale") == "true"
    assert "X-Arkviewer-Last-Parse" in r.headers


def test_health_endpoint_bypasses_503(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    init_schema(cfg.db_path)
    app = FastAPI()
    app.add_middleware(StalenessMiddleware, db_path=cfg.db_path)

    @app.get("/")
    def _root() -> dict[str, str]:
        return {"ok": "true"}

    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
