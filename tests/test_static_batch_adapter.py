"""Public streamed load construction and selected facade contracts."""

from dataclasses import dataclass, replace
from types import SimpleNamespace

import numpy as np
import pytest
import anysolver

from anyfem import StaticLoadRequest, iter_linear_static_batches
from anyfem.model import ProjectError
from anyfem.solve.build import build_fe_model
from anyfem.solve.static_batch import _LoadBuildContext
from test_static_batch_envelope_factors import _project


@dataclass
class _Record:
    case_id: str
    load_case: object
    time: float | None = None


@pytest.fixture
def solver_spy(monkeypatch):
    observed = []
    state = {"closed": False, "arguments": None}
    monkeypatch.setattr(anysolver, "StaticCase", _Record, raising=False)

    def iterator(model, cases, *, batch_size, output_dofs, progress_callback, **kwargs):
        state["arguments"] = kwargs
        dofs = np.arange(model.mesh.dof_manager.total_dofs) if output_dofs is None else np.array(output_dofs)
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
                    observed.append(record)
                if not records:
                    return
                if progress_callback is not None:
                    progress_callback({"event": "static_batch_complete"})
                count = len(records)
                yield SimpleNamespace(
                    input_indices=tuple(range(index, index + count)),
                    case_ids=tuple(record.case_id for record in records),
                    times=tuple(record.time for record in records), output_dofs=dofs,
                    displacements=np.tile(dofs[:, None], (1, count)).astype(float),
                    case_results=tuple({"status": "converged", "element_stresses": {},
                                        "reactions": {}} for _ in records), diagnostics={},
                )
                index += count
        finally:
            state["closed"] = True
            source.close()

    monkeypatch.setattr(anysolver, "iter_linear_batches", iterator, raising=False)
    return observed, state


@pytest.mark.parametrize("family", ["point", "pressure", "wet", "connection", "line", "traction", "acceleration", "gravity"])
def test_public_adapter_keeps_every_load_family(family, solver_spy):
    project, series, points, face = _project()
    envelope = {"expression": "1 + t*t"}
    if family == "point":
        series.add_point_load(project.point(points[0]), force=(2, 3, 4), moment=(5, 6, 7), envelope=envelope)
    elif family == "pressure":
        series.add_pressure(project.face(face), 11, envelope=envelope)
    elif family == "wet":
        series.add_wet_pressure(expression="17 * (x + t*t)", envelope=envelope)
    elif family == "connection":
        series.add_connection_load(project.connection_for("end").id, force=(2, 3, 4), moment=(5, 6, 7), envelope=envelope)
    elif family == "line":
        series.add_line_load(project.edge(next(iter(project.geometry.edges))), (2, 3, 4), envelope=envelope)
    elif family == "traction":
        series.add_surface_traction(project.face(face), (2, 3, 4), envelope=envelope)
    elif family == "acceleration":
        series.set_acceleration(2, 3, 4)
    else:
        series.set_gravity()
    step = project.series_children("series")[1]
    mesh = project.generate_mesh(1.0)
    expected = build_fe_model(project, mesh, step.name).load_case
    blocks = list(iter_linear_static_batches(project, mesh=mesh, load_cases=[step.name]))
    actual = solver_spy[0][0].load_case
    assert blocks[0].case_ids == (step.id,)
    assert blocks[0].labels == (step.name,)
    assert actual.follower_pressure == expected.follower_pressure
    assert actual.pressure_loads.keys() == expected.pressure_loads.keys()
    assert actual.nodal_loads.keys() == expected.nodal_loads.keys()
    for node, load in expected.nodal_loads.items():
        np.testing.assert_array_equal(actual.nodal_loads[node], load)
    for element, load in expected.pressure_loads.items():
        np.testing.assert_array_equal(actual.pressure_loads[element], load)
    np.testing.assert_array_equal(actual.gravity, expected.gravity)
    assert not blocks[0].built.fe_model.load_cases


