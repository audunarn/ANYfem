"""Prepared static adapter: ownership, ingress capture and fail-closed contracts.

The solver is replaced by a spy owner, so these tests exercise only the
ANYfem adapter: the owned application snapshot, canonical load construction,
ingress capture, output selection, the result facade and explicit refusal
when the solver cannot prepare an evaluator.  Real-solver checks live in
test_prepared_static_integration.py.
"""

from dataclasses import dataclass, replace
from types import SimpleNamespace

import numpy as np
import pytest
import anysolver

from anyfem.model import ProjectError
from anyfem.solve import (
    PreparedStaticUnavailableError,
    prepare_linear_static_evaluator,
)
from anyfem.solve.build import build_fe_model
from test_static_batch_envelope_factors import _project


class _SpyOwner:
    """Minimal stand-in for the solver's prepared evaluator contract."""

    def __init__(self, model, output_dofs, arguments, state):
        self.output_dofs = output_dofs
        self.arguments = arguments
        self._state = state
        self._state["prepared"] += 1

    def iter_batches(self, cases, *, batch_size=32, progress_callback=None,
                     cancellation_token=None, **kwargs):
        source = iter(cases)
        index = 0
        try:
            while True:
                records = []
                for _ in range(batch_size):
                    try:
                        record = next(source)
                    except StopIteration:
                        break
                    records.append(record)
                    self._state["observed"].append(record)
                if not records:
                    return
                if progress_callback is not None:
                    progress_callback({"event": "static_batch_complete",
                                       "completed": index + len(records)})
                count = len(records)
                dofs = self.output_dofs
                yield SimpleNamespace(
                    input_indices=tuple(range(index, index + count)),
                    case_ids=tuple(record.case_id for record in records),
                    times=tuple(record.time for record in records),
                    output_dofs=dofs,
                    displacements=np.tile(
                        np.arange(len(dofs), dtype=float)[:, None], (1, count)),
                    case_results=tuple(
                        {"status": "converged", "element_stresses": {}, "reactions": {}}
                        for _ in records),
                    diagnostics={"status": "converged", "batch_size": count},
                )
                index += count
        finally:
            close = getattr(source, "close", None)
            if close is not None:
                close()

    def close(self):
        self._state["closed"] += 1

    def diagnostics(self):
        return {"status": "prepared", "preparations": self._state["prepared"]}

    def envelope_result(self):
        from copy import deepcopy
        return deepcopy(self._state.get("envelope"))


@pytest.fixture
def solver_spy(monkeypatch):
    state = {"prepared": 0, "closed": 0, "observed": [], "arguments": None}

    def prepare(model, **kwargs):
        output_dofs = kwargs.get("output_dofs")
        if output_dofs is None:
            output_dofs = np.arange(model.mesh.dof_manager.total_dofs)
        else:
            output_dofs = np.asarray(output_dofs, dtype=np.intp)
        state["arguments"] = kwargs
        return _SpyOwner(model, output_dofs, kwargs, state)

    monkeypatch.setattr(anysolver, "prepare_static_evaluator", prepare, raising=False)
    return state


def _solver_load(record):
    return record.load_case


def test_envelope_configuration_and_detached_facade(solver_spy):
    from anysolver import RecoveryConfig
    project, _series, _points, _face = _project()
    config = RecoveryConfig(components=["membrane_xx"], include_stresses=True)
    with prepare_linear_static_evaluator(project, target_size=1.0,
                                        envelope_recovery_config=config) as owner:
        assert solver_spy["arguments"]["envelope_recovery_config"] is config
        assert owner.envelope_result() is None
        solver_spy["envelope"] = SimpleNamespace(complete=False, cases_processed=2,
            extrema=[{"value": np.array([3.0])}], snapshot_identity="snapshot",
            diagnostics={"certified": True})
        summary = owner.envelope_result()
        assert not summary.complete and summary.cases_processed == 2
        assert summary.snapshot_identity == "snapshot"
        summary.extrema[0]["value"][0] = 99
        summary.provenance["source_project"] = "changed"
        summary.diagnostics["certified"] = False
        fresh = owner.envelope_result()
        assert fresh.extrema[0]["value"][0] == 3
        assert fresh.provenance["source_project"] == project.name
        assert fresh.diagnostics["certified"]
    assert owner.envelope_result().cases_processed == 2


