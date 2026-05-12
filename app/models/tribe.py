import re
import typing as t

from pydantic import AliasChoices, Field, model_validator

from .base import BaseModel, BaseResponse

TRIBE_LOG_PATTERN = re.compile(
    r"^Day (?P<day>\d+), (?P<time>\d{2}:\d{2}:\d{2}): (?P<message>.*)$"
)


def strip_rich_text(value: str) -> str:
    value = re.sub(r"<RichColor[^>]*>", "", value)
    return value.replace("</>", "").strip()


class TribeMember(BaseModel):
    """A single tribe member entry."""

    player_id: int = Field(
        default=0,
        validation_alias=AliasChoices("player_id", "playerid"),
        description="Player implant ID",
    )
    name: str = Field(
        default="",
        validation_alias=AliasChoices("name", "ign"),
        description="Character name",
    )
    rank: int = Field(default=0, validation_alias="rank", description="Tribe rank")
    steam_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("steam_id", "steamid"),
        description="Platform ID when enriched",
    )
    steam_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices("steam_name", "playername"),
        description="Platform name when enriched",
    )
    level: int | None = Field(
        default=None,
        validation_alias=AliasChoices("level", "lvl"),
        description="Character level when enriched",
    )


class Tribe(BaseResponse):
    """Tribe data from arkparser export output."""

    tribe_id: int = Field(
        default=0,
        validation_alias=AliasChoices("tribeid", "tribe_id"),
        description="Tribe ID",
    )
    name: str | None = Field(
        default=None,
        validation_alias=AliasChoices("tribe", "name"),
        description="Tribe name",
    )
    owner_id: int = Field(
        default=0,
        validation_alias=AliasChoices("owner_id", "owner_player_id"),
        description="Tribe owner player ID",
    )
    owner_name: str = Field(
        default="", validation_alias="owner_name", description="Tribe owner name"
    )
    member_count: int = Field(
        default=0,
        validation_alias=AliasChoices("players", "member_count"),
        description="Total member count",
    )
    members: list[TribeMember] = Field(
        default_factory=list, description="Tribe member list"
    )
    alliance_ids: list[int] = Field(
        default_factory=list, description="Allied tribe IDs"
    )
    tame_count: int = Field(
        default=0,
        validation_alias=AliasChoices("tames", "tame_count"),
        description="Total tamed creature count",
    )
    structure_count: int = Field(
        default=0,
        validation_alias=AliasChoices("structures", "structure_count"),
        description="Total structure count",
    )
    data_file: str = Field(
        default="",
        validation_alias=AliasChoices("dataFile", "data_file"),
        description="Source tribe filename",
    )
    last_active: str | None = Field(
        default=None,
        validation_alias=AliasChoices("active", "last_active"),
        description="Last active timestamp when enriched",
    )


class TribeLogEntry(BaseModel):
    """A single parsed tribe log entry."""

    day: int = Field(default=0, description="In-game day number")
    time: str = Field(default="", description="In-game log time")
    message: str = Field(
        default="",
        validation_alias=AliasChoices("message", "clean_message"),
        description="Human-readable tribe log message",
    )

    @model_validator(mode="before")
    @classmethod
    def parse_raw_entry(cls, values: t.Any) -> t.Any:
        if isinstance(values, str):
            match = TRIBE_LOG_PATTERN.match(values.strip())
            if not match:
                return {"message": strip_rich_text(values)}
            return {
                "day": int(match.group("day")),
                "time": match.group("time"),
                "message": strip_rich_text(match.group("message")),
            }

        if not isinstance(values, dict):
            return values

        message = values.get("clean_message", values.get("message", ""))
        return {
            **values,
            "message": strip_rich_text(str(message or "")),
        }


class TribeLog(BaseResponse):
    """All tribe log entries for a single tribe."""

    tribe_id: int = Field(
        default=0,
        validation_alias=AliasChoices("tribeid", "tribe_id"),
        description="Tribe ID",
    )
    tribe_name: str = Field(
        default="",
        validation_alias=AliasChoices("tribe", "tribe_name"),
        description="Tribe name",
    )
    data_file: str = Field(
        default="",
        validation_alias=AliasChoices("dataFile", "data_file"),
        description="Source tribe filename",
    )
    logs: list[TribeLogEntry] = Field(
        default_factory=list, description="Parsed tribe log entries"
    )
