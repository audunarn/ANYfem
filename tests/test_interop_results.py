"""Phase 13: reading results back, and plotting a history.

The theme is that an external result is not the same object as a solved one and
must not pretend to be. A CalculiX FRD has three displacement components and no
rotations; a rotation reported as zero is plausible and wrong. Several of these
tests exist to hold that line.

The parsers themselves belong to ANYfileio and are tested there. What is tested
here is ANYfem's adapter: what it carries through, what it refuses, and how the
result behaves once attached.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest

from anyfem import Project, pinned, solve_linear_static, steel
from anyfem.io import (
    ImportedResults,
    ResultImportError,
    import_calculix_results,
    import_sesam_results,
)
from anyfem.post.fields import evaluate_field
from anyfem.post.history import Series, has_history, history_series
from anyfem.solve.build import build_fe_model

# A four-node shell with one RVSTRESS record. Written out rather than copied
# from a fixture directory so the test carries its own input.
SHELL_SIF = """IDENT          101               1
UNITS            1               1               1
MISOSEL          1  2.100000D+11  3.000000D-01  7.850000D+03
GELTH           10  2.000000D-02
GUNIVEC          5               0               0               1
BNTRCOS          7               1               0               0               0               1               0               0               0               1
GCOORD           1               0               0               0
GCOORD           2               1               0               0
GCOORD           3               1               1               0
GCOORD           4               0               1               0
GELMNT1        100               0              24               0               1               2               3               4
GELREF1        100               1               0               0               0               0               0               0              10               0               0               7
RVSTRESS         1               0             100               0              24               0    1.000000E+08               0               0    1.000000E+08               0
IEND
"""


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as directory:
        yield Path(directory)


def plate(side: float = 1.0, thickness: float = 0.008) -> Project:
    project = Project(name="plate")
    project.add_material(steel("S355", thickness))
    project.add_plate_section("plate", thickness=thickness, material="S355")
    geometry = project.geometry
    points = geometry.add_points(
        [(0, 0, 0), (side, 0, 0), (side, side, 0), (0, side, 0)]
    )
    edges = geometry.add_polyline(points, close=True)
    face = geometry.add_face(edges)
    project.assign_plate(face, "plate")
    for edge in edges:
        project.add_support(pinned(project.edge(edge)))
    project.load_case().add_pressure(project.face(face), 10_000.0)
    return project


def write_frd(path: Path, mesh, built, displacements) -> Path:
    """A minimal ASCII FRD carrying node coordinates and translations."""

    nodes = sorted(mesh.nodes)
    lines = ["    1C", f"    2C{len(nodes):30d}"]
    for node in nodes:
        x, y, z = mesh.nodes[node]
        lines.append(f" -1{node:10d}{x:12.5E}{y:12.5E}{z:12.5E}")
    lines.append(" -3")
    lines.append("    1PSTEP                         1")
    lines.append(f"  100CL  101{len(nodes):12d}".ljust(60) + "1")
    lines.append(" -4  DISP        4    1")
    for name in ("D1", "D2", "D3", "ALL"):
        lines.append(f" -5  {name:8s} 1    2    1    0")
    manager = built.fe_model.mesh.dof_manager
    for node in nodes:
        values = displacements[manager.get_node_dofs(node)[:3]]
        lines.append(
            f" -1{node:10d}{values[0]:12.5E}{values[1]:12.5E}{values[2]:12.5E}"
        )
    lines.append(" -3")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def solved_plate():
    project = plate()
    mesh = project.generate_mesh(0.5)
    built = build_fe_model(project, mesh)
    return project, mesh, built, solve_linear_static(project, mesh=mesh)


# ----------------------------------------------------------------------
# CalculiX
# ----------------------------------------------------------------------
def test_a_missing_file_is_refused(workspace):
    with pytest.raises(ResultImportError, match="no result file"):
        import_calculix_results(workspace / "absent.frd")
    with pytest.raises(ResultImportError, match="no result file"):
        import_sesam_results(workspace / "absent.SIF")


def test_an_frd_round_trips_the_displacements(workspace):
    """Write this solution out, read it back, and get the same numbers."""

    project, mesh, built, solution = solved_plate()
    path = write_frd(workspace / "out.frd", mesh, built, solution.displacements)

    results = import_calculix_results(path)
    assert results.format == "CalculiX"
    assert len(results.displacements) == mesh.num_nodes

    attached = results.attach(built)
    assert attached.covered == mesh.num_nodes
    assert attached.max_translation()[1] == pytest.approx(
        solution.max_translation()[1]
    )
    assert attached.component("uz") == pytest.approx(solution.component("uz"))


def test_an_frd_has_no_rotations_and_says_so(workspace):
    project, mesh, built, solution = solved_plate()
    path = write_frd(workspace / "out.frd", mesh, built, solution.displacements)
    attached = import_calculix_results(path).attach(built)

    assert not attached.results.has_rotations
    for name in ("ux", "uy", "uz"):
        assert len(attached.component(name)) == mesh.num_nodes
    for name in ("rx", "ry", "rz"):
        with pytest.raises(KeyError, match="It is not zero -- it is absent"):
            attached.component(name)


def test_absent_components_are_nan_in_the_raw_array(workspace):
    """Anything reaching around component() must not find a plausible zero."""

    project, mesh, built, solution = solved_plate()
    path = write_frd(workspace / "out.frd", mesh, built, solution.displacements)
    attached = import_calculix_results(path).attach(built)

    manager = built.fe_model.mesh.dof_manager
    for node in sorted(mesh.nodes):
        dofs = manager.get_node_dofs(node)
        assert np.all(np.isfinite(attached.displacements[dofs[:3]]))
        assert np.all(np.isnan(attached.displacements[dofs[3:6]]))


def test_an_empty_result_file_is_refused(workspace):
    path = workspace / "empty.frd"
    path.write_text("    1C\n -3\n", encoding="utf-8")
    with pytest.raises(ResultImportError, match="carries no results"):
        import_calculix_results(path)


# ----------------------------------------------------------------------
# matching
# ----------------------------------------------------------------------
def test_a_result_for_a_different_mesh_is_refused():
    _project, mesh, built, _solution = solved_plate()
    partial = ImportedResults(
        source=Path("partial.frd"),
        format="CalculiX",
        displacements={1: (0.0, 0.0, -1.0e-3)},
    )
    with pytest.raises(ResultImportError, match="does not match this model"):
        partial.attach(built)


def test_a_partial_attach_has_to_be_asked_for():
    _project, mesh, built, _solution = solved_plate()
    partial = ImportedResults(
        source=Path("partial.frd"),
        format="CalculiX",
        displacements={1: (0.0, 0.0, -1.0e-3), 2: (0.0, 0.0, -2.0e-3)},
    )
    attached = partial.attach(built, require_all_nodes=False)
    assert attached.covered == 2
    assert attached.covered < mesh.num_nodes


def test_complete_stresses_do_not_hide_partial_displacements():
    _project, mesh, built, _solution = solved_plate()
    mixed = ImportedResults(
        source=Path("mixed.frd"),
        format="CalculiX",
        displacements={1: (0.0, 0.0, -1.0e-3)},
        node_stresses={node: {"sxx": 1.0} for node in mesh.nodes},
    )

    with pytest.raises(ResultImportError, match="displacement nodes"):
        mixed.attach(built)


def test_results_with_nothing_nodal_are_refused():
    _project, _mesh, built, _solution = solved_plate()
    empty = ImportedResults(source=Path("x.frd"), format="CalculiX")
    with pytest.raises(ResultImportError, match="carries no nodal results"):
        empty.attach(built)


# ----------------------------------------------------------------------
# SESAM
# ----------------------------------------------------------------------
def test_sesam_shell_stresses_import_with_their_own_names(workspace):
    path = workspace / "shell.SIF"
    path.write_text(SHELL_SIF, encoding="utf-8")

    results = import_sesam_results(path)
    assert results.format == "SESAM"
    assert results.element_stresses
    # Names come from the file, not from a list here.
    assert set(results.components) >= {"SXX", "SYY"}
    assert not results.displacements


def test_a_sesam_model_without_stresses_is_refused(workspace):
    path = workspace / "model.SIF"
    path.write_text(
        "\n".join(
            line for line in SHELL_SIF.splitlines() if "RVSTRESS" not in line
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ResultImportError, match="no shell stresses"):
        import_sesam_results(path)


def test_imported_stress_components_reach_the_field_layer(workspace):
    """The file's names must be evaluable, not just stored."""

    path = workspace / "shell.SIF"
    path.write_text(SHELL_SIF, encoding="utf-8")
    results = import_sesam_results(path)

    # Attach to a matching one-element model built from the same four nodes.
    project = plate()
    mesh = project.generate_mesh(1.0)
    built = build_fe_model(project, mesh)
    attached = results.attach(built, require_all_nodes=False)

    assert not attached.components
    assert "magnitude" not in attached.available_fields()
    assert "max translation" not in attached.summary()
    with pytest.raises(KeyError, match="carries no 'ux'"):
        attached.component("ux")

    for name in results.components:
        field = evaluate_field(attached, name)
        assert field.name == name
    with pytest.raises(ValueError, match="unknown field"):
        evaluate_field(attached, "not_a_component")