def _assert_same_loads(actual, expected):
    assert actual.follower_pressure == expected.follower_pressure
    assert actual.pressure_loads.keys() == expected.pressure_loads.keys()
    assert actual.nodal_loads.keys() == expected.nodal_loads.keys()
    for node, load in expected.nodal_loads.items():
        np.testing.assert_array_equal(actual.nodal_loads[node], load)
    for element, load in expected.pressure_loads.items():
        np.testing.assert_array_equal(actual.pressure_loads[element], load)
    if expected.gravity is None:
        assert actual.gravity is None
    else:
        np.testing.assert_array_equal(actual.gravity, expected.gravity)


@pytest.mark.parametrize(
    "family",
    ["point", "pressure", "wet", "connection", "line", "traction", "acceleration", "gravity"],
)
def test_every_load_family_matches_the_canonical_build(family, solver_spy):
    project, series, points, face = _project()
    envelope = {"expression": "1 + t*t"}
    if family == "point":
        series.add_point_load(project.point(points[0]), force=(2, 3, 4),
                               moment=(5, 6, 7), envelope=envelope)
    elif family == "pressure":
        series.add_pressure(project.face(face), 11, envelope=envelope)
    elif family == "wet":
        series.add_wet_pressure(expression="17 * (x + t*t)", envelope=envelope)
    elif family == "connection":
        series.add_connection_load(project.connection_for("end").id, force=(2, 3, 4),
                                   moment=(5, 6, 7), envelope=envelope)
    elif family == "line":
        series.add_line_load(project.edge(next(iter(project.geometry.edges))),
                             (2, 3, 4), envelope=envelope)
    elif family == "traction":
        series.add_surface_traction(project.face(face), (2, 3, 4), envelope=envelope)
    elif family == "acceleration":
        series.set_acceleration(2, 3, 4)
    else:
        series.set_gravity()
    step = project.series_children("series")[1]
    mesh = project.generate_mesh(1.0)
    expected = build_fe_model(project, mesh, step.name).load_case
    with prepare_linear_static_evaluator(project, mesh=mesh) as evaluator:
        blocks = list(evaluator.iter_batches([step.name]))
    assert len(blocks) == 1
    assert blocks[0].case_ids == (step.id,)
    assert blocks[0].labels == (step.name,)
    _assert_same_loads(_solver_load(solver_spy["observed"][0]), expected)


def test_case_and_per_load_envelope_factors_stay_independent(solver_spy):
    project, series, points, face = _project()
    series.add_point_load(project.point(points[0]), force=(5, 0, 0),
                          envelope={"expression": "2.0"})
    series.add_pressure(project.face(face), 11.0, envelope={"expression": "0.0"})
    series.add_wet_pressure(value=17.0, envelope={"expression": "3.0"})
    connection_id = project.connection_for("end").id
    series.add_connection_load(connection_id, force=(0, 7, 0),
                                envelope={"expression": "4.0"})
    step = project.series_children("series")[1]
    project.add_combination("scaled", {step.name: -2.5})
    mesh = project.generate_mesh(1.0)
    expected = build_fe_model(project, mesh, None, combination="scaled").load_case
    from anyfem import StaticLoadRequest

    with prepare_linear_static_evaluator(project, mesh=mesh) as evaluator:
        list(evaluator.iter_batches(
            [StaticLoadRequest("scaled", combination="scaled")]))
    _assert_same_loads(_solver_load(solver_spy["observed"][0]), expected)
    # The combination factor (-2.5) and each load's own envelope factor stay
    # separate: the point load keeps envelope 2.0, the pressure vanishes.
    record = _solver_load(solver_spy["observed"][0])
    assert sum(values[0] for values in record.nodal_loads.values()) == pytest.approx(
        -2.5 * 2.0 * 5.0)
    for element, values in expected.pressure_loads.items():
        np.testing.assert_array_equal(record.pressure_loads[element], values)


def test_source_mutation_after_preparation_cannot_change_results(solver_spy):
    project, series, points, _face = _project()
    series.add_point_load(project.point(points[0]), force=(2, 0, 0))
    series.add_wet_pressure(expression="17*(x+t)")
    mesh = project.generate_mesh(1.0)
    step = project.series_children("series")[0]
    expected = build_fe_model(project, mesh, step.name).load_case
    evaluator = prepare_linear_static_evaluator(project, mesh=mesh)
    # Mutate and delete the source model after preparation.
    series.add_point_load(project.point(points[1]), force=(99, 99, 99))
    del project.load_cases[step.name]
    mesh.nodes[next(iter(mesh.nodes))][0] += 5.0
    project.geometry.add_points([(9, 9, 9)])
    blocks = list(evaluator.iter_batches([step.name]))
    _assert_same_loads(_solver_load(solver_spy["observed"][0]), expected)
    assert blocks[0].labels == (step.name,)


