"""Out-of-process parse target.

Runs in a short-lived ``multiprocessing`` child so the multi-GB arkparser
object graph is reclaimed by the OS the moment the child exits, keeping the
long-lived server process lean. This module is intentionally tiny and imports
``arkparser`` lazily (inside :func:`run_export`) so that merely importing it --
which the parent does, and which the spawn bootstrap does when unpickling the
target -- stays cheap and side-effect free.
"""

import json
import logging
import logging.handlers
import multiprocessing as mp
import shutil
import sys
import typing as t
from pathlib import Path

log = logging.getLogger("arkview.parser")

# Written into ``output_dir`` beside the ASV_*.json so the parent can report what
# the child skipped. The child cannot return a value (it is a Process target, not
# a Pool callable), and the parent already reads this directory back.
PARSE_STATS_FILE = "_parse_stats.json"


def attach_log_queue(log_queue: "mp.Queue[logging.LogRecord] | None") -> None:
    """Route this child's log records to the parent over ``log_queue``.

    The spawn child re-imports ``main`` as ``__mp_main__``, so its ``__main__``
    guard never fires and ``init_logging`` never runs here -- by design, since a
    second RotatingFileHandler on the same logs.log would fight the parent over
    rotation. Without this the child's records fall through to
    ``logging.lastResort`` (stderr, WARNING+) and never reach the log file, which
    is how a parse could skip 12% of the playerbase in silence.

    Handing records to the parent keeps exactly one process writing the file.

    INFO, not DEBUG: arkparser logs one debug traceback per unparseable cryopod
    creature, which on a populated map is tens of thousands of records per parse
    -- enough to bury the WARNING skips this queue exists to surface, and to grow
    logs.log by megabytes a minute.
    """
    if log_queue is None:
        return
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(logging.handlers.QueueHandler(log_queue))
    root.setLevel(logging.INFO)


def lower_own_priority(priority: str) -> None:
    """Set this process to the CONFIGURED priority class on Windows.

    Best-effort: the parent also throttles via ``apply_child_limits`` once it
    has the pid, but the spawn child inherits the parent's priority and runs its
    heavy ``arkparser`` import before the parent can attach. Doing it here first
    closes that gap. This MUST honour the operator's ``Priority`` setting rather
    than hard-coding a class: a hard-coded BELOW_NORMAL was the last write and so
    silently RAISED a configured LOW (IDLE) parse one class up, defeating the
    throttle it exists to enforce on the ASE game-server hosts. Silently skips on
    any error -- throttling is an optimisation, not correctness.
    """
    if sys.platform != "win32":
        return
    try:
        import psutil

        classes = {
            "LOW": psutil.IDLE_PRIORITY_CLASS,
            "BELOWNORMAL": psutil.BELOW_NORMAL_PRIORITY_CLASS,
            "NORMAL": psutil.NORMAL_PRIORITY_CLASS,
            "ABOVENORMAL": psutil.ABOVE_NORMAL_PRIORITY_CLASS,
            "HIGH": psutil.HIGH_PRIORITY_CLASS,
        }
        psutil.Process().nice(
            classes.get(priority.upper(), psutil.BELOW_NORMAL_PRIORITY_CLASS)
        )
    except Exception as exc:  # noqa: BLE001 -- best-effort, never fatal
        log.debug("Could not set own priority to %s: %s", priority, exc)


def write_parse_stats(output_dir: str, stats: dict[str, t.Any]) -> None:
    """Write this parse's sidecar counts for the parent to pick up.

    Best-effort: losing the stats file must never fail an otherwise good parse.
    """
    try:
        path = Path(output_dir) / PARSE_STATS_FILE
        path.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 -- reporting, never fatal
        log.debug("Could not write %s: %s", PARSE_STATS_FILE, exc)


