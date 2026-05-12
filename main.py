import asyncio
import logging
import os
import sys
import time as time_module
import typing as t
from pathlib import Path

import uvicorn

from app.config import load_config
from app.constants import VERSION
from app.loader import (
    parse_all_data,
    parse_cluster_data,
    parse_cluster_inventory_file,
    parse_profile_data,
    parse_tribe_data,
    parse_world_data,
)
from app.state import state
from app.watcher import ReparseScope, watcher

log = logging.getLogger("arkviewer")


def replace_by_data_file(items: list[t.Any], item: t.Any) -> list[t.Any]:
    updated: list[t.Any] = []
    replaced = False

    for existing in items:
        if getattr(existing, "data_file", "") == getattr(item, "data_file", ""):
            updated.append(item)
            replaced = True
            continue
        updated.append(existing)

    if not replaced:
        updated.append(item)

    return updated


def remove_by_data_file(items: list[t.Any], data_file: str) -> list[t.Any]:
    return [item for item in items if getattr(item, "data_file", "") != data_file]


class Manager:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.server: uvicorn.Server | None = None
        self.reparse_lock = asyncio.Lock()

    async def refresh_all(self) -> None:
        result = await asyncio.to_thread(parse_all_data, state.config)
        state.data = result["data"]
        state.day = result["day"]
        state.time = result["time"]
        state.last_export = time_module.time()

    async def reparse(
        self,
        scopes: frozenset[ReparseScope],
        changed_paths: frozenset[Path] = frozenset(),
    ) -> None:
        async with self.reparse_lock:
            state.syncing = True
            try:
                if ReparseScope.SAVE in scopes and state.config.map_file is not None:
                    world_result = await asyncio.to_thread(
                        parse_world_data, state.config
                    )
                    if world_result is not None:
                        state.data.tamed = world_result["tamed"]
                        state.data.wild = world_result["wild"]
                        state.data.structures = world_result["structures"]
                        state.data.mapstructures = world_result["mapstructures"]
                        state.day = world_result["day"]
                        state.time = world_result["time"]

                if ReparseScope.PROFILE in scopes:
                    profile_paths = sorted(
                        path for path in changed_paths if path.suffix == ".arkprofile"
                    )
                    for path in profile_paths:
                        if not path.exists():
                            state.data.players = remove_by_data_file(
                                state.data.players, path.name
                            )
                            continue

                        player = await asyncio.to_thread(
                            parse_profile_data, path, state.config
                        )
                        if player is None:
                            continue
                        state.data.players = replace_by_data_file(
                            state.data.players, player
                        )

                if ReparseScope.TRIBE in scopes:
                    tribe_paths = sorted(
                        path
                        for path in changed_paths
                        if path.suffix in {".arktribe", ".arktributetribe"}
                    )
                    for path in tribe_paths:
                        if not path.exists():
                            state.data.tribes = remove_by_data_file(
                                state.data.tribes, path.name
                            )
                            state.data.tribelogs = remove_by_data_file(
                                state.data.tribelogs, path.name
                            )
                            continue

                        tribe_result = await asyncio.to_thread(
                            parse_tribe_data, path, state.config
                        )
                        tribe = tribe_result["tribe"]
                        tribelog = tribe_result["tribelog"]

                        if tribe is not None:
                            state.data.tribes = replace_by_data_file(
                                state.data.tribes, tribe
                            )
                        if tribelog is not None:
                            state.data.tribelogs = replace_by_data_file(
                                state.data.tribelogs, tribelog
                            )

                if ReparseScope.CLUSTER in scopes:
                    cluster_dir = state.config.cluster_dir
                    if cluster_dir is not None and cluster_dir.exists():
                        cluster_paths = sorted(
                            path
                            for path in changed_paths
                            if not path.suffix and path.parent == cluster_dir
                        )

                        if not cluster_paths:
                            state.data.cloud_inventory = await asyncio.to_thread(
                                parse_cluster_data, state.config
                            )
                        else:
                            for path in cluster_paths:
                                if not path.exists():
                                    state.data.cloud_inventory.pop(path.stem, None)
                                    continue

                                inventory = await asyncio.to_thread(
                                    parse_cluster_inventory_file, path, state.config
                                )
                                if inventory is not None:
                                    state.data.cloud_inventory[path.stem] = inventory

                state.last_export = time_module.time()
            except Exception as exc:
                log.error("Reparse failed: %s", exc, exc_info=True)
            finally:
                state.syncing = False

    async def start(self) -> None:
        state.config = load_config()

        await self.refresh_all()

        watcher.start(
            loop=self.loop,
            callback=self.reparse,
            map_file=state.config.map_file,
            cluster_dir=state.config.cluster_dir,
        )

        log.info("ArkViewer v%s starting (PID %d)", VERSION, os.getpid())

    async def shutdown(self) -> None:
        watcher.stop()
        log.info("Shutting down...")

    @classmethod
    def run(cls) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        manager = cls(loop)

        try:
            loop.run_until_complete(manager.start())
        except KeyboardInterrupt:
            print("Interrupted — shutting down...")
        except Exception as e:
            logging.getLogger("arkviewer.main").critical("Fatal error", exc_info=e)
        finally:
            if not loop.is_closed():
                loop.run_until_complete(manager.shutdown())
                loop.close()


if __name__ == "__main__":
    # Allow running from both `python main.py` and as a PyInstaller exe.
    # When frozen, sys.executable points to the exe and __file__ may not exist.
    if getattr(sys, "frozen", False):
        # Ensure the working directory is the exe's directory
        os.chdir(Path(sys.executable).parent)

    Manager.run()
