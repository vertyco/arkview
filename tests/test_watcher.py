import asyncio
import time  # noqa: F401
import typing as t  # noqa: F401
from pathlib import Path

import pytest

from app.watcher import (
    Cooldown,
    IngestScope,
    classify_path,
    coalesce_events,
    debounce_for_scope,
    should_enqueue,
    wait_for_stable,
)


def test_classify_world_save(tmp_path: Path) -> None:
    p = tmp_path / "TheIsland_WP.ark"
    assert classify_path(p) == IngestScope.WORLD


def test_classify_profile(tmp_path: Path) -> None:
    p = tmp_path / "76561198000000000.arkprofile"
    assert classify_path(p) == IngestScope.PROFILE


def test_classify_tribe(tmp_path: Path) -> None:
    p = tmp_path / "12345.arktribe"
    assert classify_path(p) == IngestScope.TRIBE


def test_classify_tribute_tribe_is_ignored(tmp_path: Path) -> None:
    p = tmp_path / "12345.arktributetribe"
    assert classify_path(p) is None


def test_classify_unknown(tmp_path: Path) -> None:
    p = tmp_path / "random.bin"
    assert classify_path(p) is None


def test_should_enqueue_classified_save_files(tmp_path: Path) -> None:
    assert should_enqueue(tmp_path / "TheIsland_WP.ark", None) is True
    assert should_enqueue(tmp_path / "76561198000000000.arkprofile", None) is True
    assert should_enqueue(tmp_path / "12345.arktribe", None) is True
    assert should_enqueue(tmp_path / "random.bin", None) is False


def test_should_enqueue_extensionless_cluster_file(tmp_path: Path) -> None:
    cluster = tmp_path / "cluster"
    cluster.mkdir()
    # Cluster transfer files have NO extension -> classify_path can't see them,
    # but a change must still trigger a reparse when they live under cluster_dir.
    assert should_enqueue(cluster / "76561198000000000", cluster) is True
    # An extensionless file outside the cluster dir is still ignored.
    assert should_enqueue(tmp_path / "stray", cluster) is False
    # With no cluster dir configured, extensionless files are ignored.
    assert should_enqueue(cluster / "76561198000000000", None) is False


def test_cooldown_evicts_entries_past_window(monkeypatch: pytest.MonkeyPatch) -> None:
    cd = Cooldown(window_s=30.0)
    now = [1000.0]
    monkeypatch.setattr("app.watcher.time.monotonic", lambda: now[0])
    cd.acquire(Path("/tmp/a.arkprofile"))
    cd.acquire(Path("/tmp/b.arkprofile"))
    assert len(cd._last) == 2
    # Long after both windows expired, a new acquire must drop the stale keys
    # so the dict can't grow one-entry-per-path for the process lifetime.
    now[0] += 100.0
    cd.acquire(Path("/tmp/c.arkprofile"))
    assert set(cd._last) == {Path("/tmp/c.arkprofile")}


@pytest.mark.asyncio
async def test_coalesce_collapses_burst_to_one_event() -> None:
    seen: list[Path] = []

    async def handler(path: Path) -> None:
        seen.append(path)

    q: asyncio.Queue[Path] = asyncio.Queue()
    target = Path("/tmp/TheIsland.ark")
    for _ in range(10):
        await q.put(target)

    await asyncio.wait_for(coalesce_events(q, handler, debounce_s=0.1), timeout=1.0)
    assert seen == [target]


@pytest.mark.asyncio
async def test_coalesce_distinct_files_kept_separate() -> None:
    seen: list[Path] = []

    async def handler(path: Path) -> None:
        seen.append(path)

    q: asyncio.Queue[Path] = asyncio.Queue()
    await q.put(Path("/tmp/a.arkprofile"))
    await q.put(Path("/tmp/b.arkprofile"))

    await asyncio.wait_for(coalesce_events(q, handler, debounce_s=0.1), timeout=1.0)
    assert sorted(seen) == [Path("/tmp/a.arkprofile"), Path("/tmp/b.arkprofile")]


def test_debounce_for_scope_world_is_short() -> None:
    assert debounce_for_scope(IngestScope.WORLD) == 1.0


def test_debounce_for_scope_profile_is_longer() -> None:
    assert debounce_for_scope(IngestScope.PROFILE) >= 5.0


def test_debounce_for_scope_tribe_is_short() -> None:
    assert debounce_for_scope(IngestScope.TRIBE) == 1.0


def test_debounce_for_scope_cluster_is_longer() -> None:
    assert debounce_for_scope(IngestScope.CLUSTER) >= 5.0


def test_cooldown_blocks_repeat_within_window() -> None:
    cd = Cooldown(window_s=30.0)
    p = Path("/tmp/a.arkprofile")
    assert cd.acquire(p) is True
    assert cd.acquire(p) is False


def test_cooldown_allows_after_window(monkeypatch: pytest.MonkeyPatch) -> None:
    cd = Cooldown(window_s=30.0)
    p = Path("/tmp/a.arkprofile")
    now = [1000.0]
    monkeypatch.setattr("app.watcher.time.monotonic", lambda: now[0])
    assert cd.acquire(p) is True
    now[0] += 5.0
    assert cd.acquire(p) is False
    now[0] += 30.0
    assert cd.acquire(p) is True


def test_cooldown_different_paths_independent() -> None:
    cd = Cooldown(window_s=30.0)
    assert cd.acquire(Path("/tmp/a.arkprofile")) is True
    assert cd.acquire(Path("/tmp/b.arkprofile")) is True


@pytest.mark.asyncio
async def test_wait_for_stable_returns_when_size_stops_growing(tmp_path: Path) -> None:
    target = tmp_path / "save.ark"
    target.write_bytes(b"abc")

    async def grow() -> None:
        await asyncio.sleep(0.05)
        target.write_bytes(b"abcdef")
        await asyncio.sleep(0.05)
        target.write_bytes(b"abcdefghi")

    grow_task = asyncio.create_task(grow())
    ok = await wait_for_stable(target, quiet_s=0.2, timeout_s=2.0, poll_s=0.05)
    await grow_task
    assert ok is True
    assert target.stat().st_size == 9


@pytest.mark.asyncio
async def test_wait_for_stable_times_out_if_never_settles(tmp_path: Path) -> None:
    target = tmp_path / "save.ark"
    target.write_bytes(b"x")

    async def keep_growing() -> None:
        for i in range(20):
            await asyncio.sleep(0.05)
            target.write_bytes(b"x" * (i + 2))

    grow_task = asyncio.create_task(keep_growing())
    ok = await wait_for_stable(target, quiet_s=0.3, timeout_s=0.5, poll_s=0.05)
    grow_task.cancel()
    try:
        await grow_task
    except asyncio.CancelledError:
        pass
    assert ok is False


@pytest.mark.asyncio
async def test_wait_for_stable_missing_file_returns_false(tmp_path: Path) -> None:
    target = tmp_path / "does-not-exist.ark"
    assert (
        await wait_for_stable(target, quiet_s=0.1, timeout_s=0.3, poll_s=0.05) is False
    )
