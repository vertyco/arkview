"""Base-compliance computation for PvE structure rules.

Pure functions over ASV_Structures records (legacy dict schema). No FastAPI
imports here; common/tasks.py exposes this over HTTP.

How the computation works, end to end:

1. Group every player-owned structure by tribeid (compute_compliance).
2. For each tribe, parse each structure's raw Unreal-units position from its
   legacy "x y z" `ccc` string (parse_ccc), then group nearby structures into
   "locations" (bases) with grid-based union-find clustering (cluster_points).
   Two structures belong to the same base when they sit within roughly `gap`
   foundations of each other, directly or through a chain of structures.
3. Summarize each cluster into facts: structure count, bounding-box extent in
   foundations, and a representative center structure (build_location).
4. Classify clusters and derive violations against wiki rules 4.1/4.2
   (classify_and_judge): the biggest cluster is the "main" base, the second
   biggest is the "outpost", anything further is "extra" (not allowed), and
   clusters below `spam_threshold` structures are "spam" to clean up.
5. Attach tribe member identities from the ASV_Tribes export so consumers can
   resolve players to Discord accounts (compute_compliance).

All distance math happens in Unreal units (UU). 1 foundation = 300 UU, so the
rule "bases fit within 80 foundations" becomes a 24,000 UU bounding-box check.
"""

import typing as t

# Legacy ASVExport sentinel for unowned structures (C# int.MinValue). These are
# map decorations / abandoned debris, never a player base, so they are skipped.
ABANDONED_TRIBE_ID = -2147483648
MAX_CELL_SCAN = 2  # union cells within a 2-cell radius (see cluster_points)


def parse_ccc(ccc: str) -> t.Optional[tuple[float, float, float]]:
    """Parse a legacy 'x y z' UU coordinate string.

    Pre: ccc is a str (possibly empty). Post: 3-tuple of floats, or None.
    """
    assert isinstance(ccc, str), "ccc must be a string"
    parts = ccc.split()
    if len(parts) != 3:
        return None
    try:
        x, y, z = (float(p) for p in parts)
    except ValueError:
        return None
    assert all(isinstance(v, float) for v in (x, y, z)), "parsed coords must be floats"
    return x, y, z


