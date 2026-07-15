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
4. Classify clusters and derive violations against wiki rules 4.1 (location
   count only; the "only one location can be in a cave" clause of 4.1 is NOT
   checked, as cave detection needs per-map volume data absent from the export)
   and 4.2 (classify_and_judge): the biggest real cluster is the "main" base, the
   second biggest is the "outpost", anything further is "extra" (not allowed).
   A cluster is "spam" (cleanup litter) by its CONTENTS, not its size: only when
   it is made entirely of loose land-claim pieces (pillars, lone foundations,
   gate/door frames, signs, sleeping bags) with no real building piece or value
   structure. A small real base (e.g. a compact greenhouse) is never spam.
5. Attach tribe member identities from the ASV_Tribes export so consumers can
   resolve players to Discord accounts (compute_compliance).

All distance math happens in Unreal units (UU). 1 foundation = 300 UU, so the
rule "bases fit within 80 foundations" becomes a 24,000 UU bounding-box check.
"""

import datetime
import math
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

# Inverted spam test. A cluster is "spam" (cleanup litter) only when EVERY member
# is loose land-claim litter. Enumerating the small, stable litter set and treating
# everything else as real is robust to ARK adding classes (the old value/build
# keyword lists left ~4% of live classes UNKNOWN -> wrongly spam). Substring,
# case-insensitive. "gate" covers Gate_/GateFrame_/DinoGate/Behemoth gates.
LITTER_KEYWORDS = (
    "pillar",
    "foundation",
    "fence",
    "gate",
    "doorframe",
    "hatchframe",
    "sign",
    "billboard",
    "sleepingbag",
    "campfire",
    "beartrap",
    "flag",
)


def is_litter(struct_class: str) -> bool:
    """True for loose land-claim litter classes. Pre: str. Post: bool."""
    assert isinstance(struct_class, str), "struct_class must be a string"
    return any(k in struct_class.lower() for k in LITTER_KEYWORDS)


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


# Value-importance tiers. T0 = no value (litter/shell). Higher = more costly/
# irreplaceable -> more protection, higher lever to auto-wipe. A cluster's tier is
# its highest-tier member. Matching checks T3 first. Starter keyword sets; the full
# class->tier table is generated from the live class universe at implementation.
T3_KEYWORDS = (  # critical: tek / industrial / defensive / breeding / irreplaceable
    "tek",
    "industrial",
    "generator",
    "electric",
    "turret",
    "plant_species_x",
    "plantspeciesx",
    "incubator",
    "cryofridge",
    "cryo",
    "oilpump",
    "gascollector",
    "fishbasket",
    "beehive",
    "teleporter",
    "transmitter",
    "replicator",
    "vacuum",
)
T2_KEYWORDS = (  # standard core base infra
    "chembench",
    "smithy",
    "fabricator",
    "forge",
    "storagebox_large",
    "storagebox_huge",
    "vault",
    "preservingbin",
    "refrig",
    "fridge",
    "icebox",
    "feedingtrough",
    "trough",
    "grill",
    "compost",
    "beerbarrel",
    "loom",
    "greenhouse",
    "dedicated",
)
T1_KEYWORDS = ("mortar", "cookingpot", "torch", "storagebox", "bed")  # trivial


def structure_tier(struct_class: str) -> int:
    """Value-importance tier 0-3 for one structure class. Pre: str. Post: int 0-3."""
    assert isinstance(struct_class, str), "struct_class must be a string"
    low = struct_class.lower()
    if any(k in low for k in T3_KEYWORDS):
        return 3
    if any(k in low for k in T2_KEYWORDS):
        return 2
    if any(k in low for k in T1_KEYWORDS):
        return 1
    return 0


def cluster_tier(cluster: list[dict]) -> int:
    """Highest member tier in the cluster. Pre: list. Post: int 0-3."""
    assert isinstance(cluster, list), "cluster must be a list"
    return max((structure_tier(str(s.get("struct") or "")) for s in cluster), default=0)


def has_real_structure(cluster: list[dict]) -> bool:
    """True if the cluster holds any real structure (anything not loose land-claim
    litter), which makes it a real base, never "spam". A cluster that is entirely
    litter returns False.

    Litter shape wins over value tier: a TekPillar / TekFenceFoundation / TekGate
    is tier 3 (the bare "tek" substring), but it is still a land-claim shell, not a
    base. Letting tier override is_litter here made a pure tek-pillar spam cluster
    rank as a real outpost/extra and fabricated a too_many_locations flag for a
    one-base tribe (a stone-pillar cluster in the same layout correctly folds into
    spam), so the tier override was removed.

    Pre: cluster is a list of structure dicts. Post: bool.
    """
    assert isinstance(cluster, list), "cluster must be a list"
    for s in cluster:
        if not is_litter(str(s.get("struct") or "")):
            return True
    return False


# A tribe's main base always counts as a built area no matter how small, but an
# ADDITIONAL cluster only counts as an outpost/extra (rule 4.1) when it is a
# genuine built area, not a stray deployable. Live audit 2026-06-16 (vainne,
# VAL-PvE): single WaterWell / OilPump / SleepingBag / lone Gate / single floor
# tile clusters were ranked outpost/extra and triggering false too_many_locations
# on tribes that have exactly one real base. A real build is either substantial
# by count or shows enclosure (>=2 walls on a floor/foundation/ceiling).
BUILT_AREA_MIN_STRUCTURES = 8


def is_built_area(cluster: list[dict]) -> bool:
    """True if the cluster is a genuine built base, not stray litter/deployables.

    Two guards:
    - enclosure: >=2 real walls on a floor/foundation/ceiling (a small real base); or
    - substantial: >=BUILT_AREA_MIN_STRUCTURES NON-LITTER pieces. Counting only
      non-litter pieces (not raw len) stops a litter-heavy cluster from ranking as
      an outpost: an 8+ piece taming trap that is mostly gate/gateframe/foundation
      land-claim litter plus a few ramps has too few non-litter pieces to qualify,
      so it can no longer fabricate too_many_locations for a one-base tribe. A real
      deployable outpost (storage boxes, vaults, troughs) still qualifies.

    "wall" excludes spike walls and wall torches: SpikeWallWood_C / WallTorch_C
    are perimeter/decor deployables, not enclosure, and counting them let a
    3-piece spike trap read as a real base.

    Pre: cluster is a list of structure dicts. Post: bool.
    """
    assert isinstance(cluster, list), "cluster must be a list"
    walls = 0
    floors = 0
    real = 0
    for s in cluster:
        cls = str(s.get("struct") or "")
        if not is_litter(cls):
            real += 1
        low = cls.lower()
        if "wall" in low and "spike" not in low and "torch" not in low:
            walls += 1
        elif "foundation" in low or "ceiling" in low or "floor" in low:
            floors += 1
    if walls >= 2 and floors >= 1:
        return True
    return real >= BUILT_AREA_MIN_STRUCTURES


# Decay. PvE structure decay is ENABLED on the fleet (Game.ini: no
# bDisableStructureDecayPVE, no PvEStructureDecayPeriodMultiplier -> default 1.0,
# confirmed on center-PVE .114). A structure is demolishable when its idle age
# (now - last_ally_in_range) exceeds its material decay period. The game does NOT
# auto-remove supported bases (115-day ghost bases still stand), so "demoable" is
# the cleanup trigger. VERIFY these day-values in-game before relying on L2+; L1
# (litter) is decay-independent and does not need them.
DECAY_PERIODS_DAYS = {
    "thatch": 4.0,
    "wood": 8.0,
    "adobe": 8.0,
    "stone": 12.0,
    "metal": 16.0,
    "greenhouse": 16.0,
    "tek": 20.0,
}
DEFAULT_DECAY_DAYS = 8.0  # unknown material -> wood-equivalent
PVE_DECAY_MULTIPLIER = 1.0  # confirmed default; override if a box changes it


def material_of(struct_class: str) -> str:
    """Material keyword from a class name, or '' if none. Pre: str. Post: str."""
    assert isinstance(struct_class, str), "struct_class must be a string"
    low = struct_class.lower()
    for mat in ("tek", "metal", "greenhouse", "stone", "adobe", "thatch", "wood"):
        if mat in low:
            return mat
    return ""


def decay_period_days(
    struct_class: str, multiplier: float = PVE_DECAY_MULTIPLIER
) -> float:
    """Decay period in days for a class. Pre: str. Post: float > 0."""
    return (
        DECAY_PERIODS_DAYS.get(material_of(struct_class), DEFAULT_DECAY_DAYS)
        * multiplier
    )


def structure_age_days(s: dict, now: datetime.datetime) -> t.Optional[float]:
    """Days since last_ally_in_range, or None if unparseable. Pre: dict, aware now."""
    raw = s.get("last_ally_in_range") or ""
    try:
        dt = datetime.datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return (now - dt).total_seconds() / 86400.0


def cluster_demoable(
    cluster: list[dict],
    now: datetime.datetime,
    multiplier: float = PVE_DECAY_MULTIPLIER,
) -> bool:
    """True when the cluster's freshest piece is idle past its toughest material's
    decay period (so the whole cluster is demolishable). Pre: list, aware now."""
    assert isinstance(cluster, list), "cluster must be a list"
    ages = [a for a in (structure_age_days(s, now) for s in cluster) if a is not None]
    if not ages:
        return False
    toughest = max(
        decay_period_days(str(s.get("struct") or ""), multiplier) for s in cluster
    )
    return min(ages) > toughest


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
    # float() accepts 'nan'/'inf'; a non-finite coord would make cluster_points'
    # int(x // gap_uu) raise ValueError and 500 the whole endpoint. Treat it as
    # unparseable so the record is counted via unlocated_count, not clustered.
    if not all(math.isfinite(v) for v in (x, y, z)):
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
    foundation_uu: float,
    now: datetime.datetime,
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
    # Size the location on a contiguous building (core), not the loose
    # 20f-grouped bounding box: detached troughs/spike lines/lamp posts stay part
    # of the location without inflating its measured extent.
    core_labels = cluster_points(
        [(c[0], c[1]) for c in coords], CORE_GAP_FOUNDATIONS * foundation_uu
    )
    core_groups: dict[int, list[int]] = {}
    for i, label in enumerate(core_labels):
        core_groups.setdefault(label, []).append(i)

    # Measure the core with the LARGEST horizontal extent, not the one with the
    # most members. A sparse-but-oversized contiguous build (a 120f wall line,
    # 41 pieces) sitting next to a denser blob (a 100-piece 9x9f room, or a
    # 150-piece trough pen) must be measured against the 80f rule; picking the
    # most-populous core hid such builds and produced base_too_large false
    # negatives. Horizontal span (max of x,y) is what rule 4.2 caps.
    def _span(idxs: list[int]) -> float:
        gxs = [coords[i][0] for i in idxs]
        gys = [coords[i][1] for i in idxs]
        return max(max(gxs) - min(gxs), max(gys) - min(gys))

    core = max(core_groups.values(), key=_span)
    core_xs = [coords[i][0] for i in core]
    core_ys = [coords[i][1] for i in core]
    core_zs = [coords[i][2] for i in core]
    return {
        # A cluster is "spam" only when it is entirely loose land-claim litter
        # (no value structure, no real building piece); see has_real_structure.
        # Real clusters are left blank here and ranked main/outpost/extra later,
        # once all of the tribe's clusters are known (classify_and_judge).
        "classification": "" if has_real_structure(cluster) else "spam",
        # Whether this cluster is a genuine built base. Used by classify_and_judge:
        # the main base ignores this, but a non-main cluster must be a built area
        # to rank as outpost/extra (else it is stray litter, folded into spam).
        "built_area": is_built_area(cluster),
        "structure_count": len(cluster),
        "tier": cluster_tier(cluster),
        "demoable": cluster_demoable(cluster, now),
        "center_lat": cluster[mid].get("lat"),
        "center_lon": cluster[mid].get("lon"),
        "center_ccc": cluster[mid].get("ccc", ""),
        # Axis-aligned bounding box of the core building converted from UU to
        # foundations. This is how staff measure today (gate to gate), so 80
        # here means the wiki's "80 foundations" rule on the connected build.
        # Floored to whole foundations (// on positive extents): players only
        # ever see the integer, so an 80.4-foundation build must READ as 80 and
        # be treated as 80 by the limit check, or "limit 80" disputes never end.
        "extent_foundations": {
            "x": (max(core_xs) - min(core_xs)) // foundation_uu,
            "y": (max(core_ys) - min(core_ys)) // foundation_uu,
            "z": (max(core_zs) - min(core_zs)) // foundation_uu,
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
    # too_many_locations violation. Ties in count break by physical extent so a
    # bigger-footprint base is never demoted below a denser small one.
    real = [loc for loc in locations if loc["classification"] != "spam"]
    real.sort(
        key=lambda loc: (
            loc["structure_count"],
            max(loc["extent_foundations"]["x"], loc["extent_foundations"]["y"]),
        ),
        reverse=True,
    )
    # The largest cluster is always the tribe's main base (never penalize a tribe
    # for its single home, however small). Additional clusters only count as
    # outpost/extra when they are genuine built areas; an incidental real
    # structure that is not a full base (a lone well/pump/vault/greenhouse) is
    # classed "minor": neither a rule-4.1 location nor cleanup litter, so it can
    # neither fabricate a too_many_locations flag nor be measured for size. Pure
    # land-claim litter was already classed "spam" upstream and never reaches here.
    bases = [loc for i, loc in enumerate(real) if i == 0 or loc["built_area"]]
    base_ids = {id(loc) for loc in bases}
    for loc in real:
        if id(loc) not in base_ids:
            loc["classification"] = "minor"
    violations: list[str] = []
    for rank, loc in enumerate(bases):
        loc["classification"] = (
            "main" if rank == 0 else "outpost" if rank == 1 else "extra"
        )
    if any(loc["classification"] == "extra" for loc in bases):
        violations.append("too_many_locations")
    # Rule 4.2: every base must fit within max_extent foundations. Only the
    # horizontal axes count (z/height ignored); one violation string is enough
    # even if several locations are oversized, hence the break. Only ranked bases
    # are measured; demoted litter is irrelevant to the size rule.
    for loc in bases:
        ext = loc["extent_foundations"]
        if max(ext["x"], ext["y"]) > max_extent:
            violations.append("base_too_large")
            break
    # Rule 4.1: the outpost may not EXCEED outpost_max structures (the cap is
    # allowed: exactly outpost_max is compliant, only outpost_max+1 flags). The
    # main base has no count cap, only the size cap above.
    outposts = [loc for loc in real if loc["classification"] == "outpost"]
    if outposts and outposts[0]["structure_count"] > outpost_max:
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
    foundation_uu: float,
    members: list[dict],
    now: datetime.datetime,
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
            foundation_uu,
            now,
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
        "inactive_days": min(
            (
                a
                for a in (structure_age_days(s, now) for s in tribe_structures)
                if a is not None
            ),
            default=0.0,
        ),
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
    spam_threshold: int = 10,  # deprecated: spam is now content-based (has_real_structure); kept for API compat
    gap: float = 20.0,
    foundation_uu: float = 300.0,
    exempt_tribes: frozenset[str] = EXEMPT_TRIBES,
    now: t.Optional[datetime.datetime] = None,
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
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
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
            foundation_uu,
            member_map.get(tribeid, []),
            now,
        )
        for tribeid, tribe_structures in by_tribe.items()
    ]
    # Violators first, then biggest tribes first, so consumers can render the
    # worst offenders without re-sorting.
    results.sort(key=lambda r: (len(r["violations"]) == 0, -r["total_structures"]))
    assert len(results) <= len(by_tribe), "no synthetic tribes may appear"
    return results
