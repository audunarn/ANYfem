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


def test_geometry_is_grouped_into_engineering_subtabs(app, root):
    panel = app.panels["Geometry"]
    app.details.select("Geometry")
    root.update()
    assert tuple(panel._geometry_tabs.tab(index, "text") for index in range(4)) == (
        "Guiding geometry",
        "Operations",
        "Plates",
        "Beams",
    )
    assert panel._geometry_tabs.tab("current", "text") == "Guiding geometry"

    panel._geometry_tabs.select(panel._geometry_pages["Plates"])
    root.update()
    assert panel._geometry_pages["Plates"].winfo_ismapped()
    assert not panel._geometry_pages["Guiding geometry"].winfo_ismapped()
    assert panel._generator_type.get() == "Plate"

    panel._geometry_tabs.select(panel._geometry_pages["Beams"])
    root.update()
    assert panel._geometry_pages["Beams"].winfo_ismapped()
    assert panel._beam_generator_type.get() == "Girder"


def test_plate_and_beam_generators_create_editable_features(app, root):
    panel = app.panels["Geometry"]
    panel._generator_type.set("Plate")
    panel.guarded(panel._add_generator)()
    panel._beam_generator_type.set("Stiffener")
    panel.guarded(panel._add_beam_generator)()
    root.update()

    kinds = [feature.kind for feature in app.project.geometry.features.records]
    assert kinds[-2:] == ["generator.plate", "generator.stiffener"]
    assert app.project.geometry.group("shell")
    assert app.project.geometry.group("stiffeners")


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
