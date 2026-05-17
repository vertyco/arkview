import logging
import typing as t
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from arkparser import WorldSave
from arkparser.common.map_config import MapConfig, get_map_config
from arkparser.export import (
    export_players,
    export_structures,
    export_tribe_logs,
    export_tribes,
)
from arkparser.files import (
    CloudInventory as ParserCloudInventory,
    Profile as ParserProfile,
    Tribe as ParserTribe,
)

from .config import AppConfig
from .constants import VERSION
from .state import GameData
from .transform import (
    transform_cloud_inventory,
    transform_map_structures,
    transform_players,
    transform_structures,
    transform_tamed_objects,
    transform_tribe_logs,
    transform_tribes,
    transform_wild_objects,
)

log = logging.getLogger("arkviewer.loader")

MAP_STRUCTURE_CLASS_PATTERNS: tuple[str, ...] = (
    "WyvernNest",
    "Deinonychus",
    "RockDrakeNest",
    "MagmasaurNest",
    "BeeHive",
)


class WorldParseResult(t.TypedDict):
    tamed: list[t.Any]
    wild: list[t.Any]
    structures: list[t.Any]
    mapstructures: list[t.Any]
    day: int
    time: str
    # Map of player_id -> live pawn state (lat/lon/ccc, level, stats, gender,
    # tribe_name_live). Populated from the world save's player pawns + their
    # status components. Used to enrich profile-derived Player records so the
    # cog sees real positions/stats instead of zeros. See
    # ``extract_player_pawn_state`` for shape details.
    player_pawn_state: dict[int, dict[str, t.Any]]


class TribeParseResult(t.TypedDict):
    tribes: list[t.Any]
    tribelogs: list[t.Any]


class SingleTribeParseResult(t.TypedDict):
    tribe: t.Any | None
    tribelog: t.Any | None


class FullParseResult(t.TypedDict):
    data: GameData
    day: int
    time: str
    player_pawn_state: dict[int, dict[str, t.Any]]


def build_response_metadata(config: AppConfig) -> dict[str, str]:
    return {
        "map_name": config.map_file.stem if config.map_file else "",
        "map_path": str(config.map_file) if config.map_file else "",
        "cluster_dir": str(config.cluster_dir) if config.cluster_dir else "",
        "version": VERSION,
    }


def load_map_config(map_file: Path) -> MapConfig | None:
    # IMPORTANT: arkparser.get_map_config matches by FILENAME, not full path.
    # Passing str(map_file) (a full path) silently returns DEFAULT_MAP_CONFIG
    # (50.0 / 8000.0) for every map except those whose path string happens
    # to equal a known filename - i.e. wrong lat/lon for every non-TheIsland save.
    try:
        return get_map_config(map_file.name)
    except Exception:
        log.debug("No map config found for %s", map_file)
        return None


def load_world_save(map_file: Path) -> WorldSave | None:
    try:
        return WorldSave.load(map_file)
    except Exception as exc:
        log.error("Failed to load world save %s: %s", map_file, exc, exc_info=True)
        return None


def load_profile_file(path: Path) -> ParserProfile | None:
    try:
        return ParserProfile.load(path)
    except Exception as exc:
        # Bumped to WARNING (was DEBUG): silent failures hid the ~68 .arkprofile
        # files that arkparser couldn't parse on the live Scorched server, which
        # produced a player-count gap vs the legacy exporter with no signal in
        # the logs. WARNING surfaces them with exception class so the underlying
        # parse bug is debuggable; doesn't spam because each file is logged once.
        log.warning("Skipping profile %s: %s: %s", path.name, type(exc).__name__, exc)
        return None


def load_tribe_file(path: Path) -> ParserTribe | None:
    try:
        return ParserTribe.load(path)
    except Exception as exc:
        # .arktributetribe files are per-player tribute/obelisk data with a
        # different binary format; they always fail and that's expected. Regular
        # .arktribe failures are unexpected and warrant a WARNING.
        if path.suffix == ".arktributetribe":
            log.debug(
                "Skipping tribute tribe %s: %s: %s", path.name, type(exc).__name__, exc
            )
        else:
            log.warning("Skipping tribe %s: %s: %s", path.name, type(exc).__name__, exc)
        return None


