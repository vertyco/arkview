from pydantic import BaseModel, ConfigDict, Field

from .config import AppConfig
from .models import (
    CloudInventory,
    MapStructure,
    Player,
    Structure,
    Tamed,
    Tribe,
    TribeLog,
    Wild,
)


class GameData(BaseModel):
    """Parsed and transformed game data kept in memory."""

    tamed: list[Tamed] = Field(default_factory=list)
    wild: list[Wild] = Field(default_factory=list)
    players: list[Player] = Field(default_factory=list)
    tribes: list[Tribe] = Field(default_factory=list)
    structures: list[Structure] = Field(default_factory=list)
    tribelogs: list[TribeLog] = Field(default_factory=list)
    mapstructures: list[MapStructure] = Field(default_factory=list)
    cloud_inventory: dict[str, CloudInventory] = Field(default_factory=dict)


class AppState(BaseModel):
    """Global runtime state for the rewrite."""

    config: AppConfig = Field(default_factory=AppConfig)
    data: GameData = Field(default_factory=GameData)

    syncing: bool = False
    last_export: float = 0.0
    day: int = 0
    time: str = ""

    model_config = ConfigDict(arbitrary_types_allowed=True)


state = AppState()
