import asyncio
import logging
import os
import sys
import typing as t
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from app.config import AppConfig, load_config
from app.console import (
    disable_quickedit,
    init_logging,
    print_banner,
    reconfigure_logging,
    title_loop,
)
from app.constants import VERSION
from app.db import init_schema, maintenance, meta_get
from app.ingest import ingest_full, ingest_profile, ingest_tribe
from app.routers import banlist, cluster, data, health, scan, search, tribes
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

# WAL checkpoint + freelist reclaim cadence between full ingests.
MAINTENANCE_INTERVAL_S: t.Final[int] = 300

# Module-level flag flipped by ingest dispatch so the title bar can show
# `[Parsing]`. Assigned by the single _ingest_loop task; safe single-writer.
_syncing: bool = False


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
    app = FastAPI(title="ArkViewer", version=VERSION)
    app.add_middleware(StalenessMiddleware, db_path=cfg.db_path)
    app.include_router(health.build_router(cfg))
    app.include_router(
        cluster.build_router(cfg)
    )  # must come before data: /data/cluster vs /data/{dtype}
    app.include_router(
        search.build_router(cfg)
    )  # must come before data: /data/search/* vs /data/{dtype}
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
        global _syncing
        _syncing = True
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
        finally:
            _syncing = False

    while True:
        if queue.empty():
            await asyncio.sleep(0.5)
            continue
        await coalesce_events(queue, handle, debounce_s=1.0)


class Manager:
    """Owns the runtime: config, DB, watcher, server."""

    @staticmethod
    def run() -> None:
        disable_quickedit()
        init_logging()
        cfg_path = _resolve_config_path()
        cfg = load_config(cfg_path)
        reconfigure_logging(cfg.debug)
        print_banner(cfg.map_file)
        init_schema(cfg.db_path)

        if meta_get(cfg.db_path, "last_parse_at") is None and cfg.map_file is not None:
            if cfg.map_file.exists():
                log.info("Cold start: triggering initial parse of %s", cfg.map_file)
                global _syncing
                _syncing = True
                try:
                    ingest_full(cfg)
                except Exception as exc:
                    log.exception("Initial parse failed: %s", exc)
                finally:
                    _syncing = False
            else:
                # Save file not on disk yet — common on fresh ASE installs
                # before the server has written its first save. Don't fail
                # the cold-start path; the watcher will pick the file up
                # the moment ARK creates it.
                log.warning(
                    "Map file not yet on disk: %s. Watcher will pick it up "
                    "when ARK writes it; API serves 503 until then.",
                    cfg.map_file,
                )

        app = build_app(cfg)
        host = "0.0.0.0"
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
            # log_config=None makes uvicorn skip its own dictConfig (which
            # otherwise replaces our colored handlers) — uvicorn.* loggers
            # then propagate to our root handler and pick up _CompactFormatter.
            log_config=None,
            access_log=True,
        )
        server = uvicorn.Server(config)

        async def _maintenance_loop() -> None:
            """Periodic WAL checkpoint + freelist reclaim between full ingests.

            Skip while an ingest is in flight — `swap_staging` holds an
            exclusive write lock and `wal_checkpoint(TRUNCATE)` would block
            on it for up to the busy_timeout window.
            """
            while True:
                await asyncio.sleep(MAINTENANCE_INTERVAL_S)
                if _syncing:
                    log.debug("Skipping periodic maintenance: ingest in progress")
                    continue
                try:
                    await asyncio.to_thread(maintenance, cfg.db_path)
                except Exception as exc:
                    log.warning("Periodic maintenance failed: %s", exc)

        async def _runner() -> None:
            ingest_task = asyncio.create_task(_ingest_loop(cfg, queue))
            title_task = asyncio.create_task(title_loop(cfg.map_file, lambda: _syncing))
            maint_task = asyncio.create_task(_maintenance_loop())
            try:
                await server.serve()
            finally:
                ingest_task.cancel()
                title_task.cancel()
                maint_task.cancel()
                if observer is not None:
                    observer.stop()
                    observer.join()

        loop.run_until_complete(_runner())


if __name__ == "__main__":
    Manager.run()
