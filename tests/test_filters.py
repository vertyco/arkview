import typing as t

from fastapi import FastAPI
from fastapi.testclient import TestClient

from common.filters import filter_tamed, filter_wild
from common.models import cache
from common.tasks import router

TAMED: list[dict[str, t.Any]] = [
    {"tribeid": 1, "creature": "Rex_Character_BP_C", "cryo": False},
    {"tribeid": "2", "creature": "Raptor_Character_BP_C", "cryo": True},
    {"tribeid": 1, "creature": "Raptor_Character_BP_C", "cryo": True},
    {"creature": "Rex_Character_BP_C", "cryo": False},
    {"tribeid": 3, "creature": "Dodo_Character_BP_C"},
]

WILD: list[dict[str, t.Any]] = [
    {"creature": "Rex_Character_BP_C", "tameable": True},
    {"creature": "Dodo_Character_BP_C", "tameable": False},
    {"creature": "Raptor_Character_BP_C"},
]


def test_tamed_no_filters_returns_all_same_objects():
    result = filter_tamed(TAMED, None, None, None)
    assert result == TAMED
    assert all(a is b for a, b in zip(result, TAMED))


def test_tamed_tribe_id_exact_and_string_coerced():
    assert filter_tamed(TAMED, 1, None, None) == [TAMED[0], TAMED[2]]
    assert filter_tamed(TAMED, 2, None, None) == [TAMED[1]]


def test_tamed_missing_tribeid_never_matches():
    assert TAMED[3] not in filter_tamed(TAMED, 0, None, None)
    assert TAMED[3] in filter_tamed(TAMED, None, None, None)


def test_tamed_bad_tribeid_value_never_matches():
    rows = [{"tribeid": "abc", "creature": "x"}, {"tribeid": None, "creature": "x"}]
    assert filter_tamed(rows, 1, None, None) == []


def test_tamed_class_name_case_insensitive_substring():
    assert filter_tamed(TAMED, None, "rex", None) == [TAMED[0], TAMED[3]]
    assert filter_tamed(TAMED, None, "RAPTOR_char", None) == [TAMED[1], TAMED[2]]
    assert filter_tamed(TAMED, None, "nomatch", None) == []


def test_tamed_is_cryo_true_and_false():
    assert filter_tamed(TAMED, None, None, True) == [TAMED[1], TAMED[2]]
    assert filter_tamed(TAMED, None, None, False) == [TAMED[0], TAMED[3], TAMED[4]]


def test_tamed_combination():
    assert filter_tamed(TAMED, 1, "raptor", True) == [TAMED[2]]
    assert filter_tamed(TAMED, 1, "raptor", False) == []


def test_tamed_empty_input():
    assert filter_tamed([], 1, "rex", True) == []


def test_tamed_order_preserved():
    rows = [{"tribeid": 1, "creature": f"c{i}", "cryo": False} for i in (3, 1, 2)]
    assert [r["creature"] for r in filter_tamed(rows, 1, None, False)] == ["c3", "c1", "c2"]


def test_wild_no_filters_returns_all():
    result = filter_wild(WILD, None, None)
    assert result == WILD
    assert all(a is b for a, b in zip(result, WILD))


def test_wild_class_name_case_insensitive_substring():
    assert filter_wild(WILD, "DODO", None) == [WILD[1]]
    assert filter_wild(WILD, "_character", None) == WILD


def test_wild_tameable_true_false_and_missing_key():
    assert filter_wild(WILD, None, True) == [WILD[0]]
    assert filter_wild(WILD, None, False) == [WILD[1], WILD[2]]


def test_wild_combination_and_empty():
    assert filter_wild(WILD, "rex", True) == [WILD[0]]
    assert filter_wild(WILD, "rex", False) == []
    assert filter_wild([], "rex", True) == []


def make_client(monkeypatch, exports: dict[str, t.Any]) -> TestClient:
    monkeypatch.setattr(cache, "exports", exports)
    monkeypatch.setattr(cache, "api_key", "")
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_route_tamed_filters(monkeypatch):
    client = make_client(monkeypatch, {"tamed": {"map": "m", "day": 1, "time": "00:00", "data": TAMED}})
    resp = client.get("/data/filter/tamed", params={"tribe_id": 1, "class_name": "raptor", "is_cryo": "true"})
    assert resp.status_code == 200
    assert resp.json()["tamed"] == [TAMED[2]]


def test_route_wild_filters(monkeypatch):
    client = make_client(monkeypatch, {"wild": {"map": "m", "day": 1, "time": "00:00", "data": WILD}})
    resp = client.get("/data/filter/wild", params={"tameable": "false"})
    assert resp.status_code == 200
    assert resp.json()["wild"] == [WILD[1], WILD[2]]


def test_route_not_cached_is_404(monkeypatch):
    client = make_client(monkeypatch, {})
    assert client.get("/data/filter/tamed").status_code == 404
    assert client.get("/data/filter/wild").status_code == 404
