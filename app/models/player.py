import typing as t

from pydantic import AliasChoices, Field, model_validator

from .base import BaseModel, BaseResponse
from .inventory import InventoryItem


class PlayerStats(BaseModel):
    """Player stat values from arkparser export output."""

    hp: int = Field(default=0, validation_alias="hp", description="Health stat value")
    stamina: int = Field(
        default=0, validation_alias="stam", description="Stamina stat value"
    )
    oxygen: int = Field(
        default=0, validation_alias="oxy", description="Oxygen stat value"
    )
    food: int = Field(default=0, validation_alias="food", description="Food stat value")
    water: int = Field(
        default=0, validation_alias="water", description="Water stat value"
    )
    weight: int = Field(
        default=0, validation_alias="weight", description="Weight stat value"
    )
    melee: int = Field(
        default=0, validation_alias="melee", description="Melee stat value"
    )
    speed: int = Field(
        default=0, validation_alias="speed", description="Movement speed stat value"
    )
    fortitude: int = Field(
        default=0, validation_alias="fort", description="Fortitude stat value"
    )
    crafting: int = Field(
        default=0, validation_alias="craft", description="Crafting stat value"
    )


class Player(BaseResponse):
    """Player profile data from arkparser export output."""

    steam_id: str = Field(
        default="", validation_alias="steamid", description="Platform ID or XUID"
    )
    player_id: int = Field(
        default=0, validation_alias="playerid", description="In-game player implant ID"
    )
    steam_name: str = Field(
        default="", validation_alias="steam", description="Platform display name"
    )
    character_name: str = Field(
        default="", validation_alias="name", description="In-game character name"
    )
    tribe_id: int = Field(
        default=0, validation_alias="tribeid", description="Owning tribe ID"
    )
    tribe_name: str = Field(
        default="", validation_alias="tribe", description="Owning tribe name"
    )
    sex: str = Field(default="", validation_alias="sex", description="Character sex")
    level: int = Field(
        default=0, validation_alias="lvl", description="Current character level"
    )
    lat: float = Field(default=0.0, validation_alias="lat", description="GPS latitude")
    lon: float = Field(default=0.0, validation_alias="lon", description="GPS longitude")
    ccc: str = Field(
        default="", validation_alias="ccc", description="World coordinates string"
    )
    stats: PlayerStats = Field(
        default_factory=PlayerStats, description="Nested player stat values"
    )
    engram_points: int = Field(
        default=0,
        validation_alias="engram_points",
        description="Available engram points",
    )
    data_file: str = Field(
        default="",
        validation_alias=AliasChoices("dataFile", "data_file"),
        description="Source profile filename",
    )
    last_active: str | None = Field(
        default=None,
        validation_alias=AliasChoices("active", "last_active"),
        description="Last active timestamp when enriched",
    )
    inventory: list[InventoryItem] = Field(
        default_factory=list, description="Items inside the player inventory"
    )

    @model_validator(mode="before")
    @classmethod
    def add_stats_block(cls, values: t.Any) -> t.Any:
        if not isinstance(values, dict) or "stats" in values:
            return values
        return {**values, "stats": values}
