import typing as t

from pydantic import (
    AliasPath,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from .base import BaseResponse, ColorRegions, Location, Stats


class Ancestor(BaseModel):
    """A single entry in a creature's breeding lineage."""

    father_name: str = Field(
        default="",
        validation_alias="MaleName",
        description="Display name of the father at time of breeding",
    )
    father_id: int = Field(
        default=0,
        description="64-bit unique ID of the father",
    )
    mother_name: str = Field(
        default="",
        validation_alias="FemaleName",
        description="Display name of the mother at time of breeding",
    )
    mother_id: int = Field(
        default=0,
        description="64-bit unique ID of the mother",
    )

    @model_validator(mode="before")
    @classmethod
    def _combine_ids(cls, values: dict[str, t.Any]) -> dict[str, t.Any]:
        """Combine two-part dino IDs into 64-bit values."""
        values["father_id"] = (int(values["MaleDinoID1"]) << 32) | (
            int(values["MaleDinoID2"]) & 0xFFFFFFFF
        )
        values["mother_id"] = (int(values["FemaleDinoID1"]) << 32) | (
            int(values["FemaleDinoID2"]) & 0xFFFFFFFF
        )
        return values


class Creature(BaseResponse):
    """Base response for creatures (tamed or wild).

    Contains fields shared by all creatures regardless of tame status:
    identification, colors, location, sex, and base stats from the
    status component.
    """

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    id: int = Field(
        description="Unique 64-bit creature ID, combined from DinoID1 (upper 32) and DinoID2 (lower 32)"
    )
    class_name: str = Field(
        description="UE4 blueprint class name, e.g. 'Rex_Character_BP_C'"
    )
    location: Location = Field(
        description="World position and rotation in Unreal Engine coordinates"
    )
    lat: float = Field(
        default=0.0,
        description="GPS latitude derived from location + map_config (0.0 if unavailable)",
    )
    lon: float = Field(
        default=0.0,
        description="GPS longitude derived from location + map_config (0.0 if unavailable)",
    )
    ccc: str = Field(
        default="",
        description="UE coordinates as 'x y z' for /admincheat TeleportToCCC",
    )

    colors: ColorRegions = Field(
        default_factory=ColorRegions,
        validation_alias=AliasPath("properties", "ColorSetIndices"),
        description="Color palette indices for the 6 body regions (species-specific mapping)",
    )
    is_female: bool | None = Field(
        default=None,
        validation_alias=AliasPath("properties", "bIsFemale"),
        description="True if female, None if property absent (some species are genderless)",
    )
    tribe_id: int = Field(
        default=0,
        validation_alias=AliasPath("properties", "TargetingTeam"),
        description="Owning team ID. <50k=wild/NPC, 50k-1B=player, 1B-2B=tribe, 2B=unclaimed bred baby",
    )

    # --- Status component fields ---
    base_level: int = Field(
        default=1,
        validation_alias=AliasPath("components", "status", "BaseCharacterLevel"),
        description="Wild base level determined at spawn (1-150+ depending on server settings)",
    )
    wild_stats: Stats = Field(
        default_factory=Stats,
        validation_alias=AliasPath(
            "components", "status", "NumberOfLevelUpPointsApplied"
        ),
        description="Wild random level-up points applied at spawn across 12 stats (byte values 0-255 each)",
    )
    current_stats: Stats = Field(
        default_factory=Stats,
        validation_alias=AliasPath("components", "status", "CurrentStatusValues"),
        description="Current absolute stat values (floats). Same 12-index layout as wild_stats",
    )

    # --- Baby/maturation (both tamed and wild can be babies) ---
    is_baby: bool | None = Field(
        default=None,
        validation_alias=AliasPath("properties", "bIsBaby"),
        description="True if this creature is a juvenile (not yet fully grown). Applies to wild babies and bred tamed babies",
    )
    baby_age: float | None = Field(
        default=None,
        validation_alias=AliasPath("properties", "BabyAge"),
        description="Maturation progress as 0.0-1.0 (multiply by 100 for percentage). None if adult",
    )

    # --- ASA genetics ---
    gene_traits: list[str] = Field(
        default_factory=list,
        validation_alias=AliasPath("properties", "GeneTraits"),
        description="ASA gene traits, e.g. ['MeatCarrier[1]']. Empty for ASE creatures",
    )

    @model_validator(mode="before")
    @classmethod
    def _pre_process(cls, values: dict[str, t.Any]) -> dict[str, t.Any]:
        """Combine DinoID1 + DinoID2 into a single 64-bit ``id``.

        This is the only field that can't be expressed as a simple AliasPath
        because it's derived from two separate properties.
        """
        props = values.get("properties", {})
        values["id"] = (int(props["DinoID1"]) << 32) | (
            int(props["DinoID2"]) & 0xFFFFFFFF
        )
        return values

    @computed_field  # type: ignore[prop-decorator]
    @property
    def dino_id(self) -> str:
        """Stable string form of ``id`` used as a dedup key by AVClient."""
        return str(self.id)


class Tamed(Creature):
    """Response model for tamed creatures from a world save.

    Extends Creature with ownership, naming, imprinting, breeding,
    tamed-only stat points, and behavior settings.
    """

    # --- Ownership ---
    tribe_name: str = Field(
        default="",
        validation_alias=AliasPath("properties", "TribeName"),
        description="Display name of the owning tribe",
    )
    tamer_string: str = Field(
        default="",
        validation_alias=AliasPath("properties", "TamerString"),
        description="Name of the player who originally tamed this creature. Also used as tribe name fallback for cryo'd creatures",
    )
    taming_tribe_id: int = Field(
        default=0,
        validation_alias=AliasPath("properties", "TamingTeamID"),
        description="Team ID at tame time. May differ from tribe_id after tribe transfers. 2B = bred creature",
    )
    tamer_id: int = Field(
        default=0,
        validation_alias=AliasPath("properties", "OwningPlayerID"),
        description="Personally-claimed owner's player ID. 0 if unclaimed or tribe-owned",
    )
    tamer_name: str = Field(
        default="",
        validation_alias=AliasPath("properties", "OwningPlayerName"),
        description="Character name of the player who personally claimed this creature. Empty if tribe-owned",
    )

    # --- Naming ---
    tame_name: str = Field(
        default="",
        validation_alias=AliasPath("properties", "TamedName"),
        description="Custom name given by the player, empty if unnamed",
    )

    # --- Imprinting ---
    imprinter_name: str = Field(
        default="",
        validation_alias=AliasPath("properties", "ImprinterName"),
        description="Character name of the player who performed imprint care (cuddles/walks/feeding). Clears TamerString when set",
    )
    imprinter_id: int = Field(
        default=0,
        validation_alias=AliasPath("properties", "ImprinterPlayerDataID"),
        description="ASE unique player data ID of the imprinter. >0 indicates imprinting occurred",
    )
    imprinter_net_id: str = Field(
        default="",
        validation_alias=AliasPath("properties", "ImprinterPlayerUniqueNetId"),
        description="ASA network unique ID of the imprinter (hex string). Empty for ASE or unimprinted creatures",
    )
    imprint_quality: float = Field(
        default=0.0,
        validation_alias=AliasPath("components", "status", "DinoImprintingQuality"),
        description="Imprint completion as 0.0-1.0. Scales imprint stat bonuses and rider damage bonus",
    )

    # --- Levels & Experience ---
    extra_level: int = Field(
        default=0,
        validation_alias=AliasPath("components", "status", "ExtraCharacterLevel"),
        description="Bonus levels gained after taming (domestic level-ups)",
    )
    experience: float = Field(
        default=0.0,
        validation_alias=AliasPath("components", "status", "ExperiencePoints"),
        description="Total accumulated experience points",
    )
    tamed_stats: Stats = Field(
        default_factory=Stats,
        validation_alias=AliasPath(
            "components", "status", "NumberOfLevelUpPointsAppliedTamed"
        ),
        description="Stat points allocated by the player after taming. Same 12-index layout",
    )
    taming_effectiveness: float = Field(
        default=1.0,
        validation_alias=AliasPath(
            "components", "status", "TamedIneffectivenessModifier"
        ),
        description="Taming effectiveness as 0.0-1.0. Affects bonus tame levels. Computed from 1/(1+modifier)",
    )
    mutated_stats: Stats = Field(
        default_factory=Stats,
        validation_alias=AliasPath(
            "components", "status", "NumberOfMutationsAppliedTamed"
        ),
        description="Per-stat mutation counts (ASA only). Same 12-index layout as wild_stats",
    )

    # --- Breeding flags ---
    is_mating: bool | None = Field(
        default=None,
        validation_alias=AliasPath("properties", "bEnableTamedMating"),
        description="True if mating is enabled on this creature",
    )
    is_neutered: bool | None = Field(
        default=None,
        validation_alias=AliasPath("properties", "bIsNeutered"),
        description="True if spayed/neutered (permanently unable to breed)",
    )

    # --- Breeding ancestry ---
    mutations_female: int = Field(
        default=0,
        validation_alias=AliasPath("properties", "RandomMutationsFemale"),
        description="Accumulated mutation count on the maternal line",
    )
    mutations_male: int = Field(
        default=0,
        validation_alias=AliasPath("properties", "RandomMutationsMale"),
        description="Accumulated mutation count on the paternal line",
    )
    ancestors: list[Ancestor] = Field(
        default_factory=list,
        validation_alias=AliasPath("properties", "DinoAncestors"),
        description="Mother's side lineage (one entry per generation)",
    )
    ancestors_male: list[Ancestor] = Field(
        default_factory=list,
        validation_alias=AliasPath("properties", "DinoAncestorsMale"),
        description="Father's side lineage (one entry per generation)",
    )

    # --- Behavior settings ---
    aggression_level: int = Field(
        default=1,
        validation_alias=AliasPath("properties", "TamedAggressionLevel"),
        description="AI behavior mode: 0=Passive, 1=Neutral (default), 2=Aggressive",
    )
    ignore_whistles: bool | None = Field(
        default=None,
        validation_alias=AliasPath("properties", "bIgnoreAllWhistles"),
        description="True if the creature ignores all whistle commands",
    )
    is_turret_mode: bool | None = Field(
        default=None,
        validation_alias=AliasPath("properties", "bIsInTurretMode"),
        description="True if the creature is locked in stationary turret/attack mode",
    )
    is_wandering: bool | None = Field(
        default=None,
        validation_alias=AliasPath("properties", "bEnableTamedWandering"),
        description="True if wandering is enabled",
    )

    # --- Server transfer ---
    tamed_on_server: str = Field(
        default="",
        validation_alias=AliasPath("properties", "TamedOnServerName"),
        description="Name of the server where this creature was originally tamed",
    )
    uploaded_from_server: str = Field(
        default="",
        validation_alias=AliasPath("properties", "UploadedFromServerName"),
        description="Name of the server this creature was last uploaded from (obelisk/transmitter transfer)",
    )

    # --- Cryo state ---
    is_cryo: bool = Field(
        default=False,
        validation_alias=AliasPath("properties", "IsInCryo"),
        description="True if this creature is currently stored in a cryopod",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def level(self) -> int:
        """Total displayed level: ``base_level + extra_level``."""
        return self.base_level + self.extra_level

    @field_validator("tribe_id", mode="before")
    @classmethod
    def _clean_tribe_id(cls, value: t.Any) -> int:
        """Strip wild faction IDs (1-12) to 0 since they're meaningless for tamed creatures."""
        tribe_id = int(value or 0)
        return tribe_id if tribe_id > 50000 else 0

    @field_validator("taming_effectiveness", mode="before")
    @classmethod
    def _convert_ineffectiveness(cls, value: t.Any) -> float:
        """Convert TamedIneffectivenessModifier to effectiveness: 1/(1+modifier)."""
        modifier = float(value or 0)
        return 1.0 / (1.0 + modifier)

    @field_validator("uploaded_from_server", "tamed_on_server", mode="before")
    @classmethod
    def _strip_server_name(cls, value: t.Any) -> str:
        """Server names sometimes have a leading newline from the save format."""
        return str(value or "").strip()


class Wild(Creature):
    """Response model for wild (untamed) creatures from a world save.

    Extends Creature with wild-specific properties like random size
    scaling, baby/juvenile status, and AI behavior flags.
    """

    # --- Wild identity ---
    wild_scale: float = Field(
        default=1.0,
        validation_alias=AliasPath("properties", "WildRandomScale"),
        description="Random size multiplier applied at spawn. 1.0 = normal, range varies by species",
    )

    # --- Wild state ---
    is_flying: bool | None = Field(
        default=None,
        validation_alias=AliasPath("properties", "bIsFlying"),
        description="True if the creature is currently airborne",
    )
    can_be_damaged: bool | None = Field(
        default=None,
        validation_alias=AliasPath("properties", "bCanBeDamaged"),
        description="False for invulnerable event creatures or specific boss-arena dinos",
    )
    taming_disabled: bool | None = Field(
        default=None,
        validation_alias=AliasPath("properties", "bForceDisablingTaming"),
        description="True if this creature cannot be tamed (bosses, mission creatures, special spawns)",
    )
    tameable: bool = Field(
        default=False,
        description="Derived from RequiredTameAffinity property; True when the creature can be tamed",
    )

    @model_validator(mode="before")
    @classmethod
    def _derive_tameable(cls, values: dict[str, t.Any]) -> dict[str, t.Any]:
        """RequiredTameAffinity > 0 → tameable. Checked on root, then status component."""
        props = values.get("properties", {}) or {}
        components = values.get("components", {}) or {}
        status = components.get("status", {}) if isinstance(components, dict) else {}
        affinity = props.get("RequiredTameAffinity")
        if affinity is None and isinstance(status, dict):
            affinity = status.get("RequiredTameAffinity")
        values["tameable"] = bool(affinity is not None and float(affinity) > 0)
        return values

    @computed_field  # type: ignore[prop-decorator]
    @property
    def level(self) -> int:
        """Wild creatures display their base level."""
        return self.base_level
