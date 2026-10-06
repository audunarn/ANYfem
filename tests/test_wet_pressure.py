"""Wet surfaces and fluid pressure: equations, direct mappings, load-case series.

The loads are judged by what reaches the solver: the assembled load vector is
compared with hand calculations (hydrostatic resultants, signs against a known
side), not by checking that a solve runs.
"""

from __future__ import annotations

import numpy as np
import pytest
from anysolver import assemble_load_vector

from anyfem import Project, pinned, solve_linear_static, steel
from anyfem.model import ProjectError, WetPressure, WetSurface
from anyfem.solve import solve_linear_static_many
from anyfem.solve.build import build_fe_model

RHO_G = 1025.0 * 9.81


def _project(points, *, name="wet"):
    project = Project(name=name)
    project.add_material(steel("S355", 0.010))
    project.add_plate_section("plate", thickness=0.010, material="S355")
    geometry = project.geometry
    ids = geometry.add_points(points)
    edges = geometry.add_polyline(ids, close=True)
    face = geometry.add_face(edges)
    project.assign_plate(face, "plate")
    for edge in edges:
        project.add_support(pinned(project.edge(edge)))
    return project, face


def _total_force(built) -> np.ndarray:
    vector = assemble_load_vector(built.fe_model, built.load_case)
    if isinstance(vector, tuple):
        vector = vector[0]
    return np.array([float(vector[axis::6].sum()) for axis in range(3)])


def _wall():
    """A 2 m wide, 3 m tall vertical plate in the plane y = 0."""

    return _project([(0, 0, 0), (2, 0, 0), (2, 0, 3), (0, 0, 3)])


def _flat():
    """A 2 x 1 m horizontal plate in the plane z = 0."""

    return _project([(0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0)])


# ----------------------------------------------------------------------
# records
# ----------------------------------------------------------------------
def test_a_wet_pressure_needs_exactly_one_source():
    with pytest.raises(ValueError, match="exactly one"):
        WetPressure()
    with pytest.raises(ValueError, match="exactly one"):
        WetPressure(value=1.0, expression="1")
    with pytest.raises(ValueError, match="at least one entry"):
        WetPressure(mapping={})
    with pytest.raises(ValueError, match="integer ids"):
        WetPressure(mapping={"a": 1.0})
    with pytest.raises(ValueError, match="finite"):
        WetPressure(value=float("nan"))
    with pytest.raises(ValueError, match="mapping key"):
        WetPressure(mapping={1: 1.0}, mapping_key="node")


def test_wet_surface_records_are_validated():
    project, face = _flat()
    with pytest.raises(ValueError, match="plates"):
        WetSurface(ref=project.edge(1))
    with pytest.raises(ValueError, match="needs that point"):
        WetSurface(ref=project.face(face), fluid_side="away_from_point")
    with pytest.raises(ValueError, match="only applies"):
        WetSurface(ref=project.face(face), interior_point=(0, 0, 0))
    with pytest.raises(ValueError, match="fluid side"):
        WetSurface(ref=project.face(face), fluid_side="inside")


def test_parameter_names_cannot_shadow_the_vocabulary():
    project, _ = _flat()
    case = project.load_case()
    with pytest.raises(ValueError, match="reserved"):
        case.set_parameter("max", 1.0)
    with pytest.raises(ValueError, match="reserved"):
        case.set_parameter("t", 1.0)
    with pytest.raises(ValueError, match="identifier"):
        case.set_parameter("not a name", 1.0)


# ----------------------------------------------------------------------
# sign and magnitude against the solver
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "side, interior, expected_sign",
    [
        ("away_from_point", (1.0, 0.5, -1.0), -1.0),  # fluid above, pushes down
        ("away_from_point", (1.0, 0.5, 1.0), 1.0),  # fluid below, pushes up
    ],
)
def test_fluid_pushes_into_the_plate_from_the_side_it_is_on(
    side, interior, expected_sign
):
    project, face = _flat()
    project.add_wet_surface(
        WetSurface(
            ref=project.face(face), fluid_side=side, interior_point=interior
        )
    )
    project.load_case().add_wet_pressure(value=3000.0)
    built = build_fe_model(project, project.generate_mesh(0.25))
    # |p| * area, acting into the plate away from the fluid.
    assert _total_force(built)[2] == pytest.approx(expected_sign * 3000.0 * 2.0)


