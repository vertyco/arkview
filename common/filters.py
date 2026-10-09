import typing as t


def match_class(row: dict[str, t.Any], class_name: str | None) -> bool:
    """Case-insensitive substring match of class_name against row['creature'].

    Pre: row is a dict. Post: True when class_name is None, else True only if
    the lowered creature string contains the lowered class_name.
    """
    assert isinstance(row, dict), "row must be a dict"
    assert class_name is None or isinstance(class_name, str), "class_name must be str or None"
    if class_name is None:
        return True
    return class_name.lower() in str(row.get("creature", "")).lower()


def match_tribe(row: dict[str, t.Any], tribe_id: int | None) -> bool:
    """Exact match of tribe_id against int(row['tribeid']).

    Pre: row is a dict. Post: True when tribe_id is None; False when the row
    has no usable tribeid; otherwise True only on equality.
    """
    assert isinstance(row, dict), "row must be a dict"
    assert tribe_id is None or isinstance(tribe_id, int), "tribe_id must be int or None"
    if tribe_id is None:
        return True
    try:
        return int(row["tribeid"]) == tribe_id
    except (KeyError, TypeError, ValueError):
        return False


def match_flag(row: dict[str, t.Any], key: str, wanted: bool | None) -> bool:
    """Compare bool(row[key]) to wanted.

    Pre: row is a dict and key is a non-empty string. Post: True when wanted
    is None; a missing key counts as False.
    """
    assert isinstance(row, dict), "row must be a dict"
    assert key, "key must be non-empty"
    if wanted is None:
        return True
    return bool(row.get(key)) == wanted


def filter_tamed(
    rows: list[dict[str, t.Any]],
    tribe_id: int | None,
    class_name: str | None,
    is_cryo: bool | None,
) -> list[dict[str, t.Any]]:
    """Filter tamed rows; a None argument disables that filter.

    Pre: rows is a list of dicts. Post: result holds the same dict objects in
    original order, never more than len(rows).
    """
    assert isinstance(rows, list), "rows must be a list"
    assert tribe_id is None or isinstance(tribe_id, int), "tribe_id must be int or None"
    result = [
        row
        for row in rows
        if match_tribe(row, tribe_id) and match_class(row, class_name) and match_flag(row, "cryo", is_cryo)
    ]
    assert len(result) <= len(rows), "filter cannot add rows"
    return result


def filter_wild(
    rows: list[dict[str, t.Any]],
    class_name: str | None,
    tameable: bool | None,
) -> list[dict[str, t.Any]]:
    """Filter wild rows; a None argument disables that filter.

    Pre: rows is a list of dicts. Post: result holds the same dict objects in
    original order, never more than len(rows).
    """
    assert isinstance(rows, list), "rows must be a list"
    assert tameable is None or isinstance(tameable, bool), "tameable must be bool or None"
    result = [row for row in rows if match_class(row, class_name) and match_flag(row, "tameable", tameable)]
    assert len(result) <= len(rows), "filter cannot add rows"
    return result
