import json
import sqlite3
import typing as t  # noqa: F401
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.db import init_schema, meta_set
from app.routers.cluster import build_router


def test_cluster_lists_all_inventories(tmp_path: Path) -> None:
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
    payload = {"uploaded_creatures": [{"id": 1}], "uploaded_items": []}
    conn = sqlite3.connect(cfg.db_path)
    try:
        conn.execute(
            "INSERT INTO cluster_inventory(file_id, raw) VALUES(?, ?)",
            ("76561198000000000", json.dumps(payload)),
        )
        conn.commit()
    finally:
        conn.close()

    app = FastAPI()
    app.include_router(build_router(cfg))
    client = TestClient(app)

    r = client.get("/data/cluster")
    assert r.status_code == 200
    assert "76561198000000000" in r.json()["cluster"]

    r = client.get("/data/cluster/76561198000000000")
    assert r.status_code == 200
    assert r.json()["cluster"]["uploaded_creatures"][0]["id"] == 1

    r = client.get("/data/cluster/missing")
    assert r.status_code == 404