def test_late_source_cases_are_not_visible(solver_spy):
    project, series, points, _face = _project()
    series.add_point_load(project.point(points[0]), force=(1, 0, 0))
    mesh = project.generate_mesh(1.0)
    with prepare_linear_static_evaluator(project, mesh=mesh) as evaluator:
        late = project.load_case("late")
        late.add_point_load(project.point(points[0]), force=(3, 0, 0))
        with pytest.raises(ProjectError, match="no load case named 'late'"):
            list(evaluator.iter_batches(["late"]))
    assert not solver_spy["observed"]


def test_external_records_are_captured_once_at_ingress(solver_spy):
    project, _series, points, _face = _project()
    external = project.load_case("external")
    external.add_point_load(project.point(points[0]), force=(2, 0, 0))
    del project.load_cases["external"]
    mesh = project.generate_mesh(1.0)
    with prepare_linear_static_evaluator(project, mesh=mesh) as evaluator:
        blocks = list(evaluator.iter_batches([external]))
        assert blocks[0].labels == ("external",)
        first = _solver_load(solver_spy["observed"][0])
        # Caller mutation after ingress cannot change the captured record.
        external.point_loads[0] = replace(
            external.point_loads[0], force=np.array([42.0, 0.0, 0.0]))
        np.testing.assert_array_equal(
            first.nodal_loads[mesh.node_of_vertex[points[0]]][:3], [2.0, 0.0, 0.0])
        # A fresh ingress captures the record's current state once.
        list(evaluator.iter_batches([external]))
        second = _solver_load(solver_spy["observed"][1])
        np.testing.assert_array_equal(
            second.nodal_loads[mesh.node_of_vertex[points[0]]][:3], [42.0, 0.0, 0.0])


