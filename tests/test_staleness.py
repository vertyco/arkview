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


def test_bypass_paths_with_trailing_slash_not_503(tmp_path: Path) -> None:
    # AVClient calls stats/, banlist/, updatebanlist/ WITH trailing slashes.
    # The bypass check must tolerate the trailing slash, and /updatebanlist
    # must be exempt too (it only writes a file; needs no parsed data).
    cfg = _cfg(tmp_path)
    init_schema(cfg.db_path)  # empty DB: no last_parse_at
    app = FastAPI()
    app.add_middleware(StalenessMiddleware, db_path=cfg.db_path)

    @app.get("/stats/")
    def _stats() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/banlist/")
    def _banlist() -> dict[str, bool]:
        return {"ok": True}

    @app.put("/updatebanlist")
    def _upd() -> dict[str, bool]:
        return {"ok": True}

    @app.put("/updatebanlist/")
    def _upd_slash() -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/stats/").status_code == 200
    assert client.get("/banlist/").status_code == 200
    assert client.put("/updatebanlist").status_code == 200
    assert client.put("/updatebanlist/").status_code == 200


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
