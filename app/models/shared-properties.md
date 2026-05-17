
---

## Top-Level Fields (identical for both)
`class_name`, `names`, `location`

---

## Shared Properties (44 total)

**Core identity - nearly always present (>90%):**
| Property | Tamed % | Wild % |
|---|---|---|
| `DinoID1` | 100% | 100% |
| `DinoID2` | 100% | 100% |
| `OriginalCreationTime` | 100% | 100% |
| `UntamedPoopTimeCache` | 100% | 100% |
| `bServerInitializedDino` | 100% | 100% |
| `TargetingTeam` | 100% | 89% |
| `LastEnterStasisTime` | 99% | 100% |
| `ColorSetIndices` | 98% | 96% |
| `RequiredTameAffinity` | 98% | 93% |
| `OriginalNPCVolumeName` | 33% | 94% |
| `LastEggSpawnChanceTime` | 63% | 100% |

**Moderate frequency (30-80%):**
| Property | Tamed % | Wild % |
|---|---|---|
| `LastTimeUpdatedCharacterStatusComponent` | 77% | 57% |
| `CharacterSavedDynamicBaseRelativeRotation` | 77% | 57% |
| `LastTameConsumedFoodTime` | 77% | 57% |
| `SavedBaseWorldLocation` | 77% | 57% |
| `LastInAllyRangeSerialized` | 77% | 57% |
| `bSavedWhenStasised` | 75% | 48% |
| `bIsFemale` | 60% | 32% |
| `GeneTraits` | 36% | 33% |
| `LastUpdatedBabyAgeAtTime` | 97% | 7% |
| `TamingLastFoodConsumptionTime` | 96% | <1% |
| `DinoAncestors` | 43% | 3% |
| `DinoAncestorsMale` | 42% | 3% |

**Rare/edge-case (<30%):**
`LastInAllyRangeTime`, `NonDedicatedFreezeDinoPhysicsIfLevelUnloaded`, `bPreventHibernation`, `bIgnoreNPCCountVolumes`, `ForcedWildBabyAge`, `bIsAWildFollowerKnownServerside`, `bTargetingIgnoredByWildDinos`, `WildFollowerRefs`, `bAutoChargeActive`, `ClimberRestoreToClimbingRotation`, `bRestoreToSeeking`, `LastBeaverDamSpawn`, `RandomMutationsFemale`, `RandomMutationsMale`, `CharacterSavedDynamicBaseRelativeLocation`, `FollowStoppingDistance`, `TameIneffectivenessModifier`, `AllowWildBabyTaming`, `WaterAmount`, `bIsSleeping`, `LastUpdatedMatingAtTime`

---

## Tamed-Only Properties (59)

**Core taming data:**
`TamerString`, `TamingTeamID`, `TamedAtTime`, `TamedTimeStamp`, `TamedName`, `TamedOnServerName`, `UploadedFromServerName`, `DinoDownloadedAtTime`, `OwningPlayerID`, `OwningPlayerName`, `TribeName`, `CurrentTameAffinity`

**Imprinting:**
`ImprinterName`, `ImprinterPlayerDataID`, `ImprinterPlayerUniqueNetId`

**Baby/breeding:**
`BabyCuddleFood`, `BabyCuddleType`, `BabyCuddleWalkStartingLocation`, `BabyNextCuddleTime`, `GestationEggColorSetIndices`, `GestationEggNumberOfLevelUpPointsApplied`, `GestationEggNumberOfMutationsApplied`, `GestationEggRandomMutationsFemale`, `GestationEggRandomMutationsMale`, `GestationEggTamedIneffectivenessModifier`, `NextAllowedMatingTime`, `LastUpdatedGestationAtTime`

**Behavior/settings:**
`TamedAggressionLevel`, `TamedAITargetingRange`, `bEnableTamedMating`, `bEnableTamedWandering`, `bIgnoreAllWhistles`, `bIgnoreAllyLook`, `bIsInTurretMode`, `bWarnOfPlayers`, `CurrentBotMode`, `bIsBotIdle`, `bForceActivateAtMaxCharge`, `bChargeInfusedWebsModeOn`, `currentWebWeaponAmmo`