def test_wet_provider_evaluates_once_per_sample_outside_solver_spans(
        solver_spy, monkeypatch):
    from anyfem.solve import build as builder

    project, series, _points, _face = _project()
    series.add_wet_pressure(expression="17*(x+t)")
    mesh = project.generate_mesh(1.0)
    step = project.series_children("series")[0]
    calls = {"count": 0}
    original = builder.field_pressure

    def counting(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(builder, "field_pressure", counting)
    with prepare_linear_static_evaluator(project, mesh=mesh) as evaluator:
        list(evaluator.iter_batches([step.name, step.name, step.name]))
    assert calls["count"] == 3
    # The solver receives numeric element pressures, not providers.
    for record in solver_spy["observed"]:
        assert record.load_case.pressure_loads
        assert all(isinstance(value, float) for value in record.load_case.pressure_loads.values())


def test_provider_error_fails_the_case_without_bogus_output(solver_spy):
    project, series, _points, _face = _project()
    series.add_wet_pressure(expression="17*(x+undefined_symbol)")
    mesh = project.generate_mesh(1.0)
    step = project.series_children("series")[0]
    with prepare_linear_static_evaluator(project, mesh=mesh) as evaluator:
        with pytest.raises(ProjectError):
            list(evaluator.iter_batches([step.name]))
    assert not solver_spy["observed"]


def test_provider_mutating_observed_geometry_fails_closed(solver_spy, monkeypatch):
    from anyfem.solve import build as builder

    project, series, _points, _face = _project()
    series.add_wet_pressure(expression="17*(x+t)")
    mesh = project.generate_mesh(1.0)
    step = project.series_children("series")[0]
    evaluator = prepare_linear_static_evaluator(project, mesh=mesh)
    owned_geometry = evaluator._owned.project.geometry
    original = builder.field_pressure

    def mutating(*args, **kwargs):
        owned_geometry.add_points([(5.0, 5.0, 5.0)])
        return original(*args, **kwargs)

    monkeypatch.setattr(builder, "field_pressure", mutating)
    with pytest.raises(ProjectError, match="owned geometry changed"):
        list(evaluator.iter_batches([step.name]))
    assert not solver_spy["observed"]
    evaluator.close()


def test_missing_solver_api_fails_closed_without_falling_back(monkeypatch):
    monkeypatch.delattr(anysolver, "prepare_static_evaluator", raising=False)
    project, series, points, _face = _project()
    series.add_point_load(project.point(points[0]), force=(1, 0, 0))
    mesh = project.generate_mesh(1.0)
    with pytest.raises(PreparedStaticUnavailableError, match="prepare_static_evaluator"):
        prepare_linear_static_evaluator(project, mesh=mesh)


def test_close_is_deterministic_and_later_use_fails(solver_spy):
    project, series, points, _face = _project()
    series.add_point_load(project.point(points[0]), force=(1, 0, 0))
    mesh = project.generate_mesh(1.0)
    evaluator = prepare_linear_static_evaluator(project, mesh=mesh)
    evaluator.close()
    evaluator.close()
    assert solver_spy["closed"] == 1
    with pytest.raises(ValueError, match="closed"):
        evaluator.iter_batches([])
    diagnostics = evaluator.diagnostics()
    assert diagnostics["status"] == "closed"
    assert diagnostics["solver"]["preparations"] == 1
    with prepare_linear_static_evaluator(project, mesh=mesh) as managed:
        assert managed is not None
    assert solver_spy["closed"] == 2


def test_diagnostics_report_preparation_and_snapshot(solver_spy):
    project, series, points, _face = _project()
    series.add_point_load(project.point(points[0]), force=(1, 0, 0))
    mesh = project.generate_mesh(1.0)
    step = project.series_children("series")[0]
    with prepare_linear_static_evaluator(project, mesh=mesh) as evaluator:
        list(evaluator.iter_batches([step.name, step.name]))
        diagnostics = evaluator.diagnostics()
    assert diagnostics["status"] == "prepared"
    assert diagnostics["prepared_cases"] == 2
    assert diagnostics["snapshot"]["owned_snapshot"] is True
    assert diagnostics["snapshot"]["source_project"] == project.name
    assert step.name in diagnostics["snapshot"]["load_cases"]
    assert solver_spy["arguments"]["load_families"] is None


def test_output_selection_and_facade_metadata_cannot_mutate_the_snapshot(solver_spy):
    project, series, points, _face = _project()
    series.add_point_load(project.point(points[0]), force=(1, 0, 0))
    mesh = project.generate_mesh(1.0)
    node = mesh.node_of_vertex[points[0]]
    step = project.series_children("series")[0]
    with prepare_linear_static_evaluator(
            project, mesh=mesh, output_nodes=[node], output_connections=["end"]) as evaluator:
        block = next(evaluator.iter_batches([step.name]))
        values = block.node_displacement(node)
        assert values.shape == (6,)
        assert np.all(np.isfinite(values))
        motion = block.connection_motion("end")
        assert motion["displacement"].shape == (3,)
        missing = next(n for n in mesh.nodes if n != node and n not in
                       [item.reference_node for item in block.connections.values()])
        with pytest.raises(KeyError, match="not selected"):
            block.node_displacement(missing)
        provenance = block.snapshot_provenance
        assert provenance["owned_snapshot"] is True
        provenance["owned_snapshot"] = False
        assert block.snapshot_provenance["owned_snapshot"] is True
        assert evaluator.diagnostics()["snapshot"]["owned_snapshot"] is True
        assert block.labels == (step.name,)
        assert block.diagnostics["status"] == "converged"


def test_selection_conflicts_are_explicit(solver_spy):
    from anysolver import RecoveryConfig

    project, _series, _points, _face = _project()
    mesh = project.generate_mesh(1.0)
    with pytest.raises(ValueError, match="conflicts"):
        prepare_linear_static_evaluator(
            project, mesh=mesh, output_nodes=[1],
            recovery_config=RecoveryConfig(node_ids=[2]))


def test_explicit_dofs_reject_incomplete_node_selection(solver_spy):
    project, series, points, _face = _project()
    mesh = project.generate_mesh(1.0)
    node = mesh.node_of_vertex[points[0]]
    built = build_fe_model(project, mesh, load_case=None, require_loads=False)
    rotation = built.fe_model.mesh.nodes[node].dofs[3]
    with pytest.raises(ValueError, match="explicit output_dofs"):
        prepare_linear_static_evaluator(
            built=built, output_nodes=[node], output_dofs=[rotation])
    with pytest.raises(ValueError, match="explicit output_dofs"):
        prepare_linear_static_evaluator(
            built=built, output_refs=[project.point(points[0])], output_dofs=[rotation])
    with pytest.raises(ValueError, match="explicit output_dofs"):
        prepare_linear_static_evaluator(
            built=built, output_connections=["end"], output_dofs=[rotation])


def test_built_model_rejects_a_different_mesh(solver_spy):
    from copy import copy

    project, _series, _points, _face = _project()
    mesh = project.generate_mesh(1.0)
    built = build_fe_model(project, mesh, load_case=None, require_loads=False)
    with pytest.raises(ValueError, match="mesh conflicts"):
        prepare_linear_static_evaluator(built=built, mesh=copy(mesh))


def test_lazy_requests_labels_times_and_progress(solver_spy):
    from anyfem import StaticLoadRequest

    project, series, points, face = _project()
    series.add_point_load(project.point(points[0]), force=(2, 0, 0),
                          envelope={"expression": "t*t"})
    series.add_pressure(project.face(face), 11, envelope={"start": 2, "expression": "1+t"})
    step = project.series_children("series")[0]
    project.add_combination("twice", {step.name: 2.0})
    pulled = []
    events = []

    def requests():
        for index, time in enumerate((0.0, 0.5, 1.0)):
            pulled.append(index)
            yield StaticLoadRequest("same label", load_case=step.name, time=time)
        yield StaticLoadRequest("combination", combination="twice", time=1.0)

    with prepare_linear_static_evaluator(
            project, mesh=project.generate_mesh(1.0)) as evaluator:
        iterator = evaluator.iter_batches(requests(), batch_size=2,
                                          progress=events.append)
        first = next(iterator)
        assert pulled == [0, 1]
        second = next(iterator)
        assert first.case_ids == ("same label", "same label")
        assert first.times == (0.0, 0.5)
        assert first.labels == (step.name, step.name)
        assert second.input_indices == (2, 3)
        assert events
        iterator.close()


def test_empty_input_yields_nothing_and_multiple_streams_share_the_snapshot(solver_spy):
    project, series, points, _face = _project()
    series.add_point_load(project.point(points[0]), force=(1, 0, 0))
    mesh = project.generate_mesh(1.0)
    step = project.series_children("series")[0]
    with prepare_linear_static_evaluator(project, mesh=mesh) as evaluator:
        assert list(evaluator.iter_batches([])) == []
        assert list(evaluator.iter_batches([step.name]))
        assert list(evaluator.iter_batches([step.name]))
    assert len(solver_spy["observed"]) == 2
    assert solver_spy["prepared"] == 1


def test_no_source_model_retained_and_no_per_case_structural_scan(solver_spy, monkeypatch):
    import gc
    import weakref
    from anyfem.solve.static_batch import _LoadBuildContext
    project, series, points, _face = _project()
    series.add_point_load(project.point(points[0]), force=(2, 0, 0))
    built = build_fe_model(project, project.generate_mesh(1.0), load_case=None,
                           require_loads=False)
    name = project.series_children("series")[0].name
    references = [weakref.ref(obj) for obj in (project, built, built.fe_model)]
    evaluator = prepare_linear_static_evaluator(built=built)
    del built, series, project
    gc.collect()
    assert all(reference() is None for reference in references)
    assert evaluator._owned.fe_model is None
    def forbidden(*args):
        raise AssertionError("whole-model scan during case evaluation")
    monkeypatch.setattr(_LoadBuildContext, "_structure", forbidden)
    with evaluator:
        assert list(evaluator.iter_batches([name]))


def test_facade_and_diagnostics_metadata_do_not_mutate_snapshot(solver_spy):
    project, series, points, _face = _project()
    series.add_point_load(project.point(points[0]), force=(2, 0, 0))
    name = project.series_children("series")[0].name
    with prepare_linear_static_evaluator(project, mesh=project.generate_mesh(1.0)) as evaluator:
        first = next(evaluator.iter_batches([name]))
        first.provenance["geometry_entities"].clear()
        first.connections.clear()
        metadata = evaluator.diagnostics()
        metadata["snapshot"]["geometry_entities"].clear()
        second = next(evaluator.iter_batches([name]))
        assert second.snapshot_provenance["geometry_entities"]
        assert second.connections
        assert evaluator.diagnostics()["snapshot"]["geometry_entities"]


def test_close_active_stream_and_preserve_primary_cleanup_error(solver_spy):
    project, series, points, _face = _project()
    series.add_point_load(project.point(points[0]), force=(2, 0, 0))
    name = project.series_children("series")[0].name
    closed = []
    def requests():
        try:
            while True:
                yield name
        finally:
            closed.append(True)
    evaluator = prepare_linear_static_evaluator(project, mesh=project.generate_mesh(1.0))
    stream = evaluator.iter_batches(requests(), batch_size=1)
    next(stream)
    evaluator.close()
    assert closed == [True]
    stream.close()
    evaluator.close()
    assert solver_spy["closed"] == 1
    with pytest.raises(ValueError, match="closed"):
        next(evaluator.iter_batches([name]))
    evaluator = prepare_linear_static_evaluator(project, mesh=project.generate_mesh(1.0))
    def failed_close():
        raise RuntimeError("cleanup failed")
    evaluator._owner.close = failed_close
    primary = ValueError("primary failure")
    with pytest.raises(ValueError, match="primary failure") as captured:
        with evaluator:
            raise primary
    assert captured.value is primary
    assert "cleanup failed" in str(primary.static_batch_secondary_errors[0])


def test_cancellation_stops_ingress_and_closes_requests(solver_spy):
    from anysolver.control import CancellationToken, SolveCancelled
    project, series, points, _face = _project()
    series.add_point_load(project.point(points[0]), force=(2, 0, 0))
    name = project.series_children("series")[0].name
    token = CancellationToken()
    closed = []
    def requests():
        try:
            yield name
            yield name
        finally:
            closed.append(True)
    with prepare_linear_static_evaluator(project, mesh=project.generate_mesh(1.0)) as evaluator:
        stream = evaluator.iter_batches(requests(), batch_size=1, cancellation_token=token)
        next(stream)
        token.cancel()
        with pytest.raises(SolveCancelled):
            next(stream)
    assert closed == [True]
    assert len(solver_spy["observed"]) == 1


def test_application_snapshot_storage_is_reserved_before_solver(solver_spy):
    from anysolver import ResourceConfig
    project, _series, _points, _face = _project()
    mesh = project.generate_mesh(1.0)
    budget = 10_000_000
    with prepare_linear_static_evaluator(project, mesh=mesh,
            resource_config=ResourceConfig(memory_limit_bytes=budget)) as evaluator:
        snapshot = evaluator.diagnostics()["snapshot"]
        assert snapshot["application_retained_input_bytes"] > 0
        assert snapshot["retained_input_case_count"] == len(project.load_cases)
        assert solver_spy["arguments"]["resource_config"].memory_limit_bytes == (
            budget - snapshot["application_retained_input_bytes"]
            - snapshot["application_case_cache_limit_bytes"])
    with pytest.raises(ProjectError, match="snapshot exceeds"):
        prepare_linear_static_evaluator(project, mesh=mesh,
            resource_config=ResourceConfig(memory_limit_bytes=1))


@pytest.mark.parametrize("kind", ["code", "code_vectorized", "table", "mapping"])
def test_wet_sources_match_canonical_and_keep_owned_samples(kind, solver_spy, tmp_path, monkeypatch):
    from anyfem.model import PressureTable
    from anyfem.solve import build as builder
    monkeypatch.setenv("ANYLOADS_TRUSTED_CODE_FILE", str(tmp_path / "trusted.json"))
    project, series, _points, face = _project()
    mesh = project.generate_mesh(1.0)
    if kind in ("code", "code_vectorized"):
        code = "def pressure(t, x, parameters):\n    return 17*(x+t*t)\n"
        if kind == "code_vectorized":
            code = ("def pressure(t, x, parameters):\n"
                    "    x[:] = x + 1\n"
                    "    parameters['injected'] = 1\n"
                    "    return 17*(x+t*t)\n")
        series.add_wet_pressure(code=code, code_vectorized=kind == "code_vectorized")
    elif kind == "table":
        path = tmp_path / "pressures.csv"
        rows = ["case,x,y,z,pressure"]
        for time in (0.0, 0.5):
            for element in mesh.quads or mesh.tris:
                centroid, _ = builder._element_centroid_normal(mesh, element)
                rows.append(",".join(str(value) for value in (time, *centroid, 17*(centroid[0]+time*time))))
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        series.add_wet_pressure(table=PressureTable.from_file(path, key="time"))
    else:
        series.add_wet_pressure(mapping={face: 17.0}, mapping_key="face")
    names = [step.name for step in project.series_children("series")]
    expected = [build_fe_model(project, mesh, name).load_case for name in names]
    with prepare_linear_static_evaluator(project, mesh=mesh) as evaluator:
        list(evaluator.iter_batches(names))
        list(evaluator.iter_batches(names))
        assert "injected" not in evaluator._owned.project.load_cases["series"].parameters
    for index, record in enumerate(solver_spy["observed"]):
        _assert_same_loads(record.load_case, expected[index % len(expected)])


def test_ingress_primary_error_survives_source_close_failure(solver_spy):
    project, _series, _points, _face = _project()
    class BrokenRequests:
        def __iter__(self):
            return self
        def __next__(self):
            raise ValueError("input failed")
        def close(self):
            raise RuntimeError("source close failed")
    with prepare_linear_static_evaluator(project, mesh=project.generate_mesh(1.0)) as evaluator:
        with pytest.raises(ValueError, match="input failed") as caught:
            list(evaluator.iter_batches(BrokenRequests()))
    assert any("source close failed" in str(error)
               for error in caught.value.static_batch_secondary_errors)
    assert not solver_spy["observed"]


def test_owner_closes_every_active_request_even_if_first_close_fails(solver_spy):
    project, series, points, _face = _project()
    series.add_point_load(project.point(points[0]), force=(2, 0, 0))
    name = project.series_children("series")[0].name
    closed = []
    def requests(index):
        try:
            while True:
                yield name
        finally:
            closed.append(index)
            if index == 0:
                raise RuntimeError("first request close failed")
    evaluator = prepare_linear_static_evaluator(project, mesh=project.generate_mesh(1.0))
    streams = [evaluator.iter_batches(requests(index), batch_size=1) for index in (0, 1)]
    for stream in streams:
        next(stream)
    with pytest.raises(RuntimeError, match="first request close failed"):
        evaluator.close()
    assert closed == [0, 1]
    assert solver_spy["closed"] == 1
    for stream in streams:
        stream.close()


@pytest.mark.parametrize("mutation", ["mesh", "definitions", "mechanics", "constraints"])
def test_joint_capture_refuses_mixed_source_snapshot(mutation, solver_spy, monkeypatch):
    from anyfem.solve import prepared_static as adapter
    project, series, points, _ = _project()
    series.add_point_load(project.point(points[0]), force=(2, 0, 0))
    built = build_fe_model(project, project.generate_mesh(1.0), load_case=None, require_loads=False)
    original = adapter._owned_project
    def capture_then_mutate(source):
        owned = original(source)
        if mutation == "mesh":
            built.mesh.nodes[next(iter(built.mesh.nodes))][0] += 0.25
        elif mutation == "definitions":
            series.add_point_load(project.point(points[1]), force=(10, 0, 0))
        elif mutation == "mechanics":
            built.fe_model.mesh.nodes[next(iter(built.fe_model.mesh.nodes))].x += 0.25
        else:
            built.fe_model.boundary_conditions.clear()
        return owned
    monkeypatch.setattr(adapter, "_owned_project", capture_then_mutate)
    with pytest.raises(ProjectError, match="changed during prepared static capture"):
        prepare_linear_static_evaluator(built=built)
    assert solver_spy["closed"] == 1


def test_case_caches_have_reserved_bound_and_clear_on_case_error(solver_spy):
    from anysolver import ResourceConfig
    project, series, points, _ = _project()
    series.add_point_load(project.point(points[0]), force=(2, 0, 0))
    mesh = project.generate_mesh(1.0)
    name = project.series_children("series")[0].name
    with prepare_linear_static_evaluator(project, mesh=mesh,
            resource_config=ResourceConfig(memory_limit_bytes=10_000_000)) as evaluator:
        context = evaluator._context
        assert context.cache_limit_bytes > 0
        context.cache_limit_bytes = 1
        original_cached = context._cached
        def observe_cache(*args):
            result = original_cached(*args)
            assert context._cache_bytes <= context.cache_limit_bytes
            assert not any((context._targets, context._nodes, context._elements,
                            context._wet, context._centroids))
            return result
        context._cached = observe_cache
        for _ in range(3):
            list(evaluator.iter_batches([name]))
            assert context._cache_bytes == 0
            assert not any((context._targets, context._nodes, context._elements,
                            context._wet, context._centroids))
        external = project.load_case("invalid")
        external.add_wet_pressure(expression="undefined_symbol")
        with pytest.raises(ProjectError):
            list(evaluator.iter_batches([external]))
        assert context._cache_bytes == 0
        assert not context._wet
