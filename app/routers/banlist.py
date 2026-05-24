import logging
import typing as t
from pathlib import Path

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


def dedupe_nonempty(items: t.Iterable[str]) -> list[str]:
    """Split on whitespace, dedupe preserving order; drop empty strings.

    A ban id is a single whitespace-free token. Splitting on whitespace
    flattens any entry that contains an embedded newline (or stray spaces),
    so a single malformed input can neither inject extra lines into
    BanList.txt nor smuggle a duplicate past de-duplication.
    """
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        for tok in item.split():
            if tok not in seen:
                seen.add(tok)
                out.append(tok)
    return out


def read_banlist(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    return dedupe_nonempty(text.splitlines())


def build_router(cfg: AppConfig) -> APIRouter:
    auth = RequireBearer(cfg.api_key)
    router = APIRouter(dependencies=[Depends(auth)])

    @router.get("/banlist")
    async def get_banlist() -> dict[str, t.Any]:
        ids: list[str] = []
        if cfg.banlist_file is not None:
            ids = read_banlist(cfg.banlist_file)
        return {"banlist": ids, **await get_metadata(cfg)}

    @router.put("/updatebanlist")
    async def update_banlist(req: BanlistUpdate) -> dict[str, t.Any]:
        if cfg.banlist_file is None:
            raise HTTPException(status_code=400, detail="BanListFile not configured")
        unique = dedupe_nonempty(req.ids)
        cfg.banlist_file.parent.mkdir(parents=True, exist_ok=True)
        cfg.banlist_file.write_text(
            "\n".join(unique) + ("\n" if unique else ""), encoding="utf-8"
        )
        log.info("Ban list updated - %d entries", len(unique))
        return {"success": True, "banlist": unique, **await get_metadata(cfg)}

    return router
