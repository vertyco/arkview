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

    from arkparser import Profile, Tribe, WorldSave, export_to_files
    from arkparser.common import get_map_config

    map_path = Path(map_file)
    if not map_path.exists():
        raise FileNotFoundError(f"Map file does not exist: {map_file}")

    # lazy_properties: property blocks parse on first access and the export
    # drivers evict them per record, so the parse child's peak RSS stays
    # bounded by the headers + one record's properties instead of the whole
    # object graph (arkparser 0.6.0; output is golden-verified identical).
    # Busy-save measurements: Fjordur PvE ASE 7.1 GB / 348 s eager ->
    # 2.5 GB / 287 s lazy; ASA TheIsland 853 MB / 38 s -> 307 MB / ~36 s.
    save = WorldSave.load(map_file, lazy_properties=True)  # ASE or ASA, auto-detected
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