# ----------------------------------------------------------------------
# history series
# ----------------------------------------------------------------------
def test_a_linear_static_has_no_history():
    project = plate()
    solution = solve_linear_static(project, target_size=0.5)
    assert history_series(solution) == []
    assert not has_history(solution)


def test_a_transient_gives_a_node_trace():
    from anyfem import solve_transient

    project = plate()
    solution = solve_transient(project, target_size=0.5, dt=2.0e-4, t_end=0.004)
    series = history_series(solution)

    assert series
    trace = series[0]
    assert len(trace) == len(solution.times)
    assert trace.x_unit == "s" and trace.y_unit == "m"
    assert has_history(solution)


def test_an_incremental_solve_gives_a_load_displacement_path():
    from anyfem import solve_nonlinear_static

    project = plate()
    solution = solve_nonlinear_static(project, target_size=0.5, num_steps=4)
    names = [item.name for item in history_series(solution)]
    assert "load-displacement path" in names


def test_an_impact_offers_the_contact_force():
    from anyfem.model import Collision
    from anyfem.solve import solve_impact

    project = plate()
    solution = solve_impact(
        project, target_size=0.25,
        collision=Collision(
            mass=200.0, radius=0.15, start=(0.5, 0.5, 0.6),
            direction=(0.0, 0.0, -1.0), speed=4.0,
        ),
    )
    names = [item.name for item in history_series(solution)]
    assert "contact force" in names