def test_explicit_fluid_side_is_opposite_for_the_two_normals():
    totals = []
    for side in ("positive_normal", "negative_normal"):
        project, face = _flat()
        project.add_wet_surface(WetSurface(ref=project.face(face), fluid_side=side))
        project.load_case().add_wet_pressure(value=3000.0)
        built = build_fe_model(project, project.generate_mesh(0.25))
        totals.append(_total_force(built))
    assert totals[0] == pytest.approx(-totals[1])
    assert abs(totals[0][2]) == pytest.approx(3000.0 * 2.0)


def test_a_uniform_wet_pressure_matches_the_ordinary_pressure():
    wet, face = _flat()
    wet.add_wet_surface(WetSurface(ref=wet.face(face), fluid_side="negative_normal"))
    wet.load_case().add_wet_pressure(value=2500.0)
    plain, face = _flat()
    plain.load_case().add_pressure(plain.face(face), 2500.0)

    wet_mesh = wet.generate_mesh(0.25)
    plain_mesh = plain.generate_mesh(0.25)
    first = build_fe_model(wet, wet_mesh)
    second = build_fe_model(plain, plain_mesh)
    assert first.load_case.pressure_loads == pytest.approx(
        second.load_case.pressure_loads
    )


def test_hydrostatic_pressure_on_a_vertical_wall_has_the_exact_resultant():
    """Triangular load over the draft: F = rho g d^2 / 2 per unit width."""

    project, face = _wall()
    project.add_wet_surface(
        WetSurface(
            ref=project.face(face),
            fluid_side="away_from_point",
            interior_point=(1.0, 5.0, 1.0),  # the dry side is at +y
        )
    )
    case = project.load_case()
    case.set_parameter("rho", 1025.0)
    case.set_parameter("draft", 2.0)
    case.add_wet_pressure(expression="rho * g * max(0, draft - z)")
    # The waterline sits on an element edge, so the centroid rule is exact.
    built = build_fe_model(project, project.generate_mesh(0.5))
    force = _total_force(built)
    expected = 1025.0 * 9.80665 * 2.0**2 / 2.0 * 2.0
    # The fluid is at -y; it pushes the wall toward +y.
    assert force[1] == pytest.approx(expected, rel=1e-9)
    assert force[0] == pytest.approx(0.0, abs=1e-6)
    assert force[2] == pytest.approx(0.0, abs=1e-6)


def test_dry_elements_carry_no_pressure():
    project, face = _wall()
    project.add_wet_surface(
        WetSurface(ref=project.face(face), fluid_side="positive_normal")
    )
    case = project.load_case()
    case.set_parameter("draft", 1.0)
    case.add_wet_pressure(expression="rho_g * max(0, draft - z)".replace("rho_g", "10000"))
    built = build_fe_model(project, project.generate_mesh(0.5))
    loaded = {e: p for e, p in built.load_case.pressure_loads.items() if p != 0.0}
    mesh = built.mesh
    for element_id in loaded:
        centroid = np.mean([mesh.nodes[n] for n in mesh.corners_of(element_id)], axis=0)
        assert centroid[2] < 1.0


