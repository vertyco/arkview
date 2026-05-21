import typing as t  # noqa: F401

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import RequireBearer
from app.config import AppConfig


class BanlistUpdate(BaseModel):
    banlist: list[str]


def build_router(cfg: AppConfig) -> APIRouter:
    auth = RequireBearer(cfg.api_key)
    router = APIRouter(dependencies=[Depends(auth)])

    @router.get("/banlist")
    async def get_banlist() -> dict[str, list[str]]:
        if cfg.banlist_file is None or not cfg.banlist_file.exists():
            return {"banlist": []}
        text = cfg.banlist_file.read_text(encoding="utf-8")
        return {"banlist": [line.strip() for line in text.splitlines() if line.strip()]}

    @router.put("/updatebanlist")
    async def update_banlist(req: BanlistUpdate) -> dict[str, str]:
        if cfg.banlist_file is None:
            raise HTTPException(status_code=400, detail="BanListFile not configured")
        cfg.banlist_file.parent.mkdir(parents=True, exist_ok=True)
        cfg.banlist_file.write_text("\n".join(req.banlist) + "\n", encoding="utf-8")
        return {"status": "ok"}

    return router