def test_lazy_nonproportional_records_time_overrides_and_combinations(solver_spy):
    project, series, points, face = _project()
    series.add_point_load(project.point(points[0]), force=(2, 0, 0), envelope={"expression": "t*t"})
    series.add_pressure(project.face(face), 11, envelope={"start": 2, "expression": "1+t"})
    series.add_wet_pressure(expression="17 * (x+t)")
    step = project.series_children("series")[1]
    project.add_combination("twice", {step.name: 2.0})
    pulled = []
    def requests():
        for index, time in enumerate((0.0, 0.5, 1.0)):
            pulled.append(index)
            yield StaticLoadRequest("same label", load_case=step.name, time=time)
        yield StaticLoadRequest("combination", combination="twice", time=1.0)
    iterator = iter_linear_static_batches(
        project, mesh=project.generate_mesh(1.0), load_cases=requests(), batch_size=2,
    )
    first = next(iterator)
    assert pulled == [0, 1]
    second = next(iterator)
    assert first.case_ids == ("same label", "same label")
    assert first.times == (0.0, 0.5)
    assert second.input_indices == (2, 3)
    records = solver_spy[0]
    # The point force and pressure/wet field have distinct time dependence.
    point = [sum(load[0] for load in record.load_case.nodal_loads.values()) for record in records]
    assert point == pytest.approx([0.0, 0.5, 2.0, 4.0])
    element = next(iter(records[0].load_case.pressure_loads))
    pressures = [record.load_case.pressure_loads[element] for record in records]
    assert pressures[1] != 0.25 * pressures[2]
    assert pressures[3] == pytest.approx(2.0 * pressures[2])
    iterator.close()
    assert solver_spy[1]["closed"]


def test_empty_input_selected_facade_and_close(solver_spy):
    project, series, points, _face = _project()
    series.add_point_load(project.point(points[0]), force=(1, 0, 0))
    mesh = project.generate_mesh(1.0)
    assert list(iter_linear_static_batches(project, mesh=mesh, load_cases=[])) == []
    node = mesh.node_of_vertex[points[0]]
    block = next(iter_linear_static_batches(
        project, mesh=mesh, load_cases=[project.series_children("series")[0].name],
        output_nodes=[node], output_connections=["end"],
    ))
    np.testing.assert_array_equal(block.node_displacement(node), block.built.node_dofs(node))
    assert block.connection_motion("end")["displacement"].shape == (3,)
    missing = next(n for n in mesh.nodes if n != node and n not in
                   [item.reference_node for item in block.connections.values()])
    with pytest.raises(KeyError, match="not selected"):
        block.node_displacement(missing)


@pytest.mark.parametrize("boundary", ["provider", "consumer", "progress"])
def test_foreign_boundaries_reject_structural_mutation(boundary, solver_spy):
    project, series, points, _face = _project()
    series.add_point_load(project.point(points[0]), force=(1, 0, 0))
    mesh = project.generate_mesh(1.0)
    step = project.series_children("series")[0]
    def requests():
        if boundary == "provider":
            mesh.nodes[next(iter(mesh.nodes))][0] += 0.1
        yield step.name
        yield step.name
    def progress(_event):
        if boundary == "progress":
            mesh.nodes[next(iter(mesh.nodes))][0] += 0.1
    iterator = iter_linear_static_batches(project, mesh=mesh, load_cases=requests(), batch_size=1,
                                          progress_callback=progress)
    if boundary == "consumer":
        next(iterator)
        project.plate_sections["plate"] = replace(project.plate_sections["plate"], thickness=0.02)
    with pytest.raises(ProjectError, match="structural model changed"):
        next(iterator)
    assert solver_spy[1]["closed"]


def test_selection_conflicts_and_invalid_requests_are_explicit(solver_spy):
    from anysolver import RecoveryConfig
    project, _series, _points, _face = _project()
    mesh = project.generate_mesh(1.0)
    with pytest.raises(ValueError, match="exactly one"):
        StaticLoadRequest("invalid")
    with pytest.raises(ValueError, match="exactly one"):
        StaticLoadRequest("invalid", load_case="a", combination="b")
    with pytest.raises(ValueError, match="conflicts"):
        next(iter_linear_static_batches(project, mesh=mesh, load_cases=[], output_nodes=[1],
                                         recovery_config=RecoveryConfig(node_ids=[2])))


