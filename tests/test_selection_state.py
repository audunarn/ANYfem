"""Commercial geometry/mesh selection state without a GUI dependency."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from anyfem.geometry.entities import EntityRef
from anyfem.selection import (
    MeshEntityRef,
    Selection,
    SelectionDomain,
    SelectionFilter,
    SelectionOperation,
    entity_tag,
    owner_to_ref,
    parse_entity_tag,
    selection_key,
)


@dataclass(frozen=True)
class Owner:
    """The public shape of ANYtk3D PickOwner, without importing ANYtk3D."""

    key: str
    kind: str = ""
    priority: int = 0


@dataclass(frozen=True)
class Hit:
    owner: Owner


def face(identifier: int) -> EntityRef:
    return EntityRef("face", identifier)


def node(identifier: int) -> MeshEntityRef:
    return MeshEntityRef("node", identifier)


def element(identifier: int) -> MeshEntityRef:
    return MeshEntityRef("element", identifier)


def test_domains_and_filters_cover_every_supported_entity_kind():
    geometry = SelectionFilter("geometry", frozenset({"vertex", "edge", "face"}))
    mesh = SelectionFilter("mesh", frozenset({"node", "element", "element_face"}))

    assert geometry.qualified_kinds == {
        "geometry.vertex",
        "geometry.edge",
        "geometry.face",
    }
    assert mesh.accepts(node(1))
    assert mesh.accepts(element(2))
    assert mesh.accepts(MeshEntityRef("element_face", (2, 0)))
    assert not mesh.accepts(face(2))

    with pytest.raises(ValueError, match="does not support"):
        SelectionFilter("geometry", frozenset({"node"}))


def test_set_mode_switches_between_geometry_and_mesh_domains():
    selection = Selection("face")
    selection.select(face(1))

    selection.set_mode("node")
    assert selection.domain == SelectionDomain.MESH
    assert selection.mode == "node"
    assert selection.allowed_kinds == {"node"}
    assert selection.is_empty()

    selection.select(node(4))
    selection.set_domain("geometry")
    assert selection.domain == SelectionDomain.GEOMETRY
    assert selection.mode == "face"
    assert selection.is_empty()


def test_multi_kind_filter_accepts_only_its_active_mesh_scope():
    selection = Selection(
        domain="mesh", kinds={"node", "element"}
    )
    change = selection.apply(
        [node(1), element(8), MeshEntityRef("element_face", (8, 0)), face(3)],
        "replace",
    )

    assert selection.items == [node(1), element(8)]
    assert len(change.rejected) == 2
    assert "active mesh filter" in change.rejected[0].reason
    assert "active selection domain is mesh" in change.rejected[1].reason


def test_replace_add_toggle_and_remove_are_order_preserving_set_operations():
    selection = Selection("face")
    first = selection.apply([face(3), face(1)], SelectionOperation.REPLACE)
    assert first.changed
    assert selection.items == [face(3), face(1)]

    selection.apply([face(1), face(2)], SelectionOperation.ADD)
    assert selection.items == [face(3), face(1), face(2)]

    selection.apply([face(1), face(4)], SelectionOperation.TOGGLE)
    assert selection.items == [face(3), face(2), face(4)]

    selection.apply([face(2), face(99)], SelectionOperation.REMOVE)
    assert selection.items == [face(3), face(4)]


def test_apply_notifies_once_for_a_bulk_change_and_never_for_a_noop():
    selection = Selection("face")
    notifications = []
    selection.add_listener(lambda: notifications.append(selection.items))

    selection.apply([face(1), face(2), face(3)], "replace")
    selection.apply([face(1), face(2), face(3)], "replace")
    selection.apply([face(99)], "remove")

    assert notifications == [[face(1), face(2), face(3)]]


def test_wrong_scope_replace_is_explained_and_does_not_clear_a_valid_selection():
    selection = Selection("face")
    selection.select(face(1))
    change = selection.apply([node(4)], "replace")

    assert not change.changed
    assert selection.items == [face(1)]
    assert change.accepted == ()
    assert len(change.rejected) == 1
    assert "active selection domain is geometry" in change.rejected[0].reason
    assert selection.last_rejection == change.rejected[0].reason

    # A genuinely empty replacement is an empty-space pick and does clear.
    selection.apply([], "replace")
    assert selection.is_empty()


def test_select_all_and_invert_use_only_matching_scope_and_universe_order():
    selection = Selection("face")
    universe = [face(5), node(9), face(2), face(7)]

    selection.select_all(universe)
    assert selection.items == [face(5), face(2), face(7)]
    selection.select(face(2), extend=True)
    assert selection.items == [face(5), face(7)]

    selection.invert(universe)
    assert selection.items == [face(2)]


def test_ordered_picks_are_first_class_and_can_be_validated():
    selection = Selection("vertex")
    for identifier in (7, 3, 5):
        selection.apply([EntityRef("vertex", identifier)], "add")

    assert [ref.id for ref in selection.ordered_items] == [7, 3, 5]
    assert selection.pick_index(EntityRef("vertex", 3)) == 1
    assert selection.pick_index(EntityRef("vertex", 99)) is None
    assert selection.require_ordered(3, kind="vertex") == selection.ordered_items
    with pytest.raises(ValueError, match="needs 2 ordered points"):
        selection.require_ordered(2, kind="vertex")


def test_mesh_tags_round_trip_without_changing_geometry_tags():
    references = [
        node(4),
        element(12),
        MeshEntityRef("element_face", (12, 1)),
    ]
    assert [parse_entity_tag(entity_tag(ref)) for ref in references] == references
    assert entity_tag(face(3)) == "ent_face3"


def test_owner_adaptation_accepts_pickowner_shape_and_canonical_keys():
    assert owner_to_ref(Owner("face7", "geometry.face", 10)) == face(7)
    assert owner_to_ref(Owner("node21", "mesh.node")) == node(21)
    assert owner_to_ref(Owner("element3", "mesh.element")) == element(3)
    element_face = MeshEntityRef("element_face", (17, 2))
    assert owner_to_ref(
        Owner("mesh.element_face:17:2", "mesh.element_face")
    ) == element_face

    for ref in (face(8), node(9), element(10), element_face):
        assert owner_to_ref(Owner(selection_key(ref))) == ref


def test_owner_adaptation_unwraps_selection_hit_shape_and_handles_invalid_owner():
    assert owner_to_ref(Hit(Owner("edge11", "geometry.edge"))) == EntityRef(
        "edge", 11
    )
    assert owner_to_ref(Owner("polyline", "geometry.edge")) is None
    with pytest.raises(ValueError, match="carries no edge ID"):
        owner_to_ref(Owner("polyline", "geometry.edge"), strict=True)


def test_apply_owners_reports_unadaptable_and_out_of_scope_owners():
    selection = Selection("face")
    change = selection.apply_owners(
        [Owner("face1", "geometry.face"), Owner("node2", "mesh.node"), Owner("x")],
        "replace",
    )

    assert selection.items == [face(1)]
    assert len(change.rejected) == 2
    assert any("active selection domain" in item.reason for item in change.rejected)
    assert any("no supported" in item.reason for item in change.rejected)


def test_unadaptable_owner_pick_does_not_clear_prior_selection():
    selection = Selection("face")
    selection.select(face(7))

    change = selection.apply_owners([Owner("not-an-entity")], "replace")

    assert selection.items == [face(7)]
    assert change.before == change.after == (face(7),)
    assert change.accepted == ()
    assert len(change.rejected) == 1
    assert selection.last_rejection == change.rejected[0].reason


def test_mesh_reference_rejects_ambiguous_or_invalid_element_faces():
    with pytest.raises(ValueError, match=r"needs \(element_id"):
        MeshEntityRef("element_face", 2)
    with pytest.raises(ValueError, match="cannot be negative"):
        MeshEntityRef("node", -1)
