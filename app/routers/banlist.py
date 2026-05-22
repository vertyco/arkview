import logging
import typing as t

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import RequireBearer
from app.config import AppConfig
from app.metadata import get_metadata

log = logging.getLogger("arkviewer.banlist")


class BanlistUpdate(BaseModel):
    """Accept either {banlist:[...]} or {bans:[...]} for back-compat with AVClient."""

    banlist: list[str] | None = None
    bans: list[str] | None = None

    @property
    def ids(self) -> list[str]:
        return self.banlist if self.banlist is not None else (self.bans or [])


def _read(path) -> list[str]:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    seen: set[str] = set()
    out: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def build_router(cfg: AppConfig) -> APIRouter:
    auth = RequireBearer(cfg.api_key)
    router = APIRouter(dependencies=[Depends(auth)])

    @router.get("/banlist")
    async def get_banlist() -> dict[str, t.Any]:
        ids: list[str] = []
        if cfg.banlist_file is not None and cfg.banlist_file.exists():
            ids = _read(cfg.banlist_file)
        return {"banlist": ids, **await get_metadata(cfg)}

    @router.put("/updatebanlist")
    async def update_banlist(req: BanlistUpdate) -> dict[str, t.Any]:
        if cfg.banlist_file is None:
            raise HTTPException(status_code=400, detail="BanListFile not configured")
        seen: set[str] = set()
        unique: list[str] = []
        for sid in req.ids:
            s = sid.strip()
            if s and s not in seen:
                seen.add(s)
                unique.append(s)
        cfg.banlist_file.parent.mkdir(parents=True, exist_ok=True)
        cfg.banlist_file.write_text(
            "\n".join(unique) + ("\n" if unique else ""), encoding="utf-8"
        )
        log.info("Ban list updated - %d entries", len(unique))
        return {"success": True, "banlist": unique, **await get_metadata(cfg)}

    return router