def test_explicit_dofs_reject_incomplete_explicit_node_selection(solver_spy):
    project, series, points, _face = _project()
    mesh = project.generate_mesh(1.0)
    node = mesh.node_of_vertex[points[0]]
    built = build_fe_model(project, mesh, load_case=None, require_loads=False)
    rotation = built.fe_model.mesh.nodes[node].dofs[3]
    with pytest.raises(ValueError, match="explicit output_dofs"):
        next(iter_linear_static_batches(built=built, load_cases=[], output_nodes=[node],
                                         output_dofs=[rotation]))
    with pytest.raises(ValueError, match="explicit output_dofs"):
        next(iter_linear_static_batches(built=built, load_cases=[], output_refs=[project.point(points[0])],
                                         output_dofs=[rotation]))
    with pytest.raises(ValueError, match="explicit output_dofs"):
        next(iter_linear_static_batches(built=built, load_cases=[], output_connections=["end"],
                                         output_dofs=[rotation]))


def test_built_model_rejects_a_different_mesh(solver_spy):
    from copy import copy
    project, _series, _points, _face = _project()
    mesh = project.generate_mesh(1.0)
    built = build_fe_model(project, mesh, load_case=None, require_loads=False)
    with pytest.raises(ValueError, match="mesh conflicts"):
        next(iter_linear_static_batches(built=built, mesh=copy(mesh), load_cases=[]))


@pytest.mark.parametrize("primary_kind", ["input", "solver", "cancel"])
def test_primary_failure_survives_cleanup_mutation(primary_kind, solver_spy, monkeypatch):
    from anysolver import CancellationToken, SolveCancelled
    project, series, points, _face = _project()
    series.add_point_load(project.point(points[0]), force=(1, 0, 0))
    mesh = project.generate_mesh(1.0)
    step = project.series_children("series")[0]
    token = CancellationToken()
    primary = RuntimeError("primary failing case: case-17")
    if primary_kind == "cancel":
        token.cancel("primary cancelled case: case-17")
        primary = None
    def requests():
        try:
            if primary_kind == "input":
                raise primary
            yield step.name
        finally:
            mesh.nodes[next(iter(mesh.nodes))][0] += 0.1
    if primary_kind == "solver":
        def failed_solver(model, cases, **kwargs):
            source = iter(cases)
            try:
                next(source)
                raise primary
                yield
            finally:
                source.close()
        monkeypatch.setattr(anysolver, "iter_linear_batches", failed_solver)
    if primary_kind == "cancel":
        # Cancellation after construction begins must still close the provider.
        def cancelled_requests():
            try:
                token.cancel("primary cancelled case: case-17")
                yield step.name
            finally:
                mesh.nodes[next(iter(mesh.nodes))][0] += 0.1
        token = CancellationToken()
        source = cancelled_requests()
    else:
        source = requests()
    expected_type = SolveCancelled if primary_kind == "cancel" else RuntimeError
    with pytest.raises(expected_type) as captured:
        list(iter_linear_static_batches(project, mesh=mesh, load_cases=source,
                                        cancellation_token=token))
    if primary is not None:
        assert captured.value is primary
    notes = " ".join(captured.value.__notes__)
    assert "structural model changed" in notes
    assert captured.value.static_batch_secondary_errors


def test_wet_callback_mutation_is_rejected_before_solver_record(solver_spy, monkeypatch):
    from anyfem.solve import build as builder
    project, series, _points, _face = _project()
    series.add_wet_pressure(expression="17*(x+t)")
    mesh = project.generate_mesh(1.0)
    original = builder.field_pressure
    def callback(*args, **kwargs):
        mesh.nodes[next(iter(mesh.nodes))][0] += 0.1
        return original(*args, **kwargs)
    monkeypatch.setattr(builder, "field_pressure", callback)
    with pytest.raises(ProjectError, match="structural model changed"):
        list(iter_linear_static_batches(project, mesh=mesh,
                                        load_cases=[project.series_children("series")[0].name]))
    assert not solver_spy[0]


def test_fixed_scope_connection_and_wet_mapping_are_reused(solver_spy, monkeypatch):
    from anyfem.solve import build as builder
    project, series, points, face = _project()
    series.add_pressure(project.face(face), 11)
    series.add_point_load(project.point(points[0]), force=(1, 0, 0))
    series.add_wet_pressure(expression="17*(x+t)")
    series.add_connection_load(project.connection_for("end").id, force=(0, 1, 0))
    calls = {"connections": 0, "wet": 0, "centroid": 0, "targets": 0}
    for name, key in [("_resolve_connections", "connections"), ("_wet_elements", "wet"),
                      ("_element_centroid_normal", "centroid"), ("_attribute_targets", "targets")]:
        original = getattr(builder, name)
        def observed(*args, _original=original, _key=key, **kwargs):
            calls[_key] += 1
            return _original(*args, **kwargs)
        monkeypatch.setattr(builder, name, observed)
    mesh = project.generate_mesh(1.0)
    step = project.series_children("series")[0]
    iterator = iter_linear_static_batches(project, mesh=mesh, batch_size=1,
                                          load_cases=[step.name, step.name, step.name])
    next(iterator)
    first = dict(calls)
    next(iterator)
    next(iterator)
    assert calls == first
    assert calls["connections"] == 1
    iterator.close()


