"""Load series: loads stored once, envelopes that say when they act, derived steps.

A time history of hundreds of steps must not copy its loads into every case, so
these tests check that one stored load reaches each step scaled by its own
envelope, against a hand calculation, and that the bookkeeping around it
(persistence, undo, refusal, staleness) holds.
"""

from __future__ import annotations

import numpy as np
import pytest
from anysolver import assemble_load_vector

from anyfem import commands as cmd
from anyfem import solve_linear_static, solve_linear_static_many
from anyfem.io.project_file import project_from_dict, project_to_dict
from anyfem.jobs import analysis_hash
from anyfem.model import ProjectError
from anyfem.model.records import AnalysisDefinition
from test_connections import E, _cantilever_plate, _connect


def _series(duration=1.0, step=0.1, **options):
    project, edges, face = _cantilever_plate()
    stack = cmd.CommandStack(project)
    stack.run(cmd.AddLoadSeries("S", duration=duration, step=step, **options))
    return project, edges, face, stack


def total_force(solution):
    vector = assemble_load_vector(solution.built.fe_model, solution.built.load_case)
    if isinstance(vector, tuple):
        vector = vector[0]
    return np.array([float(vector[axis::6].sum()) for axis in range(3)])


# ----------------------------------------------------------------------
# the series and its steps
# ----------------------------------------------------------------------
def test_a_series_derives_one_step_per_time_and_stores_nothing_in_them():
    project, _, _, _ = _series(duration=60.0, step=0.1)
    steps = project.series_children("S")
    assert len(steps) == 600 and project.load_cases["S"].times[-1] == pytest.approx(59.9)
    assert [s.name for s in steps[:2]] == ["S_000", "S_001"] and steps[-1].name == "S_599"
    assert [s.number for s in steps[:3]] == [1, 2, 3] and steps[10].time == pytest.approx(1.0)
    assert all(not s.point_loads and not s.wet_pressures and s.parent == "S" for s in steps)
    assert project.load_cases["S"].is_template and not steps[0].is_template
    # Re-deriving gives the same ids, so a reopened project keeps its identities.
    ids = [s.id for s in steps]
    project.expand_load_series("S")
    assert [s.id for s in project.series_children("S")] == ids


def test_series_times_must_increase_and_be_a_whole_number_of_steps():
    project, *_ = _cantilever_plate()
    with pytest.raises(ProjectError, match="increase"):
        project.add_load_series("bad", [0.0, 1.0, 1.0])
    with pytest.raises(ProjectError, match="whole number"):
        cmd.CommandStack(project).run(cmd.AddLoadSeries("x", duration=1.0, step=0.3))
    with pytest.raises(ProjectError, match="duration and a step"):
        cmd.CommandStack(project).run(cmd.AddLoadSeries("x"))
    project.load_case("taken")
    with pytest.raises(ProjectError, match="already exists"):
        project.add_load_series("taken", [0.0, 1.0])


def test_a_series_definition_is_never_solved_and_says_what_to_do():
    project, edges, face, stack = _series()
    stack.run(cmd.AddPressure(project.face(face), 100.0, case="S"))
    with pytest.raises(ProjectError, match="load series definition"):
        solve_linear_static(project, target_size=0.25, load_case="S")
    with pytest.raises(ProjectError, match="Batch linear static"):
        solve_linear_static_many(project, target_size=0.25, load_cases=["S"])


def test_a_timeless_template_still_names_the_missing_time():
    project, edges, face = _cantilever_plate()
    from anyfem.model import WetSurface

    project.add_wet_surface(WetSurface(project.face(face)))
    case = project.load_case("tmpl")
    case.add_wet_pressure(expression="1000 * t")
    with pytest.raises(ProjectError, match="unknown name 't'.*has no time"):
        solve_linear_static(project, target_size=0.25, load_case="tmpl")


def test_loads_are_added_to_the_definition_never_to_a_step():
    project, edges, face, stack = _series()
    with pytest.raises(ProjectError, match="step of the load series 'S'"):
        stack.run(cmd.AddPressure(project.face(face), 100.0, case=project.series_children("S")[3].name))
    stack.run(cmd.AddPressure(project.face(face), 100.0, case="S"))
    assert len(project.load_cases["S"].pressures) == 1


