from pydantic import AliasChoices, Field

from .base import BaseModel


class InventoryItem(BaseModel):
    """A single item inside a player, creature, or structure inventory."""

    class_name: str = Field(
        default="",
        validation_alias="itemId",
        description="Blueprint class name of the item",
    )
    quantity: int = Field(
        default=1,
        validation_alias="qty",
        description="Stack quantity",
    )
    is_blueprint: bool = Field(
        default=False,
        validation_alias="blueprint",
        description="Whether the item is a blueprint",
    )
    uploaded_time: float | None = Field(
        default=None,
        validation_alias="uploadedTime",
        description="Upload timestamp when present in transfer data",
    )


class CloudItem(BaseModel):
    """A single uploaded item inside a cloud inventory file."""

    blueprint: str = Field(
        default="", description="Blueprint path of the uploaded item"
    )
    name: str = Field(default="", description="Base item name")
    custom_name: str = Field(default="", description="Player-assigned custom name")
    display_name: str = Field(
        default="", description="Resolved display name shown to the client"
    )
    item_id1: int = Field(default=0, description="Upper 32-bit item ID component")
    item_id2: int = Field(default=0, description="Lower 32-bit item ID component")
    item_id: str = Field(
        default="",
        validation_alias=AliasChoices("item_id", "unique_id"),
        description="Stable unique item ID",
    )
    quantity: int = Field(default=1, description="Stack quantity")
    quality_index: int = Field(default=0, description="Numeric item quality tier")
    quality_name: str = Field(
        default="Primitive", description="Friendly item quality name"
    )
    durability: float = Field(default=0.0, description="Current durability value")
    rating: float = Field(default=0.0, description="Ark quality rating")
    slot_index: int = Field(default=0, description="Inventory slot index when present")
    is_blueprint: bool = Field(
        default=False, description="Whether the uploaded item is a blueprint"
    )
    is_engram: bool = Field(
        default=False, description="Whether the uploaded item is an engram"
    )
    upload_time: float = Field(default=0.0, description="Upload timestamp")


class UploadedCreatureStats(BaseModel):
    """Current and max stat values for an uploaded creature."""

    health: float = Field(default=0.0, description="Current health")
    max_health: float = Field(default=0.0, description="Maximum health")
    stamina: float = Field(default=0.0, description="Current stamina")
    max_stamina: float = Field(default=0.0, description="Maximum stamina")
    torpidity: float = Field(default=0.0, description="Current torpidity")
    max_torpidity: float = Field(default=0.0, description="Maximum torpidity")
    oxygen: float = Field(default=0.0, description="Current oxygen")
    max_oxygen: float = Field(default=0.0, description="Maximum oxygen")
    food: float = Field(default=0.0, description="Current food")
    max_food: float = Field(default=0.0, description="Maximum food")
    water: float = Field(default=0.0, description="Current water")
    max_water: float = Field(default=0.0, description="Maximum water")
    weight: float = Field(default=0.0, description="Current weight")
    max_weight: float = Field(default=0.0, description="Maximum weight")
    melee_damage: float = Field(default=100.0, description="Melee damage percentage")
    movement_speed: float = Field(
        default=100.0, description="Movement speed percentage"
    )
    crafting_skill: float = Field(
        default=100.0, description="Crafting skill percentage"
    )


class UploadedCreature(BaseModel):
    """A creature uploaded to cluster storage."""

    class_name: str = Field(default="", description="Creature blueprint class path")
    blueprint: str = Field(default="", description="Creature blueprint reference")
    name: str = Field(default="", description="Uploaded creature name")
    species: str = Field(default="", description="Friendly creature species name")
    dino_id1: int = Field(default=0, description="Upper 32-bit dino ID component")
    dino_id2: int = Field(default=0, description="Lower 32-bit dino ID component")
    unique_id: str = Field(default="", description="Stable unique dino ID string")
    level: int = Field(default=1, description="Creature level at upload time")
    experience: float = Field(
        default=0.0, description="Creature experience at upload time"
    )
    stats: UploadedCreatureStats = Field(
        default_factory=UploadedCreatureStats,
        description="Uploaded creature stat values",
    )
    upload_time: int = Field(default=0, description="Upload timestamp")
    version: float = Field(default=0.0, description="Serialized creature version")


class CloudInventory(BaseModel):
    """One cluster inventory file summary."""

    file_id: str = Field(
        default="", description="Filename stem of the cloud inventory file"
    )
    creature_count: int = Field(default=0, description="Number of uploaded creatures")
    item_count: int = Field(default=0, description="Number of uploaded items")
    character_count: int = Field(default=0, description="Number of uploaded characters")
    creatures: list[UploadedCreature] = Field(
        default_factory=list,
        validation_alias=AliasChoices("uploaded_creatures", "creatures"),
        description="Uploaded creatures in this inventory file",
    )
    items: list[CloudItem] = Field(
        default_factory=list,
        validation_alias=AliasChoices("uploaded_items", "items"),
        description="Uploaded items in this inventory file",
    )
