"""Headless contracts for GUI work that must scale with model size."""

from __future__ import annotations

from anyfem import Project
from anyfem.commands import AddPoint, CommandStack
from anyfem.ui.app import AnyFemApp


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

