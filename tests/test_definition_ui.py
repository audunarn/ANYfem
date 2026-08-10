"""Small real-Tk acceptance test for the persistent Definitions task."""

from __future__ import annotations

import tkinter as tk

import pytest

from anyfem import commands as cmd
from anygeometry.entities import EntityRef

pytest.importorskip("anytk3d", reason="the viewport needs ANYfem[gui]")


@pytest.fixture(scope="module")
def root():
    try:
        window = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available for tkinter")
    window.geometry("1200x800+40+40")
    window.update()
    yield window
    window.destroy()


def test_definitions_details_creates_and_replays_named_region(root):
    from anyfem.ui.app import AnyFemApp

    app = AnyFemApp(root)
    root.update()
    try:
        assert "Definitions" in app.panels
        panel = app.panels["Definitions"]
        app.details.select("Definitions")

        point = app.run(cmd.AddPoint(0, 0, 0))
        app.selection.set_mode("vertex")
        app.selection.select(EntityRef("vertex", point))
        panel._region_name.set("Inspection points")
        panel._create_region()
        root.update()

        regions = tuple(item for item in app.project.regions if not item.hidden)
        assert [item.name for item in regions] == ["Inspection points"]
        region_id = regions[0].id
        assert app.tree.tree.exists(f"region:{region_id}")
        assert app.commands.undo_label == "create region"

        app.undo()
        assert region_id not in app.project.regions
        app.redo()
        assert app.project.regions[region_id].name == "Inspection points"
    finally:
        app.destroy()
        root.update()
