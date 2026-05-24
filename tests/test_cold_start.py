from app.db import init_schema, meta_set
from main import _needs_cold_start


def test_empty_db_needs_cold_start(tmp_path) -> None:
    db = tmp_path / "v.db"
    init_schema(db)  # no last_parse_at yet
    assert _needs_cold_start(db) is True


def test_parsed_db_no_flag_does_not(tmp_path) -> None:
    db = tmp_path / "v.db"
    init_schema(db)
    meta_set(db, "last_parse_at", "1000")
    assert _needs_cold_start(db) is False


def test_reparse_pending_forces_cold_start(tmp_path) -> None:
    db = tmp_path / "v.db"
    init_schema(db)
    meta_set(db, "last_parse_at", "1000")
    meta_set(db, "reparse_pending", "1")
    assert _needs_cold_start(db) is True


def test_cleared_flag_does_not(tmp_path) -> None:
    db = tmp_path / "v.db"
    init_schema(db)
    meta_set(db, "last_parse_at", "1000")
    meta_set(db, "reparse_pending", "0")
    assert _needs_cold_start(db) is False