def prepare_scratch_copy(map_path: Path, output_dir: str) -> Path:
    """Copy the live save into ``output_dir``/scratch and return the copy's path.

    Windows only, and the reason this exists: parsing the live ``.ark`` keeps a
    handle (and, with lazy properties, an mmap section) open on it for the whole
    export. ARK saves by writing ``<Map>.tmp`` and renaming it over ``<Map>.ark``;
    on Windows that replace fails while any process holds the file open without
    FILE_SHARE_DELETE, and an active file mapping blocks it regardless of share
    flags. ARK neither logs nor retries the failed promote, so the world simply
    stops saving while profiles and tribes keep committing. Parsing a copy makes
    the whole class of blocked saves impossible; the copy cost is one file per
    parse cycle.

    Pre: ``map_path`` exists. Post: the returned path exists in the scratch dir
    with the source's mtime preserved (``WorldSave.load`` stamps ``file_mtime``
    from it). Any leftover scratch dir from a previous parse child is purged
    first: the previous child has exited (its handles are released), and a
    failed in-place cleanup there must not accumulate stale multi-GB copies.
    SQLite sidecars (``-wal``/``-shm``/``-journal``) are copied when present so
    an ASA save opened from the scratch dir sees a consistent database.
    """
    assert map_path.exists(), f"source save vanished: {map_path}"
    scratch = Path(output_dir) / "scratch"
    if scratch.exists():
        shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True, exist_ok=True)
    copied = Path(shutil.copy2(map_path, scratch / map_path.name))
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = map_path.with_name(map_path.name + suffix)
        if sidecar.exists():
            shutil.copy2(sidecar, scratch / sidecar.name)
    assert copied.exists(), f"scratch copy missing after copy: {copied}"
    return copied


