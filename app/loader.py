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


def build_response_metadata(config: AppConfig) -> dict[str, str]:
    return {
        "map_name": config.map_file.stem if config.map_file else "",
        "map_path": str(config.map_file) if config.map_file else "",
        "cluster_dir": str(config.cluster_dir) if config.cluster_dir else "",
        "version": VERSION,
    }


def load_map_config(map_file: Path) -> MapConfig | None:
    try:
        return get_map_config(str(map_file))
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
        log.debug("Skipping profile %s: %s", path.name, exc)
        return None


def load_tribe_file(path: Path) -> ParserTribe | None:
    try:
        return ParserTribe.load(path)
    except Exception as exc:
        log.debug("Skipping tribe %s: %s", path.name, exc)
        return None


def load_cluster_file(path: Path) -> dict[str, t.Any] | None:
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return None
        return ParserCloudInventory.load(path).to_dict()
    except Exception as exc:
        log.debug("Skipping cluster file %s: %s", path.name, exc)
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
    for pattern in ("*.arktribe", "*.arktributetribe"):
        for path in sorted(map_dir.glob(pattern)):
            if path.stem in seen:
                continue
            tribe = load_tribe_file(path)
            if tribe is None:
                continue
            seen.add(path.stem)
            tribes.append((path, tribe))
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
    raw_structures = export_structures(world_save, map_config)
    raw_map_structures = export_structures(
        SimpleNamespace(structure_objects=get_map_structure_objects(world_save)),
        map_config,
    )
    day, time_text = format_game_time(world_save.game_time)

    return {
        "tamed": transform_tamed_objects(raw_tamed, metadata),
        "wild": transform_wild_objects(raw_wild, metadata),
        "structures": transform_structures(raw_structures, metadata),
        "mapstructures": transform_map_structures(raw_map_structures, metadata),
        "day": day,
        "time": time_text,
    }


def parse_players_data(config: AppConfig) -> list[t.Any]:
    if config.map_file is None:
        return []

    metadata = build_response_metadata(config)
    loaded_profiles = load_profiles(config.map_file.parent)
    profiles = [profile for _, profile in loaded_profiles]
    raw_players = export_players(SimpleNamespace(profiles=profiles))
    players = transform_players(raw_players, metadata)
    last_active_by_file = {
        path.name: get_last_active(path) for path, _ in loaded_profiles
    }

    for player in players:
        if player.data_file:
            player.last_active = last_active_by_file.get(player.data_file)

    return players


def parse_profile_data(path: Path, config: AppConfig) -> t.Any | None:
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

    world_result = parse_world_data(config)
    if world_result is not None:
        data.tamed = world_result["tamed"]
        data.wild = world_result["wild"]
        data.structures = world_result["structures"]
        data.mapstructures = world_result["mapstructures"]
        day = world_result["day"]
        time_text = world_result["time"]

    data.players = parse_players_data(config)
    tribe_result = parse_tribes_data(config)
    data.tribes = tribe_result["tribes"]
    data.tribelogs = tribe_result["tribelogs"]
    data.cloud_inventory = parse_cluster_data(config)

    return {"data": data, "day": day, "time": time_text}