# ----------------------------------------------------------------------
# direct mapping
# ----------------------------------------------------------------------
def _two_plates():
    project = Project(name="two")
    project.add_material(steel("S355", 0.010))
    project.add_plate_section("plate", thickness=0.010, material="S355")
    geometry = project.geometry
    p = geometry.add_points(
        [(0, 0, 0), (1, 0, 0), (2, 0, 0), (2, 1, 0), (1, 1, 0), (0, 1, 0)]
    )
    shared = geometry.add_line(p[1], p[4])
    left = [
        geometry.add_line(p[0], p[1]),
        shared,
        geometry.add_line(p[4], p[5]),
        geometry.add_line(p[5], p[0]),
    ]
    right = [
        geometry.add_line(p[1], p[2]),
        geometry.add_line(p[2], p[3]),
        geometry.add_line(p[3], p[4]),
        shared,
    ]
    faces = [geometry.add_face(left), geometry.add_face(right)]
    project.assign_plates(faces, "plate")
    for edge in {*left, *right} - {shared}:
        project.add_support(pinned(project.edge(edge)))
    return project, faces


def test_pressure_can_be_mapped_directly_to_plates():
    project, (left, right) = _two_plates()
    project.add_wet_surface(WetSurface(ref=project.face(left), fluid_side="negative_normal"))
    project.add_wet_surface(WetSurface(ref=project.face(right), fluid_side="negative_normal"))
    project.load_case().add_wet_pressure(mapping={left: 1000.0, right: 4000.0})

    built = build_fe_model(project, project.generate_mesh(0.25))
    # |sign| is 1 and the plates face +z or -z together; compare magnitudes.
    force = _total_force(built)
    assert abs(force[2]) == pytest.approx(1000.0 * 1.0 + 4000.0 * 1.0)


def test_pressure_can_be_mapped_directly_to_elements():
    project, (left, _right) = _two_plates()
    project.add_wet_surface(WetSurface(ref=project.face(left), fluid_side="negative_normal"))
    mesh = project.generate_mesh(0.25)
    elements = sorted(mesh.elements_of_face[left])
    first, second = elements[0], elements[1]
    project.load_case().add_wet_pressure(
        mapping={first: 2000.0, second: 500.0}, mapping_key="element"
    )
    built = build_fe_model(project, mesh)
    loads = built.load_case.pressure_loads
    assert set(loads) == {first, second}
    assert abs(loads[first]) == pytest.approx(2000.0)
    assert abs(loads[second]) == pytest.approx(500.0)


def test_mapping_to_a_dry_plate_or_unknown_element_is_refused():
    project, (left, right) = _two_plates()
    project.add_wet_surface(WetSurface(ref=project.face(left)))
    mesh = project.generate_mesh(0.5)

    dry = project.load_case("dry")
    dry.add_wet_pressure(mapping={right: 1.0})
    with pytest.raises(ProjectError, match="not on a selected wet surface"):
        build_fe_model(project, mesh, "dry")

    other = project.load_case("other")
    other.add_wet_pressure(mapping={10**9: 1.0}, mapping_key="element")
    with pytest.raises(ProjectError, match="not on a selected wet surface"):
        build_fe_model(project, mesh, "other")


def test_a_plate_on_two_wet_surfaces_is_refused():
    project, face = _flat()
    project.add_wet_surface(WetSurface(ref=project.face(face), name="a"))
    project.add_wet_surface(WetSurface(ref=project.face(face), name="b"))
    project.load_case().add_wet_pressure(value=1.0)
    with pytest.raises(ProjectError, match="two wet surfaces"):
        build_fe_model(project, project.generate_mesh(0.5))


def test_a_pressure_can_be_limited_to_chosen_wet_surfaces():
    project, (left, right) = _two_plates()
    a = project.add_wet_surface(WetSurface(ref=project.face(left), name="a"))
    project.add_wet_surface(WetSurface(ref=project.face(right), name="b"))
    project.load_case().add_wet_pressure(value=100.0, surfaces=(a.id,))
    mesh = project.generate_mesh(0.5)
    built = build_fe_model(project, mesh)
    assert set(built.load_case.pressure_loads) == set(mesh.elements_of_face[left])


# ----------------------------------------------------------------------
# validation
# ----------------------------------------------------------------------
def test_wet_pressure_without_wet_surfaces_blocks_the_solve():
    project, _ = _flat()
    project.load_case().add_wet_pressure(value=1.0)
    with pytest.raises(ProjectError, match="no wet surfaces"):
        project.validate()


