"""Out-of-process parse target.

Runs in a short-lived ``multiprocessing`` child so the multi-GB arkparser
object graph is reclaimed by the OS the moment the child exits, keeping the
long-lived server process lean. This module is intentionally tiny and imports
``arkparser`` lazily (inside :func:`run_export`) so that merely importing it --
which the parent does, and which the spawn bootstrap does when unpickling the
target -- stays cheap and side-effect free.
"""

import logging
from pathlib import Path

log = logging.getLogger("arkview.parser")


def run_export(map_file: str, output_dir: str, cluster_dir: str | None) -> None:
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
    from arkparser import Profile, Tribe, WorldSave, export_to_files
    from arkparser.common import get_map_config

    map_path = Path(map_file)
    if not map_path.exists():
        raise FileNotFoundError(f"Map file does not exist: {map_file}")

    save = WorldSave.load(map_file)  # auto-detects ASE flat-binary vs ASA SQLite
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
