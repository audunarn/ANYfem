"""Manufactured stress references and a live-to-saved shell history integration."""
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("anyfatigue.plate")
from anyfatigue.anyfem_adapter import surface_mesh_from_anyfem
from anyfatigue.extract import extract
from anyfatigue.measures import StressMeasure
from anyfatigue.model import ArrayStressSource
from anyfatigue.sn_curves import get_curve
from anyfatigue.timeseries import LoadBlock, TimeSeriesSettings, assess_time_series
from anymesher.mesh import Mesh

from anyfem.application.fatigue import (
    FatigueInput, FatigueMeshEdgeRef, load_fatigue_input, prepare_fatigue_location, prepare_fatigue_locations,
    _receiving_sheet_groups,
)
from anyfem.selection import MeshEntityRef


@pytest.fixture
def junction():
    xyz = ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (1, 0, 1), (0, 0, 1))
    mesh = Mesh(nodes={i: np.array(p, dtype=float) for i, p in enumerate(xyz, 1)},
                quads={17: (1, 2, 3, 4), 41: (1, 2, 5, 6)},
                elements_of_face={8: [17], 9: [41]}, node_of_vertex={4: 1},
                nodes_of_edge={11: [1, 2], 12: [2, 5], 13: [1, 4]})
    shell_mesh, _, _ = surface_mesh_from_anyfem(mesh)
    stress = np.zeros((1, 2, 6))
    stress[0, :, 1] = [10., 90.]
    return FatigueInput(ArrayStressSource(shell_mesh, ["case"], stress, -stress), mesh)


def test_receiver_joins_connected_sheet_charts_without_crossing_the_web():
    xyz = ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
           (2, 0, 0), (2, 1, 0), (1, 0, 1), (1, 1, 1))
    mesh = Mesh(nodes={i: np.array(p, dtype=float) for i, p in enumerate(xyz, 1)},
                quads={17: (1, 2, 3, 4), 42: (2, 5, 6, 3), 99: (2, 3, 8, 7)},
                elements_of_face={8: [17], 9: [42], 10: [99]})
    surface, _, _ = surface_mesh_from_anyfem(mesh)
    geometry = SimpleNamespace(
        sheets={1: SimpleNamespace(id=1, face_use_ids=(8, 9, 10))},
        face_uses={fid: SimpleNamespace(face_id=fid) for fid in (8, 9, 10)})
    thicknesses = {8: .02, 9: .02, 10: .02}
    groups = _receiving_sheet_groups(mesh, geometry, thicknesses)
    assert groups == {8: (8, 9), 9: (8, 9), 10: (10,)}
    source = ArrayStressSource(surface, ["case"], np.zeros((1, 3, 6)), np.zeros((1, 3, 6)))
    data = FatigueInput(source, mesh, thicknesses, groups)
    assert data.plate_scope_for((17, 42)) == (8, (17, 42))
    prepared = prepare_fatigue_location(data, (17,), target=FatigueMeshEdgeRef((5, 6)), thickness_mm=20)
    assert prepared.element_ids == (17, 42) and len(prepared.stations) == 2
    with pytest.raises(ValueError, match="connected receiving sheet"):
        data.plate_scope_for((17, 99))
    assert _receiving_sheet_groups(mesh, geometry, {**thicknesses, 9: .01})[8] == (8,)
    geometry.sheets[2] = SimpleNamespace(id=2, face_use_ids=(9,))
    assert _receiving_sheet_groups(mesh, geometry, thicknesses)[8] == (8,)


