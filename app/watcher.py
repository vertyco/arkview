import asyncio
import enum
import logging
import threading
import typing as t
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

log = logging.getLogger("arkviewer.watcher")

DEBOUNCE_SECONDS = 1.5


class ReparseScope(enum.Enum):
    SAVE = "save"
    PROFILE = "profile"
    TRIBE = "tribe"
    CLUSTER = "cluster"


EXT_SCOPE: dict[str, ReparseScope] = {
    ".ark": ReparseScope.SAVE,
    ".arkprofile": ReparseScope.PROFILE,
    ".arktribe": ReparseScope.TRIBE,
    ".arktributetribe": ReparseScope.TRIBE,
}


class ArkEventHandler(FileSystemEventHandler):
    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        callback: t.Callable[
            [frozenset[ReparseScope], frozenset[Path]], t.Coroutine[t.Any, t.Any, None]
        ],
        default_scope: ReparseScope,
    ) -> None:
        super().__init__()
        self.loop = loop
        self.callback = callback
        self.default_scope = default_scope
        self.pending_scopes: set[ReparseScope] = set()
        self.pending_paths: set[Path] = set()
        self.debounce_timer: threading.Timer | None = None
        self.lock = threading.Lock()

    def scope_for(self, path: Path) -> ReparseScope:
        return EXT_SCOPE.get(path.suffix, self.default_scope)

    def handle(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return

        path = Path(str(event.src_path))
        self.schedule_reparse(self.scope_for(path), path)

    def on_modified(self, event: FileSystemEvent) -> None:
        self.handle(event)

    def on_created(self, event: FileSystemEvent) -> None:
        self.handle(event)

    def on_deleted(self, event: FileSystemEvent) -> None:
        self.handle(event)

    def schedule_reparse(self, scope: ReparseScope, path: Path) -> None:
        with self.lock:
            self.pending_scopes.add(scope)
            self.pending_paths.add(path)

            if self.debounce_timer is not None:
                self.debounce_timer.cancel()

            self.debounce_timer = threading.Timer(DEBOUNCE_SECONDS, self.fire)
            self.debounce_timer.daemon = True
            self.debounce_timer.start()

    def fire(self) -> None:
        with self.lock:
            scopes = frozenset(self.pending_scopes)
            paths = frozenset(self.pending_paths)
            self.pending_scopes.clear()
            self.pending_paths.clear()

        self.loop.call_soon_threadsafe(
            self.loop.create_task, self.callback(scopes, paths)
        )


class FileWatcher:
    def __init__(self) -> None:
        self.observer: BaseObserver | None = None

    def start(
        self,
        loop: asyncio.AbstractEventLoop,
        callback: t.Callable[
            [frozenset[ReparseScope], frozenset[Path]], t.Coroutine[t.Any, t.Any, None]
        ],
        map_file: Path | None,
        cluster_dir: Path | None,
    ) -> None:
        if self.observer is not None:
            self.stop()

        if not map_file and not cluster_dir:
            log.warning("No map file or cluster dir configured, watcher not started")
            return

        observer = Observer()

        if map_file and map_file.parent.exists():
            observer.schedule(
                ArkEventHandler(
                    loop=loop, callback=callback, default_scope=ReparseScope.SAVE
                ),
                str(map_file.parent),
                recursive=False,
            )

        if cluster_dir and cluster_dir.exists():
            observer.schedule(
                ArkEventHandler(
                    loop=loop, callback=callback, default_scope=ReparseScope.CLUSTER
                ),
                str(cluster_dir),
                recursive=False,
            )

        observer.start()
        self.observer = observer

    def stop(self) -> None:
        if self.observer is None:
            return

        try:
            self.observer.stop()
            self.observer.join(timeout=5)
        finally:
            self.observer = None


watcher = FileWatcher()
