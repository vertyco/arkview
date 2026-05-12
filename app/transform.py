import typing as t

from .constants import MAP_STRUCTURE_PATTERNS
from .models import (
    CloudInventory,
    InventoryItem,
    MapStructure,
    Player,
    Structure,
    Tamed,
    Tribe,
    TribeLog,
    Wild,
)


def add_metadata(
    raw_value: dict[str, t.Any], metadata: dict[str, t.Any] | None = None
) -> dict[str, t.Any]:
    if not metadata:
        return raw_value
    return {**metadata, **raw_value}


def transform_inventory(raw_items: list[dict[str, t.Any]]) -> list[InventoryItem]:
    items: list[InventoryItem] = []
    for raw_item in raw_items:
        if not raw_item.get("itemId"):
            continue
        items.append(InventoryItem.model_validate(raw_item))
    return items


def transform_tamed_objects(
    raw_tamed_objects: list[dict[str, t.Any]],
    metadata: dict[str, t.Any] | None = None,
) -> list[Tamed]:
    return [
        Tamed.model_validate(add_metadata(raw_tamed_object, metadata))
        for raw_tamed_object in raw_tamed_objects
    ]


def transform_wild_objects(
    raw_wild_objects: list[dict[str, t.Any]],
    metadata: dict[str, t.Any] | None = None,
) -> list[Wild]:
    return [
        Wild.model_validate(add_metadata(raw_wild_object, metadata))
        for raw_wild_object in raw_wild_objects
    ]


def transform_players(
    raw_players: list[dict[str, t.Any]],
    metadata: dict[str, t.Any] | None = None,
) -> list[Player]:
    return [
        Player.model_validate(add_metadata(raw_player, metadata))
        for raw_player in raw_players
    ]


def transform_structures(
    raw_structures: list[dict[str, t.Any]],
    metadata: dict[str, t.Any] | None = None,
) -> list[Structure]:
    return [
        Structure.model_validate(add_metadata(raw_structure, metadata))
        for raw_structure in raw_structures
    ]


def transform_map_structures(
    raw_structures: list[dict[str, t.Any]],
    metadata: dict[str, t.Any] | None = None,
) -> list[MapStructure]:
    structures: list[MapStructure] = []
    for raw_structure in raw_structures:
        class_name = str(raw_structure.get("struct", ""))
        structure_type = ""
        for pattern, mapped_type in MAP_STRUCTURE_PATTERNS.items():
            if pattern in class_name:
                structure_type = mapped_type
                break
        if not structure_type:
            continue
        structures.append(
            MapStructure.model_validate(
                add_metadata({**raw_structure, "type": structure_type}, metadata)
            )
        )
    return structures


def transform_tribes(
    raw_tribes: list[dict[str, t.Any]],
    metadata: dict[str, t.Any] | None = None,
) -> list[Tribe]:
    return [
        Tribe.model_validate(add_metadata(raw_tribe, metadata))
        for raw_tribe in raw_tribes
    ]


def transform_tribe_logs(
    raw_tribe_logs: list[dict[str, t.Any]],
    metadata: dict[str, t.Any] | None = None,
) -> list[TribeLog]:
    return [
        TribeLog.model_validate(add_metadata(raw_tribe_log, metadata))
        for raw_tribe_log in raw_tribe_logs
    ]


def transform_cloud_inventory(
    raw_cloud_inventory: dict[str, t.Any],
    file_id: str = "",
    metadata: dict[str, t.Any] | None = None,
) -> CloudInventory:
    raw_value = add_metadata(raw_cloud_inventory, metadata)
    if not file_id:
        return CloudInventory.model_validate(raw_value)
    return CloudInventory.model_validate({**raw_value, "file_id": file_id})


def transform_cloud_inventories(
    raw_cloud_inventories: dict[str, dict[str, t.Any]],
    metadata: dict[str, t.Any] | None = None,
) -> dict[str, CloudInventory]:
    return {
        file_id: transform_cloud_inventory(raw_cloud_inventory, file_id, metadata)
        for file_id, raw_cloud_inventory in raw_cloud_inventories.items()
    }
