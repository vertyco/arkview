from pydantic import BaseModel, Field


class BaseResponse(BaseModel):
    """Info returned in every API response"""

    map_name: str = Field(description="Stem of the map file, e.g. 'TheIsland'")
    map_path: str = Field(description="Full path to the map file")
    cluster_dir: str = Field(
        default="", description="Full path to the cluster directory, if configured"
    )
    version: str = Field(description="ArkViewer version")


class ColorRegions(BaseModel):
    c0: int = Field(default=0, validation_alias="0", description="Color region 0")
    c1: int = Field(default=0, validation_alias="1", description="Color region 1")
    c2: int = Field(default=0, validation_alias="2", description="Color region 2")
    c3: int = Field(default=0, validation_alias="3", description="Color region 3")
    c4: int = Field(default=0, validation_alias="4", description="Color region 4")
    c5: int = Field(default=0, validation_alias="5", description="Color region 5")


class Stats(BaseModel):
    """Current stat float values or level-up stat points allocated depending on context."""

    hp: int | float = Field(
        default=0, validation_alias="0", description="Health points"
    )
    stamina: int | float = Field(default=0, validation_alias="1", description="Stamina")
    torpidity: int | float = Field(
        default=0, validation_alias="2", description="Torpidity"
    )
    oxygen: int | float = Field(default=0, validation_alias="3", description="Oxygen")
    food: int | float = Field(default=0, validation_alias="4", description="Food")
    water: int | float = Field(default=0, validation_alias="5", description="Water")
    temperature: int | float = Field(
        default=0, validation_alias="6", description="Temperature"
    )
    weight: int | float = Field(default=0, validation_alias="7", description="Weight")
    melee: int | float = Field(
        default=0, validation_alias="8", description="Melee damage"
    )
    speed: int | float = Field(default=0, validation_alias="9", description="Speed")
    fortitude: int | float = Field(
        default=0, validation_alias="10", description="Fortitude"
    )
    crafting: int | float = Field(
        default=0, validation_alias="11", description="Crafting skill"
    )


class Location(BaseModel):
    x: float = Field(description="X UE coordinate")
    y: float = Field(description="Y UE coordinate")
    z: float = Field(description="Z UE coordinate")
    pitch: float = Field(description="Pitch rotation")
    yaw: float = Field(description="Yaw rotation")
    roll: float = Field(description="Roll rotation")
