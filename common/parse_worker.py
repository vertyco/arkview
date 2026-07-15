"""Out-of-process parse target.

Runs in a short-lived ``multiprocessing`` child so the multi-GB arkparser
object graph is reclaimed by the OS the moment the child exits, keeping the
long-lived server process lean. This module is intentionally tiny and imports
``arkparser`` lazily (inside :func:`run_export`) so that merely importing it --
which the parent does, and which the spawn bootstrap does when unpickling the
target -- stays cheap and side-effect free.
"""

import logging
import sys
from pathlib import Path

log = logging.getLogger("arkview.parser")


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


def run_export(
    map_file: str, output_dir: str, cluster_dir: str | None, priority: str = "LOW"
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
    rather than failing the whole parse.
    """
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
    for p in sorted(map_dir.glob("*.arkprofile")):
        try:
            profiles.append(Profile.load(p))
        except Exception as exc:
            log.warning("Skipping profile %s: %s: %s", p.name, type(exc).__name__, exc)
    save.profiles = profiles

    tribes: list[object] = []
    for tp in sorted(map_dir.glob("*.arktribe")):
        try:
            tribes.append(Tribe.load(tp))
        except Exception as exc:
            log.warning("Skipping tribe %s: %s: %s", tp.name, type(exc).__name__, exc)
    save.tribes = tribes

    log.info("Loaded sidecars: profiles=%d tribes=%d", len(profiles), len(tribes))

    written = export_to_files(
        save,
        output_dir,
        map_config,
        wrap=True,
        cluster=cluster_dir or None,
    )
    log.info("Wrote %s export file(s) to %s", len(written), output_dir)
