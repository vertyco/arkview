from pydantic import AliasChoices, Field

from .base import BaseResponse
from .inventory import InventoryItem


class Structure(BaseResponse):
    """Placed structure data from arkparser export output."""

    tribe_id: int = Field(
        default=0, validation_alias="tribeid", description="Owning tribe ID"
    )
    tribe_name: str | None = Field(
        default=None, validation_alias="tribe", description="Owning tribe name"
    )
    class_name: str = Field(
        default="",
        validation_alias="struct",
        description="Structure blueprint class name",
    )
    custom_name: str = Field(
        default="", validation_alias="name", description="Player-assigned custom name"
    )
    lat: float = Field(default=0.0, validation_alias="lat", description="GPS latitude")
    lon: float = Field(default=0.0, validation_alias="lon", description="GPS longitude")
    ccc: str = Field(
        default="", validation_alias="ccc", description="World coordinates string"
    )
    is_powered: bool | None = Field(
        default=None,
        validation_alias=AliasChoices("is_powered", "isSwitchedOn"),
        description="Whether the structure is powered or switched on",
    )
    is_locked: bool | None = Field(
        default=None,
        validation_alias=AliasChoices("is_locked", "locked"),
        description="Whether the structure is locked",
    )
    inventory: list[InventoryItem] = Field(
        default_factory=list, description="Items inside the structure inventory"
    )


class MapStructure(BaseResponse):
    """Map-worthy structure entry used for marker and filter responses."""

    type: str = Field(default="", description="Normalized structure type key")
    class_name: str = Field(
        default="",
        validation_alias="struct",
        description="Structure blueprint class name",
    )
    lat: float = Field(default=0.0, validation_alias="lat", description="GPS latitude")
    lon: float = Field(default=0.0, validation_alias="lon", description="GPS longitude")
    ccc: str = Field(
        default="", validation_alias="ccc", description="World coordinates string"
    )
    inventory: list[InventoryItem] = Field(
        default_factory=list, description="Items inside the structure inventory"
    )
