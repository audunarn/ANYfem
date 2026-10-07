"""Each public static load keeps its own envelope and combination factor."""

from __future__ import annotations

import numpy as np
import pytest

from anyfem import Project, commands, fixed, steel
from anyfem.model import WetSurface
from anyfem.solve.build import build_fe_model


def _project():
    project = Project(name="independent envelope factors")
    project.add_material(steel("S355", 0.01))
    project.add_plate_section("plate", thickness=0.01, material="S355")
    geometry = project.geometry
    points = geometry.add_points([(0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0)])
    edges = geometry.add_polyline(points, close=True)
    face = geometry.add_face(edges)
    project.assign_plate(face, "plate")
    project.add_support(fixed(project.edge(edges[3])))
    commands.CommandStack(project).run(
        commands.AddConnection(refs=(project.edge(edges[1]),), name="end")
    )
    project.add_wet_surface(
        WetSurface(project.face(face), fluid_side="negative_normal")
    )
    series = project.add_load_series("series", [0.0, 0.5])
    return project, series, points, face


@pytest.mark.parametrize("case_factor", [1.0, -2.5])
@pytest.mark.parametrize(
    "preceding, envelope_factor",
    [("none", 1.0), ("point", 0.0), ("point", 2.0),
     ("pressure", 0.0), ("pressure", 2.0), ("both", -0.5)],
)
def test_public_build_keeps_wet_and_connection_factors_independent(
    preceding, envelope_factor, case_factor
):
    project, series, points, face = _project()
    envelope = {"expression": str(envelope_factor)}
    if preceding in {"point", "both"}:
        series.add_point_load(project.point(points[0]), force=(5, 0, 0), envelope=envelope)
    if preceding in {"pressure", "both"}:
        series.add_pressure(project.face(face), 11.0, envelope=envelope)
    series.add_wet_pressure(value=17.0, envelope={"expression": "3.0"})
    series.add_wet_pressure(value=19.0, envelope={"expression": "0.5"})
    connection_id = project.connection_for("end").id
    series.add_connection_load(connection_id, force=(0, 7, 0), envelope={"expression": "4.0"})
    series.add_connection_load(connection_id, force=(0, 13, 0), envelope={"expression": "0.25"})
    step = project.series_children("series")[1]
    mesh = project.generate_mesh(1.0)
    if case_factor == 1.0:
        built = build_fe_model(project, mesh, step.name)
    else:
        project.add_combination("scaled", {step.name: case_factor})
        built = build_fe_model(project, mesh, None, combination="scaled")

    pressure = case_factor * (3.0 * 17.0 + 0.5 * 19.0)
    if preceding in {"pressure", "both"}:
        pressure += case_factor * envelope_factor * 11.0
    assert built.load_case.pressure_loads
    for values in built.load_case.pressure_loads.values():
        np.testing.assert_allclose(values, pressure, rtol=0.0, atol=1.0e-12)
    reference_node = next(iter(built.connections.values())).reference_node
    np.testing.assert_array_equal(
        built.load_case.nodal_loads[reference_node],
        [0, case_factor * (4.0 * 7.0 + 0.25 * 13.0), 0, 0, 0, 0],
    )
    point_total = sum(values[0] for values in built.load_case.nodal_loads.values())
    expected_point = (
        case_factor * envelope_factor * 5.0 if preceding in {"point", "both"} else 0.0
    )
    assert point_total == pytest.approx(expected_point, rel=0.0, abs=1.0e-12)
