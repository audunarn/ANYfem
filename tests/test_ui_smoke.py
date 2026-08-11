"""Application smoke tests.

These drive the real window: build geometry through the same commands the
buttons use, mesh it, solve it on the worker thread, and show the results.
Skipped when no display is available.

One module-scoped root is used throughout; creating and destroying Tk roots
per test is unreliable on Windows.
"""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk
from pathlib import Path

import numpy as np
import pytest

from anyfem import commands as cmd
from anyfem.geometry.entities import EntityRef
from anyfem.model.attributes import Support

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


def build_plate(app, width=2.0, height=1.0):
    points = [
        app.run(cmd.AddPoint(x, y))
        for x, y in ((0, 0), (width, 0), (width, height), (0, height))
    ]
    face = app.run(cmd.AddPlate(points))
    app.run(cmd.AssignPlate(face, "plate"))
    return points, face


def test_plate_sections_automatically_create_thickness_qualified_dnv_materials(
    app, root
):
    sections = app.panels["Sections"]
    sections._grade.set("S355")
    sections._auto_dnv_plate.set(True)

    sections._plate_name.set("deck 10")
    sections._plate_thickness.set("10")
    sections._add_plate_section()
    root.update()
    thin_name = app.project.plate_sections["deck 10"].material

    sections._plate_name.set("deck 20")
    sections._plate_thickness.set("20")
    sections._add_plate_section()
    root.update()
    thick_name = app.project.plate_sections["deck 20"].material

    assert thin_name == "S355-DNV-C208-t10mm-NL"
    assert thick_name == "S355-DNV-C208-t20mm-NL"
    assert app.project.materials[thin_name].hardening["thickness"] == pytest.approx(
        0.010
    )
    assert app.project.materials[thick_name].hardening["thickness"] == pytest.approx(
        0.020
    )

    material_count = len(app.project.materials)
    sections._plate_name.set("bulkhead 10")
    sections._plate_thickness.set("10")
    sections._add_plate_section()
    root.update()
    assert len(app.project.materials) == material_count
    assert app.project.plate_sections["bulkhead 10"].material == thin_name


def test_custom_material_name_is_separate_from_readonly_dnv_grade(app, root):
    sections = app.panels["Sections"]
    sections._material_name.set("S355_NL")
    sections._grade.set("S355")
    sections._grade_thickness.set("10")

    sections._add_material()
    root.update()

    material = app.project.materials["S355_NL"]
    assert str(sections._grade_box.cget("state")) == "readonly"
    assert material.hardening["grade"] == "S355"
    assert material.hardening["thickness"] == pytest.approx(0.010)


def test_assigning_a_new_plate_section_replaces_the_existing_one(app, root):
    _points, face = build_plate(app)
    sections = app.panels["Sections"]
    sections._grade.set("S355")
    sections._auto_dnv_plate.set(True)
    sections._plate_name.set("nonlinear plate")
    sections._plate_thickness.set("20")
    sections._add_plate_section()
    app.selection.set_mode("face")
    app.selection.select(EntityRef("face", face))

    sections._assign_plate()
    root.update()

    assert app.project.face_sections == {face: "nonlinear plate"}
    assert len(app.project.section_assignments) == 1
    assert app.project.resolve_section_assignments() == ()
    app.undo()
    assert app.project.face_sections == {face: "plate"}


def test_assigning_the_edited_plate_definition_reaches_the_solver_material(app, root):
    from types import SimpleNamespace

    from anyfem.solve.build import build_fe_model

    _points, face = build_plate(app)
    original_id = app.project.plate_sections["plate"].id
    sections = app.panels["Sections"]
    sections._grade.set("S355")
    sections._auto_dnv_plate.set(True)
    sections._plate_name.set("plate")
    sections._plate_thickness.set("10")
    app.selection.set_mode("face")
    app.selection.select(EntityRef("face", face))

    sections._assign_plate()
    app.generate_mesh(0.5)
    root.update()
    built = build_fe_model(
        app.project,
        app.mesh,
        load_case=None,
        require_loads=False,
        require_supports=False,
    )

    definition = app.project.plate_sections["plate"]
    assert definition.id == original_id
    assert definition.material == "S355-DNV-C208-t10mm-NL"
    assert app.project.materials[definition.material].hardening["kind"] == "dnv_c208"
    shell = next(
        element
        for element in built.fe_model.mesh.elements.values()
        if hasattr(element, "thickness")
    )
    assert shell.material_name == definition.material
    assert built.fe_model.get_material(shell.material_name).hardening_curve is not None
    constitutive = app.panels["Results"]._constitutive_summary(
        SimpleNamespace(
            built=built,
            raw_result=SimpleNamespace(
                element_states={shell.element_id: {"alpha": [0.0, 0.002]}}
            ),
        )
    )
    assert "NONLINEAR PLASTICITY ACTIVE" in constitutive
    assert "yielded elements 1/1" in constitutive

    sections.refresh()
    sections._material_choice.set(definition.material)
    sections._refresh_material_details()
    details = sections._material_details.cget("text")
    assert "NONLINEAR PLASTICITY ACTIVE" in details
    assert "product thickness = 10 mm" in details
    usage = sections._section_usage.item(f"plate:{definition.id}", "values")
    assert "Plates 1" in usage[2]
    solve = app.panels["Solve"]
    solve._analysis.set("Nonlinear static")
    solve.refresh()
    assert "Shell plasticity active" in solve._material_response.cget("text")

    from anyfem.post.results import NonlinearSolution

    raw = SimpleNamespace(
        element_states={shell.element_id: {"alpha": np.array([0.0, 0.002])}}
    )
    app.solution = NonlinearSolution(
        displacements=np.zeros(built.fe_model.mesh.dof_manager.total_dofs),
        built=built,
        raw_result=raw,
    )
    results = app.panels["Results"]
    results.refresh()
    assert "equivalent_plastic_strain" in tuple(
        results._field_box.cget("values")
    )
    results._component.set("equivalent_plastic_strain")
    app.show_results()
    assert app.viewport._scene.legend["title"] == (
        "Equivalent plastic strain (PEEQ)"
    )


