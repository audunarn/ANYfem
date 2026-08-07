"""Selection state and the tag encoding that carries it to the viewport."""

from __future__ import annotations

import pytest

from anyfem.geometry.entities import EntityRef
from anyfem.selection import (
    SELECTION_MODES,
    Selection,
    entity_tag,
    mode_label,
    parse_entity_tag,
)


def test_tag_round_trip():
    for kind in SELECTION_MODES:
        ref = EntityRef(kind, 42)
        assert parse_entity_tag(entity_tag(ref)) == ref


def test_unknown_tags_parse_to_none():
    assert parse_entity_tag("") is None
    assert parse_entity_tag("something_else") is None
    assert parse_entity_tag("ent_solid3") is None
    assert parse_entity_tag("ent_face") is None
    assert parse_entity_tag("ent_facex") is None


def test_default_mode_and_switching():
    selection = Selection()
    assert selection.mode == "face"

    selection.set_mode("edge")
    assert selection.mode == "edge"

    with pytest.raises(ValueError, match="unknown selection mode"):
        selection.set_mode("solid")


def test_switching_mode_drops_the_wrong_kind():
    selection = Selection(mode="face")
    selection.select(EntityRef("face", 1))
    assert len(selection) == 1

    selection.set_mode("edge")
    assert selection.is_empty()


def test_selecting_replaces_unless_extended():
    selection = Selection(mode="face")
    selection.select(EntityRef("face", 1))
    selection.select(EntityRef("face", 2))
    assert selection.items == [EntityRef("face", 2)]

    selection.select(EntityRef("face", 3), extend=True)
    assert selection.items == [EntityRef("face", 2), EntityRef("face", 3)]


def test_extending_an_already_selected_item_toggles_it_off():
    selection = Selection(mode="face")
    selection.select(EntityRef("face", 1))
    selection.select(EntityRef("face", 1), extend=True)
    assert selection.is_empty()


def test_selection_order_is_preserved():
    """Arcs are defined start, via, end, so click order has to survive."""

    selection = Selection(mode="vertex")
    for point in (7, 3, 5):
        selection.select(EntityRef("vertex", point), extend=True)
    assert [ref.id for ref in selection.items] == [7, 3, 5]


def test_selecting_the_wrong_kind_is_refused():
    selection = Selection(mode="face")
    with pytest.raises(ValueError, match="cannot select a vertex"):
        selection.select(EntityRef("vertex", 1))


def test_handle_tag_selects_and_clears():
    selection = Selection(mode="face")
    assert selection.handle_tag(entity_tag(EntityRef("face", 4))) == EntityRef(
        "face", 4
    )
    # A click on empty space clears.
    assert selection.handle_tag("") is None
    assert selection.is_empty()


def test_handle_tag_ignores_the_wrong_kind():
    selection = Selection(mode="face")
    selection.select(EntityRef("face", 1))
    assert selection.handle_tag(entity_tag(EntityRef("edge", 2))) is None
    assert selection.is_empty()


def test_shift_click_on_empty_space_keeps_the_selection():
    selection = Selection(mode="face")
    selection.select(EntityRef("face", 1))
    selection.handle_tag("", extend=True)
    assert len(selection) == 1


def test_tags_reflect_the_selection():
    selection = Selection(mode="edge")
    selection.select_many([EntityRef("edge", 1), EntityRef("edge", 2)])
    assert selection.tags() == ["ent_edge1", "ent_edge2"]


def test_select_many_filters_by_mode():
    selection = Selection(mode="edge")
    selection.select_many(
        [EntityRef("edge", 1), EntityRef("face", 9), EntityRef("edge", 2)]
    )
    assert [ref.id for ref in selection.items] == [1, 2]


def test_listeners_fire_on_change():
    selection = Selection(mode="face")
    seen = []
    selection.add_listener(lambda: seen.append(1))

    selection.select(EntityRef("face", 1))
    selection.select(EntityRef("face", 1))  # no change, no notification
    selection.clear()
    assert len(seen) == 2


def test_describe_reads_naturally():
    selection = Selection(mode="face")
    assert selection.describe() == "No plate selected"
    selection.select(EntityRef("face", 3))
    assert selection.describe() == "Plate 3"
    selection.select(EntityRef("face", 4), extend=True)
    assert selection.describe() == "2 plates selected"


def test_mode_labels_use_modelling_words():
    assert mode_label("vertex") == "Point"
    assert mode_label("edge") == "Line"
    assert mode_label("face") == "Plate"
