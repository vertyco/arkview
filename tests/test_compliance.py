import datetime

from common.compliance import (
    ABANDONED_TRIBE_ID,
    cluster_demoable,
    cluster_points,
    cluster_tier,
    compute_compliance,
    decay_period_days,
    has_real_structure,
    is_built_area,
    is_litter,
    material_of,
    parse_ccc,
    structure_age_days,
    structure_tier,
)


def test_parse_ccc_valid():
    assert parse_ccc("100.5 -200 30") == (100.5, -200.0, 30.0)


def test_parse_ccc_invalid():
    assert parse_ccc("") is None
    assert parse_ccc("1 2") is None
    assert parse_ccc("a b c") is None


def test_cluster_points_two_groups():
    # gap = 6000 UU (20 foundations). Two groups 60000 UU apart -> 2 clusters.
    points = [(0.0, 0.0), (3000.0, 0.0), (60000.0, 0.0), (63000.0, 0.0)]
    labels = cluster_points(points, gap_uu=6000.0)
    assert labels[0] == labels[1]
    assert labels[2] == labels[3]
    assert labels[0] != labels[2]


def test_cluster_points_chain_merges():
    # Points spaced 3000 UU apart chain into one cluster even over a long span.
    points = [(float(i * 3000), 0.0) for i in range(30)]
    labels = cluster_points(points, gap_uu=6000.0)
    assert len(set(labels)) == 1


def test_cluster_points_empty():
    assert cluster_points([], gap_uu=6000.0) == []


FOUNDATION = 300.0  # UU per foundation


def make_structure(tribeid: int, x: float, y: float, tribe: str = "TestTribe") -> dict:
    return {
        "tribeid": tribeid,
        "tribe": tribe,
        "struct": "StorageBox_Large_C",
        "lat": 50.0,
        "lon": 50.0,
        "ccc": f"{x} {y} 100",
    }