def test_point_markers_support_click_shift_click_and_box_selection(app, root):
    """The retained point batch must behave like selectable CAD vertices."""

    from anytk3d import Point3D

    first = app.run(cmd.AddPoint(0.0, 0.0, 0.0))
    second = app.run(cmd.AddPoint(1.0, 0.0, 0.0))
    third = app.run(cmd.AddPoint(1.0, 1.0, 0.0))
    fourth = app.run(cmd.AddPoint(0.0, 1.0, 0.0))
    app.run(cmd.AddPlate((first, second, third, fourth)))
    app.selection_strip.set_context("vertex")
    app.selection_strip.tool.set("Single")
    app.selection_strip._apply_canvas()
    app.show_geometry(reset_view=True)
    root.update()

    # A previous click-construction task must not invisibly retain LMB after
    # the engineer explicitly chooses Point selection.
    geometry_panel = app.panels["Geometry"]
    geometry_panel._construction_mode.set("Point")
    geometry_panel._start_construction()
    assert app.viewport.construction_active
    geometry_panel._mode.set("vertex")
    geometry_panel._change_mode()
    assert not app.viewport.construction_active
    assert "selection is active" in app._status.cget("text")

    canvas = app.viewport.canvas
    inner = canvas.canvas

    def screen(x):
        projected = canvas.camera.project_point(
            Point3D(x, 0.0, 0.0), canvas._plot_width(), canvas.height
        )
        assert projected is not None
        return round(projected[0]), round(projected[1])

    x1, y1 = screen(0.0)
    x2, y2 = screen(1.0)
    inner.event_generate("<ButtonPress-1>", x=x1, y=y1)
    root.update()
    assert app.selection.ordered_items == (EntityRef("vertex", first),)
    inner.event_generate("<ButtonRelease-1>", x=x1, y=y1)
    root.update()
    assert app.selection.ordered_items == (EntityRef("vertex", first),)

    inner.event_generate("<ButtonPress-1>", x=x2, y=y2, state=0x0001)
    root.update()
    assert app.selection.ordered_items == (
        EntityRef("vertex", first),
        EntityRef("vertex", second),
    )
    inner.event_generate("<ButtonRelease-1>", x=x2, y=y2, state=0x0001)
    root.update()
    assert app.selection.ordered_items == (
        EntityRef("vertex", first),
        EntityRef("vertex", second),
    )

    app.selection.clear()
    app.selection_strip.tool.set("Box")
    app.selection_strip.depth.set("Visible")
    app.selection_strip._apply_canvas()
    inner.event_generate("<ButtonPress-1>", x=x1 - 10, y=y1 - 10)
    root.update()
    inner.event_generate("<B1-Motion>", x=x1 + 10, y=y1 + 10)
    root.update()
    inner.event_generate("<ButtonRelease-1>", x=x1 + 10, y=y1 + 10)
    root.update()
    assert app.selection.ordered_items == (EntityRef("vertex", first),)


def test_large_visible_box_selects_standalone_points(app, root):
    """A window enclosing unconnected point markers selects every point."""

    from anytk3d import Point3D

    first = app.run(cmd.AddPoint(0.0, 0.0, 0.0))
    second = app.run(cmd.AddPoint(2.0, 0.0, 0.0))
    app.selection_strip.set_context("vertex")
    app.selection_strip.tool.set("Box")
    app.selection_strip.depth.set("Visible")
    app.selection_strip.operation.set("Replace")
    app.selection_strip._apply_canvas()
    app.show_geometry(reset_view=True)
    root.update()

    canvas = app.viewport.canvas
    inner = canvas.canvas
    projected = [
        canvas.camera.project_point(point, canvas._plot_width(), canvas.height)
        for point in (Point3D(0.0, 0.0, 0.0), Point3D(2.0, 0.0, 0.0))
    ]
    assert all(point is not None for point in projected)
    xs = [round(point[0]) for point in projected if point is not None]
    ys = [round(point[1]) for point in projected if point is not None]
    start = min(xs) - 100, min(ys) - 100
    end = max(xs) + 100, max(ys) + 100

    inner.event_generate("<ButtonPress-1>", x=start[0], y=start[1])
    root.update()
    inner.event_generate("<B1-Motion>", x=end[0], y=end[1])
    root.update()
    inner.event_generate("<ButtonRelease-1>", x=end[0], y=end[1])
    root.update()

    assert app.selection.ordered_items == (
        EntityRef("vertex", first),
        EntityRef("vertex", second),
    )


def test_workplane_click_construction_defers_until_apply_and_escape_cancels(app, root):
    panel = app.panels["Geometry"]
    panel._construction_mode.set("Line")
    panel._workplane_grid.set("250 mm")
    panel._workplane_tolerance.set("20 mm")
    panel._start_construction()

    task = app.viewport.construction_task
    assert task is not None
    task.add_plane_coordinates(0.0, 0.0, app.session.active_workplane.resolve(
        app.project.coordinate_systems
    ))
    task.add_plane_coordinates(1.0, 0.0, app.session.active_workplane.resolve(
        app.project.coordinate_systems
    ))
    assert not app.project.geometry.vertices
    panel._apply_construction()
    assert len(app.project.geometry.vertices) == 2
    assert len(app.project.geometry.edges) == 1

    app.undo()
    assert not app.project.geometry.vertices
    panel._construction_mode.set("Point")
    panel._start_construction()
    app.viewport.construction_task.add((2.0, 3.0, 0.0))
    app.viewport._handle_construction_escape()
    root.update()
    assert not app.project.geometry.vertices
    assert not app.viewport.construction_active


def support_and_load(app, face):
    for edge in app.project.geometry.edges:
        app.run(
            cmd.AddSupport(
                Support(f"s{edge}", EntityRef("edge", edge),
                        {"ux": 0.0, "uy": 0.0, "uz": 0.0})
            )
        )
    app.run(cmd.AddPressure(EntityRef("face", face), 10_000.0))