def test_an_equation_with_an_undefined_name_or_time_is_reported_when_built():
    project, face = _flat()
    project.add_wet_surface(WetSurface(ref=project.face(face)))
    project.load_case("a").add_wet_pressure(expression="rho * z")
    project.load_case("b").add_wet_pressure(expression="sin(t)")
    mesh = project.generate_mesh(0.5)
    with pytest.raises(ProjectError, match="unknown name 'rho'"):
        build_fe_model(project, mesh, "a")
    with pytest.raises(ProjectError, match="unknown name 't'.*has no time"):
        build_fe_model(project, mesh, "b")


def test_a_wet_pressure_naming_an_unknown_wet_surface_is_reported():
    project, face = _flat()
    project.add_wet_surface(WetSurface(ref=project.face(face)))
    project.load_case().add_wet_pressure(value=1.0, surfaces=("nope",))
    with pytest.raises(ProjectError, match="unknown wet surface"):
        project.validate()


def test_a_case_with_only_wet_pressure_counts_as_loaded():
    project, face = _flat()
    project.add_wet_surface(WetSurface(ref=project.face(face)))
    project.load_case().add_wet_pressure(value=1.0)
    project.validate()  # supports present, so a loaded model is ready


# ----------------------------------------------------------------------
# quasi-static series
# ----------------------------------------------------------------------
def _wave_project():
    project, face = _flat()
    project.add_wet_surface(
        WetSurface(
            ref=project.face(face),
            fluid_side="away_from_point",
            interior_point=(1.0, 0.5, -5.0),
        )
    )
    template = project.load_case("wave")
    template.set_parameter("period", 8.0)
    template.add_wet_pressure(expression="2000 * sin(2 * pi * t / period)")
    return project, face


def test_a_series_makes_one_case_per_step_with_its_own_time():
    project, _ = _wave_project()
    cases = project.add_load_case_series(
        "step", [0.0, 2.0, 4.0, 6.0], template="wave"
    )
    assert [case.name for case in cases] == ["step_0", "step_1", "step_2", "step_3"]
    assert [case.time for case in cases] == [0.0, 2.0, 4.0, 6.0]
    assert {case.series for case in cases} == {"step"}
    assert [case.step for case in cases] == [0, 1, 2, 3]
    assert all(case.parameters == {"period": 8.0} for case in cases)
    assert all(len(case.wet_pressures) == 1 for case in cases)
    # The template is untouched and shares no mutable state with the steps.
    assert project.load_cases["wave"].time is None
    cases[0].set_parameter("period", 1.0)
    assert cases[1].parameters["period"] == 8.0


def test_series_steps_follow_the_equation_in_time():
    project, _ = _wave_project()
    project.add_load_case_series("step", [0.0, 2.0, 4.0, 6.0], template="wave")
    mesh = project.generate_mesh(0.25)
    totals = [
        _total_force(build_fe_model(project, mesh, f"step_{i}"))[2] for i in range(4)
    ]
    area = 2.0
    # The interior point is below, so the fluid is above the plate and pushes
    # down while the pressure is positive.
    assert totals == pytest.approx(
        [0.0, -2000.0 * area, 0.0, 2000.0 * area], abs=1e-6
    )


def test_a_series_is_solved_together_with_one_factorization():
    project, _ = _wave_project()
    project.add_load_case_series("step", [0.0, 2.0, 4.0, 6.0], template="wave")
    mesh = project.generate_mesh(0.25)
    batch = solve_linear_static_many(
        project, mesh=mesh, load_cases=[f"step_{i}" for i in range(4)]
    )
    single = solve_linear_static(project, mesh=mesh, load_case="step_1")
    peak = [shape.max_translation()[1] for shape in batch.shapes]
    assert peak[0] == pytest.approx(0.0, abs=1e-12)
    assert peak[2] == pytest.approx(0.0, abs=1e-12)
    assert peak[1] == pytest.approx(single.max_translation()[1])
    # Linear response: the antiphase step is the mirror image.
    assert batch.shapes[3].displacements == pytest.approx(
        -batch.shapes[1].displacements
    )