def load_cluster_file(path: Path) -> dict[str, t.Any] | None:
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return None
        return ParserCloudInventory.load(path).to_dict()
    except Exception as exc:
        log.warning(
            "Skipping cluster file %s: %s: %s", path.name, type(exc).__name__, exc
        )
        return None


def load_profiles(map_dir: Path) -> list[tuple[Path, ParserProfile]]:
    profiles: list[tuple[Path, ParserProfile]] = []
    for path in sorted(map_dir.glob("*.arkprofile")):
        profile = load_profile_file(path)
        if profile is not None:
            profiles.append((path, profile))
    return profiles


def load_tribes(map_dir: Path) -> list[tuple[Path, ParserTribe]]:
    tribes: list[tuple[Path, ParserTribe]] = []
    seen: set[str] = set()
    tribute_skipped = 0
    for pattern in ("*.arktribe", "*.arktributetribe"):
        for path in sorted(map_dir.glob(pattern)):
            if path.stem in seen:
                continue
            tribe = load_tribe_file(path)
            if tribe is None:
                if path.suffix == ".arktributetribe":
                    tribute_skipped += 1
                continue
            seen.add(path.stem)
            tribes.append((path, tribe))
    if tribute_skipped:
        log.info(
            "Skipped %d .arktributetribe file(s) (tribute/obelisk format, not parseable as tribe data)",
            tribute_skipped,
        )
    return tribes


