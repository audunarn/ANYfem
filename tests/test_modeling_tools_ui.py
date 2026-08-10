"""Focused Geometry Details-panel coverage for the owner modeling commands."""

from __future__ import annotations

import tkinter as tk

import pytest
from anygeometry import EntityRef

from anyfem import commands as cmd

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


@pytest.fixture
def app(root):
    from anyfem.ui.app import AnyFemApp

    widget = AnyFemApp(root)
    root.update()
    yield widget
    widget.destroy()
    root.update()


def _plate(app) -> int:
    points = [
        app.run(cmd.AddPoint(x, y))
        for x, y in ((0, 0), (4, 0), (4, 3), (0, 3))
    ]
    return app.run(cmd.AddPlate(points))


def test_details_panel_copies_and_measures_through_public_commands(app, root):
    start = app.run(cmd.AddPoint(0, 0))
    end = app.run(cmd.AddPoint(2, 0))
    edge = app.run(cmd.AddLine(start, end))
    app.selection.set_mode("edge")
    app.selection.select(EntityRef("edge", edge))
    panel = app.panels["Geometry"]
    for variable, value in zip(panel._copy_offset, ("0", "1", "0")):
        variable.set(value)

    panel.guarded(panel._copy_selected)()
    root.update()
    assert len(app.project.geometry.edges) == 2
    assert app.project.geometry.features.records[-1].kind == "geometry.copy"
    assert app.selection.items != [EntityRef("edge", edge)]

    panel._measurement.set("Length")
    panel.guarded(panel._measure)()
    root.update()
    assert "length: 2" in app._status.cget("text")


def test_details_panel_creates_an_editable_structural_generator(app, root):
    panel = app.panels["Geometry"]
    panel._generator_type.set("Cylinder")
    panel._generator_length.set("2")
    panel._generator_radius_start.set("1")
    panel._generator_segments.set("8")
    panel._generator_longitudinal.set("")
    panel._generator_transverse.set("")

    panel.guarded(panel._add_generator)()
    root.update()
    feature = app.project.geometry.features.records[-1]
    assert feature.kind == "generator.cylinder"
    assert feature.state == "ok"
    assert app.project.geometry.group("shell")
    assert "editable feature" in app._status.cget("text")


def test_details_panel_keeps_neutral_trim_separate_from_mesh_decomposition(app, root):
    face = _plate(app)
    app.selection.set_mode("face")
    app.selection.select(EntityRef("face", face))
    panel = app.panels["Geometry"]
    for variable, value in zip(panel._trim_centre, ("2", "1.5", "0")):
        variable.set(value)
    panel._trim_radius.set("0.5")

    panel.guarded(panel._trim_hole)()
    root.update()
    assert list(app.project.geometry.faces) == [face]
    assert len(app.project.geometry.faces[face].holes) == 1
    assert "not decomposed" in app._status.cget("text")

