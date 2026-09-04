"""Headless contracts for GUI work that must scale with model size."""

from __future__ import annotations

from anyfem import Project
from anyfem.commands import AddPoint, CommandStack
from anyfem.ui.app import AnyFemApp
from anyfem.ui.tree import (
    TREE_ENTITY_ROW_LIMIT,
    bounded_entity_ids,
    bounded_unowned_entity_ids,
)


class _AppHarness:
    """The command/refresh part of AnyFemApp without constructing Tk."""

    def __init__(self) -> None:
        self.project = Project("batch-refresh")
        self.commands = CommandStack(self.project)
        self._refresh_suspended = 0
        self.refresh_count = 0
        self.commands.add_listener(self.refresh_all)

    def refresh_all(self) -> None:
        if not self._refresh_suspended:
            self.refresh_count += 1


def test_many_ui_commands_coalesce_refresh_but_keep_individual_undo_steps():
    app = _AppHarness()
    commands = [AddPoint(float(index), 0.0) for index in range(100)]

    created = AnyFemApp.run_many(app, commands)

    assert created == list(range(1, 101))
    assert len(app.project.geometry.vertices) == 100
    assert len(app.commands.history()) == 100
    assert app.refresh_count == 1

    assert app.commands.undo()
    assert len(app.project.geometry.vertices) == 99
    assert app.refresh_count == 2


class _CountingIds:
    """50k mapping-key stand-in that exposes accidental eager traversal."""

    def __init__(self, size: int = 50_000) -> None:
        self.size = size
        self.iterations = 0
        self.yielded = 0
        self.membership_checks = 0

    def __iter__(self):
        self.iterations += 1
        for identifier in range(1, self.size + 1):
            self.yielded += 1
            yield identifier

    def __contains__(self, identifier: object) -> bool:
        self.membership_checks += 1
        return isinstance(identifier, int) and 1 <= identifier <= self.size


def test_virtual_tree_never_eagerly_materialises_all_50k_entity_rows():
    ids = _CountingIds()

    visible = bounded_entity_ids(ids)

    assert len(visible) == TREE_ENTITY_ROW_LIMIT
    assert visible == list(range(1, TREE_ENTITY_ROW_LIMIT + 1))
    assert ids.iterations == 1
    assert ids.yielded == TREE_ENTITY_ROW_LIMIT


def test_virtual_tree_numeric_jump_uses_index_membership_without_a_scan():
    ids = _CountingIds()

    assert bounded_entity_ids(ids, "Point 49,999") == [49_999]
    assert bounded_entity_ids(ids, "entity 50,001") == []
    assert ids.membership_checks == 2
    assert ids.iterations == 0
    assert ids.yielded == 0


def test_virtual_tree_skips_collapsed_generated_ids_without_materializing_rest():
    ids = _CountingIds()
    generated = frozenset(range(1, 49_991))

    visible = bounded_unowned_entity_ids(ids, generated, limit=5)

    assert visible == [49_991, 49_992, 49_993, 49_994, 49_995]
    assert ids.iterations == 1
    assert ids.yielded == 49_995


def test_virtual_tree_numeric_jump_does_not_expose_generated_topology():
    ids = _CountingIds()

    assert bounded_unowned_entity_ids(ids, frozenset({49_999}), "Point 49,999") == []
    assert ids.membership_checks == 0
    assert ids.iterations == 0
