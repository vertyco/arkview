"""
Ban list management routes.

Reads and writes BanList.txt - the text file ARK uses to track banned Steam IDs.
Each line is a single Steam ID / platform ID.
"""

import logging
import typing as t

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.auth import verify_api_key
from app.metadata import get_metadata
from app.state import state

log = logging.getLogger("arkviewer.banlist")

router = APIRouter(tags=["banlist"], dependencies=[Depends(verify_api_key)])


def read_ban_file() -> list[str]:
    """Read BanList.txt and return a deduplicated, stripped list of IDs."""
    ban_file = state.config.ban_file
    if ban_file is None:
        raise HTTPException(status_code=503, detail="BanListFile not configured")
    if not ban_file.exists():
        return []
    lines = ban_file.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and stripped not in seen:
            seen.add(stripped)
            result.append(stripped)
    return result


def write_ban_file(ids: list[str]) -> None:
    """Write the deduplicated list of IDs to BanList.txt."""
    ban_file = state.config.ban_file
    if ban_file is None:
        raise HTTPException(status_code=503, detail="BanListFile not configured")
    ban_file.write_text("\n".join(ids) + ("\n" if ids else ""), encoding="utf-8")


@router.get("/banlist")
async def get_banlist() -> JSONResponse:
    """Return all banned platform IDs with metadata."""
    ids = read_ban_file()
    meta = get_metadata()
    return JSONResponse(content={"banlist": ids, **meta})


class UpdateBanListRequest(BaseModel):
    banlist: list[str] | None = None
    bans: list[str] | None = None

    @property
    def ids(self) -> list[str]:
        """Return whichever field was provided (prefer banlist over bans)."""
        return self.banlist or self.bans or []


@router.put("/updatebanlist")
async def update_banlist(body: UpdateBanListRequest) -> JSONResponse:
    """Replace the entire ban list with the provided list of IDs."""
    seen: set[str] = set()
    unique: list[str] = []
    for steam_id in body.ids:
        stripped = steam_id.strip()
        if stripped and stripped not in seen:
            seen.add(stripped)
            unique.append(stripped)

    write_ban_file(unique)
    log.info("Ban list updated - %d entries", len(unique))
    meta = get_metadata()
    return JSONResponse(content={"success": True, **meta})


@router.post("/banlist/{steam_id}", status_code=201)
async def add_ban(steam_id: str) -> dict[str, t.Any]:
    """Add steam_id to BanList.txt. Idempotent."""
    ids = read_ban_file()
    if steam_id in ids:
        return {"status": "already_banned", "steam_id": steam_id}
    ids.append(steam_id)
    write_ban_file(ids)
    log.info("Banned %s (%d total)", steam_id, len(ids))
    return {"status": "banned", "steam_id": steam_id}


@router.delete("/banlist/{steam_id}")
async def remove_ban(steam_id: str) -> dict[str, t.Any]:
    """Remove steam_id from BanList.txt. Raises 404 if not found."""
    ids = read_ban_file()
    if steam_id not in ids:
        raise HTTPException(
            status_code=404, detail=f"{steam_id} is not in the ban list"
        )
    ids = [i for i in ids if i != steam_id]
    write_ban_file(ids)
    log.info("Unbanned %s (%d remaining)", steam_id, len(ids))
    return {"status": "unbanned", "steam_id": steam_id}
