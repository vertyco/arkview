import typing as t  # noqa: F401
from pathlib import Path

import pytest

from app.config import AppConfig, load_config  # noqa: F401

DEFAULT_INI = """[Settings]
Port = 8000
MapFilePath =
ClusterFolderPath =
BanListFile =
Debug = False
DSN =
APIKey =
"""


def test_load_config_creates_default_when_missing(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.ini"
    cfg = load_config(cfg_path)
    assert cfg_path.exists()
    assert cfg.port == 8000
    assert cfg.map_file is None
    assert cfg.debug is False


def test_load_config_reads_existing(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.ini"
    cfg_path.write_text(DEFAULT_INI.replace("Port = 8000", "Port = 8123"))
    cfg = load_config(cfg_path)
    assert cfg.port == 8123


def test_blank_port_falls_back_to_default(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.ini"
    # A present-but-empty `Port =` must not crash startup with int("").
    cfg_path.write_text(DEFAULT_INI.replace("Port = 8000", "Port ="))
    cfg = load_config(cfg_path)
    assert cfg.port == 8000


def test_db_path_defaults_next_to_config(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.ini"
    cfg = load_config(cfg_path)
    assert cfg.db_path == cfg_path.parent / "arkviewer.db"


def test_db_path_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = tmp_path / "config.ini"
    override = tmp_path / "elsewhere" / "av.db"
    monkeypatch.setenv("ARKVIEWER_DB", str(override))
    cfg = load_config(cfg_path)
    assert cfg.db_path == override


def test_no_module_level_state() -> None:
    import app.config as mod

    # Disallow regressions to a module-level `state` global.
    public_attrs = {n for n in dir(mod) if not n.startswith("_")}
    assert "state" not in public_attrs
