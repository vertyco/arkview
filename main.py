import asyncio
import logging
import os
import sys
import typing as t  # noqa: F401
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from app.config import AppConfig, load_config
from app.db import init_schema, meta_get
from app.ingest import ingest_full, ingest_profile, ingest_tribe
from app.routers import banlist, cluster, data, health, scan, tribes
from app.staleness import StalenessMiddleware
from app.watcher import (
    Cooldown,
    IngestScope,
    classify_path,
    coalesce_events,
    cooldown_for_scope,
    start_watcher,
    wait_for_stable,
)

log = logging.getLogger("arkviewer")


def _resolve_config_path() -> Path:
    env = os.environ.get("ARKVIEWER_CONFIG")
    if env:
        return Path(env)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "config.ini"
    return Path(__file__).parent / "config.ini"


def _scope_for(cfg: AppConfig, path: Path) -> IngestScope | None:
    """Classify path, falling back to CLUSTER when it lives under cluster_dir."""
    base = classify_path(path)
    if base is not None:
        return base
    if cfg.cluster_dir is not None and not path.suffix:
        try:
            path.relative_to(cfg.cluster_dir)
        except ValueError:
            return None
        return IngestScope.CLUSTER
    return None


def build_app(cfg: AppConfig) -> FastAPI:
    app = FastAPI(title="ArkViewer", version="3.0.0")
    app.add_middleware(StalenessMiddleware, db_path=cfg.db_path)
    app.include_router(health.build_router(cfg))
    app.include_router(
        cluster.build_router(cfg)
    )  # must come before data: /data/cluster vs /data/{dtype}
    app.include_router(data.build_router(cfg))
    app.include_router(tribes.build_router(cfg))
    app.include_router(scan.build_router(cfg))
    app.include_router(banlist.build_router(cfg))
    return app


async def _ingest_loop(cfg: AppConfig, queue: asyncio.Queue[Path]) -> None:
    """Forever-running: pull events, debounce, stability gate, cooldown, dispatch.

    Cooldown registry is per-scope so each ARK file type has its own window
    (profiles+cluster heavy, world+tribe light). The stability gate prevents
    parsing mid-write — arkparser would otherwise hit truncated reads when
    the game is in the middle of flushing a save chunk.
    """
    cooldowns: dict[IngestScope, Cooldown] = {
        s: Cooldown(window_s=cooldown_for_scope(s)) for s in IngestScope
    }

    async def handle(path: Path) -> None:
        scope = _scope_for(cfg, path)
        if scope is None:
            return
        if not await wait_for_stable(path, quiet_s=0.75, timeout_s=30.0):
            log.warning("Skipping %s: never stabilised", path)
            return
        if not cooldowns[scope].acquire(path):
            log.debug("Cooldown active for %s (scope=%s)", path, scope)
            return
        try:
            if scope == IngestScope.WORLD:
                await asyncio.to_thread(ingest_full, cfg)
            elif scope == IngestScope.PROFILE:
                await asyncio.to_thread(ingest_profile, cfg, path)
            elif scope == IngestScope.TRIBE:
                await asyncio.to_thread(ingest_tribe, cfg, path)
            elif scope == IngestScope.CLUSTER:
                # Cluster files splice into export_all output; single-file
                # cluster reparse isn't meaningful, so trigger full reparse.
                # 30s cooldown prevents thrash.
                await asyncio.to_thread(ingest_full, cfg)
        except Exception as exc:
            log.exception("ingest failed for %s: %s", path, exc)

    while True:
        if queue.empty():
            await asyncio.sleep(0.5)
            continue
        await coalesce_events(queue, handle, debounce_s=1.0)


class Manager:
    """Owns the runtime: config, DB, watcher, server."""

    @staticmethod
    def run() -> None:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        cfg_path = _resolve_config_path()
        cfg = load_config(cfg_path)
        init_schema(cfg.db_path)

        if meta_get(cfg.db_path, "last_parse_at") is None and cfg.map_file is not None:
            log.info("Cold start: triggering initial parse of %s", cfg.map_file)
            try:
                ingest_full(cfg)
            except Exception as exc:
                log.exception("Initial parse failed: %s", exc)

        app = build_app(cfg)
        host = "127.0.0.1" if cfg.debug else "0.0.0.0"
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        queue: asyncio.Queue[Path] = asyncio.Queue()
        watch_dirs: list[Path] = []
        if cfg.map_file is not None and cfg.map_file.parent.exists():
            watch_dirs.append(cfg.map_file.parent)
        if cfg.cluster_dir is not None and cfg.cluster_dir.exists():
            watch_dirs.append(cfg.cluster_dir)

        observer = start_watcher(watch_dirs, queue, loop) if watch_dirs else None

        config = uvicorn.Config(
            app=app,
            host=host,
            port=cfg.port,
            loop="asyncio",
            log_level="info",
        )
        server = uvicorn.Server(config)

        async def _runner() -> None:
            ingest_task = asyncio.create_task(_ingest_loop(cfg, queue))
            try:
                await server.serve()
            finally:
                ingest_task.cancel()
                if observer is not None:
                    observer.stop()
                    observer.join()

        loop.run_until_complete(_runner())


if __name__ == "__main__":
    Manager.run()
