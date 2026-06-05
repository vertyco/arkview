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

# Pipe/cable runs bridge physically separate bases (live audit 2026-06-04: 17/60
# island-pve base_too_large flags existed ONLY because of irrigation/cable runs,
# e.g. 241f with pipes vs 38.9f walls-only). They count toward totals but are
# excluded from clustering and extent measurement. Water tanks/taps and tree sap
# taps are the endpoints of those same irrigation runs and create phantom build
# locations when counted (live check 2026-06-04 VAL/ISLAND PvE). Electrical runs
# (ElectricCable*_C, ElectricJunction_C, BP_Wire_Flex_C) match via "cable",
# "junction" and "flex"; generators stay counted (real base footprint). Keywords
# are deliberately specific: a bare "tap" would also match StructureTurretCatapult,
# and a bare "wire" would match C4Tripwire (fleet survey 2026-06-04: "flex"
# covers every legit wire class).
UTILITY_KEYWORDS = (
    "pipe",
    "cable",
    "flex",
    "junction",
    "outlet",
    "watertank",
    "watertap",
    "saptap",
)

# A lone sleeping bag is not a spam violation. Live audit: 199/287 island-pve
# violators were spam-only, dominated by single-structure "clusters" (sleeping
# bags, campfires, sap taps). Only flag spam when it shows a littering pattern.
SPAM_MIN_CLUSTERS = 3  # this many separate spam clusters, or
SPAM_MIN_STRUCTURES = 15  # this many total structures across spam clusters

# Staff tribes are not subject to player base rules (lowercase exact names).
# Overridable per request via the endpoint's `exempt` query param.
EXEMPT_TRIBES = frozenset({"server admin", "server staff"})