**Structure/mount:**
`SaddleStructures`, `LadderStructures`, `DockingBayLocation`, `WildRidingStartTransform`, `bHadStaticBase`, `bHadStaticMapActorBase`

**Death/misc:**
`CorpseDestructionTime`, `CorpseDestructionTimer`, `bIsDead`, `BondedDinoData`, `FuelPercent`, `ReserveFuelPercent`, `LastFeatherPluckTime`, `LastPassengerTrainingLevels`, `CurrentSpecificHarvestResourceIndex`, `ColorSetNames`, `NursingTroughFoodEffectivenessMultiplier`, `bIsNursing`, `bNurseVisualActive`

---

## Wild-Only Properties (38)

**Core wild data:**
`WildRandomScale`, `BabyAge`, `bIsBaby`, `bBabyInitiallyUnclaimed`, `bIsParentWildDino`, `WildFollowingParentRef`, `bCanBeDamaged`, `ResourceAmount`, `RequiredTamingFoodIndex`, `SavedLastValidTameVersion`, `bForceDisablingTaming`

**AI/behavior:**
`AIRangeMultiplier`, `WanderRadiusMultiplier`, `bDontWander`, `bIsFlying`, `bIsUsingCamo`, `UpdateWanderTargetTime`, `AffinityDecayStart`, `HarvestResourceLevels`

**Location/state:**
`bIsUnderground`, `isUnderground`, `UndergroundVisState`, `bClimberRestoreToAttached`, `bIsLatched`, `LastLatchedISMCBodyIndex`, `LatchTargetTransform`, `isBuried`

**Loot system:**
`HasLoot`, `HasLootTarget`, `LootDropTime`, `LootPickupTime`, `EndLootingTime`

**Misc:**
`LastScreamTime`, `LastWildNestSpawnTime`, `LastUnstasisStructureTime`, `bIncrementedZoneManagerDirectLink`, `bPreventSaving`, `bTargetingIgnoreWildDinos`

---

## Component Keys (Status Component)

**Shared (15):** `BaseCharacterLevel`, `CurrentStatusStates`, `CurrentStatusValues`, `EquippedItems`, `InventoryItems`, `LastInventoryRefreshTime`, `MaxTamingEffectivenessBaseLevelMultiplier`, `NumberOfLevelUpPointsApplied`, `PaintingRevisionMap`, `StatusValueModifiers`, `UniquePaintingId`, `UniquePaintingIdMap`, `bInitializedMe`, `bReplicateGlobalStatusValues`, `bServerFirstInitialized`

**Tamed-only (13):** `CraftingStartTimes`, `DinoImprintingQuality`, `DisplayDefaultItemInventoryCount`, `ExperiencePoints`, `ExtraCharacterLevel`, `ItemSlots`, `NumberOfLevelUpPointsAppliedTamed`, `NumberOfMutationsAppliedTamed`, `SortingInputAmounts`, `SortingInputs`, `TamedIneffectivenessModifier`, `bAllowLevelUps`, `bInitializedBaseLevelMaxStatusValues`

**Wild-only (0):** None.

---

## Suggested Pydantic Model Structure

Based on frequency, good fields for a `Creature` base model would be the **high-frequency shared** properties + shared component keys:

```python
class Creature(BaseModel):
    # Top-level
    class_name: str
    names: list[str]
    location: Location | None

    # Core identity (shared, ~100%)
    dino_id1: int
    dino_id2: int
    targeting_team: int
    original_creation_time: float
    color_set_indices: list[int] | None
    is_female: bool

    # Status component (shared)
    base_level: int  # BaseCharacterLevel
    level_up_points: list[int] | None  # NumberOfLevelUpPointsApplied
    current_status_values: list[float] | None  # CurrentStatusValues
```

Then `TamedCreature(Creature)` adds ownership/imprinting/taming fields, and `WildCreature(Creature)` adds `wild_random_scale`, `baby_age`, etc. The rare/edge-case shared props can live in an `Optional[dict]` or be omitted.