def test_a_series_builder_adds_step_dependent_loads():
    project, face = _flat()
    project.add_wet_surface(WetSurface(ref=project.face(face), fluid_side="negative_normal"))
    seen = []

    def builder(case, time, step):
        seen.append((case.name, time, step))
        case.add_wet_pressure(value=1000.0 * (step + 1))

    project.add_load_case_series("ramp", [0.0, 1.0, 2.0], builder=builder)
    assert [item[2] for item in seen] == [0, 1, 2]
    mesh = project.generate_mesh(0.5)
    forces = [
        abs(_total_force(build_fe_model(project, mesh, f"ramp_{i}"))[2])
        for i in range(3)
    ]
    assert forces == pytest.approx([2000.0, 4000.0, 6000.0])


def test_series_arguments_are_checked_and_failures_leave_no_cases():
    project, _ = _wave_project()
    with pytest.raises(ProjectError, match="at least one"):
        project.add_load_case_series("s", [], template="wave")
    with pytest.raises(ProjectError, match="template case or a builder"):
        project.add_load_case_series("s", [0.0])
    with pytest.raises(ProjectError, match="no load case named"):
        project.add_load_case_series("s", [0.0], template="missing")
    with pytest.raises(ProjectError, match="finite"):
        project.add_load_case_series("s", [0.0, float("inf")], template="wave")

    def boom(case, time, step):
        if step == 1:
            raise RuntimeError("builder failed")

    before = set(project.load_cases)
    with pytest.raises(RuntimeError):
        project.add_load_case_series("s", [0.0, 1.0, 2.0], template="wave", builder=boom)
    assert set(project.load_cases) == before

    project.add_load_case_series("s", [0.0], template="wave")
    with pytest.raises(ProjectError, match="already exist"):
        project.add_load_case_series("s", [0.0], template="wave")


def test_combining_wave_cases_evaluates_each_at_its_own_time():
    project, _ = _wave_project()
    project.add_load_case_series("step", [2.0, 6.0], template="wave")
    project.add_combination("both", {"step_0": 1.0, "step_1": 1.0})
    built = build_fe_model(project, project.generate_mesh(0.5), None, combination="both")
    assert _total_force(built)[2] == pytest.approx(0.0, abs=1e-6)


# ----------------------------------------------------------------------
# persistence
# ----------------------------------------------------------------------
def _round_trip(project):
    import json

    from anyfem.io.project_file import project_from_dict, project_to_dict

    data = project_to_dict(project)
    return data, project_from_dict(json.loads(json.dumps(data)))


def test_wet_loads_survive_a_save_and_reload_exactly():
    project, (left, right) = _two_plates()
    project.add_wet_surface(
        WetSurface(
            ref=project.face(left),
            name="port side",
            fluid_side="away_from_point",
            interior_point=(0.5, 0.5, -3.0),
        )
    )
    project.add_wet_surface(WetSurface(ref=project.face(right), name="stbd"))
    template = project.load_case("wave")
    template.set_parameter("period", 8.0)
    template.add_wet_pressure(expression="2000*sin(2*pi*t/period)", name="wave")
    template.add_wet_pressure(mapping={left: 5.0, right: 7.5}, name="direct")
    template.add_wet_pressure(value=10.0, scale=2.0, name="tank")
    project.add_load_case_series("step", [0.0, 2.0, 4.0], template="wave")

    data, loaded = _round_trip(project)

    assert data["anyfem"]["format"] == 9
    assert [s.name for s in loaded.wet_surfaces] == ["port side", "stbd"]
    port = loaded.wet_surfaces[0]
    assert port.fluid_side == "away_from_point"
    assert port.interior_point == pytest.approx([0.5, 0.5, -3.0])
    assert port.region is not None
    assert sorted(loaded.load_cases) == ["step_0", "step_1", "step_2", "wave"]
    step = loaded.load_cases["step_1"]
    assert (step.time, step.series, step.step) == (2.0, "step", 1)
    assert step.parameters == {"period": 8.0}
    kinds = [(w.kind, w.name) for w in step.wet_pressures]
    assert kinds == [("expression", "wave"), ("mapping", "direct"), ("uniform", "tank")]
    assert step.wet_pressures[1].mapping == {left: 5.0, right: 7.5}
    assert step.wet_pressures[2].scale == 2.0
    # The same document again: the encoding is stable.
    assert _round_trip(loaded)[0] == data
    # And it builds the same loads as the original.
    mesh = project.generate_mesh(0.5)
    assert build_fe_model(loaded, mesh, "step_1").load_case.pressure_loads == pytest.approx(
        build_fe_model(project, mesh, "step_1").load_case.pressure_loads
    )