@pytest.mark.parametrize("target,kwargs", [
    (SimpleNamespace(kind="edge", id=11), {"side": "auto"}),
    (SimpleNamespace(kind="vertex", id=4), {"direction": (0, 1, 0)}),
    (SimpleNamespace(kind="vertex", id=4), {"reference_edge": SimpleNamespace(kind="edge", id=11)}),
    (MeshEntityRef("node", 1), {"direction": (0, 1, 0)}),
])
def test_junction_selection_excludes_the_second_plate(junction, target, kwargs):
    for surface, expected in (("top", 10.), ("bottom", -10.)):
        result = prepare_fatigue_location(junction, [17], target=target, thickness_mm=20.,
                                          surface=surface, **kwargs)
        assert result.face_id == 8 and result.element_ids == (17,)
        for s in result.stations:
            assert np.array(s.probes)[:, 1] == pytest.approx([.01, .03])
        assert all(p.tensors[0, 1] == pytest.approx(expected)
                   for p in extract(result.source, result.location))


def test_direct_and_invalid_selections(junction):
    prepared = prepare_fatigue_location(junction, [41, 17])
    assert [p.tensors[0, 1] for p in extract(prepared.source, prepared.location)] == [90, 10]
    for elements, kind, ident, error in (
        ([17, 41], "edge", 11, "one identifiable plate"),
        ([17], "edge", 12, "whole picked line"),
        ([17], "node", 5, "selected plate"),
        ([17], "vertex", 4, "direction"),
    ):
        with pytest.raises(ValueError, match=error):
            prepare_fatigue_location(junction, elements, target=SimpleNamespace(kind=kind, id=ident),
                                     thickness_mm=20)
    with pytest.raises(ValueError, match="shell elements"):
        prepare_fatigue_location(junction, [999])


def test_closed_edge_and_single_unambiguous_point_tangent(junction):
    mesh = junction.mesh
    mesh.nodes_of_edge = {61: [1, 2, 3, 4, 1]}
    args = dict(surface="top", thickness_mm=20, side="auto")
    loop = prepare_fatigue_location(junction, [17], target=SimpleNamespace(kind="edge", id=61), **args)
    point = prepare_fatigue_location(junction, [17], target=MeshEntityRef("node", 1), **args)
    assert loop.location.closed and len(loop.stations) == 4
    assert point.stations[0].direction == pytest.approx(loop.stations[0].direction)
    assert point.stations[0].direction == pytest.approx((2**-.5, 2**-.5, 0))
    foreign = MeshEntityRef("node", 1, mesh_id="another mesh")
    with pytest.raises(ValueError, match="different result mesh"):
        prepare_fatigue_location(junction, [17], target=foreign, **args)


def test_batch_uses_one_receiving_plate_and_keeps_station_order(junction, monkeypatch):
    import anyfatigue.plate as plate

    original = plate.plate_stress_source
    calls = []
    def counted(source, elements):
        calls.append(tuple(elements))
        return original(source, elements)
    monkeypatch.setattr(plate, "plate_stress_source", counted)
    # Submitted shell sides without a corresponding source geometry line are also usable.
    a = FatigueMeshEdgeRef((3, 2))
    b = FatigueMeshEdgeRef((3, 4))
    assert a == FatigueMeshEdgeRef((2, 3))
    assert junction.plate_scope_for([17]) == (8, (17,))
    prepared = prepare_fatigue_locations(junction, [17], targets=[a, b], thickness_mm=20)
    assert calls == [(17,)]
    assert [loc.node_ids for loc in prepared.locations] == [(2, 3), (3, 4)]
    assert prepared.location is prepared.locations[0]
    assert np.allclose([s.xyz for s in prepared.stations], [junction.mesh.nodes[n] for n in (2, 3, 3, 4)])
    assert all(p.tensors[0, 1] == pytest.approx(10.) for loc in prepared.locations
               for p in extract(prepared.source, loc))

    points = [MeshEntityRef("node", n) for n in (2, 3)]
    prepared = prepare_fatigue_locations(junction, [17], targets=points, reference_edge=a, thickness_mm=20)
    assert calls == [(17,), (17,)]
    assert [loc.name for loc in prepared.locations] == ["Toe node 2", "Toe node 3"]
    assert len(prepared.stations) == 2