def wait_for_solution(app, root, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        root.update()
        if app.solution is not None:
            return app.solution
        time.sleep(0.01)
    raise AssertionError("the solve did not finish in time")


def wait_for_mesh(app, root, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        root.update()
        if app.mesh is not None and not app.mesh_task_manager.busy:
            return app.mesh
        time.sleep(0.01)
    raise AssertionError("the mesh did not finish in time")


# ----------------------------------------------------------------------
def test_app_opens_with_a_usable_default_project(app):
    assert app.project.materials
    assert app.project.plate_sections
    assert app.selection.mode == "vertex"
    assert app.mesh is None


def test_building_geometry_updates_the_view_and_tree(app, root):
    _points, face = build_plate(app)
    root.update()

    assert face in app.project.geometry.faces
    assert app.tree.tree.exists(f"ent_face{face}")
    assert app._view_mode == "geometry"


def test_full_workflow_geometry_to_results(app, root):
    _points, face = build_plate(app)
    support_and_load(app, face)

    app.generate_mesh(0.25)
    root.update()
    assert app.mesh is not None
    assert app._view_mode == "mesh"

    app.solve()
    solution = wait_for_solution(app, root)
    assert solution.max_translation()[1] > 0.0
    assert app._view_mode == "results"


def test_model_and_load_bc_navigation_restore_geometry_scoping(app, root):
    """A mesh display must never trap geometry-based assignment work."""

    build_plate(app)
    app.generate_mesh(0.5)
    app.selection.set_mode("element")
    root.update()
    assert app._view_mode == "mesh"

    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from descendants(child)

    model_button = next(
        widget
        for widget in descendants(app)
        if isinstance(widget, ttk.Button) and widget.cget("text") == "Model"
    )
    model_button.invoke()
    root.update()
    assert app.details.current() == "Geometry"
    assert app._view_mode == "geometry"
    assert app.selection.domain.value == "geometry"

    app.show_mesh()
    app.selection.set_mode("element")
    app.details.select("Loads & BC")
    root.update()
    assert app._view_mode == "geometry"
    assert app.selection.mode == "edge"
    assert "select points, lines or plates" in app._status.cget("text")

    loads = app.panels["Loads & BC"]
    loads._scope_geometry("face")
    assert app._view_mode == "geometry"
    assert app.selection.mode == "face"


def test_a_geometry_change_invalidates_the_mesh_and_result(app, root):
    _points, face = build_plate(app)
    support_and_load(app, face)
    app.generate_mesh(0.5)
    app.solve()
    wait_for_solution(app, root)

    app.undo()
    root.update()
    assert app.mesh is None
    assert app.solution is None
    assert app._view_mode == "geometry"


def test_selection_drives_the_viewport_highlight(app, root):
    _points, face = build_plate(app)
    app.selection.set_mode("face")
    app.selection.select(EntityRef("face", face))
    root.update()

    assert app.viewport.canvas.highlighted_tags() == {f"ent_face{face}"}


def test_clicking_the_model_selects_it(app, root):
    _points, face = build_plate(app)
    app.selection.set_mode("face")
    app.viewport.fit()
    root.update()

    canvas = app.viewport.canvas.canvas
    width = canvas.winfo_width() // 2
    height = canvas.winfo_height() // 2
    canvas.event_generate("<ButtonPress-1>", x=width, y=height)
    canvas.event_generate("<ButtonRelease-1>", x=width, y=height)
    root.update()

    assert app.selection.items == [EntityRef("face", face)]


def test_tree_selection_and_viewport_selection_agree(app, root):
    _points, face = build_plate(app)
    app.selection.set_mode("face")
    app.selection.select(EntityRef("face", face))
    root.update()

    assert app.tree.tree.selection() == (f"ent_face{face}",)


def test_panels_report_errors_instead_of_raising(app, root):
    """A bad entry becomes a status message, not a traceback."""

    panel = app.panels["Mesh"]
    panel._size.set("not a number")
    panel.guarded(panel._generate)()
    root.update()

    assert "must be a number" in app._status.cget("text")


def test_solving_without_a_mesh_is_reported(app, root):
    panel = app.panels["Solve"]
    panel.guarded(panel._solve)()
    root.update()
    assert "mesh" in app._status.cget("text").lower()


def test_undo_and_redo_buttons_track_the_stack(app, root):
    assert str(app._undo_button.cget("state")) == "disabled"

    app.run(cmd.AddPoint(0.0, 0.0))
    root.update()
    assert str(app._undo_button.cget("state")) == "normal"
    assert "add point" in app._undo_button.cget("text")

    app.undo()
    root.update()
    assert str(app._redo_button.cget("state")) == "normal"


def test_extrude_from_the_geometry_panel(app, root):
    start = app.run(cmd.AddPoint(0.0, 0.0))
    end = app.run(cmd.AddPoint(1.0, 0.0))
    edge = app.run(cmd.AddLine(start, end))

    app.selection.set_mode("edge")
    app.selection.select(EntityRef("edge", edge))
    panel = app.panels["Geometry"]
    for variable, value in zip(panel._extrude, ("0", "0", "2")):
        variable.set(value)
    panel.guarded(panel._extrude_lines)()
    root.update()

    assert len(app.project.geometry.faces) == 1


def test_seeding_pins_reach_the_mesher(app, root):
    _points, face = build_plate(app)
    edges = sorted(app.project.geometry.edges)

    app.selection.set_mode("edge")
    app.selection.select(EntityRef("edge", edges[0]))
    panel = app.panels["Mesh"]
    panel._divisions.set("7")
    panel.guarded(panel._pin)()
    root.update()

    app.generate_mesh(0.5)
    assert app.mesh.seeding[edges[0]] == 7


def test_views_switch_without_error(app, root):
    build_plate(app)
    for name in ("iso", "top", "front", "side"):
        app.viewport.set_view(name)
        root.update()
    with pytest.raises(ValueError, match="unknown view"):
        app.viewport.set_view("nowhere")


# ----------------------------------------------------------------------
# decomposition from the Geometry panel
# ----------------------------------------------------------------------
def test_punch_a_hole_from_the_panel(app, root):
    _points, face = build_plate(app, 4.0, 3.0)
    app.selection.set_mode("face")
    app.selection.select(EntityRef("face", face))

    panel = app.panels["Geometry"]
    for variable, value in zip(panel._hole_centre, ("2", "1.5", "0")):
        variable.set(value)
    panel._hole_radius.set("0.6")
    panel.guarded(panel._punch)()
    root.update()

    assert len(app.project.geometry.faces) == 4
    assert "hole punched" in app._status.cget("text")


def test_strip_a_plate_from_the_panel(app, root):
    _points, face = build_plate(app, 6.0, 2.0)
    app.selection.set_mode("face")
    app.selection.select(EntityRef("face", face))

    panel = app.panels["Geometry"]
    panel._axis.set("0")
    panel._strips.set("3")
    panel.guarded(panel._strip_plates)()
    root.update()

    assert len(app.project.geometry.faces) == 3


def test_revolve_from_the_panel(app, root):
    start = app.run(cmd.AddPoint(2.0, 0.0, 0.0))
    end = app.run(cmd.AddPoint(2.0, 0.0, 3.0))
    edge = app.run(cmd.AddLine(start, end))
    app.selection.set_mode("edge")
    app.selection.select(EntityRef("edge", edge))

    panel = app.panels["Geometry"]
    panel._angle.set("360")
    panel.guarded(panel._revolve_lines)()
    root.update()

    assert len(app.project.geometry.faces) == 4


def test_a_refused_operation_reaches_the_status_bar(app, root):
    """A modelling refusal must not become a traceback."""

    _points, face = build_plate(app, 4.0, 3.0)
    app.selection.set_mode("face")
    app.selection.select(EntityRef("face", face))

    panel = app.panels["Geometry"]
    for variable, value in zip(panel._hole_centre, ("2", "1.5", "0")):
        variable.set(value)
    panel._hole_radius.set("50")          # far bigger than the plate
    panel.guarded(panel._punch)()
    root.update()

    assert "does not fit" in app._status.cget("text")
    assert len(app.project.geometry.faces) == 1


def test_mappability_check_reports_from_the_panel(app, root):
    _points, face = build_plate(app)
    app.selection.set_mode("face")
    app.selection.select(EntityRef("face", face))

    panel = app.panels["Geometry"]
    panel.guarded(panel._check)()
    root.update()
    assert "all mappable" in app._status.cget("text")


def test_decomposition_from_the_panel_is_undoable(app, root):
    _points, face = build_plate(app, 6.0, 2.0)
    app.selection.set_mode("face")
    app.selection.select(EntityRef("face", face))

    panel = app.panels["Geometry"]
    panel._strips.set("3")
    panel.guarded(panel._strip_plates)()
    root.update()
    assert len(app.project.geometry.faces) == 3

    app.undo()
    root.update()
    assert list(app.project.geometry.faces) == [face]


# ----------------------------------------------------------------------
# load cases and combinations from the panels
# ----------------------------------------------------------------------
def supported_plate(app):
    points, face = build_plate(app, 4.0, 2.0)
    app.selection.set_mode("edge")
    for edge in app.project.geometry.edges:
        app.selection.select(EntityRef("edge", edge), extend=True)
    loads = app.panels["Loads & BC"]
    loads.guarded(loads._add_support)()
    app.selection.set_mode("face")
    app.selection.select(EntityRef("face", face))
    return points, face, loads


def test_the_panels_are_split_into_sections_and_loads(app):
    assert "Sections" in app.panels
    assert "Loads & BC" in app.panels


def test_solve_details_page_packs_without_shadowing_tk_options(app, root):
    """Solver form state must not replace tkinter.Misc._options()."""

    app.details.select("Solve")
    root.update()

    panel = app.panels["Solve"]
    assert app.details.current() == "Solve"
    assert panel.winfo_ismapped()
    assert callable(panel._options)
    assert "modes" in panel._analysis_options


def test_load_panel_explains_the_viewport_symbols(app):
    panel = app.panels["Loads & BC"]
    key = next(
        child for child in panel.winfo_children()
        if child.cget("text") == "Viewport key"
    )
    text = " ".join(child.cget("text") for child in key.winfo_children())
    for meaning in (
        "pressure", "force", "moment", "translational restraint",
        "rotational restraint", "mass", "acceleration",
    ):
        assert meaning in text


def test_mesh_panel_opens_anymesher_as_a_standalone_tool(app, monkeypatch):
    import anymesher.gui

    call = {}

    def fake_open(parent, **options):
        call.update(parent=parent, options=options)

    monkeypatch.setattr(anymesher.gui, "open_mesher", fake_open)
    app.panels["Mesh"]._open_mesher()

    assert call["parent"] is app.winfo_toplevel()
    assert call["options"] == {}
    assert "standalone ANYmesher" in app._status.cget("text")


def test_creating_a_case_and_a_combination_from_the_panel(app, root):
    _points, _face, loads = supported_plate(app)
    loads.guarded(loads._add_pressure)()

    loads._case.set("live")
    loads.guarded(loads._add_case)()
    loads._pressure.set("4000")
    loads.guarded(loads._add_pressure)()

    loads._combination_name.set("ULS")
    loads._factors.set("default 1.2 live 1.5")
    loads.guarded(loads._add_combination)()
    root.update()

    assert sorted(app.project.load_cases) == ["default", "live"]
    assert "ULS" in app.project.combinations
    assert app.project.combinations["ULS"].factors == {"default": 1.2, "live": 1.5}


def test_solving_a_combination_from_the_solve_panel(app, root):
    _points, _face, loads = supported_plate(app)
    loads.guarded(loads._add_pressure)()
    loads._combination_name.set("ULS")
    loads._factors.set("default 1.4")
    loads.guarded(loads._add_combination)()

    app.generate_mesh(0.5)
    solve = app.panels["Solve"]
    solve.refresh()
    solve._target.set("combination: ULS")
    solve.guarded(solve._solve)()
    solution = wait_for_solution(app, root)

    assert solution.built.combination == "ULS"
    assert solution.max_translation()[1] > 0.0


def test_a_bad_combination_string_is_reported(app, root):
    _points, _face, loads = supported_plate(app)
    loads._combination_name.set("bad")
    loads._factors.set("default")
    loads.guarded(loads._add_combination)()
    root.update()
    assert "pairs of case name and factor" in app._status.cget("text")


def test_prescribed_displacement_from_the_panel(app, root):
    points, _face = build_plate(app, 2.0, 1.0)
    app.selection.set_mode("vertex")
    app.selection.select(EntityRef("vertex", points[0]))

    loads = app.panels["Loads & BC"]
    for dof, held in loads._dofs.items():
        held.set(dof == "uz")
    loads._prescribed.set("5")
    loads.guarded(loads._add_support)()
    root.update()

    assert app.project.supports[0].constraints == {"uz": 0.005}
    assert "prescribed" in app._status.cget("text")


def test_mass_and_acceleration_from_the_panel(app, root):
    _points, _face, loads = supported_plate(app)
    loads._mass.set("250")
    loads.guarded(loads._add_mass)()
    loads.guarded(loads._set_acceleration)()
    root.update()

    assert app.project.masses[0].value == 250.0
    assert app.project.load_case().gravity is not None


def test_an_imperfection_from_the_sections_panel(app, root):
    from anyfem.ui.scene import COLOR_IMPERFECTION

    _points, face = build_plate(app, 2.0, 1.0)
    app.selection.set_mode("face")
    app.selection.select(EntityRef("face", face))

    sections = app.panels["Sections"]
    sections._imperfection_amplitude.set("4")
    sections.guarded(sections._add_imperfection)()
    root.update()

    assert len(app.project.imperfections) == 1
    assert app.project.imperfections[0].amplitude == pytest.approx(0.004)
    imperfection = app.project.imperfections[0]
    key = f"imperfection:{imperfection.id}"
    assert app.tree.tree.exists(key)
    assert "4 mm" in app.tree.tree.item(key, "text")
    assert any(
        line.color == COLOR_IMPERFECTION for line in app.viewport._scene.lines
    )

    app._tree_action("edit", (key,))
    root.update()
    assert sections._imperfection_amplitude.get() == "4"
    sections._imperfection_amplitude.set("6")
    sections._waves.set("2 1")
    sections._add_imperfection()
    root.update()
    assert len(app.project.imperfections) == 1
    assert app.project.imperfections[0].id == imperfection.id
    assert app.project.imperfections[0].amplitude == pytest.approx(0.006)
    assert app.project.imperfections[0].waves == (2, 1)

    app._tree_action("delete", (key,))
    root.update()
    assert not app.project.imperfections
    app.undo()
    root.update()
    assert app.project.imperfections[0].id == imperfection.id


def test_the_overlay_can_be_toggled(app, root):
    _points, _face, loads = supported_plate(app)
    loads.guarded(loads._add_pressure)()
    root.update()

    app._show_attributes.set(False)
    app.refresh_views()
    root.update()
    without = len(app.viewport._scene.arrows)

    app._show_attributes.set(True)
    app.refresh_views()
    root.update()
    with_overlay = len(app.viewport._scene.arrows)

    assert without == 0
    assert with_overlay > 0


def test_the_overlay_does_not_break_picking(app, root):
    """A pressure arrow sits over the plate; the plate must still be pickable."""

    _points, face, loads = supported_plate(app)
    loads.guarded(loads._add_pressure)()
    app.viewport.fit()
    root.update()

    canvas = app.viewport.canvas.canvas
    x = canvas.winfo_width() // 2
    y = canvas.winfo_height() // 2
    assert app.viewport.canvas.pick_at(x, y) == f"ent_face{face}"


def test_load_and_mass_tree_rows_select_their_geometry(app, root):
    points, face = build_plate(app)
    app.run(cmd.AddPressure(EntityRef("face", face), 10_000.0))
    app.run(
        cmd.AddSurfaceTraction(
            EntityRef("face", face), (100.0, 0.0, 0.0)
        )
    )
    app.run(cmd.AddMass(EntityRef("vertex", points[0]), 25.0, name="payload"))
    root.update()

    assert app.tree.tree.item("loads", "text") == "Loads (2)"
    case_row = app.tree.tree.get_children("loads")[0]
    assert any(
        ":traction:" in row
        for row in app.tree.tree.get_children(case_row)
    )
    pressure_row = next(
        row
        for row in app.tree.tree.get_children(case_row)
        if ":pressure:" in row
    )
    app.tree.tree.selection_set(pressure_row)
    app.tree._on_tree_select(None)
    assert app.selection.items == [EntityRef("face", face)]

    mass_row = app.tree.tree.get_children("masses")[0]
    app.tree.tree.selection_set(mass_row)
    app.tree._on_tree_select(None)
    assert app.selection.items == [EntityRef("vertex", points[0])]


def test_results_view_respects_the_loads_and_bc_toggle(app, root):
    _points, face = build_plate(app)
    support_and_load(app, face)
    app.generate_mesh(0.3)
    app.solve()
    wait_for_solution(app, root)

    app._show_attributes.set(True)
    app.show_results()
    with_overlay = app.viewport._scene
    assert with_overlay.arrows
    assert with_overlay.points

    app._show_attributes.set(False)
    app.show_results()
    without_overlay = app.viewport._scene
    assert not without_overlay.arrows
    assert not without_overlay.points


def test_mesh_panel_applies_element_order_through_the_real_stack(app, root):
    build_plate(app)
    panel = app.panels["Mesh"]
    panel._order.set("quadratic")
    panel._size.set("0.5")

    panel.guarded(panel._generate)()
    mesh = wait_for_mesh(app, root)

    assert app.project.element_order == "quadratic"
    assert mesh.is_quadratic


# ----------------------------------------------------------------------
# analyses from the Solve panel
# ----------------------------------------------------------------------
def solvable_plate(app):
    points, face = build_plate(app, 1.0, 1.0)
    support_and_load(app, face)
    app.generate_mesh(0.2)
    return points, face


def run_analysis(app, root, name, **options):
    app.solution = None
    solve = app.panels["Solve"]
    solve._analysis.set(name)
    solve._show_options()
    for key, value in options.items():
        solve._analysis_options[key].set(value)
    solve.guarded(solve._solve)()
    return wait_for_solution(app, root, timeout=60.0)


def test_every_analysis_runs_from_the_panel(app, root):
    solvable_plate(app)

    for name, options in (
        ("Linear static", {}),
        ("Modal", {"modes": "3"}),
        ("Buckling", {"modes": "2"}),
        ("Nonlinear static", {"steps": "4"}),
        ("Transient", {"dt": "0.0005", "t_end": "0.004"}),
    ):
        solution = run_analysis(app, root, name, **options)
        assert solution is not None, name
        assert app._view_mode == "results"


def test_live_job_log_and_stale_artifact_field_survive_result_transition(app, root):
    solvable_plate(app)
    results = app.panels["Results"]
    # Persisted result artifacts legitimately call their vector field
    # "displacement". Reproduce switching from that selection to a live solve.
    results._component.set("displacement")

    solution = run_analysis(app, root, "Nonlinear static", steps="2")

    assert solution is not None
    assert results.field_name() == "magnitude"
    assert app._view_mode == "results"
    transcript = app.panels["Solve"]._report.get("1.0", "end")
    assert "queued" in transcript
    assert "building immutable model snapshot" in transcript
    assert "preflight passed" in transcript
    assert "completed" in transcript


def test_the_options_shown_follow_the_analysis(app, root):
    solve = app.panels["Solve"]

    # winfo_ismapped is False for every widget on an unselected notebook tab,
    # so ask the geometry manager instead: pack_forget clears it.
    def packed(name):
        return bool(solve._option_frames[name].winfo_manager())

    solve._analysis.set("Modal")
    solve._show_options()
    root.update()
    assert packed("modes")
    assert not packed("dt")

    solve._analysis.set("Transient")
    solve._show_options()
    root.update()
    assert packed("dt")
    assert not packed("modes")


def test_modes_can_be_browsed_in_the_results_panel(app, root):
    solvable_plate(app)
    solution = run_analysis(app, root, "Modal", modes="3")
    assert len(solution) == 3

    results = app.panels["Results"]
    assert app.shape_index == 0
    results.guarded(results._next)()
    root.update()
    assert app.shape_index == 1
    assert app.current_shape().label == "mode 2"

    results.guarded(results._previous)()
    root.update()
    assert app.shape_index == 0


def test_browsing_wraps_around(app, root):
    solvable_plate(app)
    run_analysis(app, root, "Modal", modes="2")
    results = app.panels["Results"]

    results.guarded(results._previous)()
    root.update()
    assert app.shape_index == 1


def test_animating_a_transient_walks_every_step(app, root):
    solvable_plate(app)
    solution = run_analysis(app, root, "Transient", dt="0.0005", t_end="0.003")
    results = app.panels["Results"]
    results._playback_fps.set("20")
    assert results.playback_fps() == 20.0
    assert results._playback_delay_ms() == 50

    results.guarded(results._animate)()
    root.update()
    # Playback yields between frames so the Tk event loop remains responsive.
    assert app.shape_index < len(solution) - 1
    while results._animation_after is not None:
        results.after_cancel(results._animation_after)
        results._animation_after = None
        results._animation_step()
    assert app.shape_index == len(solution) - 1


def test_animating_a_static_result_is_reported(app, root):
    solvable_plate(app)
    run_analysis(app, root, "Linear static")
    results = app.panels["Results"]
    results.guarded(results._animate)()
    root.update()
    assert "only one shape" in app._status.cget("text")


def test_progress_is_reported_while_solving(app, root):
    solvable_plate(app)
    run_analysis(app, root, "Nonlinear static", steps="4")
    # The report pane carries the finished summary.
    text = app.panels["Solve"]._report.get("1.0", "end")
    assert "nodes" in text
    assert "steps" in text


def test_the_view_label_names_the_shape_on_show(app, root):
    solvable_plate(app)
    run_analysis(app, root, "Modal", modes="2")
    root.update()
    assert "mode 1" in app._view_label.cget("text")


def test_an_unknown_analysis_is_refused(app, root):
    solvable_plate(app)
    with pytest.raises(ValueError, match="unknown analysis"):
        app.solve("Telepathy")


def test_solving_before_meshing_is_refused(app):
    build_plate(app)
    with pytest.raises(ValueError, match="mesh"):
        app.solve("Linear static")


# ----------------------------------------------------------------------
# postprocessing from the Results panel
# ----------------------------------------------------------------------
def solved_plate(app, root):
    points, face = build_plate(app, 1.0, 1.0)
    support_and_load(app, face)
    app.generate_mesh(0.2)
    app.solve("Linear static")
    wait_for_solution(app, root)
    return points, face


def test_the_field_picker_offers_stress(app):
    values = list(app.panels["Results"]._field_box.cget("values"))
    assert "von_mises" in values
    assert "magnitude" in values
    assert "top_xx" in values


def test_contouring_by_von_mises(app, root):
    solved_plate(app, root)
    results = app.panels["Results"]
    results._component.set("von_mises")
    results.guarded(results._show)()
    root.update()

    assert app.viewport._scene.legend["unit"] == "Pa"
    assert app.viewport._scene.legend["title"] == "von Mises"


def test_results_quick_mm_mpa_units_and_colormap(app, root):
    solved_plate(app, root)
    results = app.panels["Results"]
    results._component.set("von_mises")
    results._display_units.set("Engineering (mm / MPa)")
    results._colormap.set("Viridis")
    results.guarded(results._show)()
    root.update()

    legend = app.viewport._scene.legend
    assert legend["unit"] == "MPa"
    assert legend["levels"][-1] < 10_000.0
    assert legend["colors"][0] == "#440154"
    assert app.viewport.canvas._thickness_legend["colors"] == legend["colors"]


def test_manual_colour_range_from_the_panel(app, root):
    solved_plate(app, root)
    results = app.panels["Results"]
    results._component.set("von_mises")
    results._limits.set("0 3e7")
    results.guarded(results._show)()
    root.update()

    levels = app.viewport._scene.legend["levels"]
    assert levels[0] == pytest.approx(0.0)
    assert levels[-1] == pytest.approx(3.0e7)


def test_a_bad_colour_range_is_reported(app, root):
    solved_plate(app, root)
    results = app.panels["Results"]
    results._limits.set("nonsense")
    results.guarded(results._show)()
    root.update()
    assert "colour range" in app._status.cget("text")


def test_probing_from_the_panel(app, root):
    _points, face = solved_plate(app, root)
    app.selection.set_mode("face")
    app.selection.select(EntityRef("face", face))

    results = app.panels["Results"]
    results.guarded(results._probe)()
    root.update()

    text = results._readout.get("1.0", "end")
    assert "displacement" in text
    assert "von_mises" in text


def test_clicking_in_the_results_view_probes_automatically(app, root):
    _points, face = solved_plate(app, root)
    app.selection.set_mode("face")
    app.viewport.fit()
    root.update()

    canvas = app.viewport.canvas.canvas
    x = canvas.winfo_width() // 2
    y = canvas.winfo_height() // 2
    canvas.event_generate("<ButtonPress-1>", x=x, y=y)
    canvas.event_generate("<ButtonRelease-1>", x=x, y=y)
    root.update()

    assert app.selection.items == [EntityRef("face", face)]
    assert "displacement" in results_text(app)


def results_text(app):
    return app.panels["Results"]._readout.get("1.0", "end")


def test_along_line_from_the_panel(app, root):
    _points, _face = solved_plate(app, root)
    edges = sorted(app.project.geometry.edges)
    app.selection.set_mode("edge")
    app.selection.select(EntityRef("edge", edges[0]))

    results = app.panels["Results"]
    results._component.set("uz")
    results.guarded(results._along_line)()
    root.update()

    text = results_text(app)
    assert "uz along edge" in text
    assert "distance" in text


def test_along_line_needs_exactly_one_line(app, root):
    solved_plate(app, root)
    app.selection.set_mode("edge")
    for edge in sorted(app.project.geometry.edges)[:2]:
        app.selection.select(EntityRef("edge", edge), extend=True)

    results = app.panels["Results"]
    results.guarded(results._along_line)()
    root.update()
    assert "exactly 1" in app._status.cget("text")


def test_the_envelope_toggle_uses_every_shape(app, root):
    points, face = build_plate(app, 1.0, 1.0)
    support_and_load(app, face)
    app.generate_mesh(0.25)
    app.solve("Transient", dt=0.0005, t_end=0.004)
    solution = wait_for_solution(app, root)
    assert len(solution) > 1

    results = app.panels["Results"]
    results._envelope.set(True)
    results.guarded(results._show)()
    root.update()

    assert "envelope" in app.viewport._scene.legend["title"]


def test_probing_before_solving_is_reported(app, root):
    build_plate(app)
    app.selection.set_mode("face")
    app.selection.select(EntityRef("face", 1))
    results = app.panels["Results"]
    results.guarded(results._probe)()
    root.update()
    assert "run a solve first" in app._status.cget("text")


# ----------------------------------------------------------------------
# files
# ----------------------------------------------------------------------
def workspace_dir():
    import tempfile
    return tempfile.mkdtemp()


def test_save_new_and_open_round_trip(app, root):
    from pathlib import Path

    _points, face = build_plate(app, 2.0, 1.0)
    support_and_load(app, face)
    directory = Path(workspace_dir())

    app.save_project(path=str(directory / "model.anyfem"))
    root.update()
    assert app.path is not None
    assert "saved" in app._status.cget("text")

    app.new_project()
    root.update()
    assert not app.project.geometry.faces

    app.open_project(str(directory / "model.anyfem"))
    root.update()
    assert len(app.project.geometry.faces) == 1
    assert app.project.supports


def test_new_clears_the_mesh_and_result(app, root):
    _points, face = build_plate(app)
    support_and_load(app, face)
    app.generate_mesh(0.5)
    app.solve("Linear static")
    wait_for_solution(app, root)

    app.new_project()
    root.update()
    assert app.mesh is None
    assert app.solution is None


def test_exporting_a_deck_from_the_app(app, root):
    from pathlib import Path

    _points, face = build_plate(app)
    support_and_load(app, face)
    app.generate_mesh(0.5)
    directory = Path(workspace_dir())

    app.export_deck(path=str(directory / "deck.inp"))
    root.update()
    assert (directory / "deck.inp").exists()
    # The status says what a generated deck is, and is not.
    assert "handoff" in app._status.cget("text")


def test_exporting_a_deck_needs_a_mesh(app, root):
    build_plate(app)
    app.guarded(lambda: app.export_deck(path="x.inp"))()
    root.update()
    assert "mesh" in app._status.cget("text")


def test_importing_a_sesam_model_and_solving_it(app, root):
    from pathlib import Path

    from test_io import write_sesam_plate

    directory = Path(workspace_dir())
    app.import_sesam_model(str(write_sesam_plate(directory / "plate.FEM")))
    root.update()

    assert app.imported is not None
    assert app.mesh is not None
    assert app._view_mode == "mesh"
    assert "Geometry editing is off" in app._status.cget("text")

    group = app.imported.groups["group 1"]
    app.project.load_case().add_pressure(group, 20_000.0)
    app.solve("Linear static")
    solution = wait_for_solution(app, root)
    assert solution.max_translation()[1] > 0.0


def test_saving_an_imported_model_embeds_it_for_portable_reopen(app, root):
    from pathlib import Path

    from test_io import write_sesam_plate

    directory = Path(workspace_dir())
    app.import_sesam_model(str(write_sesam_plate(directory / "plate.FEM")))
    destination = directory / "x.anyfem"
    app.guarded(lambda: app.save_project(path=str(destination)))()
    root.update()
    assert "saved x.anyfem" in app._status.cget("text")
    app.open_project(str(destination))
    root.update()
    assert app.project.mesh_only
    assert app.imported is not None
    assert app.mesh is not None


def test_completed_result_saves_reopens_lazily_and_contours(app, root):
    from pathlib import Path

    solvable_plate(app)
    run_analysis(app, root, "Linear static")
    destination = Path(workspace_dir()) / "retained.anyfem"
    app.save_project(path=str(destination))
    root.update()

    job_id = app.active_job_id
    assert job_id is not None
    artifact_id = app.project.jobs[job_id].result_artifact_id
    assert artifact_id in app.project.artifacts

    app.new_project()
    app.open_project(str(destination))
    root.update()
    assert job_id in app.result_datasets
    dataset = app.result_datasets[job_id]
    assert "displacement" in dataset.field_keys

    results = app.panels["Results"]
    results._component.set("displacement")
    results.guarded(results._show)()
    root.update()
    assert app._view_mode == "results"


def test_opening_a_broken_file_is_reported(app, root):
    from pathlib import Path

    directory = Path(workspace_dir())
    bad = directory / "bad.anyfem"
    bad.write_text("{not json", encoding="utf-8")

    app.guarded(lambda: app.open_project(str(bad)))()
    root.update()
    assert "not valid JSON" in app._status.cget("text")


# ----------------------------------------------------------------------
# impact from the Solve panel
# ----------------------------------------------------------------------
def test_impact_runs_from_the_panel(app, root):
    points, face = build_plate(app, 1.0, 1.0)
    app.selection.set_mode("edge")
    for edge in app.project.geometry.edges:
        app.run(
            cmd.AddSupport(
                Support(f"s{edge}", EntityRef("edge", edge),
                        {"ux": 0.0, "uy": 0.0, "uz": 0.0})
            )
        )
    app.generate_mesh(0.25)

    solve = app.panels["Solve"]
    solve._analysis.set("Impact")
    solve._show_options()
    solve._analysis_options["mass"].set("200")
    solve._analysis_options["radius"].set("0.15")
    solve._analysis_options["speed"].set("4")
    solve._analysis_options["start"].set("0.5 0.5 0.6")
    solve.guarded(solve._solve)()

    solution = wait_for_solution(app, root, timeout=90.0)
    assert solution.touched()
    assert solution.peak_displacement > 0.0


def test_the_impact_options_are_the_ones_shown(app, root):
    solve = app.panels["Solve"]
    solve._analysis.set("Impact")
    solve._show_options()
    root.update()

    def packed(name):
        return bool(solve._option_frames[name].winfo_manager())

    assert packed("mass") and packed("radius") and packed("speed")
    assert not packed("modes")


def test_a_bad_vector_entry_is_reported(app, root):
    build_plate(app)
    solve = app.panels["Solve"]
    solve._analysis.set("Impact")
    solve._show_options()
    solve._analysis_options["start"].set("0 0")
    solve.guarded(solve._solve)()
    root.update()
    assert "three numbers" in app._status.cget("text")


def test_the_sphere_is_drawn_with_the_result(app, root):
    build_plate(app, 1.0, 1.0)
    for edge in app.project.geometry.edges:
        app.run(
            cmd.AddSupport(
                Support(f"s{edge}", EntityRef("edge", edge),
                        {"ux": 0.0, "uy": 0.0, "uz": 0.0})
            )
        )
    app.generate_mesh(0.25)
    solve = app.panels["Solve"]
    solve._analysis.set("Impact")
    solve._show_options()
    solve._analysis_options["start"].set("0.5 0.5 0.6")
    solve._analysis_options["speed"].set("3")
    solve.guarded(solve._solve)()
    wait_for_solution(app, root, timeout=90.0)
    root.update()

    assert len(app.viewport._scene.spheres) == 1


# ----------------------------------------------------------------------
# Phase 13: result import and the history plot
# ----------------------------------------------------------------------
def test_the_history_plot_follows_the_result(app, root):
    """A transient fills the plot; a linear static empties it again."""

    _points, face = build_plate(app)
    support_and_load(app, face)
    app.generate_mesh(0.5)
    root.update()
    panel = app.panels["Results"]
    assert panel.plot.series_names == []

    from anyfem.solve.run import solve_transient

    app.solution = solve_transient(
        app.project, mesh=app.mesh, dt=2.0e-4, t_end=0.004
    )
    app.refresh_panels()
    root.update()
    assert panel.plot.series_names
    assert panel.plot.canvas.find_all()

    from anyfem.solve.run import solve_linear_static

    app.solution = solve_linear_static(app.project, mesh=app.mesh)
    app.refresh_panels()
    root.update()
    assert panel.plot.series_names == []


def test_importing_results_needs_a_mesh(app, root):
    from anyfem.io.results import ImportedResults

    results = ImportedResults(
        source=Path("x.frd"), format="CalculiX",
        displacements={1: (0.0, 0.0, -1.0e-3)},
    )
    app.mesh = None
    with pytest.raises(ValueError, match="generate or import a mesh first"):
        app._attach_results(results)


def test_importing_a_result_shows_it(app, root):
    from anyfem.io.results import ImportedResults

    _points, face = build_plate(app)
    support_and_load(app, face)
    app.generate_mesh(0.5)
    root.update()
    built = app.built()
    manager = built.fe_model.mesh.dof_manager
    results = ImportedResults(
        source=Path("demo.frd"), format="CalculiX",
        displacements={
            node: (0.0, 0.0, -1.0e-3 * index)
            for index, node in enumerate(sorted(app.mesh.nodes), start=1)
        },
    )
    app._attach_results(results)
    root.update()

    assert app.solution is not None
    assert app.solution.covered == app.mesh.num_nodes
    # The field menu offers the file's components, not ANYfem's whole list.
    assert "rx" not in app.panels["Results"]._field_box.cget("values")
