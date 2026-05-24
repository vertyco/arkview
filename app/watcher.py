import asyncio
import enum
import logging
import time
import typing as t
from dataclasses import dataclass, field
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

log = logging.getLogger("arkviewer.watcher")


class IngestScope(enum.Enum):
    WORLD = "world"
    PROFILE = "profile"
    TRIBE = "tribe"
    CLUSTER = "cluster"


_DEBOUNCE_BY_SCOPE: t.Final[dict[IngestScope, float]] = {
    IngestScope.WORLD: 1.0,
    IngestScope.PROFILE: 5.0,
    IngestScope.TRIBE: 1.0,
    IngestScope.CLUSTER: 5.0,
}

_COOLDOWN_BY_SCOPE: t.Final[dict[IngestScope, float]] = {
    IngestScope.WORLD: 0.0,
    IngestScope.PROFILE: 30.0,
    IngestScope.TRIBE: 5.0,
    IngestScope.CLUSTER: 30.0,
}


def debounce_for_scope(scope: IngestScope) -> float:
    assert scope in _DEBOUNCE_BY_SCOPE
    return _DEBOUNCE_BY_SCOPE[scope]


def cooldown_for_scope(scope: IngestScope) -> float:
    assert scope in _COOLDOWN_BY_SCOPE
    return _COOLDOWN_BY_SCOPE[scope]


@dataclass(slots=True)
class Cooldown:
    """Per-path rate limiter. `acquire(path)` returns True iff allowed now.

    Pre: `window_s` >= 0.
    Post: True the first call, False until `window_s` seconds have passed
    since the last True return for that path.
    """

    window_s: float
    _last: dict[Path, float] = field(default_factory=dict)

    def acquire(self, path: Path) -> bool:
        assert self.window_s >= 0
        now = time.monotonic()
        # Evict entries past their cooldown window so `_last` stays bounded by
        # the number of paths active within `window_s` rather than growing one
        # entry per unique path ever seen (a slow leak on a long-running
        # watcher). A past-window entry would return True anyway.
        stale = [p for p, ts in self._last.items() if now - ts >= self.window_s]
        for p in stale:
            del self._last[p]
        last = self._last.get(path)
        if last is not None and now - last < self.window_s:
            return False
        self._last[path] = now
        return True


async def wait_for_stable(
    path: Path,
    quiet_s: float = 0.75,
    timeout_s: float = 30.0,
    poll_s: float = 0.1,
) -> bool:
    """Block until `path`'s size + mtime are unchanged for `quiet_s` seconds.

    Pre: `path` is a Path; `quiet_s` > 0; `timeout_s` > 0.
    Post: True when the file stops changing; False on timeout or vanish.
    Why: ARK writes saves in chunks; firing the parser on the first watchdog
    event would race the writer and produce truncated reads in arkparser.
    """
    assert quiet_s > 0 and timeout_s > 0 and poll_s > 0
    deadline = time.monotonic() + timeout_s
    last_sig: tuple[int, float] | None = None
    last_change = time.monotonic()
    while time.monotonic() < deadline:
        try:
            st = path.stat()
        except FileNotFoundError:
            return False
        sig = (st.st_size, st.st_mtime)
        if sig != last_sig:
            last_sig = sig
            last_change = time.monotonic()
        elif time.monotonic() - last_change >= quiet_s:
            return True
        await asyncio.sleep(poll_s)
    return False


def classify_path(path: Path) -> IngestScope | None:
    """Suffix-based scope classifier (CLUSTER decided by caller via cluster_dir).

    Pre: `path` is a Path.
    Post: returns IngestScope.WORLD for *.ark, PROFILE for *.arkprofile,
    TRIBE for *.arktribe. None for .arktributetribe and everything else.
    """
    assert isinstance(path, Path)
    if path.suffix == ".ark":
        return IngestScope.WORLD
    if path.suffix == ".arkprofile":
        return IngestScope.PROFILE
    if path.suffix == ".arktribe":
        return IngestScope.TRIBE
    if path.suffix == ".arktributetribe":
        return None
    return None


def should_enqueue(path: Path, cluster_dir: Path | None) -> bool:
    """True if a changed file warrants an ingest event.

    Mirrors the downstream scope decision: any classifiable save file, plus
    extensionless files living under `cluster_dir`. Cluster transfer files
    have no suffix, so `classify_path` can't see them — without this branch
    the watcher silently drops every cluster upload and the CLUSTER scope is
    dead code.

    Pre: `path` is a Path. Post: bool.
    """
    assert isinstance(path, Path)
    if classify_path(path) is not None:
        return True
    if cluster_dir is not None and not path.suffix:
        try:
            path.relative_to(cluster_dir)
        except ValueError:
            return False
        return True
    return False


async def coalesce_events(
    queue: asyncio.Queue[Path],
    handler: t.Callable[[Path], t.Awaitable[None]],
    debounce_s: float = 1.0,
) -> None:
    """Coalesce a burst of events on the same path into one handler call.

    Pre: `queue` is a non-empty asyncio.Queue of Paths; `debounce_s` > 0.
    Post: each distinct path dispatched to `handler` exactly once after the
    debounce window expires.
    """
    assert debounce_s > 0
    pending: set[Path] = set()
    while not queue.empty():
        pending.add(queue.get_nowait())
    if not pending:
        return
    await asyncio.sleep(debounce_s)
    while not queue.empty():
        pending.add(queue.get_nowait())
    for path in pending:
        await handler(path)


class _Forwarder(FileSystemEventHandler):
    """watchdog handler that forwards changed-file paths into an asyncio.Queue."""

    def __init__(
        self,
        queue: asyncio.Queue[Path],
        loop: asyncio.AbstractEventLoop,
        cluster_dir: Path | None = None,
    ) -> None:
        self.queue = queue
        self.loop = loop
        self.cluster_dir = cluster_dir

    def enqueue(self, src: str) -> None:
        path = Path(src)
        if not should_enqueue(path, self.cluster_dir):
            return
        self.loop.call_soon_threadsafe(self.queue.put_nowait, path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self.enqueue(str(event.src_path))

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self.enqueue(str(event.src_path))


def start_watcher(
    watch_dirs: list[Path],
    queue: asyncio.Queue[Path],
    loop: asyncio.AbstractEventLoop,
    cluster_dir: Path | None = None,
) -> BaseObserver:
    """Start a recursive watchdog Observer on each dir."""
    assert watch_dirs, "at least one dir required"
    handler = _Forwarder(queue, loop, cluster_dir)
    observer = Observer()
    for d in watch_dirs:
        assert d.exists(), f"watch dir missing: {d}"
        observer.schedule(handler, str(d), recursive=False)
    observer.start()
    return observer