def test_mesh_sides_require_real_connectivity_and_a_current_reference(junction):
    options = dict(thickness_mm=20, side="auto")
    for invalid in ((1, 3), (2, 5)):
        with pytest.raises(ValueError, match="shell side on the selected plate"):
            prepare_fatigue_locations(junction, [17], targets=[FatigueMeshEdgeRef((1, 2)),
                                                               FatigueMeshEdgeRef(invalid)], **options)
    with pytest.raises(ValueError, match="different result mesh"):
        prepare_fatigue_location(junction, [17], target=MeshEntityRef("node", 2),
                                 reference_edge=FatigueMeshEdgeRef((2, 3), "old mesh"), **options)
    with pytest.raises(ValueError, match="node 3.*pass through"):
        prepare_fatigue_locations(junction, [17], targets=[MeshEntityRef("node", 2), MeshEntityRef("node", 3)],
                                  reference_edge=FatigueMeshEdgeRef((1, 2)), **options)


@pytest.mark.parametrize("borrowed_reader", [True, False])
def test_displayed_dataset_reuses_reader_when_supported(junction, monkeypatch, borrowed_reader):
    from anyfatigue import anyfem_artifacts

    dataset = SimpleNamespace(identity={"frame_kind": "static"}, table_keys=(), path="retained.anyres.h5")
    calls = []
    def read(argument, *, times):
        calls.append(argument)
        assert times is None
        return junction.source
    if borrowed_reader:
        monkeypatch.setattr(anyfem_artifacts, "read_result_dataset", read, raising=False)
        monkeypatch.setattr(anyfem_artifacts, "read_result", lambda *_a, **_kw: pytest.fail("reopened result"))
    else:
        monkeypatch.delattr(anyfem_artifacts, "read_result_dataset", raising=False)
        monkeypatch.setattr(anyfem_artifacts, "read_result", read)
    bound = load_fatigue_input(dataset=dataset, mesh=junction.mesh)
    assert bound.source is junction.source
    assert calls == [dataset if borrowed_reader else dataset.path]


