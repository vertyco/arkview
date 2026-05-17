"""
Post-transform enrichment.

These functions accept already-transformed model lists and fill in counts /
cross-references that require data from multiple collections.

Called after transform, before the results are written to state.
"""

from app.models import Player, Structure, Tamed, Tribe


def _synthesize_missing_tribes(
    tribes: list[Tribe],
    referenced_ids: set[int],
    metadata_source: Tribe | Tamed | Structure | Player | None,
) -> None:
    """Append placeholder Tribe entries for tribe_ids that appear in tamed /
    structures / players but have no matching ``.arktribe`` file.

    Purpose: the legacy (v2) exporter synthesized a tribe record from any
    referenced ``tribe_id`` even when no ``.arktribe`` existed (e.g. tribes
    that never renamed themselves from the "Tribe of <player>" default and
    therefore have no on-disk tribe file). Without this, ``Map stats``-style
    queries reported 95 tribes instead of 1,550 on busy servers because most
    tribes don't have a .arktribe.
    Preconditions: ``tribes`` is the freshly-transformed list (may be empty);
    ``referenced_ids`` is the set of tribe_ids appearing on tamed/structures/
    players; ``metadata_source`` is any model whose ``map_name``/``map_path``/
    ``cluster_dir``/``version`` we can reuse for the placeholder records.
    Postconditions: ``tribes`` is mutated in-place to include a synthetic
    Tribe (``name=None``, ``data_file=""``) for every referenced_id not
    already present.
    Side effects: list mutation only; no I/O.
    """
    existing_ids: set[int] = {t.tribe_id for t in tribes}
    missing_ids = referenced_ids - existing_ids - {0, 2000000000}  # 2B = "unclaimed"
    if not missing_ids:
        return

    # Pull metadata fields from any existing model so the synthetic Tribe
    # validates (BaseResponse requires map_name / map_path / version).
    if metadata_source is not None:
        map_name = metadata_source.map_name
        map_path = metadata_source.map_path
        cluster_dir = metadata_source.cluster_dir
        version = metadata_source.version
    else:
        map_name = ""
        map_path = ""
        cluster_dir = ""
        version = ""

    for tribe_id in sorted(missing_ids):
        tribes.append(
            Tribe(
                map_name=map_name,
                map_path=map_path,
                cluster_dir=cluster_dir,
                version=version,
                tribe_id=tribe_id,
                name=None,
                owner_id=0,
                owner_name="",
                member_count=0,
                members=[],
                alliance_ids=[],
                tame_count=0,
                structure_count=0,
                data_file="",
                last_active=None,
            )
        )


def enrich_tribes(
    tribes: list[Tribe],
    tamed: list[Tamed],
    structures: list[Structure],
    players: list[Player] | None = None,
) -> None:
    """Populate computed fields on each Tribe (and each Player) in-place.

    Purpose: cross-reference data across collections so each Tribe carries
    tame/structure counts + member identity, and each Player carries the
    name of the tribe they belong to. (The .arkprofile only stores tribe_id;
    the tribe_name lives in the matching .arktribe.)

    Preconditions: inputs are the freshly-transformed lists from
    ``parse_all_data``/``parse_world_data``/``parse_players_data``. Fields
    like ``tribe.tame_count`` start at their defaults.

    Postconditions, on each ``Tribe`` instance:
        - ``tame_count`` and ``structure_count`` reflect the actual counts.
        - ``members`` entries with a matching player profile have
          ``steam_id``, ``steam_name``, and ``level`` filled in.
        - ``last_active`` is set to the most recent member activity.
    Postconditions, on each ``Player`` instance:
        - ``tribe_name`` is set to the matching ``Tribe.name`` when
          ``player.tribe_id`` resolves to a tribe in the list. (Previously
          left empty, which caused the cog to log
          ``Player X has no tribe (None - <tribeid>)`` for every player.)

    Side effects: mutates the passed-in Tribe and Player models in place.
    Failure modes: none beyond the inputs themselves; missing cross-refs
    leave default values untouched.
    """
    tame_counts: dict[int, int] = {}
    for tame in tamed:
        if tame.tribe_id:
            tame_counts[tame.tribe_id] = tame_counts.get(tame.tribe_id, 0) + 1

    struct_counts: dict[int, int] = {}
    for struct in structures:
        if struct.tribe_id:
            struct_counts[struct.tribe_id] = struct_counts.get(struct.tribe_id, 0) + 1

    # Synthesize placeholder Tribe records for tribe_ids that appear in
    # tamed/structures/players but have no on-disk .arktribe (most "Tribe of X"
    # default-named tribes don't get a file). Without this the Map Stats / Find
    # Tribe commands would report 95 tribes on a server that actually has 1,550.
    player_tribe_ids: set[int] = (
        {p.tribe_id for p in players if p.tribe_id} if players else set()
    )
    referenced_ids = set(tame_counts) | set(struct_counts) | player_tribe_ids
    metadata_source: Tribe | Tamed | Structure | Player | None = None
    for candidate_list in (tribes, tamed, structures, players or []):
        if candidate_list:
            metadata_source = candidate_list[0]
            break
    _synthesize_missing_tribes(tribes, referenced_ids, metadata_source)

    tribe_name_by_id: dict[int, str] = {}
    for tribe in tribes:
        tribe.tame_count = tame_counts.get(tribe.tribe_id, 0)
        tribe.structure_count = struct_counts.get(tribe.tribe_id, 0)
        if tribe.name:
            tribe_name_by_id[tribe.tribe_id] = tribe.name

    if players:
        # Player tribe_name enrichment. Profiles don't store the tribe NAME -
        # only the tribe_id - so the cog used to see ``tribe_name=""`` for
        # every player and log spam ``Player X has no tribe (None - <tribeid>)``.
        for player in players:
            if not player.tribe_name and player.tribe_id:
                name = tribe_name_by_id.get(player.tribe_id, "")
                if not name and player.tribe_id == player.player_id:
                    # Solo player: ARK auto-names their tribe "Tribe of <character>".
                    # The synthesized tribe record has name=None (no .arktribe file),
                    # so we reconstruct the conventional name from the character name.
                    display = player.character_name or player.steam_name or ""
                    if display:
                        name = f"Tribe of {display}"
                player.tribe_name = name

        players_by_tribe: dict[int, list[Player]] = {}
        for player in players:
            players_by_tribe.setdefault(player.tribe_id, []).append(player)

        for tribe in tribes:
            tribe_players = players_by_tribe.get(tribe.tribe_id, [])
            for member in tribe.members:
                for player in tribe_players:
                    if player.player_id == member.player_id:
                        member.steam_id = player.steam_id
                        member.steam_name = player.steam_name
                        member.level = player.level
                        break

            active_dates = [p.last_active for p in tribe_players if p.last_active]
            if active_dates:
                tribe.last_active = max(active_dates)