def test_source_exhaustion_and_early_close_check_and_release_provider(solver_spy):
    project, series, points, _face = _project()
    series.add_point_load(project.point(points[0]), force=(1, 0, 0))
    mesh = project.generate_mesh(1.0)
    step = project.series_children("series")[0]
    closed = []
    def requests():
        try:
            yield step.name
            yield step.name
        finally:
            closed.append(True)
    iterator = iter_linear_static_batches(project, mesh=mesh, load_cases=requests(), batch_size=1)
    next(iterator)
    iterator.close()
    assert closed == [True]
    def mutating_end():
        yield step.name
        mesh.nodes[next(iter(mesh.nodes))][0] += 0.1
    with pytest.raises(ProjectError, match="structural model changed"):
        list(iter_linear_static_batches(project, mesh=mesh, load_cases=mutating_end(), batch_size=1))


def test_public_combinations_keep_follower_dead_refusal(solver_spy):
    project, series, _points, face = _project()
    series.add_pressure(project.face(face), 11)
    series.follower_pressure = True
    dead = project.load_case("dead")
    dead.add_pressure(project.face(face), 7)
    step = project.series_children("series")[0]
    project.add_combination("invalid", {step.name: 1, dead.name: 1})
    with pytest.raises(ProjectError, match="mixes follower and dead"):
        list(iter_linear_static_batches(project, mesh=project.generate_mesh(1.0),
                                        load_cases=[StaticLoadRequest("invalid", combination="invalid")]))


def test_geometry_output_and_recovery_node_scope(solver_spy):
    from anysolver import RecoveryConfig
    project, series, points, _face = _project()
    series.add_point_load(project.point(points[0]), force=(1, 0, 0))
    mesh = project.generate_mesh(1.0)
    node = mesh.node_of_vertex[points[0]]
    block = next(iter_linear_static_batches(
        project, mesh=mesh, load_cases=[project.series_children("series")[0].name],
        output_refs=[project.point(points[0])], recovery_config=RecoveryConfig(node_ids=[node]),
    ))
    assert len(block.output_dofs) == 6
    assert block.node_displacement(node).shape == (6,)


@pytest.mark.parametrize("kind", ["code", "table", "mapping"])
def test_public_wet_variants_keep_time_and_spatial_fields(kind, solver_spy, tmp_path, monkeypatch):
    from anyfem.model import PressureTable
    from anyfem.solve import build as builder
    monkeypatch.setenv("ANYLOADS_TRUSTED_CODE_FILE", str(tmp_path / "trusted.json"))
    project, series, _points, face = _project()
    mesh = project.generate_mesh(1.0)
    if kind == "code":
        series.add_wet_pressure(code="def pressure(t, x):\n    return 17*(x+t) if x>t else 0\n")
    elif kind == "table":
        path = tmp_path / "pressures.csv"
        rows = ["case,x,y,z,pressure"]
        for time in (0.0, 0.5):
            for element in mesh.quads or mesh.tris:
                centroid, _normal = builder._element_centroid_normal(mesh, element)
                rows.append(",".join(str(value) for value in (time, *centroid, 17*(centroid[0]+time))))
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        series.add_wet_pressure(table=PressureTable.from_file(path, key="time"))
    else:
        series.add_wet_pressure(mapping={face: 17.0}, mapping_key="face")
    steps = project.series_children("series")
    expected = [build_fe_model(project, mesh, step.name).load_case for step in steps]
    list(iter_linear_static_batches(project, mesh=mesh, load_cases=(step.name for step in steps)))
    for record, reference in zip(solver_spy[0], expected):
        assert record.load_case.pressure_loads.keys() == reference.pressure_loads.keys()
        for element, values in reference.pressure_loads.items():
            np.testing.assert_array_equal(record.load_case.pressure_loads[element], values)