def close_save_handles(save: object) -> None:
    """Best-effort release of a parsed save's retained file handles.

    The lazy ASE reader mmaps its source and the lazy ASA path retains a SQLite
    connection; both live for the save's lifetime and ``WorldSave`` exposes no
    close API. Releasing them here lets the scratch dir delete cleanly on
    Windows (rmtree fails on a still-mapped file). Best-effort only: the child
    process exits right after the export, which releases everything regardless,
    and the next parse purges whatever this pass could not delete.
    """
    reader = getattr(save, "_lazy_reader", None)
    if reader is not None:
        try:
            reader.close()
        except Exception as exc:  # noqa: BLE001 -- cleanup, never fatal
            log.debug("Could not close lazy reader: %s", exc)
    conn = getattr(save, "_lazy_conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception as exc:  # noqa: BLE001 -- cleanup, never fatal
            log.debug("Could not close lazy SQLite connection: %s", exc)


def read_parse_stats(output_dir: str) -> dict[str, t.Any]:
    """Read back the child's sidecar counts. Returns ``{}`` when unavailable."""
    try:
        path = Path(output_dir) / PARSE_STATS_FILE
        if not path.is_file():
            return {}
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except Exception as exc:  # noqa: BLE001 -- reporting, never fatal
        log.debug("Could not read %s: %s", PARSE_STATS_FILE, exc)
        return {}


def run_export(
    map_file: str,
    output_dir: str,
    cluster_dir: str | None,
    priority: str = "LOW",
    log_queue: "mp.Queue[logging.LogRecord] | None" = None,
) -> None:
    """Parse one save and write ``ASV_*.json`` to ``output_dir``, then return.

    The process this runs in exits immediately after, so every byte the parse
    allocated is returned to the OS. ``wrap=True`` reproduces the legacy
    ASVExport ``{map, day, time, data}`` envelope that ``load_outputs`` and the
    HTTP layer expect. ``cluster_dir`` folds cluster-uploaded tames into
    ``ASV_Tamed.json`` (matching the old ``all "<map>" "<cluster>" "<out>"``
    invocation).

    Player and tribe data live in ``.arkprofile`` / ``.arktribe`` sidecars
    alongside the map (not in the world save itself), so they are loaded from
    the map's directory and attached to ``save`` before exporting -- the same
    directory scan the C# exporter did. A single corrupt sidecar is skipped
    rather than failing the whole parse -- so the skip count is written to
    ``_parse_stats.json`` and the skips are logged over ``log_queue``, because a
    silent skip means a player vanishes from every id-matched feature.
    """
    attach_log_queue(log_queue)
    lower_own_priority(priority)

    from arkparser import WorldSave
    from arkparser.common import get_map_config

    map_path = Path(map_file)
    if not map_path.exists():
        raise FileNotFoundError(f"Map file does not exist: {map_file}")

    # On Windows, never parse the live save: holding it open (the lazy reader
    # mmaps it for the whole export) blocks ARK's tmp-then-rename save promote,
    # so the world silently stops saving. Parse a scratch copy instead. On
    # Linux a rename succeeds over open handles, so the copy is skipped.
    parse_path = map_path
    scratch_dir: Path | None = None
    if sys.platform == "win32":
        parse_path = prepare_scratch_copy(map_path, output_dir)
        scratch_dir = parse_path.parent

    # lazy_properties: property blocks parse on first access and the export
    # drivers evict them per record, so the parse child's peak RSS stays
    # bounded by the headers + one record's properties instead of the whole
    # object graph (arkparser 0.6.0; output is golden-verified identical).
    # Busy-save measurements: Fjordur PvE ASE 7.1 GB / 348 s eager ->
    # 2.5 GB / 287 s lazy; ASA TheIsland 853 MB / 38 s -> 307 MB / ~36 s.
    save = WorldSave.load(
        str(parse_path), lazy_properties=True
    )  # ASE or ASA, auto-detected
    try:
        run_export_stages(save, map_path, output_dir, cluster_dir, get_map_config)
    finally:
        if scratch_dir is not None:
            close_save_handles(save)
            shutil.rmtree(scratch_dir, ignore_errors=True)


def run_export_stages(
    save: t.Any,
    map_path: Path,
    output_dir: str,
    cluster_dir: str | None,
    get_map_config: t.Callable[[str], t.Any],
) -> None:
    """Attach sidecars to ``save`` and write the ASV_*.json exports.

    Split from :func:`run_export` so the scratch-copy lifetime (create, parse,
    release handles, delete) lives in one function and the export pipeline in
    another. Pre: ``save`` is a loaded ``WorldSave``. Post: exports are written
    to ``output_dir`` and ``_parse_stats.json`` records sidecar skip counts.

    Profiles and tribes are read from the LIVE map directory, not the scratch
    copy: ``Profile.load``/``Tribe.load`` do a full read and close the handle
    immediately, so their block window on ARK's sidecar saves is milliseconds.
    """
    from arkparser import Profile, Tribe, export_to_files

    map_config = get_map_config(map_path.name)

    map_dir = map_path.parent
    profiles: list[object] = []
    profiles_skipped: list[dict[str, str]] = []
    for p in sorted(map_dir.glob("*.arkprofile")):
        try:
            profiles.append(Profile.load(p))
        except Exception as exc:
            log.warning("Skipping profile %s: %s: %s", p.name, type(exc).__name__, exc)
            profiles_skipped.append(
                {"file": p.name, "error": f"{type(exc).__name__}: {exc}"}
            )
    save.profiles = profiles

    tribes: list[object] = []
    tribes_skipped: list[dict[str, str]] = []
    for tp in sorted(map_dir.glob("*.arktribe")):
        try:
            tribes.append(Tribe.load(tp))
        except Exception as exc:
            log.warning("Skipping tribe %s: %s: %s", tp.name, type(exc).__name__, exc)
            tribes_skipped.append(
                {"file": tp.name, "error": f"{type(exc).__name__}: {exc}"}
            )
    save.tribes = tribes

    log.info("Loaded sidecars: profiles=%d tribes=%d", len(profiles), len(tribes))
    write_parse_stats(
        output_dir,
        {
            "profiles_loaded": len(profiles),
            "profiles_skipped": len(profiles_skipped),
            "tribes_loaded": len(tribes),
            "tribes_skipped": len(tribes_skipped),
            "skipped": {
                "profiles": profiles_skipped[:50],
                "tribes": tribes_skipped[:50],
            },
        },
    )

    written = export_to_files(
        save,
        output_dir,
        map_config,
        wrap=True,
        cluster=cluster_dir or None,
    )
    log.info("Wrote %s export file(s) to %s", len(written), output_dir)