def is_utility(struct_class: str) -> bool:
    """True for utility-run structures that connect bases without being base footprint.

    Pre: struct_class is a str (possibly empty). Post: bool.
    """
    assert isinstance(struct_class, str), "struct_class must be a string"
    low = struct_class.lower()
    return any(k in low for k in UTILITY_KEYWORDS)


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
    """Label 2D points into clusters via exact single-linkage within gap_uu.

    Two points share a label when they sit within gap_uu of each other,
    directly or through a chain of points. Grid buckets (cell side = gap_uu)
    bound the search: two points within gap_uu can never be more than one cell
    apart on either axis, so only the 3x3 neighborhood is scanned, with an
    exact euclidean check per candidate. This replaces the earlier blind
    cell-union approach, which merged separate bases up to ~3*gap_uu apart
    (live audit 2026-06-04: 18/60 island-pve base_too_large flags were pure
    grid artifacts, e.g. 169.3f reported vs 57.5f actual).

    Pre: gap_uu > 0. Post: returns one int label per input point.
    """
    assert gap_uu > 0, "gap_uu must be positive"
    if not points:
        return []
    cells: dict[tuple[int, int], list[int]] = {}
    for i, (x, y) in enumerate(points):
        cells.setdefault((int(x // gap_uu), int(y // gap_uu)), []).append(i)

    labels = [-1] * len(points)
    gap_sq = gap_uu * gap_uu
    for start in range(len(points)):
        if labels[start] != -1:
            continue
        # Flood-fill one connected component. Each point enters the stack at
        # most once (its label is set before pushing), so the loop is bounded
        # by the number of points.
        labels[start] = start
        stack = [start]
        while stack:
            i = stack.pop()
            xi, yi = points[i]
            ci, cj = int(xi // gap_uu), int(yi // gap_uu)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for j in cells.get((ci + dx, cj + dy), ()):
                        if labels[j] != -1:
                            continue
                        xj, yj = points[j]
                        if (xi - xj) ** 2 + (yi - yj) ** 2 <= gap_sq:
                            labels[j] = start
                            stack.append(j)

    assert len(labels) == len(points), "one label per point required"
    assert all(label >= 0 for label in labels), "every point must be labeled"
    return labels


# A location is grouped loosely (gap, default 20 foundations) but SIZED on its
# largest contiguous building: structures linked within this many foundations.
# Live audit 2026-06-05: a stray trough / spike-wall line / lamp post inside the
# 20f gap was inflating bounding boxes far past the wiki limit (e.g. a compliant
# 69x70f build reported as 103x87f), causing constant "my base isn't that big"
# disputes. Core sizing cut base_too_large flags ~85% across VAL/ISLAND/RAG PvE
# while every surviving flag was a genuinely contiguous 100f+ build.
CORE_GAP_FOUNDATIONS = 5.0


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
    # Pick a real structure nearest the bounding-box center to represent the
    # base. Using an actual structure (not the abstract midpoint) means the
    # reported lat/lon/ccc is somewhere staff can teleport to and find walls.
    cx = (min(xs) + max(xs)) / 2
    cy = (min(ys) + max(ys)) / 2
    mid = min(
        range(len(coords)),
        key=lambda i: (coords[i][0] - cx) ** 2 + (coords[i][1] - cy) ** 2,
    )
    # Size the location on its largest contiguous building (core), not the
    # loose 20f-grouped bounding box: detached troughs/spike lines/lamp posts
    # stay part of the location without inflating its measured extent.
    core_labels = cluster_points(
        [(c[0], c[1]) for c in coords], CORE_GAP_FOUNDATIONS * foundation_uu
    )
    core_groups: dict[int, list[int]] = {}
    for i, label in enumerate(core_labels):
        core_groups.setdefault(label, []).append(i)
    core = max(core_groups.values(), key=len)
    core_xs = [coords[i][0] for i in core]
    core_ys = [coords[i][1] for i in core]
    core_zs = [coords[i][2] for i in core]
    return {
        # Tiny clusters are "spam" (stray pens, lone foundations); everything
        # else is left blank here and ranked main/outpost/extra later, once
        # all of the tribe's clusters are known (classify_and_judge).
        "classification": "spam" if len(cluster) < spam_threshold else "",
        "structure_count": len(cluster),
        "center_lat": cluster[mid].get("lat"),
        "center_lon": cluster[mid].get("lon"),
        "center_ccc": cluster[mid].get("ccc", ""),
        # Axis-aligned bounding box of the core building converted from UU to
        # foundations. This is how staff measure today (gate to gate), so 80
        # here means the wiki's "80 foundations" rule on the connected build.
        "extent_foundations": {
            "x": round((max(core_xs) - min(core_xs)) / foundation_uu, 1),
            "y": round((max(core_ys) - min(core_ys)) / foundation_uu, 1),
            "z": round((max(core_zs) - min(core_zs)) / foundation_uu, 1),
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
    # Stray small clusters are flagged for cleanup, but only when they show a
    # littering pattern: several separate clusters or a meaningful structure
    # count. A single forgotten sleeping bag is not a violation.
    spam_locs = [loc for loc in locations if loc["classification"] == "spam"]
    spam_total = sum(loc["structure_count"] for loc in spam_locs)
    if len(spam_locs) >= SPAM_MIN_CLUSTERS or spam_total >= SPAM_MIN_STRUCTURES:
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
    # Utility runs (pipes/cables) count toward the tribe's total but are kept
    # out of clustering and extent math: a water line to the ocean is not part
    # of the base footprint and chains separate bases into one giant cluster.
    solid: list[dict] = []
    utility_count = 0
    for s in tribe_structures:
        if is_utility(str(s.get("struct") or "")):
            utility_count += 1
            continue
        solid.append(s)
    # Split solid structures into locatable (valid "x y z" ccc) and
    # unlocatable. Unlocatable ones still count toward the tribe's total but
    # cannot be clustered, so they are surfaced separately as unlocated_count.
    located: list[dict] = []
    coords: list[tuple[float, float, float]] = []
    for s in solid:
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
        "unlocated_count": len(solid) - len(located),
        "utility_count": utility_count,
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
    exempt_tribes: frozenset[str] = EXEMPT_TRIBES,
) -> list[dict]:
    """Compute per-tribe base compliance facts from ASV_Structures records.

    Pre: structures is a list of legacy structure dicts. Post: one record per
    real tribe (abandoned and exempt excluded), each with locations +
    violations + members.
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
    # Exempt staff/admin tribes wholesale (lowercase name match, same field the
    # record's tribe name comes from) so they never appear as violators.
    by_tribe = {
        tribeid: tribe_structures
        for tribeid, tribe_structures in by_tribe.items()
        if str(tribe_structures[0].get("tribe") or "").lower() not in exempt_tribes
    }

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