def test_a_project_without_wet_loads_keeps_its_old_encoding():
    project, face = _flat()
    project.load_case().add_pressure(project.face(face), 1.0)
    data, _loaded = _round_trip(project)
    assert data["anyfem"]["format"] == 8
    assert "wet_surfaces" not in data
    assert all(
        key not in case
        for case in data["load_cases"]
        for key in ("wet_pressures", "time", "parameters", "series", "step")
    )


def test_an_older_reader_refuses_a_file_it_would_silently_misread():
    from anyfem.io.project_file import (
        FORMAT_VERSION,
        ProjectFileError,
        project_from_dict,
        project_to_dict,
    )

    project, face = _flat()
    project.add_wet_surface(WetSurface(ref=project.face(face)))
    data = project_to_dict(project)
    assert data["anyfem"]["format"] > FORMAT_VERSION
    data["anyfem"]["format"] = 99
    with pytest.raises(ProjectFileError, match="upgrade ANYfem"):
        project_from_dict(data)


def test_a_malformed_wet_pressure_in_a_file_is_reported_not_dropped():
    from anyfem.io.project_file import (
        ProjectFileError,
        project_from_dict,
        project_to_dict,
    )

    project, face = _flat()
    project.add_wet_surface(WetSurface(ref=project.face(face)))
    project.load_case().add_wet_pressure(value=1.0)
    data = project_to_dict(project)
    entry = data["load_cases"][0]["wet_pressures"][0]
    entry["expression"] = "1"
    with pytest.raises(ProjectFileError, match="exactly one"):
        project_from_dict(data)
    entry.pop("value")
    entry["expression"] = "__import__(" + chr(39) + "os" + chr(39) + ")"
    with pytest.raises(ProjectFileError):
        project_from_dict(data)


def test_a_wet_surface_protects_its_region_from_deletion():
    from anyfem.commands import _region_users

    project, face = _flat()
    surface = project.add_wet_surface(WetSurface(ref=project.face(face)))
    assert any("wet surface" in item for item in _region_users(project, surface.region.id))


# ----------------------------------------------------------------------
# commands
# ----------------------------------------------------------------------
def test_wet_commands_are_undoable_and_redoable():
    from anyfem import commands as cmd

    project, face = _flat()
    stack = cmd.CommandStack(project)
    surface = stack.run(cmd.AddWetSurface(project.face(face), name="hull"))
    load = stack.run(cmd.AddWetPressure(expression="1000*z", case="c"))
    series = stack.run(cmd.AddLoadCaseSeries("s", (0.0, 1.0), template="c"))
    assert [c.name for c in series] == ["s_0", "s_1"]

    stack.undo()
    assert not any(name.startswith("s_") for name in project.load_cases)
    stack.undo()
    assert load not in project.load_case("c").wet_pressures
    stack.undo()
    assert project.wet_surfaces == []
    stack.redo()
    stack.redo()
    stack.redo()
    assert project.wet_surfaces == [surface]
    assert project.load_case("c").wet_pressures == [load]
    assert {"s_0", "s_1"} <= set(project.load_cases)


