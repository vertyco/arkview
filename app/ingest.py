import logging
import time
import typing as t
from dataclasses import dataclass
from pathlib import Path

from app.config import AppConfig
from app.constants import ARKPARSER_VERSION
from app.db import batch_insert, connect, maintenance, meta_set, swap_staging

log = logging.getLogger("arkviewer.ingest")


ASV_TO_TABLE: t.Final[dict[str, str]] = {
    "ASV_Tamed": "tamed",
    "ASV_Wild": "wild",
    "ASV_Players": "players",
    "ASV_Tribes": "tribes",
    "ASV_Structures": "structures",
    "ASV_TribeLogs": "tribelogs",
    "ASV_MapStructures": "mapstructures",
}


@dataclass(slots=True)
class IngestResult:
    tamed: int = 0
    wild: int = 0
    players: int = 0
    tribes: int = 0
    structures: int = 0
    tribelogs: int = 0
    mapstructures: int = 0
    cluster_files: int = 0
    elapsed_s: float = 0.0


def _row_for(table: str, asv: dict[str, t.Any]) -> dict[str, t.Any]:
    """Build the indexed-column row for a given table from an ASV-shape dict.

    Pre: `table` in DATASET_TABLES; `asv` is a single ASV record.
    Post: returns dict with indexed cols + `raw=<original asv>`.
    """
    assert isinstance(table, str) and table, "table must be a non-empty string"
    assert isinstance(asv, dict), "asv must be a dict"
    out: dict[str, t.Any] = {"raw": asv}
    if table == "tamed":
        out["tribeid"] = int(asv.get("tribeid") or 0)
        out["creature"] = str(asv.get("creature") or "")
        out["lvl"] = int(asv.get("lvl") or 0)
        out["cryo"] = bool(asv.get("cryo"))
        # Promote `uploaded_from_server` flag to an indexed column so the
        # overlimit query (and any future filter) can use it without
        # scanning raw JSON. arkparser sets this on cluster-cryopod imports.
        out["uploaded"] = bool(
            asv.get("uploaded_from_server") or asv.get("uploadedServer")
        )
    elif table == "wild":
        out["creature"] = str(asv.get("creature") or "")
        out["lvl"] = int(asv.get("lvl") or 0)
        out["tameable"] = bool(asv.get("tameable"))
    elif table == "players":
        out["playerid"] = int(asv.get("playerid") or 0)
        out["steamid"] = str(asv.get("steamid") or "")
        out["tribeid"] = int(asv.get("tribeid") or 0)
    elif table == "tribes":
        out["tribeid"] = int(asv.get("tribeid") or 0)
    elif table == "structures":
        out["tribeid"] = int(asv.get("tribeid") or 0)
        out["struct"] = str(asv.get("struct") or "")
    elif table == "tribelogs":
        out["tribeid"] = int(asv.get("tribeid") or 0)
    elif table == "mapstructures":
        out["struct"] = str(asv.get("struct") or "")
    return out