# ----------------------------------------------------------------------
# envelopes reach the solver
# ----------------------------------------------------------------------
def test_an_envelope_window_by_number_switches_a_load_on_and_off():
    project, edges, face, stack = _series(duration=1.0, step=0.1)  # numbers 1..10
    stack.run(cmd.AddPressure(project.face(face), 1000.0, case="S",
                              envelope={"start": 3, "stop": 6}))
    area = 2.0
    forces = {}
    for number in (1, 2, 3, 4, 6, 7, 10):
        solution = solve_linear_static(
            project, target_size=0.25, load_case=project.series_children("S")[number - 1].name)
        forces[number] = total_force(solution)[2]
    acting = {n for n, f in forces.items() if abs(f) > 1.0}
    assert acting == {3, 4, 6}
    assert abs(forces[3]) == pytest.approx(1000.0 * area, rel=1e-9)


def test_a_connection_load_follows_its_expression_and_the_whole_series_solves_at_once():
    """Rotation of a rigid end is linear in the moment, so it must follow the
    envelope factor exactly; the batch and the single-step solves must agree."""

    project, edges, face = _cantilever_plate()
    stack = _connect(project, (project.edge(edges[1]),), name="end")
    stack.run(cmd.AddLoadSeries("S", duration=1.0, step=0.1,
                                parameters={"swing": 0.5, "omega": 2.0 * np.pi}))
    stack.run(cmd.AddConnectionLoad(
        "end", moment=(0, 0, 1e3), case="S",
        envelope={"start": 2, "stop": 8, "expression": "1 + swing * sin(omega * t)"}))
    batch = solve_linear_static_many(project, target_size=0.25)
    names = [s.name for s in project.series_children("S")]
    history = batch.connection_history("end")
    assert list(history["case"]) == names
    np.testing.assert_allclose(history["time"], np.arange(10) * 0.1, atol=1e-12)
    rotation = history["rotation"][:, 2]
    times = np.arange(10) * 0.1
    factor = np.where((np.arange(1, 11) >= 2) & (np.arange(1, 11) <= 8),
                      1.0 + 0.5 * np.sin(2.0 * np.pi * times), 0.0)
    unit = 1e3 * 2.0 / (E * 0.020 * 1.0**3 / 12.0)  # beam estimate, only to anchor scale
    assert rotation[0] == 0.0 and rotation[9] == 0.0
    scale = rotation[3] / factor[3]
    np.testing.assert_allclose(rotation, scale * factor, atol=1e-12, rtol=1e-8)
    assert scale == pytest.approx(unit, rel=0.03)
    single = solve_linear_static(project, target_size=0.25, load_case=names[4])
    assert single.connection_motion("end")["rotation"][2] == pytest.approx(rotation[4], rel=1e-9)


def test_points_make_a_ramp_and_tau_counts_from_the_window_start():
    project, edges, face, stack = _series(duration=1.0, step=0.1)
    stack.run(cmd.AddPressure(project.face(face), 1000.0, case="S",
                              envelope={"by": "time", "points": [[0.0, 0.0], [0.5, 1.0]]}))
    stack.run(cmd.AddPressure(project.face(face), 1000.0, case="S",
                              envelope={"start": 6, "expression": "tau"}))  # from case 6: tau = t - t(6)
    steps = project.series_children("S")
    totals = [total_force(solve_linear_static(project, target_size=0.25, load_case=steps[i].name))[2]
              for i in (0, 2, 5, 7, 9)]
    ramp = np.interp([0.0, 0.2, 0.5, 0.7, 0.9], [0.0, 0.5], [0.0, 1.0])
    tau = np.array([0.0, 0.0, 0.0, 0.2, 0.4])  # time since case 6 (t = 0.5)
    np.testing.assert_allclose(totals, 2000.0 * (ramp + tau), atol=1e-6)


def test_overlapping_loads_add_and_an_unknown_parameter_is_reported():
    project, edges, face, stack = _series()
    stack.run(cmd.AddPressure(project.face(face), 100.0, case="S"))
    stack.run(cmd.AddPressure(project.face(face), 100.0, case="S", envelope={"start": 5}))
    steps = project.series_children("S")
    assert total_force(solve_linear_static(project, target_size=0.25, load_case=steps[0].name))[2] == pytest.approx(200.0)
    assert total_force(solve_linear_static(project, target_size=0.25, load_case=steps[6].name))[2] == pytest.approx(400.0)
    stack.run(cmd.AddPressure(project.face(face), 1.0, case="S", envelope={"expression": "nope * t"}))
    with pytest.raises(ProjectError, match="reads \\['nope'\\]"):
        project.validate()


