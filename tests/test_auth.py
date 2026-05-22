import typing as t  # noqa: F401

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.auth import RequireBearer


def _make_app(api_key: str) -> TestClient:
    app = FastAPI()
    auth = RequireBearer(api_key)

    @app.get("/secure")
    def _route(_: None = Depends(auth)) -> dict[str, str]:
        return {"ok": "true"}

    return TestClient(app)


def test_no_key_configured_allows_all() -> None:
    client = _make_app(api_key="")
    assert client.get("/secure").status_code == 200


def test_correct_bearer_passes() -> None:
    client = _make_app(api_key="secret")
    r = client.get("/secure", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200


def test_wrong_bearer_401() -> None:
    client = _make_app(api_key="secret")
    r = client.get("/secure", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_missing_header_401() -> None:
    client = _make_app(api_key="secret")
    assert client.get("/secure").status_code == 401


def test_raw_token_passes_for_legacy_avclient() -> None:
    """AVClient sends `Authorization: <key>` without Bearer prefix."""
    client = _make_app(api_key="secret")
    r = client.get("/secure", headers={"Authorization": "secret"})
    assert r.status_code == 200


def test_raw_wrong_token_401() -> None:
    client = _make_app(api_key="secret")
    r = client.get("/secure", headers={"Authorization": "nope"})
    assert r.status_code == 401