def _load_world_and_export(
    cfg: AppConfig,
) -> tuple[dict[str, list[dict[str, t.Any]]], int, str]:
    """Load the world save + sidecar profile/tribe files; return (export_dict, day, time).

    `export_all` reads `save.profiles` and `save.tribes` lists which the
    caller must assemble. We glob `*.arkprofile` and `*.arktribe` from the
    map's directory and inject them onto the WorldSave instance before the
    export runs. `.arktributetribe` files use a different binary format and
    are skipped - arkparser raises on them.
    """
    from arkparser import Profile, Tribe, WorldSave, export_all
    from arkparser.common import get_map_config

    assert cfg.map_file is not None, "map_file required for world ingest"
    save = WorldSave.load(cfg.map_file)
    map_config = get_map_config(cfg.map_file.name)

    map_dir = cfg.map_file.parent
    profiles: list[t.Any] = []
    for p in sorted(map_dir.glob("*.arkprofile")):
        try:
            profiles.append(Profile.load(p))
        except Exception as exc:
            log.warning("Skipping profile %s: %s: %s", p.name, type(exc).__name__, exc)
    save.profiles = profiles

    tribes: list[t.Any] = []
    for tp in sorted(map_dir.glob("*.arktribe")):
        try:
            tribes.append(Tribe.load(tp))
        except Exception as exc:
            log.warning("Skipping tribe %s: %s: %s", tp.name, type(exc).__name__, exc)
    save.tribes = tribes

    log.info(
        "Loaded sidecars: profiles=%d tribes=%d (dir=%s)",
        len(profiles),
        len(tribes),
        map_dir,
    )

    cluster = (
        str(cfg.cluster_dir) if cfg.cluster_dir and cfg.cluster_dir.exists() else None
    )
    data = export_all(save, map_config, cluster=cluster)

    game_time = float(save.game_time)
    day = int(game_time // 86400)
    rem = int(game_time % 86400)
    h, m = rem // 3600, (rem % 3600) // 60
    del save
    return data, day, f"{h:02d}:{m:02d}"


def _iter_cluster_rows(cfg: AppConfig) -> t.Iterator[dict[str, t.Any]]:
    """Walk `cfg.cluster_dir`, yield {file_id, raw} for each CloudInventory.

    Pre: `cfg.cluster_dir` may be None.
    Post: yields one dict per parsable file; logs and skips unparsable.
    """
    from arkparser import CloudInventory

    if cfg.cluster_dir is None or not cfg.cluster_dir.exists():
        return
    for path in sorted(cfg.cluster_dir.iterdir()):
        if not path.is_file():
            continue
        # Empty / stub cluster files are normal: ARK creates them when a
        # player connects but never uploads anything. Skip silently rather
        # than spamming WARNING for hundreds of them per parse.
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size < 16:
            log.debug("Skipping empty cluster file %s (%d bytes)", path.name, size)
            continue
        try:
            cloud = CloudInventory.load(path)
        except Exception as exc:
            log.warning(
                "Skipping cluster file %s: %s: %s",
                path.name,
                type(exc).__name__,
                exc,
            )
            continue
        yield {"file_id": path.stem, "raw": cloud.to_dict()}


def ingest_full(cfg: AppConfig) -> IngestResult:
    """Full reparse: world save + cluster, swap all dataset tables atomically."""
    assert cfg.map_file is not None
    t0 = time.perf_counter()
    data, day, time_text = _load_world_and_export(cfg)

    result = IngestResult()
    for asv_key, table in ASV_TO_TABLE.items():
        rows = (_row_for(table, asv) for asv in data.get(asv_key, []))
        n = swap_staging(cfg.db_path, table, rows)
        setattr(result, table, n)

    # Cluster inventory: arkparser's `export_all` only folds cluster
    # uploads into `ASV_Tamed` (with `cryo=true`). To expose the raw
    # per-file CloudInventory contents at `/data/cluster/{file_id}`, walk
    # the cluster dir ourselves and stash each file's `to_dict()`. Files
    # are small (one per cluster transfer); read cost is negligible vs
    # the world save parse.
    result.cluster_files = swap_staging(
        cfg.db_path, "cluster_inventory", _iter_cluster_rows(cfg)
    )

    meta_set(cfg.db_path, "day", str(day))
    meta_set(cfg.db_path, "time", time_text)
    meta_set(cfg.db_path, "last_parse_at", str(int(time.time())))
    # Stamp the arkparser version that produced this ingest so the next
    # boot can detect a version change and auto-wipe the cache.
    meta_set(cfg.db_path, "arkparser_version", ARKPARSER_VERSION)
    # Reclaim freelist pages from swap_staging churn + truncate WAL. Without
    # this the DB file grows unbounded across reparses and a bloated WAL can
    # cause spurious readonly errors on subsequent profile/tribe writes.
    maintenance(cfg.db_path)
    result.elapsed_s = time.perf_counter() - t0
    log.info(
        "ingest_full: tamed=%d wild=%d players=%d tribes=%d structures=%d "
        "tribelogs=%d mapstructures=%d cluster=%d in %.1fs",
        result.tamed,
        result.wild,
        result.players,
        result.tribes,
        result.structures,
        result.tribelogs,
        result.mapstructures,
        result.cluster_files,
        result.elapsed_s,
    )
    return result


def _load_one_profile(path: Path) -> dict[str, t.Any] | None:
    """Parse a single .arkprofile and return the ASV_Players-shape record."""
    from arkparser import Profile
    from arkparser.export import export_players

    profile = Profile.load(path)
    rows = export_players([profile])
    return rows[0] if rows else None


def ingest_profile(cfg: AppConfig, path: Path) -> int:
    """PROFILE-scope reparse: refresh a single player record."""
    assert path.suffix == ".arkprofile" or path.stem.isdigit()
    record = _load_one_profile(path)
    if record is None:
        return 0
    pid = int(record.get("playerid") or 0)
    with connect(cfg.db_path) as conn:
        conn.execute("DELETE FROM players WHERE playerid=?", (pid,))
    batch_insert(cfg.db_path, "players", [_row_for("players", record)])
    return 1


def _load_one_tribe(
    path: Path,
) -> tuple[dict[str, t.Any] | None, dict[str, t.Any] | None]:
    """Parse a single .arktribe and return (ASV_Tribes record, ASV_TribeLogs record)."""
    from arkparser import Tribe
    from arkparser.export import export_tribe_logs, export_tribes

    tribe = Tribe.load(path)
    tribes = export_tribes([tribe])
    logs = export_tribe_logs([tribe])
    return (tribes[0] if tribes else None, logs[0] if logs else None)


def ingest_tribe(cfg: AppConfig, path: Path) -> int:
    """TRIBE-scope reparse: refresh a single tribe + its log."""
    assert isinstance(path, Path)
    tribe_row, log_row = _load_one_tribe(path)
    if tribe_row is None:
        return 0
    tid = int(tribe_row.get("tribeid") or 0)
    with connect(cfg.db_path) as conn:
        conn.execute("DELETE FROM tribes WHERE tribeid=?", (tid,))
        conn.execute("DELETE FROM tribelogs WHERE tribeid=?", (tid,))
    batch_insert(cfg.db_path, "tribes", [_row_for("tribes", tribe_row)])
    if log_row is not None:
        batch_insert(cfg.db_path, "tribelogs", [_row_for("tribelogs", log_row)])
    return 1