def test_an_envelope_by_time_needs_a_case_with_a_time():
    project, edges, face = _cantilever_plate()
    project.load_case().add_pressure(project.face(face), 10.0, envelope={"by": "time", "start": 1.0})
    with pytest.raises(ProjectError, match="needs a case with a time|has none"):
        project.validate()


# ----------------------------------------------------------------------
# commands, persistence, provenance
# ----------------------------------------------------------------------
def test_series_commands_undo_redo_and_delete_take_the_steps_with_them():
    project, edges, face, stack = _series(duration=1.0, step=0.1)
    stack.run(cmd.AddPressure(project.face(face), 5.0, case="S"))
    assert len(project.series_children("S")) == 10
    stack.run(cmd.SetLoadSeriesTimes("S", duration=2.0, step=0.5))
    assert [s.time for s in project.series_children("S")] == [0.0, 0.5, 1.0, 1.5]
    stack.undo()
    assert len(project.series_children("S")) == 10
    stack.redo()
    assert len(project.series_children("S")) == 4
    with pytest.raises(ProjectError, match="step of the load series"):
        stack.run(cmd.DeleteLoadCase(project.series_children("S")[0].name))
    stack.run(cmd.DeleteLoadCase("S"))
    assert "S" not in project.load_cases and not project.series_children("S")
    stack.undo()
    assert len(project.series_children("S")) == 4 and project.load_cases["S"].pressures
    stack.undo(); stack.undo(); stack.undo()  # set times, add pressure, add series
    assert "S" not in project.load_cases and not project.series_children("S")
    stack.redo()
    assert len(project.series_children("S")) == 10


def test_the_file_stores_the_series_once_and_rebuilds_the_steps():
    project, edges, face, stack = _series(duration=60.0, step=0.1, parameters={"a": 2.0})
    stack.run(cmd.AddPressure(project.face(face), 5.0, case="S",
                              envelope={"start": 20, "stop": 300, "expression": "a * sin(t)"}))
    data = project_to_dict(project)
    assert data["anyfem"]["format"] == 11
    definition = next(c for c in data["load_cases"] if c["name"] == "S")
    assert len(definition["times"]) == 600 and len(definition["pressures"]) == 1
    assert definition["pressures"][0]["envelope"] == {
        "by": "number", "start": 20.0, "stop": 300.0, "expression": "a * sin(t)"}
    restored = project_from_dict(data)
    assert len(restored.series_children("S")) == 600
    assert restored.load_cases["S"].pressures[0].envelope == project.load_cases["S"].pressures[0].envelope
    assert [c.id for c in restored.series_children("S")] == [c.id for c in project.series_children("S")]
    assert project_to_dict(restored) == data
    # Without a series or envelope the file stays at its earlier format.
    plain, *_ = _cantilever_plate()
    assert project_to_dict(plain)["anyfem"]["format"] == 8


def test_an_envelope_in_a_file_is_validated_when_it_is_read():
    from anyfem.io.project_file import ProjectFileError

    project, edges, face, stack = _series()
    stack.run(cmd.AddPressure(project.face(face), 5.0, case="S", envelope={"start": 2}))
    data = project_to_dict(project)
    definition = next(c for c in data["load_cases"] if c["name"] == "S")
    definition["pressures"][0]["envelope"] = {"start": 9, "stop": 2}
    with pytest.raises(ProjectFileError, match="before its start"):
        project_from_dict(data)


def test_a_change_to_the_series_definition_makes_its_steps_stale():
    project, edges, face, stack = _series()
    stack.run(cmd.AddPressure(project.face(face), 5.0, case="S"))
    batch = AnalysisDefinition("batch", type="batch_linear_static", target_kind="load_cases", target_id="batch")
    single = AnalysisDefinition("one", target_kind="load_case", target_id=project.series_children("S")[3].name)
    before = (analysis_hash(batch, None, document=project_to_dict(project)),
              analysis_hash(single, None, document=project_to_dict(project)))
    load = project.load_cases["S"].pressures[0]
    stack.run(cmd.EditAttribute(type(load)(ref=load.ref, value=6.0, region=load.region, id=load.id)))
    after = (analysis_hash(batch, None, document=project_to_dict(project)),
             analysis_hash(single, None, document=project_to_dict(project)))
    assert after[0] != before[0] and after[1] != before[1]