def test_wet_attributes_can_be_edited_and_deleted_by_id():
    from dataclasses import replace

    from anyfem import commands as cmd

    project, face = _flat()
    stack = cmd.CommandStack(project)
    surface = stack.run(cmd.AddWetSurface(project.face(face)))
    load = stack.run(cmd.AddWetPressure(value=1.0))

    stack.run(cmd.EditAttribute(replace(surface, fluid_side="negative_normal")))
    assert project.wet_surfaces[0].fluid_side == "negative_normal"
    stack.run(cmd.DeleteAttribute(load.id))
    assert project.load_case().wet_pressures == []
    stack.undo()
    assert project.load_case().wet_pressures == [load]
    stack.run(cmd.DeleteAttribute(surface.id))
    assert project.wet_surfaces == []
    stack.undo()
    assert project.wet_surfaces[0].id == surface.id


# ----------------------------------------------------------------------
# results and workflow
# ----------------------------------------------------------------------
def test_a_batch_loading_change_makes_the_batch_results_stale():
    from anyfem.io.project_file import project_to_dict
    from anyfem.jobs import _selected_loading_semantics
    from anyfem.model.records import AnalysisDefinition

    project, _ = _wave_project()
    project.add_load_case_series("step", [0.0, 2.0], template="wave")
    analysis = AnalysisDefinition(
        name="batch",
        type="batch_linear_static",
        target_kind="load_cases",
        target_id="batch",
        settings={"load_cases": ["step_0", "step_1"]},
    )
    before = _selected_loading_semantics(analysis, project_to_dict(project))
    project.load_cases["step_1"].set_time(3.0)
    after = _selected_loading_semantics(analysis, project_to_dict(project))
    assert before != after
    assert [c["time"] for c in after["cases"]] == [0.0, 3.0]
    # An unrelated case does not disturb it.
    project.load_case("other").add_wet_pressure(value=1.0)
    assert _selected_loading_semantics(analysis, project_to_dict(project)) == after


def test_the_batch_workflow_runs_a_series_without_a_default_case():
    from anyfem.application.workflow import ANALYSES, _execute_analysis_job

    project, _ = _wave_project()
    project.load_cases.pop("wave")

    def build(case, time, step):
        case.set_parameter("period", 8.0)
        case.add_wet_pressure(expression="2000*sin(2*pi*t/period)")

    project.add_load_case_series("step", [0.0, 2.0, 4.0, 6.0], builder=build)
    assert "default" not in project.load_cases
    mesh = project.generate_mesh(0.5)
    solution = _execute_analysis_job(
        project=project,
        solver_function=ANALYSES["Batch linear static"],
        analysis_name="Batch linear static",
        options={"mesh": mesh, "load_cases": list(project.load_cases)},
        progress=lambda message: None,
    )
    assert solution.case_names == ("step_0", "step_1", "step_2", "step_3")


def test_deleting_a_wet_plate_detaches_and_restores_its_wet_surface():
    from anyfem import commands as cmd

    project, (left, right) = _two_plates()
    project.add_wet_surface(WetSurface(ref=project.face(left)))
    project.add_wet_surface(WetSurface(ref=project.face(right)))
    stack = cmd.CommandStack(project)
    stack.run(cmd.DeleteEntity(project.face(left)))
    assert [s.ref.id for s in project.wet_surfaces] == [right]
    stack.undo()
    assert sorted(s.ref.id for s in project.wet_surfaces) == sorted([left, right])


def test_a_time_template_is_skipped_by_default_but_not_when_named():
    project, _ = _wave_project()
    project.add_load_case_series("step", [0.0, 2.0], template="wave")
    assert project.load_cases["wave"].is_template
    assert not project.load_cases["step_0"].is_template

    mesh = project.generate_mesh(0.5)
    batch = solve_linear_static_many(project, mesh=mesh)
    assert batch.case_names == ("step_0", "step_1")
    with pytest.raises(ProjectError, match="has no time"):
        solve_linear_static_many(project, mesh=mesh, load_cases=["wave"])
