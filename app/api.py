from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware

from app.constants import VERSION
from app.routers import banlist, data, health


def create_app() -> FastAPI:
    app = FastAPI(
        title="ArkViewer",
        version=VERSION,
        description="FastAPI REST service for ARK save file data",
    )

    app.add_middleware(GZipMiddleware, minimum_size=1024)

    app.include_router(health.router)
    app.include_router(data.router)
    app.include_router(data.filter_router)
    app.include_router(banlist.router)

    return app
