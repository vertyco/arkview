import logging
import time
import typing as t
from dataclasses import dataclass
from pathlib import Path

from app.config import AppConfig
from app.constants import ARKPARSER_VERSION
from app.db import maintenance, meta_set, replace_by_key, swap_staging

log = logging.getLogger("arkviewer.ingest")


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


def _load_world(cfg: AppConfig) -> tuple[t.Any, t.Any, int, str]:
    """Load the world save + sidecar profiles/tribes; return the LIVE save.

    Unlike the old `_load_world_and_export`, this does NOT call `export_all`.
    It returns the live `WorldSave` so `ingest_full` can export ONE dataset at
    a time and free it before building the next — capping peak RAM at
    (object graph + one dataset) instead of (object graph + all datasets).

    Pre: `cfg.map_file` is set and on disk.
    Post: returns (save, map_config, day, time_text); `save.profiles` and
    `save.tribes` are populated for the player/tribe exporters.
    """
    from arkparser import Profile, Tribe, WorldSave
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
    assert save is not None

    game_time = float(save.game_time)
    day = int(game_time // 86400)
    rem = int(game_time % 86400)
    h, m = rem // 3600, (rem % 3600) // 60
    return save, map_config, day, f"{h:02d}:{m:02d}"


def _load_cluster_invs(cfg: AppConfig) -> list[tuple[str, t.Any]]:
    """Parse every cluster file ONCE; return (file_id, CloudInventory) pairs.

    The old path parsed cluster files twice per reparse — once inside
    arkparser's `export_all(cluster=dir)` and again in `_iter_cluster_rows`.
    We parse once here and reuse the same instances for the tamed splice, the
    players splice, and the `/data/cluster` rows.

    Pre: `cfg.cluster_dir` may be None.
    Post: one pair per parsable file; stub (<16 byte) and unparsable files are
    skipped (debug for stubs, warning for parse errors — same policy as before).
    """
    from arkparser import CloudInventory

    out: list[tuple[str, t.Any]] = []
    if cfg.cluster_dir is None or not cfg.cluster_dir.exists():
        return out
    for path in sorted(cfg.cluster_dir.iterdir()):
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size < 16:
            log.debug("Skipping empty cluster file %s (%d bytes)", path.name, size)
            continue
        try:
            out.append((path.stem, CloudInventory.load(path)))
        except Exception as exc:
            log.warning(
                "Skipping cluster file %s: %s: %s", path.name, type(exc).__name__, exc
            )
    assert isinstance(out, list)
    return out


def ingest_full(cfg: AppConfig) -> IngestResult:
    """Full reparse: stream each dataset to its table, freeing between types.

    Peak RAM = WorldSave object graph + the single largest dataset list at a
    time (not all seven at once, as the old `export_all` dict held). Cluster
    files are parsed once and reused for the tamed splice, the players splice,
    and the `/data/cluster` rows.

    Pre: `cfg.map_file` is set.
    Post: every dataset table swapped atomically; meta updated; on success the
    `reparse_pending` upgrade flag is cleared.
    """
    assert cfg.map_file is not None
    from arkparser import (
        export_cluster_uploads,
        export_map_structures,
        export_players,
        export_structures,
        export_tamed,
        export_tribe_logs,
        export_tribes,
        export_wild,
    )

    t0 = time.perf_counter()
    save, map_config, day, time_text = _load_world(cfg)
    cluster_invs = _load_cluster_invs(cfg)
    invs = [inv for _, inv in cluster_invs]
    result = IngestResult()

    # Tamed = in-world tames + cluster-uploaded tames. Use `+=` (in-place
    # extend) not `tamed + ...` so we never hold two copies of the tamed list.
    tamed = export_tamed(save, map_config)
    if invs:
        tamed += export_cluster_uploads(invs, map_config)
    result.tamed = swap_staging(
        cfg.db_path, "tamed", (_row_for("tamed", asv) for asv in tamed)
    )
    del tamed

    # Players need the cluster inventories for upload splicing; do players then
    # the raw /data/cluster rows, then free the inventories before the big
    # wild/structures lists so cluster never coexists with them.
    result.players = swap_staging(
        cfg.db_path,
        "players",
        (
            _row_for("players", asv)
            for asv in export_players(save, map_config, invs or None)
        ),
    )
    result.cluster_files = swap_staging(
        cfg.db_path,
        "cluster_inventory",
        ({"file_id": fid, "raw": inv.to_dict()} for fid, inv in cluster_invs),
    )
    del cluster_invs, invs

    result.wild = swap_staging(
        cfg.db_path,
        "wild",
        (_row_for("wild", asv) for asv in export_wild(save, map_config)),
    )
    result.structures = swap_staging(
        cfg.db_path,
        "structures",
        (_row_for("structures", asv) for asv in export_structures(save, map_config)),
    )
    result.tribes = swap_staging(
        cfg.db_path, "tribes", (_row_for("tribes", asv) for asv in export_tribes(save))
    )
    result.tribelogs = swap_staging(
        cfg.db_path,
        "tribelogs",
        (_row_for("tribelogs", asv) for asv in export_tribe_logs(save)),
    )
    result.mapstructures = swap_staging(
        cfg.db_path,
        "mapstructures",
        (
            _row_for("mapstructures", asv)
            for asv in export_map_structures(save, map_config)
        ),
    )
    del save

    meta_set(cfg.db_path, "day", str(day))
    meta_set(cfg.db_path, "time", time_text)
    meta_set(cfg.db_path, "last_parse_at", str(int(time.time())))
    # Stamp the arkparser version that produced this ingest so the next boot
    # can detect a version change.
    meta_set(cfg.db_path, "arkparser_version", ARKPARSER_VERSION)
    # Fresh data has landed: clear the upgrade-reparse flag (init_schema sets it
    # to "1" on an arkparser version bump so the cached data is refreshed
    # without a cold-start 503).
    meta_set(cfg.db_path, "reparse_pending", "0")
    # Reclaim freelist pages from swap churn + truncate WAL.
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
    # replace_by_key keeps players_search in sync; a plain batch_insert would
    # leave the FTS sidecar pointing at the old rowid (stale/wrong search).
    replace_by_key(
        cfg.db_path, "players", "playerid", pid, [_row_for("players", record)]
    )
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
    # replace_by_key keeps tribes_search in sync (tribelogs has no FTS sidecar).
    replace_by_key(
        cfg.db_path, "tribes", "tribeid", tid, [_row_for("tribes", tribe_row)]
    )
    log_rows = [_row_for("tribelogs", log_row)] if log_row is not None else []
    replace_by_key(cfg.db_path, "tribelogs", "tribeid", tid, log_rows)
    return 1