def get_last_active(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def format_game_time(game_time: float) -> tuple[int, str]:
    day = int(game_time // 86400)
    remainder = int(game_time % 86400)
    hours = remainder // 3600
    minutes = (remainder % 3600) // 60
    return day, f"{hours:02d}:{minutes:02d}"


def get_map_structure_objects(world_save: WorldSave) -> list[t.Any]:
    objects = list(world_save.get_map_resources())
    objects.extend(world_save.get_terminals())
    objects.extend(world_save.get_supply_drops())
    objects.extend(world_save.get_artifact_crates())
    for pattern in MAP_STRUCTURE_CLASS_PATTERNS:
        objects.extend(world_save.get_objects_by_class(pattern))
    return objects


def inject_gps_and_ccc(
    raw_list: list[dict[str, t.Any]], map_config: MapConfig | None
) -> None:
    """In-place: derive flat ``lat``/``lon``/``ccc`` from nested ``location`` dict.

    Consumers (the arktools cog) need flat GPS coordinates at the top level,
    not just the UE-coordinate ``location`` sub-object that ``GameObject.to_dict()``
    produces.
    """
    for raw in raw_list:
        loc = raw.get("location") or {}
        if not isinstance(loc, dict):
            continue
        x = loc.get("x", 0.0) or 0.0
        y = loc.get("y", 0.0) or 0.0
        z = loc.get("z", 0.0) or 0.0
        raw["ccc"] = f"{x} {y} {z}"
        if map_config is not None:
            try:
                raw["lat"] = float(map_config.ue_to_lat(y))
                raw["lon"] = float(map_config.ue_to_lon(x))
            except Exception:
                raw["lat"] = 0.0
                raw["lon"] = 0.0
        else:
            raw["lat"] = 0.0
            raw["lon"] = 0.0


# Stat-index layout shared with arkparser's CharacterStatusComponent indexed
# properties: 0=hp, 1=stamina, 2=torpidity, 3=oxygen, 4=food, 5=water,
# 6=temperature (unused for players), 7=weight, 8=melee, 9=speed,
# 10=fortitude, 11=crafting.
_STAT_KEYS_BY_INDEX = (
    "hp",
    "stamina",
    "torpidity",
    "oxygen",
    "food",
    "water",
    "temperature",
    "weight",
    "melee",
    "speed",
    "fortitude",
    "crafting",
)


def _read_indexed_int_array(status_obj: t.Any, name: str) -> dict[int, int]:
    """Read a property that appears N times with index=0..N-1 into {index: value}.

    Purpose: arkparser exposes indexed save properties (like
    ``NumberOfLevelUpPointsApplied[0..11]``) as repeated entries with the same
    ``name`` but different ``index`` fields. ``get_property_value`` only returns
    the first hit. Walk ``status_obj.properties`` directly to collect every index.
    Preconditions: ``status_obj.properties`` is a list of property objects with
    ``.name``, ``.index``, and ``.value`` attributes.
    Postconditions: returns a dict from index to value (empty if no matches).
    Side effects: none.
    """
    out: dict[int, int] = {}
    props = getattr(status_obj, "properties", None) or []
    if not isinstance(props, list):
        return out
    for prop in props:
        if getattr(prop, "name", None) != name:
            continue
        idx = getattr(prop, "index", None)
        val = getattr(prop, "value", None)
        if idx is None or val is None:
            continue
        try:
            out[int(idx)] = int(val)
        except (TypeError, ValueError):
            continue
    return out


def _build_obj_id_lookup(world_save: WorldSave) -> dict[int, t.Any]:
    """Build an ``id -> GameObject`` lookup table for the world's objects."""
    lookup: dict[int, t.Any] = {}
    for obj in getattr(world_save, "objects", []) or []:
        obj_id = getattr(obj, "id", None)
        if isinstance(obj_id, int):
            lookup[obj_id] = obj
    return lookup


def extract_player_pawn_state(
    world_save: WorldSave,
    map_config: MapConfig | None,
) -> dict[int, dict[str, t.Any]]:
    """Pull per-player live data from the world save's player pawns.

    Purpose: the .arkprofile file alone doesn't carry the player's current
    position, level, or stat-point allocations. That data lives on the
    player-pawn GameObject in the world save (plus its status component).
    This function builds a ``{player_id: pawn_state}`` lookup so the loader
    can later merge it into the Player records.
    Preconditions: ``world_save`` is a fully-loaded WorldSave; ``map_config``
    may be None (in which case lat/lon are reported as 0.0 but ccc still
    populates).
    Postconditions: returns a dict keyed by ``LinkedPlayerDataID``. Each
    value contains whichever of these fields the pawn / status component had:
    ``lat``, ``lon``, ``ccc``, ``sex``, ``level`` (only when the status
    component carries the level fields - typically only for recently-online
    players), ``stats`` (dict of allocated stat-points by name, when
    available), ``tribe_name_live``, ``is_sleeping``.
    Side effects: none beyond reading from world_save.
    """
    obj_lookup = _build_obj_id_lookup(world_save)
    pawns: list[t.Any] = list(world_save.get_player_pawns())
    result: dict[int, dict[str, t.Any]] = {}

    for pawn in pawns:
        player_id = pawn.get_property_value("LinkedPlayerDataID")
        if not isinstance(player_id, int):
            continue

        entry: dict[str, t.Any] = {}

        # Position. The pawn's location is always present for in-world pawns.
        loc = getattr(pawn, "location", None)
        if loc is not None:
            x = float(getattr(loc, "x", 0.0) or 0.0)
            y = float(getattr(loc, "y", 0.0) or 0.0)
            z = float(getattr(loc, "z", 0.0) or 0.0)
            entry["ccc"] = f"{x} {y} {z}"
            if map_config is not None:
                try:
                    entry["lat"] = float(map_config.ue_to_lat(y))
                    entry["lon"] = float(map_config.ue_to_lon(x))
                except Exception:
                    pass

        # Gender from the pawn's class name (matches C# ContentPlayer.cs:315).
        class_name = str(getattr(pawn, "class_name", "") or "").lower()
        if "female" in class_name:
            entry["sex"] = "Female"
        elif "male" in class_name:
            entry["sex"] = "Male"

        # Live tribe name straight off the pawn (set by the game whenever a
        # player is added to / removed from a tribe).
        tribe_name = pawn.get_property_value("TribeName")
        if tribe_name:
            entry["tribe_name_live"] = str(tribe_name)

        entry["is_sleeping"] = bool(
            pawn.get_property_value("bIsSleeping", default=False)
        )

        # Resolve the status component (id reference) to get level + stats.
        # Sleeping/offline pawns often have only ExperiencePoints + CurrentStatusValues;
        # BaseCharacterLevel / ExtraCharacterLevel / NumberOfLevelUpPointsApplied
        # are only present for online (or recently-online) pawns. C# reference
        # falls back to level 1 in the same case (ContentPlayer.cs:565).
        status_ref = pawn.get_property_value("MyCharacterStatusComponent")
        if isinstance(status_ref, int):
            status_obj = obj_lookup.get(status_ref)
        else:
            status_obj = None

        if status_obj is not None:
            base_level = status_obj.get_property_value(
                "BaseCharacterLevel", default=None
            )
            extra_level = status_obj.get_property_value(
                "ExtraCharacterLevel", default=None
            )
            if base_level is not None or extra_level is not None:
                entry["level"] = int(base_level or 1) + int(extra_level or 0)

            points = _read_indexed_int_array(status_obj, "NumberOfLevelUpPointsApplied")
            if points:
                entry["stats"] = {
                    _STAT_KEYS_BY_INDEX[i]: int(v)
                    for i, v in points.items()
                    if 0 <= i < len(_STAT_KEYS_BY_INDEX)
                }

            experience = status_obj.get_property_value("ExperiencePoints", default=None)
            if experience is not None:
                try:
                    entry["experience"] = float(experience)
                except (TypeError, ValueError):
                    pass

        if entry:
            result[player_id] = entry

    return result


def apply_player_pawn_state(
    players: list[t.Any],
    pawn_state: dict[int, dict[str, t.Any]],
) -> None:
    """Merge pawn-derived data into profile-based Player records in-place.

    Purpose: profile-only Player records have ``lat=lon=0.0``, ``level=1``,
    and empty stats because .arkprofile files don't store live state. The
    pawn in the world save does. Apply the pre-extracted pawn_state dict
    so the API serves real values.
    Preconditions: ``pawn_state`` keyed by the same ``player_id`` that
    Player models carry; non-empty values override defaults but don't
    overwrite already-populated profile data when there's no pawn match.
    Postconditions: each Player whose ``player_id`` matches has its
    lat/lon/ccc, level (if the pawn knew it), stats (if the pawn knew
    them), and sex updated. Players without a matching pawn are untouched.
    Side effects: mutates Player models in place.
    """
    if not pawn_state:
        return

    for player in players:
        entry = pawn_state.get(player.player_id)
        if not entry:
            continue

        if "lat" in entry:
            player.lat = entry["lat"]
        if "lon" in entry:
            player.lon = entry["lon"]
        if "ccc" in entry:
            player.ccc = entry["ccc"]
        if "sex" in entry and not player.sex:
            player.sex = entry["sex"]
        if "level" in entry:
            player.level = entry["level"]
        if "tribe_name_live" in entry and not player.tribe_name:
            # Only override if the profile didn't carry a name (which it never
            # does - profiles only store tribe_id, not tribe name).
            player.tribe_name = entry["tribe_name_live"]

        stats = entry.get("stats")
        if stats and player.stats is not None:
            ps = player.stats
            for stat_key in (
                "hp",
                "stamina",
                "oxygen",
                "food",
                "water",
                "weight",
                "melee",
                "speed",
                "fortitude",
                "crafting",
            ):
                if stat_key in stats:
                    setattr(ps, stat_key, stats[stat_key])


def parse_world_data(config: AppConfig) -> WorldParseResult | None:
    if config.map_file is None:
        return None

    world_save = load_world_save(config.map_file)
    if world_save is None:
        return None

    map_config = load_map_config(config.map_file)
    metadata = build_response_metadata(config)

    raw_tamed = [
        obj.to_dict() for obj in world_save.get_tamed_creatures() if obj.has_location
    ]
    raw_wild = [
        obj.to_dict() for obj in world_save.get_wild_creatures() if obj.has_location
    ]
    inject_gps_and_ccc(raw_tamed, map_config)
    inject_gps_and_ccc(raw_wild, map_config)
    raw_structures = export_structures(world_save, map_config)
    raw_map_structures = export_structures(
        SimpleNamespace(structure_objects=get_map_structure_objects(world_save)),
        map_config,
    )
    pawn_state = extract_player_pawn_state(world_save, map_config)
    day, time_text = format_game_time(world_save.game_time)

    return {
        "tamed": transform_tamed_objects(raw_tamed, metadata),
        "wild": transform_wild_objects(raw_wild, metadata),
        "structures": transform_structures(raw_structures, metadata),
        "mapstructures": transform_map_structures(raw_map_structures, metadata),
        "day": day,
        "time": time_text,
        "player_pawn_state": pawn_state,
    }


def parse_players_data(config: AppConfig) -> list[t.Any]:
    if config.map_file is None:
        return []

    metadata = build_response_metadata(config)
    map_config = load_map_config(config.map_file)
    loaded_profiles = load_profiles(config.map_file.parent)
    profiles = [profile for _, profile in loaded_profiles]
    raw_players = export_players(SimpleNamespace(profiles=profiles), map_config)
    players = transform_players(raw_players, metadata)
    last_active_by_file = {
        path.name: get_last_active(path) for path, _ in loaded_profiles
    }

    for player in players:
        if player.data_file:
            player.last_active = last_active_by_file.get(player.data_file)
        # v2 always wrote ccc="0 0 0" for profile-only players (no world pawn),
        # which is what the cog's ``Player.position`` parser expects. v3 used
        # to leave ccc="" here which made ``"".split(" ")`` blow up downstream.
        # Pawn merge later overwrites with the real coords when available.
        if not player.ccc:
            player.ccc = "0 0 0"

    return players


def parse_profile_data(
    path: Path,
    config: AppConfig,
    pawn_state: dict[int, dict[str, t.Any]] | None = None,
) -> t.Any | None:
    """Re-parse a single .arkprofile file (PROFILE-scope incremental reparse).

    Purpose: feed back a single Player model when an .arkprofile changes on
    disk. Optionally apply pre-cached pawn state (from the last SAVE-scope
    parse) so the resulting Player still carries lat/lon/level/stats from the
    world save instead of regressing to profile-only zeros.
    """
    profile = load_profile_file(path)
    if profile is None:
        return None

    metadata = build_response_metadata(config)
    raw_players = export_players(SimpleNamespace(profiles=[profile]))
    players = transform_players(raw_players, metadata)
    if not players:
        return None

    player = players[0]
    player.last_active = get_last_active(path)
    player.data_file = path.name
    if not player.ccc:
        # v2 always wrote ccc="0 0 0" for profile-only players; cog's
        # ``Player.position`` crashes on "". Pawn merge below may overwrite.
        player.ccc = "0 0 0"
    if pawn_state:
        apply_player_pawn_state([player], pawn_state)
    return player


def parse_tribes_data(config: AppConfig) -> TribeParseResult:
    if config.map_file is None:
        return {"tribes": [], "tribelogs": []}

    metadata = build_response_metadata(config)
    loaded_tribes = load_tribes(config.map_file.parent)
    tribes = [tribe for _, tribe in loaded_tribes]
    raw_tribes = export_tribes(SimpleNamespace(tribes=tribes))
    raw_tribelogs = export_tribe_logs(SimpleNamespace(tribes=tribes))

    file_by_tribe_id = {
        int(tribe.tribe_id or 0): path.name for path, tribe in loaded_tribes
    }
    last_active_by_file = {
        path.name: get_last_active(path) for path, _ in loaded_tribes
    }

    tribes_with_files = [
        {
            **raw_tribe,
            "dataFile": file_by_tribe_id.get(int(raw_tribe.get("tribeid", 0) or 0), ""),
        }
        for raw_tribe in raw_tribes
    ]
    tribelogs_with_files = [
        {
            **raw_tribelog,
            "dataFile": file_by_tribe_id.get(
                int(raw_tribelog.get("tribeid", 0) or 0), ""
            ),
        }
        for raw_tribelog in raw_tribelogs
    ]

    tribe_models = transform_tribes(tribes_with_files, metadata)
    tribelog_models = transform_tribe_logs(tribelogs_with_files, metadata)

    for tribe in tribe_models:
        if tribe.data_file:
            tribe.last_active = last_active_by_file.get(tribe.data_file)

    return {"tribes": tribe_models, "tribelogs": tribelog_models}


def parse_tribe_data(path: Path, config: AppConfig) -> SingleTribeParseResult:
    tribe = load_tribe_file(path)
    if tribe is None:
        return {"tribe": None, "tribelog": None}

    metadata = build_response_metadata(config)
    raw_tribes = export_tribes(SimpleNamespace(tribes=[tribe]))
    raw_tribelogs = export_tribe_logs(SimpleNamespace(tribes=[tribe]))

    tribe_model = None
    if raw_tribes:
        tribe_model = transform_tribes(
            [{**raw_tribes[0], "dataFile": path.name}], metadata
        )[0]
        tribe_model.last_active = get_last_active(path)

    tribelog_model = None
    if raw_tribelogs:
        tribelog_model = transform_tribe_logs(
            [{**raw_tribelogs[0], "dataFile": path.name}], metadata
        )[0]

    return {"tribe": tribe_model, "tribelog": tribelog_model}


def parse_cluster_inventory_file(path: Path, config: AppConfig) -> t.Any | None:
    raw_cloud_inventory = load_cluster_file(path)
    if raw_cloud_inventory is None:
        return None
    return transform_cloud_inventory(
        raw_cloud_inventory, path.stem, build_response_metadata(config)
    )


def parse_cluster_data(config: AppConfig) -> dict[str, t.Any]:
    if config.cluster_dir is None or not config.cluster_dir.exists():
        return {}

    inventories: dict[str, t.Any] = {}
    for path in sorted(config.cluster_dir.iterdir()):
        if path.suffix or not path.is_file():
            continue
        inventory = parse_cluster_inventory_file(path, config)
        if inventory is not None:
            inventories[path.stem] = inventory
    return inventories


def parse_all_data(config: AppConfig) -> FullParseResult:
    data = GameData()
    day = 0
    time_text = ""
    pawn_state: dict[int, dict[str, t.Any]] = {}

    world_result = parse_world_data(config)
    if world_result is not None:
        data.tamed = world_result["tamed"]
        data.wild = world_result["wild"]
        data.structures = world_result["structures"]
        data.mapstructures = world_result["mapstructures"]
        day = world_result["day"]
        time_text = world_result["time"]
        pawn_state = world_result.get("player_pawn_state") or {}

    data.players = parse_players_data(config)
    # Merge live pawn state (lat/lon/ccc/level/stats/sex) into profile-derived
    # player records. Without this, every player reports lat=lon=0 and level=1
    # because .arkprofile files don't carry that data.
    apply_player_pawn_state(data.players, pawn_state)

    tribe_result = parse_tribes_data(config)
    data.tribes = tribe_result["tribes"]
    data.tribelogs = tribe_result["tribelogs"]
    data.cloud_inventory = parse_cluster_data(config)

    return {
        "data": data,
        "day": day,
        "time": time_text,
        "player_pawn_state": pawn_state,
    }
