from common.compliance import (
    ABANDONED_TRIBE_ID,
    cluster_points,
    compute_compliance,
    parse_ccc,
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
    # chain structures across 100 foundations: one cluster, extent > 80
    structures = [
        make_structure(1000, x=i * 10 * FOUNDATION, y=0.0) for i in range(11)
    ] + block(1000, 20, origin_x=0.0)
    result = compute_compliance(structures, max_extent=80.0)
    assert "base_too_large" in result[0]["violations"]


def test_spam_flagged_when_pattern():
    # 3 separate spam clusters = littering pattern -> violation
    structures = (
        block(1000, 50, origin_x=0.0)
        + block(1000, 3, origin_x=200_000.0)
        + block(1000, 3, origin_x=400_000.0)
        + block(1000, 3, origin_x=600_000.0)
    )
    result = compute_compliance(structures, spam_threshold=10)
    classes = [loc["classification"] for loc in result[0]["locations"]]
    assert "spam" in classes
    assert "spam_present" in result[0]["violations"]
    assert "too_many_locations" not in result[0]["violations"]


def test_single_small_spam_cluster_not_flagged():
    # One stray 3-structure cluster is shown as spam but is not a violation
    structures = block(1000, 50, origin_x=0.0) + block(1000, 3, origin_x=200_000.0)
    result = compute_compliance(structures, spam_threshold=10)
    classes = [loc["classification"] for loc in result[0]["locations"]]
    assert "spam" in classes
    assert "spam_present" not in result[0]["violations"]


def test_spam_flagged_by_structure_total():
    # Two clusters but 18 total spam structures (>= 15) -> violation
    structures = (
        block(1000, 50, origin_x=0.0)
        + block(1000, 9, origin_x=200_000.0)
        + block(1000, 9, origin_x=400_000.0)
    )
    result = compute_compliance(structures, spam_threshold=10)
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
    # exactly 80 foundations extent: > comparison, not >=
    structures = [make_structure(1000, x=0.0, y=0.0) for _ in range(10)]
    structures += [make_structure(1000, x=80 * FOUNDATION, y=0.0) for _ in range(10)]
    # chain so it stays one cluster (spacing 15 foundations = 4500 UU < gap 6000 UU)
    structures += [
        make_structure(1000, x=i * 15 * FOUNDATION, y=0.0) for i in range(1, 6)
    ]
    result = compute_compliance(structures, max_extent=80.0)
    assert "base_too_large" not in result[0]["violations"]


def test_outpost_at_299_ok():
    structures = block(1000, 500, origin_x=0.0) + block(1000, 299, origin_x=200_000.0)
    result = compute_compliance(structures, outpost_max=300)
    assert "outpost_too_big" not in result[0]["violations"]


def test_spam_only_tribe_has_no_main():
    structures = block(1000, 3, origin_x=0.0)
    result = compute_compliance(structures, spam_threshold=10)
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