def cluster_points(points: list[tuple[float, float]], gap_uu: float) -> list[int]:
    """Label 2D points into clusters via grid union-find.

    Cell size = gap_uu. Cells within MAX_CELL_SCAN Chebyshev distance are
    unioned, so any two points closer than gap_uu always share a label
    (no false splits); points up to ~3*gap_uu apart may merge (acceptable:
    bases that close are effectively one base).

    Pre: gap_uu > 0. Post: returns one int label per input point.
    """
    assert gap_uu > 0, "gap_uu must be positive"
    if not points:
        return []
    # Why a grid instead of pairwise distances: comparing every structure to
    # every other one is O(n^2) and a single megabase can hold 20k+ structures.
    # Snapping points onto a coarse grid and unioning neighboring cells gives
    # the same "things near each other belong together" answer in O(n).
    cells: dict[tuple[int, int], int] = {}
    parent: list[int] = []

    # Classic union-find (disjoint set) over grid cells. parent[i] points at
    # another set member; following the chain reaches the set's root, which
    # serves as the cluster label. find() uses path-halving so repeated lookups
    # flatten the chains.
    def find(i: int) -> int:
        assert 0 <= i < len(parent), "index out of range"
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        assert 0 <= a < len(parent), "index out of range"
        assert 0 <= b < len(parent), "index out of range"
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # Pass 1: assign each point to a square grid cell of side gap_uu. Each
    # newly seen cell becomes its own one-element set.
    point_cells: list[tuple[int, int]] = []
    for x, y in points:
        cell = (int(x // gap_uu), int(y // gap_uu))
        point_cells.append(cell)
        if cell not in cells:
            cells[cell] = len(parent)
            parent.append(len(parent))

    # Pass 2: union every occupied cell with occupied neighbors up to
    # MAX_CELL_SCAN cells away (a 5x5 window). Points closer than gap_uu can
    # land in adjacent cells at worst, so they always end up unioned: no base
    # is ever split in half. The cost is some over-merge (see docstring).
    for (cx, cy), idx in cells.items():
        for dx in range(-MAX_CELL_SCAN, MAX_CELL_SCAN + 1):
            for dy in range(-MAX_CELL_SCAN, MAX_CELL_SCAN + 1):
                neighbor = cells.get((cx + dx, cy + dy))
                if neighbor is not None and neighbor != idx:
                    union(idx, neighbor)

    # Pass 3: every point's label is the root of its cell's set. Points whose
    # cells were unioned share a root, i.e. share a base.
    labels = [find(cells[cell]) for cell in point_cells]
    assert len(labels) == len(points), "one label per point required"
    return labels


def build_location(
    cluster: list[dict],
    coords: list[tuple[float, float, float]],
    spam_threshold: int,
    foundation_uu: float,
) -> dict:
    """Summarize one structure cluster into a location record (classification set later).

    Pre: cluster and coords are non-empty and same length. Post: dict with extent/center facts.
    """
    assert cluster and len(cluster) == len(coords), "cluster/coords must align"
    assert foundation_uu > 0, "foundation_uu must be positive"
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    zs = [c[2] for c in coords]
    # Pick a real structure nearest the bounding-box center to represent the
    # base. Using an actual structure (not the abstract midpoint) means the
    # reported lat/lon/ccc is somewhere staff can teleport to and find walls.
    cx = (min(xs) + max(xs)) / 2
    cy = (min(ys) + max(ys)) / 2
    mid = min(
        range(len(coords)),
        key=lambda i: (coords[i][0] - cx) ** 2 + (coords[i][1] - cy) ** 2,
    )
    return {
        # Tiny clusters are "spam" (stray pens, lone foundations); everything
        # else is left blank here and ranked main/outpost/extra later, once
        # all of the tribe's clusters are known (classify_and_judge).
        "classification": "spam" if len(cluster) < spam_threshold else "",
        "structure_count": len(cluster),
        "center_lat": cluster[mid].get("lat"),
        "center_lon": cluster[mid].get("lon"),
        "center_ccc": cluster[mid].get("ccc", ""),
        # Axis-aligned bounding box converted from UU to foundations. This is
        # how staff measure today (gate to gate), so 80 here means the wiki's
        # "80 foundations" rule, independent of which map the save came from.
        "extent_foundations": {
            "x": round((max(xs) - min(xs)) / foundation_uu, 1),
            "y": round((max(ys) - min(ys)) / foundation_uu, 1),
            "z": round((max(zs) - min(zs)) / foundation_uu, 1),
        },
    }


def classify_and_judge(
    locations: list[dict], max_extent: float, outpost_max: int
) -> list[str]:
    """Assign main/outpost/extra to non-spam locations (descending size) and return violations.

    Pre: every location dict has classification '' or 'spam'. Post: all classifications set.
    """
    assert all(
        loc["classification"] in ("", "spam") for loc in locations
    ), "pre-set classes invalid"
    assert max_extent > 0 and outpost_max > 0, "limits must be positive"
    # Rank real (non-spam) clusters by size: biggest = "main" base, second =
    # "outpost", any further cluster = "extra". Rule 4.1 allows exactly one
    # main and one outpost, so the existence of any "extra" is itself the
    # too_many_locations violation.
    real = [loc for loc in locations if loc["classification"] != "spam"]
    real.sort(key=lambda loc: loc["structure_count"], reverse=True)
    violations: list[str] = []
    for rank, loc in enumerate(real):
        loc["classification"] = (
            "main" if rank == 0 else "outpost" if rank == 1 else "extra"
        )
    if any(loc["classification"] == "extra" for loc in real):
        violations.append("too_many_locations")
    # Rule 4.2: every base must fit within max_extent foundations. Only the
    # horizontal axes count (z/height ignored); one violation string is enough
    # even if several locations are oversized, hence the break.
    for loc in real:
        ext = loc["extent_foundations"]
        if max(ext["x"], ext["y"]) > max_extent:
            violations.append("base_too_large")
            break
    # Rule 4.1: the outpost must stay under outpost_max structures (the main
    # base has no count cap, only the size cap above).
    outposts = [loc for loc in real if loc["classification"] == "outpost"]
    if outposts and outposts[0]["structure_count"] >= outpost_max:
        violations.append("outpost_too_big")
    # Any stray small cluster is flagged for cleanup; it does not count as a
    # base location but it is still against the rules to leave it around.
    if any(loc["classification"] == "spam" for loc in locations):
        violations.append("spam_present")
    return violations


def build_tribe_record(
    tribeid: int,
    tribe_structures: list[dict],
    gap_uu: float,
    max_extent: float,
    outpost_max: int,
    spam_threshold: int,
    foundation_uu: float,
    members: list[dict],
) -> dict:
    """Build one tribe's compliance record from its raw structure list.

    Pre: tribe_structures is non-empty, gap_uu > 0. Post: complete record dict.
    """
    assert tribe_structures, "tribe_structures must be non-empty"
    assert gap_uu > 0, "gap_uu must be positive"
    # Split structures into locatable (valid "x y z" ccc) and unlocatable.
    # Unlocatable ones still count toward the tribe's total but cannot be
    # clustered, so they are surfaced separately as unlocated_count.
    located: list[dict] = []
    coords: list[tuple[float, float, float]] = []
    for s in tribe_structures:
        parsed = parse_ccc(s.get("ccc", "") or "")
        if parsed is None:
            continue
        located.append(s)
        coords.append(parsed)
    # Cluster on the horizontal plane only (x, y); a tall tower is one base.
    labels = cluster_points([(c[0], c[1]) for c in coords], gap_uu=gap_uu)
    # Invert labels into groups: cluster label -> indices of its structures.
    groups: dict[int, list[int]] = {}
    for i, label in enumerate(labels):
        groups.setdefault(label, []).append(i)
    locations = [
        build_location(
            [located[i] for i in idxs],
            [coords[i] for i in idxs],
            spam_threshold,
            foundation_uu,
        )
        for idxs in groups.values()
    ]
    violations = classify_and_judge(locations, max_extent, outpost_max)
    locations.sort(key=lambda loc: loc["structure_count"], reverse=True)
    # tribe name taken from first record's field; stale renames are possible
    tribe_name = str(tribe_structures[0].get("tribe") or "")
    return {
        "tribeid": tribeid,
        "tribe": tribe_name,
        "total_structures": len(tribe_structures),
        "unlocated_count": len(tribe_structures) - len(located),
        "locations": locations,
        "violations": violations,
        "members": members,
    }


def compute_compliance(
    structures: list[dict],
    tribes: t.Optional[list[dict]] = None,
    *,
    max_extent: float = 80.0,
    outpost_max: int = 300,
    spam_threshold: int = 10,
    gap: float = 20.0,
    foundation_uu: float = 300.0,
) -> list[dict]:
    """Compute per-tribe base compliance facts from ASV_Structures records.

    Pre: structures is a list of legacy structure dicts. Post: one record per
    real tribe (abandoned excluded), each with locations + violations + members.
    """
    assert isinstance(structures, list), "structures must be a list"
    assert (
        min(max_extent, float(outpost_max), float(spam_threshold), gap, foundation_uu)
        > 0
    ), "params must be positive"
    # Group structures by owning tribe, dropping the synthetic "abandoned"
    # tribe (unowned debris that can never violate base rules).
    by_tribe: dict[int, list[dict]] = {}
    for s in structures:
        tribeid = int(s.get("tribeid", ABANDONED_TRIBE_ID))
        if tribeid == ABANDONED_TRIBE_ID:
            continue
        by_tribe.setdefault(tribeid, []).append(s)

    # Index tribe members from the ASV_Tribes export (sourced from .arktribe
    # sidecar files) so each record carries who to contact. Consumers map the
    # steamid/gameid to Discord accounts for pings.
    member_map: dict[int, list[dict]] = {}
    for tribe in tribes or []:
        members = [
            {
                "steamid": str(m.get("steamid", "")),
                "playername": str(m.get("playername", "")),
            }
            for m in tribe.get("members", [])
        ]
        member_map[int(tribe.get("tribeid", 0))] = members

    # Rules are written in foundations; clustering runs in raw Unreal units.
    gap_uu = gap * foundation_uu
    results = [
        build_tribe_record(
            tribeid,
            tribe_structures,
            gap_uu,
            max_extent,
            outpost_max,
            spam_threshold,
            foundation_uu,
            member_map.get(tribeid, []),
        )
        for tribeid, tribe_structures in by_tribe.items()
    ]
    # Violators first, then biggest tribes first, so consumers can render the
    # worst offenders without re-sorting.
    results.sort(key=lambda r: (len(r["violations"]) == 0, -r["total_structures"]))
    assert len(results) <= len(by_tribe), "no synthetic tribes may appear"
    return results