# ----------------------------------------------------------------------
# a long history is solved through a few load shapes
# ----------------------------------------------------------------------
def _wave_series(steps=24):
    from anyfem.model import WetSurface

    project, edges, face = _cantilever_plate()
    project.add_wet_surface(WetSurface(project.face(face), fluid_side="negative_normal"))
    stack = _connect(project, (project.edge(edges[1]),), name="end")
    stack.run(cmd.AddLoadSeries("S", duration=steps * 0.1, step=0.1, parameters={"omega": 6.0}))
    stack.run(cmd.AddWetPressure(expression="1000 * max(0, 1 + sin(omega * t - 3 * x))", case="S"))
    stack.run(cmd.AddConnectionLoad("end", moment=(0, 0, 500.0), case="S",
                                    envelope={"expression": "sin(2 * t)"}))
    return project


def test_the_compressed_batch_matches_solving_every_case():
    project = _wave_series(24)
    full = solve_linear_static_many(project, target_size=0.25, load_tolerance=None)
    fast = solve_linear_static_many(project, target_size=0.25, load_tolerance=1.0e-9)
    note = fast.info["load_compression"]
    assert "load_compression" not in full.info
    assert note["cases"] == 24 and note["solved_load_shapes"] < 12
    assert note["check_error_relative_to_largest_case"] < 1.0e-8
    assert fast.case_names == full.case_names
    scale = max(np.linalg.norm(s.displacements) for s in full.shapes)
    worst = max(np.linalg.norm(a.displacements - b.displacements)
                for a, b in zip(fast.shapes, full.shapes))
    assert worst / scale < 1.0e-8
    np.testing.assert_allclose(fast.connection_history("end")["rotation"],
                               full.connection_history("end")["rotation"],
                               rtol=1e-6, atol=1e-9 * np.abs(full.connection_history("end")["rotation"]).max())
    # Each step still keeps its own solver case for reactions and recovery.
    assert fast.shapes[3].built.load_case is not fast.shapes[4].built.load_case


def test_a_looser_tolerance_solves_fewer_shapes_and_reports_its_error():
    from anyfem.model import WetSurface

    project = _wave_series(100)
    # A short wavelength makes the sweep of the waterline across elements a
    # field with many shapes, most of them small.
    wave = project.load_cases["S"].wet_pressures[0]
    project.load_cases["S"].wet_pressures[0] = type(wave)(
        expression="1000 * max(0, 0.3 + sin(omega * t - 9 * x + 5 * y))", id=wave.id)
    tight = solve_linear_static_many(project, target_size=0.25, load_tolerance=1.0e-9)
    loose = solve_linear_static_many(project, target_size=0.25, load_tolerance=1.0e-2)
    a, b = tight.info["load_compression"], loose.info["load_compression"]
    assert b["solved_load_shapes"] < a["solved_load_shapes"]
    assert b["dropped_load_norm_relative"] > a["dropped_load_norm_relative"]
    assert b["check_error_relative_to_largest_case"] < 1.0e-1
    assert a["check_error_relative_to_largest_case"] < 1.0e-8


def test_few_cases_gravity_and_prescribed_motion_are_solved_in_full():
    short = _wave_series(6)
    assert "load_compression" not in solve_linear_static_many(short, target_size=0.25).info

    heavy = _wave_series(24)
    heavy.load_case("S").set_acceleration(0.0, 0.0, -9.81)
    assert "load_compression" not in solve_linear_static_many(heavy, target_size=0.25).info

    moved = _wave_series(24)
    from anyfem.model import prescribed

    clamp = moved.supports[0].ref
    moved.supports.clear()
    moved.add_support(prescribed(clamp, ux=0.0, uy=0.0, uz=0.001, rx=0.0, ry=0.0, rz=0.0))
    assert "load_compression" not in solve_linear_static_many(moved, target_size=0.25).info