@pytest.fixture(scope="module")
def solved_plate():
    from anyfem import Project, pinned, steel
    from anyfem.solve.run import solve_linear_static

    p = Project(name="Fatigue test")
    p.add_material(steel("S355", .01))
    p.add_plate_section("plate", thickness=.01, material="S355")
    corners = p.geometry.add_points([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
    edges = p.geometry.add_polyline(corners, close=True)
    plate = p.geometry.add_face(edges)
    p.assign_plate(plate, "plate")
    for edge in edges:
        p.add_support(pinned(p.edge(edge)))
    p.load_case().add_pressure(p.face(plate), 1e4)
    return solve_linear_static(p, target_size=.25), edges


def test_immutable_batch_times_thickness_and_physical_directors(solved_plate, monkeypatch):
    from anyfem.io.result_artifact import result_artifact_payload
    from anyfem.post.results import LinearBatchSolution, LinearSolution

    static, _ = solved_plate
    built = static.built
    shapes = [LinearSolution(sign * static.displacements, built) for sign in (-1., 1.)]
    for shape in shapes:
        assert shape.retained_stress_recovery("global") is None
        recovered = shape.stresses(return_global=True)
        shape._requested_global_stress = recovered
        assert shape.retained_stress_recovery("global") is recovered
    names = ("first", "second")
    for name, time in zip(names, (0., None)):
        monkeypatch.setitem(built.project.load_cases, name, SimpleNamespace(time=time))
    batch = LinearBatchSolution(built, shapes, case_names=names)
    payload = result_artifact_payload(batch)
    assert tuple(payload.tables["load_case_names"]) == names
    np.testing.assert_array_equal(payload.tables["load_case_times_s"], [0., np.nan])
    assert set(payload.tables["shell_face_thickness_m"].values()) == {.01}
    directors = payload.tables["shell_surface_directors"]
    for eid, record in shapes[0]._requested_global_stress.element_stresses.items():
        np.testing.assert_array_equal(directors[str(eid)], record["physical_director"])
    built.project.load_cases["first"].time = None
    assert "load_case_times_s" not in result_artifact_payload(batch).tables


def test_native_transient_production_recovery_save_reopen_and_rainflow(solved_plate, tmp_path, monkeypatch):
    """A manufactured fully reversed displacement history exercises production recovery and I/O."""
    from anyfem.application.workflow import _execute_analysis_job
    from anyfem.io.artifacts import ArtifactStore
    from anyfem.io.result_artifact import write_solution_artifact
    from anyfem.post.results import ShapeView, TransientSolution

    static, edges = solved_plate
    p = static.built.project
    mesh = static.built.mesh
    times = np.array([0., .5, 1., 1.5, 2.])
    factors = (1., -1., 1., -1., 1.)
    shapes = [ShapeView(f * static.displacements, static.built, value=t) for f, t in zip(factors, times)]
    solution = TransientSolution(static.built, shapes, status="completed", times=times)
    def unexpected_recovery(*args, **kwargs):
        pytest.fail("Opening a live result or reading its retained tensors ran the solver recovery")

    with monkeypatch.context() as guard:
        guard.setattr("anyfesolver.recover_stress_result", unexpected_recovery)
        source = load_fatigue_input(solution=solution, mesh=mesh, project=p)
    _execute_analysis_job(project=p, solver_function=lambda **kw: solution, analysis_name="Transient",
                          options={"built": static.built}, progress=lambda msg: None,
                          requested_stress=True, requested_global_stress=True, requested_patch_stress=True)
    # The job has now retained both recoveries for every frame. The direct and
    # weld reads below must reuse those exact outputs, including qualified nodes.
    monkeypatch.setattr("anyfesolver.recover_stress_result", unexpected_recovery)
    store = ArtifactStore(tmp_path / "plate.anyfem")
    identity = dict(mesh_id="mesh", document_id=p.document_id, model_hash="model", mesh_hash="hash")
    store.write_mesh(mesh, **identity)
    artifact = write_solution_artifact(store, solution, job_id="history", analysis_hash="analysis", **identity)
    dataset = store.open_result(artifact)
    retained = load_fatigue_input(dataset=dataset, mesh=mesh, project=p)
    assert retained.source.case_times == source.source.case_times == tuple(times)
    assert retained.source.case_labels == source.source.case_labels
    eid = next(iter(mesh.shells))
    from_case = source.for_cases((source.source.case_labels[1],))
    saved_case = retained.for_cases((retained.source.case_labels[1],))
    row = from_case.source.mesh.element_row(eid)
    for side in ("top", "bottom"):
        assert np.array_equal(from_case.source.element_stress([row], side),
                              saved_case.source.element_stress([row], side))
    assert retained.thickness_mm_for([eid]) == source.thickness_mm_for([eid]) == 10.
    curve = get_curve("D", "air")
    settings = TimeSeriesSettings(curve, 10., 1., 1.,
                                  (LoadBlock("record", source.source.case_labels),),
                                  measure=StressMeasure.parse("component:xx"))
    for data in (source, retained):
        target = prepare_fatigue_location(data, [eid], target=p.edge(edges[0]),
                                          thickness_mm=10, side="auto")
        result = assess_time_series(target.source, (target.location,), settings)
        assert result.critical.damage > 0.
        if data is source:
            expected = [pt.damage for pt in result.points]
        else:
            assert [pt.damage for pt in result.points] == pytest.approx(expected, rel=1e-12)
    # Full storage and a monotone producer time axis are separate requirements.
    solution.info["raw"] = SimpleNamespace(history_storage_mode="selected")
    with pytest.raises(ValueError, match="full displacement"):
        load_fatigue_input(solution=solution, mesh=mesh)
    solution.info.clear()
    solution.times = times[::-1]
    with pytest.raises(ValueError, match="increasing"):
        load_fatigue_input(solution=solution, mesh=mesh)