def block(
    tribeid: int, count: int, origin_x: float, span_foundations: float = 10.0
) -> list[dict]:
    """count structures evenly spread over span_foundations, starting at origin_x."""
    out = []
    for i in range(count):
        x = origin_x + (i % 10) * (span_foundations / 10.0) * FOUNDATION
        y = (i // 10) * FOUNDATION
        out.append(make_structure(tribeid, x, y))
    return out


def litter_block(tribeid: int, count: int, origin_x: float) -> list[dict]:
    """A cluster of loose land-claim litter (stone pillars). Under content-based
    classification this is the only kind of cluster that is 'spam'."""
    out = block(tribeid, count, origin_x)
    for s in out:
        s["struct"] = "Pillar_Stone_C"
    return out


def make_struct_class(tribeid: int, x: float, cls: str) -> dict:
    s = make_structure(tribeid, x, 0.0)
    s["struct"] = cls
    return s


def test_single_compliant_base():
    structures = block(1000, count=50, origin_x=0.0)
    result = compute_compliance(structures)
    assert len(result) == 1
    tribe = result[0]
    assert tribe["tribeid"] == 1000
    assert tribe["total_structures"] == 50
    assert len(tribe["locations"]) == 1
    assert tribe["locations"][0]["classification"] == "main"
    assert tribe["violations"] == []


def test_main_plus_outpost_ok():
    structures = block(1000, 50, origin_x=0.0) + block(1000, 20, origin_x=200_000.0)
    result = compute_compliance(structures)
    classes = sorted(loc["classification"] for loc in result[0]["locations"])
    assert classes == ["main", "outpost"]
    assert result[0]["violations"] == []


def test_too_many_locations():
    structures = (
        block(1000, 50, origin_x=0.0)
        + block(1000, 30, origin_x=200_000.0)
        + block(1000, 20, origin_x=400_000.0)
    )
    result = compute_compliance(structures)
    classes = [loc["classification"] for loc in result[0]["locations"]]
    assert "extra" in classes
    assert "too_many_locations" in result[0]["violations"]


def test_outpost_too_big():
    structures = block(1000, 500, origin_x=0.0) + block(1000, 350, origin_x=200_000.0)
    result = compute_compliance(structures, outpost_max=300)
    assert "outpost_too_big" in result[0]["violations"]


def test_base_too_large():
    # contiguous run of structures spanning 100 foundations (4f spacing keeps
    # it one core building): extent > 80 -> violation
    structures = [
        make_structure(1000, x=i * 4 * FOUNDATION, y=0.0) for i in range(26)
    ] + block(1000, 20, origin_x=0.0)
    result = compute_compliance(structures, max_extent=80.0)
    assert "base_too_large" in result[0]["violations"]


def test_spam_flagged_when_pattern():
    # 3 separate loose-litter clusters = littering pattern -> violation
    structures = (
        block(1000, 50, origin_x=0.0)
        + litter_block(1000, 3, origin_x=200_000.0)
        + litter_block(1000, 3, origin_x=400_000.0)
        + litter_block(1000, 3, origin_x=600_000.0)
    )
    result = compute_compliance(structures)
    classes = [loc["classification"] for loc in result[0]["locations"]]
    assert "spam" in classes
    assert "spam_present" in result[0]["violations"]
    assert "too_many_locations" not in result[0]["violations"]


def test_single_small_spam_cluster_not_flagged():
    # One stray 3-piece litter cluster is shown as spam but is not a violation
    structures = block(1000, 50, origin_x=0.0) + litter_block(
        1000, 3, origin_x=200_000.0
    )
    result = compute_compliance(structures)
    classes = [loc["classification"] for loc in result[0]["locations"]]
    assert "spam" in classes
    assert "spam_present" not in result[0]["violations"]


def test_spam_flagged_by_structure_total():
    # Two litter clusters but 18 total litter structures (>= 15) -> violation
    structures = (
        block(1000, 50, origin_x=0.0)
        + litter_block(1000, 9, origin_x=200_000.0)
        + litter_block(1000, 9, origin_x=400_000.0)
    )
    result = compute_compliance(structures)
    assert "spam_present" in result[0]["violations"]


def test_abandoned_tribe_excluded():
    structures = block(ABANDONED_TRIBE_ID, 50, origin_x=0.0)
    assert compute_compliance(structures) == []


def test_unlocated_counted():
    structures = block(1000, 20, origin_x=0.0)
    structures.append(
        {
            "tribeid": 1000,
            "tribe": "TestTribe",
            "struct": "X_C",
            "lat": 0,
            "lon": 0,
            "ccc": "",
        }
    )
    result = compute_compliance(structures)
    assert result[0]["unlocated_count"] == 1
    assert result[0]["total_structures"] == 21


def test_members_enriched_from_tribes():
    structures = block(1000, 50, origin_x=0.0)
    tribes = [
        {"tribeid": 1000, "members": [{"steamid": "abc123", "playername": "Bob"}]},
        {"tribeid": 2000, "members": [{"steamid": "zzz", "playername": "Nobody"}]},
    ]
    result = compute_compliance(structures, tribes=tribes)
    assert result[0]["members"] == [{"steamid": "abc123", "playername": "Bob"}]


def test_extent_exactly_80_not_violating():
    # exactly 80 foundations of contiguous extent: > comparison, not >=
    structures = [make_structure(1000, x=0.0, y=0.0) for _ in range(10)]
    structures += [make_structure(1000, x=80 * FOUNDATION, y=0.0) for _ in range(10)]
    # 4f spacing keeps the whole span one core building
    structures += [
        make_structure(1000, x=i * 4 * FOUNDATION, y=0.0) for i in range(1, 20)
    ]
    result = compute_compliance(structures, max_extent=80.0)
    assert "base_too_large" not in result[0]["violations"]


def test_stray_structure_does_not_inflate_extent():
    # A compliant 10f building with a lone trough 15 foundations away: same
    # location (within the 20f gap) but the extent must stay the core's 10f,
    # not stretch to 25f. (Live dispute: 69x70f build reported as 103x87f
    # because of a spike-wall line and a feeding trough.)
    structures = block(1000, 50, origin_x=0.0)
    stray = make_structure(1000, x=25 * FOUNDATION, y=0.0)
    stray["struct"] = "FeedingTrough_C"
    structures.append(stray)
    result = compute_compliance(structures, max_extent=80.0)
    assert len(result[0]["locations"]) == 1
    ext = result[0]["locations"][0]["extent_foundations"]
    assert max(ext["x"], ext["y"]) <= 10.0
    assert "base_too_large" not in result[0]["violations"]


def test_two_buildings_bridged_within_gap_sized_by_core():
    # Two compliant 40f buildings 15 foundations apart chain into ONE location
    # at the 20f gap, but the size check measures the larger core building,
    # so no base_too_large even though the loose bbox spans ~95f.
    structures = block(1000, 100, origin_x=0.0, span_foundations=40.0) + block(
        1000, 80, origin_x=55 * FOUNDATION, span_foundations=40.0
    )
    result = compute_compliance(structures, max_extent=80.0)
    real = [loc for loc in result[0]["locations"] if loc["classification"] != "spam"]
    assert len(real) == 1
    assert "base_too_large" not in result[0]["violations"]


def test_outpost_at_299_ok():
    structures = block(1000, 500, origin_x=0.0) + block(1000, 299, origin_x=200_000.0)
    result = compute_compliance(structures, outpost_max=300)
    assert "outpost_too_big" not in result[0]["violations"]


def test_outpost_at_300_ok():
    # cap is inclusive: exactly outpost_max is compliant, only +1 over flags
    structures = block(1000, 500, origin_x=0.0) + block(1000, 300, origin_x=200_000.0)
    result = compute_compliance(structures, outpost_max=300)
    assert "outpost_too_big" not in result[0]["violations"]


def test_outpost_at_301_flags():
    structures = block(1000, 500, origin_x=0.0) + block(1000, 301, origin_x=200_000.0)
    result = compute_compliance(structures, outpost_max=300)
    assert "outpost_too_big" in result[0]["violations"]


def test_spam_only_tribe_has_no_main():
    structures = litter_block(1000, 3, origin_x=0.0)
    result = compute_compliance(structures)
    classes = [loc["classification"] for loc in result[0]["locations"]]
    assert classes == ["spam"]
    # a single small stray cluster is below the littering thresholds
    assert "spam_present" not in result[0]["violations"]


def test_negative_coords_cluster():
    points_structures = block(1000, 20, origin_x=-500_000.0)
    result = compute_compliance(points_structures)
    assert len(result[0]["locations"]) == 1


def test_duplicate_points_single_cluster():
    structures = [make_structure(1000, x=0.0, y=0.0) for _ in range(15)]
    result = compute_compliance(structures)
    assert len(result[0]["locations"]) == 1
    assert result[0]["locations"][0]["structure_count"] == 15


def test_no_grid_overmerge():
    # Two blocks ~31 foundations apart (> gap 20f) must stay separate clusters.
    # The old grid union-find merged anything sharing nearby cells (~3x gap).
    structures = block(1000, 20, origin_x=0.0) + block(1000, 20, origin_x=12_000.0)
    result = compute_compliance(structures)
    classes = sorted(loc["classification"] for loc in result[0]["locations"])
    assert classes == ["main", "outpost"]
    assert "base_too_large" not in result[0]["violations"]


def test_cluster_points_exact_distance():
    # 6001 UU apart with gap 6000 -> two clusters even though cells are adjacent
    labels = cluster_points([(0.0, 0.0), (6001.0, 0.0)], gap_uu=6000.0)
    assert labels[0] != labels[1]
    labels = cluster_points([(0.0, 0.0), (5999.0, 0.0)], gap_uu=6000.0)
    assert labels[0] == labels[1]


def pipe_structure(tribeid: int, x: float, y: float) -> dict:
    s = make_structure(tribeid, x, y)
    s["struct"] = "WaterPipe_Metal_Straight_C"
    return s


def test_utility_runs_do_not_chain_bases():
    # Two compliant blocks 200 foundations apart connected by a pipe run.
    # Pipes must not merge them into one oversized "base".
    pipes = [pipe_structure(1000, x=float(x), y=0.0) for x in range(3000, 60_000, 3000)]
    structures = (
        block(1000, 20, origin_x=0.0) + block(1000, 20, origin_x=60_000.0) + pipes
    )
    result = compute_compliance(structures, max_extent=80.0)
    assert "base_too_large" not in result[0]["violations"]
    classes = sorted(loc["classification"] for loc in result[0]["locations"])
    assert classes == ["main", "outpost"]
    assert result[0]["utility_count"] == len(pipes)
    assert result[0]["total_structures"] == 40 + len(pipes)


def test_electrical_runs_are_utility():
    # Electrical cable/junction runs (incl. flex wires) must not chain two
    # compliant bases into one oversized "base", but a C4 tripwire is a real
    # structure (no bare-"wire" keyword false positive).
    electrical_classes = (
        "ElectricCableStraight_C",
        "ElectricCableIntersection_C",
        "Electric_Cable_Vertical_C",
        "ElectricCableDiagonal_C",
        "ElectricJunction_C",
        "BP_Wire_Flex_C",
    )
    run = []
    for n in range(3000, 60_000, 3000):
        s = make_structure(1000, x=float(n), y=0.0)
        s["struct"] = electrical_classes[n // 3000 % len(electrical_classes)]
        run.append(s)
    tripwire = make_structure(1000, x=200_000.0, y=0.0)
    tripwire["struct"] = "C4Tripwire_C"
    structures = (
        block(1000, 20, origin_x=0.0)
        + block(1000, 20, origin_x=60_000.0)
        + run
        + [tripwire]
    )
    result = compute_compliance(structures, max_extent=80.0)
    assert "base_too_large" not in result[0]["violations"]
    assert result[0]["utility_count"] == len(run)
    # the C4 tripwire is NOT utility (no bare-"wire"/"flex" match): under the
    # inverted test a non-litter structure is a real 1-piece location, not spam.
    assert result[0]["total_structures"] == 40 + len(run) + 1
    singles = [loc for loc in result[0]["locations"] if loc["structure_count"] == 1]
    assert len(singles) == 1


def test_water_tanks_and_taps_are_utility():
    # A remote irrigation endpoint (tank + taps + sap tap) must not register as
    # an extra build location for rule 4.1, but a catapult must (no bare-"tap"
    # keyword false positive).
    waterworks = []
    for n, cls in enumerate(
        ("WaterTank_Metal_C", "WaterTap_Metal_C", "WaterTap_C", "TreeSapTap_SM_C")
    ):
        s = make_structure(1000, x=200_000.0 + n * 300.0, y=0.0)
        s["struct"] = cls
        waterworks.append(s)
    catapults = []
    for n in range(12):
        s = make_structure(1000, x=400_000.0 + n * 300.0, y=0.0)
        s["struct"] = "StructureTurretCatapult_C"
        catapults.append(s)
    structures = (
        block(1000, 20, origin_x=0.0)
        + block(1000, 20, origin_x=60_000.0)
        + waterworks
        + catapults
    )
    result = compute_compliance(structures)
    assert result[0]["utility_count"] == len(waterworks)
    # main + outpost + the catapult battery = 3 real locations -> violation
    assert "too_many_locations" in result[0]["violations"]
    real = [loc for loc in result[0]["locations"] if loc["classification"] != "spam"]
    assert len(real) == 3


def test_exempt_tribe_excluded():
    structures = block(1000, 500, origin_x=0.0, span_foundations=120.0) + block(
        2000, 50, origin_x=300_000.0
    )
    for s in structures:
        if s["tribeid"] == 1000:
            s["tribe"] = "Server Admin"
    result = compute_compliance(structures)
    assert [r["tribeid"] for r in result] == [2000]


def test_exempt_override():
    structures = block(1000, 50, origin_x=0.0)
    for s in structures:
        s["tribe"] = "Server Admin"
    result = compute_compliance(structures, exempt_tribes=frozenset())
    assert len(result) == 1


# --- content-based spam classification (incident 2026-06-14 fix) ---


def test_small_value_cluster_is_not_spam():
    # A small cluster (< old count threshold) with value structures
    # (block() uses StorageBox_Large_C) must NOT be spam (never auto-destroyed).
    # With no walls/foundation it is also not a built area, so it ranks "minor",
    # not "outpost": a 4-box stash must not fabricate a too_many_locations flag.
    structures = block(1000, 50, origin_x=0.0) + block(1000, 4, origin_x=200_000.0)
    result = compute_compliance(structures)
    classes = sorted(loc["classification"] for loc in result[0]["locations"])
    assert "spam" not in classes
    assert classes == ["main", "minor"]


def test_small_greenhouse_is_not_spam():
    # A 4-piece greenhouse (crops + glass), exactly the kind the incident
    # destroyed, is a real base and must never be spam.
    gh = [
        make_struct_class(1000, 200_000.0 + n * 300.0, cls)
        for n, cls in enumerate(
            (
                "CropPlotLarge_SM_C",
                "CropPlotLarge_SM_C",
                "Greenhouse_Wall_C",
                "Greenhouse_Ceiling_C",
            )
        )
    ]
    result = compute_compliance(block(1000, 50, origin_x=0.0) + gh)
    classes = [loc["classification"] for loc in result[0]["locations"]]
    assert "spam" not in classes


def test_small_stone_shell_no_value_is_not_spam():
    # Walls/ceilings with no loot are still a real build, not loose litter.
    shell = [
        make_struct_class(1000, 200_000.0 + n * 300.0, cls)
        for n, cls in enumerate(
            ("Wall_Stone_C", "Wall_Stone_C", "Ceiling_Stone_C", "Foundation_Stone_C")
        )
    ]
    result = compute_compliance(block(1000, 50, origin_x=0.0) + shell)
    classes = [loc["classification"] for loc in result[0]["locations"]]
    assert "spam" not in classes


def test_litter_cluster_is_spam():
    structures = block(1000, 50, origin_x=0.0) + litter_block(
        1000, 4, origin_x=200_000.0
    )
    result = compute_compliance(structures)
    spam = [loc for loc in result[0]["locations"] if loc["classification"] == "spam"]
    assert len(spam) == 1 and spam[0]["structure_count"] == 4


def test_stray_deployables_do_not_trigger_too_many_locations():
    # vainne live report 2026-06-16: a tribe with ONE real base plus a lone
    # WaterWell and a lone OilPump (each its own cluster, far away) was flagged
    # too_many_locations. Stray deployables are not built areas -> "minor".
    structures = (
        block(1000, 50, origin_x=0.0)
        + [make_struct_class(1000, 200_000.0, "WaterWellWaterIntake_C")]
        + [make_struct_class(1000, 400_000.0, "OilPump_C")]
    )
    result = compute_compliance(structures)
    assert "too_many_locations" not in result[0]["violations"]
    classes = sorted(loc["classification"] for loc in result[0]["locations"])
    assert classes == ["main", "minor", "minor"]


def test_real_second_base_still_counts_as_outpost():
    # A genuine second build (walls on a foundation) is a real outpost and is
    # still allowed; a third real build trips too_many_locations as before.
    def shell(origin_x: float) -> list[dict]:
        return [
            make_struct_class(1000, origin_x + n * 300.0, cls)
            for n, cls in enumerate(
                (
                    "Foundation_Stone_C",
                    "Wall_Stone_C",
                    "Wall_Stone_C",
                    "Ceiling_Stone_C",
                )
            )
        ]

    two = block(1000, 50, origin_x=0.0) + shell(200_000.0)
    assert "too_many_locations" not in compute_compliance(two)[0]["violations"]
    three = two + shell(400_000.0)
    assert "too_many_locations" in compute_compliance(three)[0]["violations"]


def test_mixed_cluster_with_one_value_is_not_spam():
    # 5 pillars + 1 storage box in the SAME cluster: the value piece protects it.
    mixed = litter_block(1000, 5, origin_x=200_000.0)
    mixed.append(make_struct_class(1000, 200_000.0, "StorageBox_Large_C"))
    result = compute_compliance(block(1000, 50, origin_x=0.0) + mixed)
    classes = [loc["classification"] for loc in result[0]["locations"]]
    assert "spam" not in classes


def test_large_litter_pile_is_spam_by_content():
    # Content, not count: 20 pure pillars is spam even though it is over the old
    # spam_threshold, and trips spam_present (>= 15 litter structures).
    structures = block(1000, 50, origin_x=0.0) + litter_block(
        1000, 20, origin_x=200_000.0
    )
    result = compute_compliance(structures)
    spam = [loc for loc in result[0]["locations"] if loc["classification"] == "spam"]
    assert len(spam) == 1 and spam[0]["structure_count"] == 20
    assert "spam_present" in result[0]["violations"]


# --- Task 1: inverted litter test ---


def test_is_litter_claim_pieces():
    for cls in (
        "Pillar_Stone_C",
        "Foundation_Stone_C",
        "GateFrame_Stone_C",
        "Gate_Stone_C",
        "Sign_Large_Wood_C",
        "SleepingBag_C",
        "Campfire_C",
        "FenceFoundation_Wood_SM_C",
        "BearTrapLarge_C",
    ):
        assert is_litter(cls), cls


def test_is_litter_excludes_real():
    for cls in (
        "Wall_Stone_C",
        "Ceiling_Stone_C",
        "StorageBox_Huge_C",
        "Greenhouse_Wall_C",
        "BP_Ramp_Stone_C",
        "IceBox_C",
    ):
        assert not is_litter(cls), cls


def test_has_real_structure_pure_litter_is_spam():
    cluster = [{"struct": "Pillar_Stone_C"}, {"struct": "GateFrame_Stone_C"}]
    assert has_real_structure(cluster) is False


def test_has_real_structure_one_wall_makes_it_real():
    cluster = [{"struct": "Pillar_Stone_C"}, {"struct": "Wall_Stone_C"}]
    assert has_real_structure(cluster) is True


def test_has_real_structure_ramp_makes_it_real():
    # ramp is a missed-real class under the old keyword test; inverted test protects it
    assert has_real_structure([{"struct": "BP_Ramp_Stone_C"}]) is True


# --- Task 2: value tiers ---


def test_structure_tier_levels():
    assert structure_tier("Pillar_Stone_C") == 0
    assert structure_tier("Mortar_C") == 1
    assert structure_tier("StorageBox_C") == 1  # small storage -> trivial
    assert structure_tier("StorageBox_Huge_C") == 2  # large/huge -> standard
    assert structure_tier("IceBox_C") == 2  # preserving fridge -> standard value
    assert structure_tier("ChemBench_C") == 2
    assert structure_tier("Greenhouse_Wall_C") == 2
    assert structure_tier("TekGenerator_C") == 3
    assert structure_tier("StructureTurretPlant_C") == 3
    assert structure_tier("IndustrialForge_C") == 3  # industrial beats forge(T2)


def test_cluster_tier_is_max_member():
    cluster = [
        {"struct": "Pillar_Stone_C"},
        {"struct": "ChemBench_C"},
        {"struct": "StorageBox_C"},
    ]
    assert cluster_tier(cluster) == 2


# --- Task 3: decay / demoable ---

NOW = datetime.datetime(2026, 6, 15, 12, 0, tzinfo=datetime.timezone.utc)


def _aged(cls, days):
    dt = NOW - datetime.timedelta(days=days)
    return {"struct": cls, "ccc": "0 0 0", "last_ally_in_range": dt.isoformat()}


def test_material_of():
    assert material_of("Wall_Stone_C") == "stone"
    assert material_of("Wall_Metal_C") == "metal"
    assert material_of("TekWall_C") == "tek"
    assert material_of("Greenhouse_Wall_C") == "greenhouse"
    assert material_of("Pillar_Wood_SM_New_C") == "wood"
    assert material_of("Mystery_C") == ""


def test_structure_age_days():
    assert abs(structure_age_days(_aged("Wall_Stone_C", 10), NOW) - 10.0) < 0.01
    assert structure_age_days({"last_ally_in_range": ""}, NOW) is None


def test_decay_period_days():
    assert decay_period_days("Wall_Stone_C") == 12.0
    assert decay_period_days("Wall_Metal_C") == 16.0


def test_cluster_demoable_stone():
    # stone period 12d. Freshest piece 13d idle -> demoable.
    assert cluster_demoable([_aged("Wall_Stone_C", 13)], NOW) is True
    assert cluster_demoable([_aged("Wall_Stone_C", 11)], NOW) is False


def test_cluster_demoable_uses_toughest_material():
    # mixed stone(12d)+metal(16d): not demoable until past the toughest (16d)
    c = [_aged("Wall_Stone_C", 17), _aged("Wall_Metal_C", 17)]
    assert cluster_demoable(c, NOW) is True
    c2 = [_aged("Wall_Stone_C", 17), _aged("Wall_Metal_C", 14)]  # metal fresher
    assert cluster_demoable(c2, NOW) is False


def test_compliance_emits_tier_demoable_inactive():
    structs = [_aged("Wall_Stone_C", 20) for _ in range(40)]
    for i, s in enumerate(structs):
        s["tribeid"] = 7
        s["tribe"] = "Ghosts"
        s["ccc"] = f"{(i % 10) * 300} {(i // 10) * 300} 0"
    result = compute_compliance(structs, now=NOW)
    rec = result[0]
    assert rec["inactive_days"] >= 19.9
    loc = rec["locations"][0]
    assert loc["tier"] == 0
    assert loc["demoable"] is True


# --- Review 2026-07-05 fixes: crash guard, tek-litter, trap promotion, core sizing ---


def test_parse_ccc_rejects_non_finite():
    # float() happily parses these; a non-finite coord would make int(x // gap)
    # raise ValueError and 500 the whole endpoint.
    assert parse_ccc("nan nan nan") is None
    assert parse_ccc("1e999 0 0") is None  # inf
    assert parse_ccc("0 -inf 0") is None


def test_compute_compliance_survives_non_finite_ccc():
    # One corrupt record must not crash the endpoint; it is counted as unlocated.
    good = block(1000, 20, origin_x=0.0)
    bad = make_structure(1000, 0.0, 0.0)
    bad["struct"] = "Wall_Stone_C"
    bad["ccc"] = "nan nan nan"
    result = compute_compliance(good + [bad])
    assert result[0]["total_structures"] == 21
    assert result[0]["unlocated_count"] == 1


def test_tek_pillar_spam_is_not_a_base():
    # TekPillar is tier 3 via the bare 'tek' substring but is still land-claim
    # litter: two pure tek-pillar clusters must be spam, never outpost/extra, so a
    # one-base tribe is not falsely flagged too_many_locations. Stone pillars in
    # the same layout always behaved this way; tek was the regression.
    tek = [
        make_struct_class(1000, 200_000.0 + n * 300.0, "TekPillar_C") for n in range(10)
    ]
    tek += [
        make_struct_class(1000, 400_000.0 + n * 300.0, "TekPillar_C") for n in range(10)
    ]
    result = compute_compliance(block(1000, 50, origin_x=0.0) + tek)
    classes = [loc["classification"] for loc in result[0]["locations"]]
    assert "too_many_locations" not in result[0]["violations"]
    assert "outpost" not in classes and "extra" not in classes
    assert "spam" in classes


def test_taming_trap_does_not_fabricate_outpost():
    # 9-piece trap: 5 gateframes (litter) + 4 ramps. Too few non-litter pieces to
    # be a built area, so it stays 'minor' and cannot make a one-base tribe trip
    # too_many_locations.
    trap = [
        make_struct_class(1000, 200_000.0 + n * 300.0, "GateFrame_Stone_C")
        for n in range(5)
    ]
    trap += [
        make_struct_class(1000, 200_000.0 + (5 + n) * 300.0, "BP_Ramp_Stone_C")
        for n in range(4)
    ]
    assert is_built_area(trap) is False
    result = compute_compliance(block(1000, 50, origin_x=0.0) + trap)
    assert "too_many_locations" not in result[0]["violations"]


def test_spike_trap_and_wall_torch_are_not_built_areas():
    # SpikeWallWood / WallTorch must not count toward the 2-walls enclosure test.
    spike = [
        make_struct_class(1000, 200_000.0, "SpikeWallWood_C"),
        make_struct_class(1000, 200_300.0, "SpikeWallWood_C"),
        make_struct_class(1000, 200_600.0, "Foundation_Wood_C"),
    ]
    assert is_built_area(spike) is False
    torch = [
        make_struct_class(1000, 0.0, "WallTorch_C"),
        make_struct_class(1000, 300.0, "WallTorch_C"),
        make_struct_class(1000, 600.0, "Floor_Wood_C"),
    ]
    assert is_built_area(torch) is False


def test_storage_outpost_still_counts_as_built_area():
    # Guard against over-tightening: a real deployable outpost (>=8 non-litter
    # pieces, no walls) is still a built area and ranks as the outpost.
    boxes = block(1000, 12, origin_x=200_000.0)  # StorageBox_Large_C
    assert is_built_area(boxes) is True
    result = compute_compliance(block(1000, 50, origin_x=0.0) + boxes)
    classes = sorted(loc["classification"] for loc in result[0]["locations"])
    assert classes == ["main", "outpost"]


def test_oversized_sparse_core_measured_next_to_denser_blob():
    # Core sizing must measure the largest-EXTENT contiguous build, not the most
    # populous. A 120f contiguous wall line (41 pieces) beside a denser 9x9f blob
    # (100 pieces) in one location must still flag base_too_large.
    line = []
    for n in range(41):
        s = make_structure(1000, x=n * 3 * FOUNDATION, y=0.0)
        s["struct"] = "Wall_Stone_C"
        line.append(s)
    blob = []
    for n in range(100):
        s = make_structure(
            1000, x=-15 * FOUNDATION - (n % 10) * FOUNDATION, y=(n // 10) * FOUNDATION
        )
        s["struct"] = "Wall_Stone_C"
        blob.append(s)
    result = compute_compliance(line + blob, max_extent=80.0)
    assert len(result[0]["locations"]) == 1
    assert "base_too_large" in result[0]["violations"]