def test_a_series_of_mismatched_lengths_is_refused():
    with pytest.raises(ValueError, match="x values against"):
        Series(name="bad", x=np.zeros(4), y=np.zeros(3))


def test_a_series_reports_its_peak_by_magnitude():
    series = Series(
        name="s", x=np.array([0.0, 1.0, 2.0]), y=np.array([1.0, -5.0, 2.0])
    )
    assert series.peak() == (1.0, -5.0)


# ----------------------------------------------------------------------
# plot arithmetic (no display needed)
# ----------------------------------------------------------------------
def test_ticks_are_round_and_stay_inside_the_range():
    from anyfem.ui.plot import nice_ticks

    for low, high in ((0.0, 1.0), (0.0, 0.0237), (-3.0, 7.0), (1.2, 1.7)):
        ticks = nice_ticks(low, high)
        assert ticks
        assert all(low - 1e-9 <= value <= high + 1e-9 for value in ticks)
        # Round: every tick is a whole number of steps.
        if len(ticks) > 1:
            step = ticks[1] - ticks[0]
            assert all(
                abs(value / step - round(value / step)) < 1e-6 for value in ticks
            )


def test_ticks_fill_the_axis_rather_than_rounding_up_to_two():
    """A span of 10 asking for 5 ticks must not draw 2."""

    from anyfem.ui.plot import nice_ticks

    assert len(nice_ticks(-3.0, 7.0, count=5)) >= 4


def test_a_degenerate_or_reversed_range_still_gives_ticks():
    from anyfem.ui.plot import nice_ticks

    assert nice_ticks(5.0, 5.0) == [5.0]
    assert nice_ticks(7.0, -3.0) == nice_ticks(-3.0, 7.0)


def test_a_non_finite_range_is_refused():
    from anyfem.ui.plot import nice_ticks

    with pytest.raises(ValueError, match="finite"):
        nice_ticks(0.0, float("nan"))
    with pytest.raises(ValueError, match="no axis"):
        nice_ticks(0.0, 1.0, count=1)


def test_padded_range_survives_flat_and_non_finite_data():
    from anyfem.ui.plot import padded_range

    low, high = padded_range([2.0, 2.0, 2.0])
    assert low < 2.0 < high
    # NaN is what an imported result carries where a component is absent.
    assert padded_range([1.0, np.nan, 3.0]) == padded_range([1.0, 3.0])
    assert padded_range([]) == (0.0, 1.0)


def test_mapping_to_canvas_inverts_for_the_vertical_axis():
    from anyfem.ui.plot import map_to_canvas

    assert map_to_canvas([0, 5, 10], 0, 10, 0, 100) == [0.0, 50.0, 100.0]
    assert map_to_canvas([0, 5, 10], 0, 10, 0, 100, invert=True) == [
        100.0, 50.0, 0.0
    ]
    # A zero-width range must not divide by zero.
    assert map_to_canvas([3.0], 3.0, 3.0, 0.0, 10.0) == [0.0]
