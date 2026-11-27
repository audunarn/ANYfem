"""D2-1 shell result-axes data slice: request gate, station records, persistence.

The records come from the solver's opt-in ``anyfesolver.shell_axes_outputs``
(ANYfeSolver#94); these tests check what ANYfem adds: the gate, the columnar
record (bit-identical to the solver module's own per-element output), typed
refusals, the worker step, transport and the artifact round trip, and that a
run without an ``axes_*`` request is unchanged.
"""

from __future__ import annotations

import numpy as np
import pytest

from anyfem import Project, fixed, pinned, solve_linear_static, solve_linear_static_many, solve_nonlinear_static, steel
from anyfem.model.records import OutputRequest, RegionRef

pytest.importorskip("anyfesolver.shell_axes_outputs")

from anyfesolver.shell_axes_outputs import ShellAxesRefusal, recover_shell_axes  # noqa: E402

from anyfem.post.shell_axes_record import FAMILIES, STATIC_KEYS, read_shell_axes_table, recover_shell_axes_record  # noqa: E402

THICKNESS = 0.008


def _plate(side=1.0, origin=(0.0, 0.0, 0.0), pressure=1.0e4, tension=True, in_plane=0.0):
    project = Project(name="axes plate")
    project.add_material(steel("S355", THICKNESS))
    project.add_plate_section("plate", thickness=THICKNESS, material="S355")
    ox, oy, oz = origin
    points = project.geometry.add_points(
        [(ox, oy, oz), (ox + side, oy, oz), (ox + side, oy + side, oz), (ox, oy + side, oz)]
    )
    edges = project.geometry.add_polyline(points, close=True)
    face = project.geometry.add_face(edges)
    project.assign_plate(face, "plate")
    for edge in (edges[3:] if in_plane else edges):
        project.add_support(fixed(project.edge(edge)) if in_plane else pinned(project.edge(edge)))
    project.load_case().add_pressure(project.face(face), pressure)
    if in_plane:
        # cantilever from the x = 0 edge: in-plane pull and shear at a free corner
        project.load_case().add_point_load(project.point(points[2]), force=(in_plane, 0.3 * in_plane, 0.0))
    if tension:
        project.load_case("second").add_pressure(project.face(face), -0.5 * pressure)
    return project


def _request(keys, recovery="native"):
    return OutputRequest(quantity_keys=tuple(keys), region=RegionRef("model"), location="element",
                         recovery=recovery, basis="global")


# ---------------------------------------------------------------- gate
def test_axes_request_is_qualified_for_linear_static_only():
    request = _request(("axes_membrane_xx",))
    assert request.problems_for_analysis("linear_static") == ()
    assert request.problems_for_analysis("batch_linear_static") == ()
    for analysis in ("nonlinear_static", "arc_length", "transient", "modal", "buckling", "capacity", "impact"):
        problems = " ".join(request.problems_for_analysis(analysis))
        assert "[AXES_ANALYSIS_NOT_QUALIFIED]" in problems, analysis
    assert "[AXES_IMPORT_NOT_QUALIFIED]" in " ".join(request.problems_for_analysis("imported"))


def test_axes_request_on_patch_recovery_is_refused():
    problems = " ".join(_request(("axes_membrane_xx",), recovery="patch").problems_for_analysis("linear_static"))
    assert "[AXES_PATCH_NOT_QUALIFIED]" in problems


def test_ordinary_request_diagnostics_are_unchanged():
    # A stress request keeps exactly today's messages (no axes text added).
    assert _request(("stress_von_mises",)).problems_for_analysis("modal") == (
        "quantity 'stress_von_mises' is unavailable for analysis 'modal'",)
    assert _request(("bogus",)).problems_for_analysis("linear_static") == (
        "quantity 'bogus' is not a qualified ANYfem result quantity",)


# ---------------------------------------------------------------- record
@pytest.fixture(scope="module")
def plate_solution():
    return solve_linear_static(_plate(pressure=1.0e3, in_plane=2.0e5), target_size=0.25)


def test_record_is_the_solver_module_output_bit_for_bit(plate_solution):
    fe = plate_solution.built.fe_model
    u = np.asarray(plate_solution.displacements, float)
    record = recover_shell_axes_record(fe, u)
    ids = record["element_ids"]
    assert len(ids) == sum(1 for e in fe.mesh.elements.values() if "Shell" in type(e).__name__) > 0
    offsets = record["station_offsets"]
    for row, eid in enumerate(ids):
        element = fe.mesh.elements[int(eid)]
        direct = recover_shell_axes(element, fe.mesh, u[np.asarray(element.get_dof_mapping(fe.mesh))],
                                    fe.get_material(element.material_name)).axes
        lo, hi = offsets[row], offsets[row + 1]
        assert record["element_refusal"][row] == ""
        assert record["branch"][row] == direct.branch
        assert np.array_equal(record["station_positions"][lo:hi], direct.arrays["recovery_station_positions"])
        for fam, key in (("membrane", "membrane_tensors"), ("bending", "bending_tensors"),
                         ("shear", "shear_vectors")):
            certified = bool(direct.certified[fam]) and fam not in direct.refusals
            assert bool(record[f"{fam}_certified"][row]) is certified
            stored = record[key][lo:hi]
            if certified:
                source = {"membrane": "global_membrane_resultant_tensors",
                          "bending": "global_bending_resultant_tensors",
                          "shear": "global_transverse_shear_resultants"}[fam]
                assert np.array_equal(stored, direct.arrays[source])
            else:
                # a refused family carries no value: NaN rows plus its reason
                assert np.isnan(stored).all()
                assert record[f"{fam}_refusal_reason"][row] == direct.refusals[fam].reason


def test_certified_membrane_reproduces_native_local_stresses_frame_free(plate_solution):
    """D2-1 (ii) storage check without any frame: the in-plane invariants of the
    stored global membrane tensor equal those of the element's native local
    membrane stresses times the thickness, to 1e-13 of the family scale."""

    fe = plate_solution.built.fe_model
    u = np.asarray(plate_solution.displacements, float)
    record = recover_shell_axes_record(fe, u)
    native = plate_solution.stresses().element_stresses
    offsets = record["station_offsets"]
    checked = 0
    scale = np.nanmax(np.abs(record["membrane_tensors"]))
    assert scale > 0
    for row, eid in enumerate(record["element_ids"]):
        if not record["membrane_certified"][row]:
            continue
        local = native[int(eid)]
        tensors = record["membrane_tensors"][offsets[row]:offsets[row + 1]]
        h = record["thickness"][row]
        trace = np.trace(tensors, axis1=1, axis2=2)
        frob = np.einsum("sij,sij->s", tensors, tensors)
        xx, yy, xy = (h * np.asarray(local[k], float) for k in ("membrane_xx", "membrane_yy", "membrane_xy"))
        assert np.allclose(np.sort(trace), np.sort(xx + yy), rtol=0, atol=1e-13 * scale * 4)
        assert np.allclose(np.sort(frob), np.sort(xx**2 + yy**2 + 2 * xy**2), rtol=0, atol=1e-13 * scale**2 * 8)
        checked += 1
    assert checked > 0


def test_non_shell_elements_are_not_recorded():
    from test_analyses import strut_project

    project, _section, _start, _end = strut_project()
    solution = solve_linear_static(project, target_size=0.25)
    record = recover_shell_axes_record(solution.built.fe_model, solution.displacements)
    assert record["element_ids"].size == 0 and record["station_offsets"].tolist() == [0]


def test_far_from_origin_plate_records_the_typed_geometry_refusal():
    solution = solve_linear_static(_plate(origin=(1.0e7, 0.0, 0.0)), target_size=0.25)
    record = recover_shell_axes_record(solution.built.fe_model, solution.displacements)
    assert len(record["element_ids"]) > 0
    assert set(record["element_refusal"]) == {"AXES_GEOMETRY_CONDITIONING"}
    assert record["station_offsets"][-1] == 0
    for fam in FAMILIES:
        assert not record[f"{fam}_certified"].any()


# ---------------------------------------------------------------- worker, transport, artifact
def _run_job(project, analysis, solver, requested_shell_axes, target_size=0.5, **kw):
    from anyfem.application.workflow import _execute_analysis_job
    from anyfem.solve.build import build_fe_model

    mesh = project.generate_mesh(target_size)
    build = {"load_case": None} if analysis == "Batch linear static" else {}
    built = build_fe_model(project, mesh, **build)
    return _execute_analysis_job(project=project, solver_function=solver, analysis_name=analysis,
                                 options=dict(kw, built=built), progress=lambda msg: None,
                                 requested_shell_axes=requested_shell_axes)


def test_worker_step_attaches_records_only_on_request():
    project = _plate()
    plain = _run_job(project, "Linear static", solve_linear_static, False, target_size=0.25)
    assert not hasattr(plain, "_requested_shell_axes")
    with_axes = _run_job(project, "Linear static", solve_linear_static, True, target_size=0.25)
    record = with_axes._requested_shell_axes
    assert record["provenance"]["analysis"] == "linear_static"
    assert len(record["element_ids"]) > 0
    # the axes step does not touch the solution itself
    assert np.array_equal(plain.displacements, with_axes.displacements)


def test_worker_step_refuses_unqualified_analyses():
    from anyfem.application.workflow import _attach_shell_axes_records

    project = _plate(tension=False)
    nonlinear = solve_nonlinear_static(project, target_size=0.5)
    with pytest.raises(ValueError, match="AXES_ANALYSIS_NOT_QUALIFIED"):
        _attach_shell_axes_records(nonlinear, lambda msg: None)


def test_transport_carries_the_record():
    from anyfem.result_transport import decode_result, encode_result

    solution = _run_job(_plate(), "Linear static", solve_linear_static, True, target_size=0.5)
    decoded = decode_result(encode_result(solution))
    record, carried = solution._requested_shell_axes, decoded._requested_shell_axes
    assert set(record) == set(carried)
    for key, value in record.items():
        if isinstance(value, np.ndarray):
            assert value.dtype == carried[key].dtype and np.array_equal(value, carried[key], equal_nan=True)
        else:
            assert value == carried[key]


def _payload_arrays(payload):
    return {key: np.asarray(values) for key, (descriptor, values) in payload.fields.items()}


@pytest.mark.parametrize("batch", [False, True])
def test_artifact_round_trip_and_unchanged_ordinary_payload(tmp_path, batch):
    from anyfem.io.artifacts import ArtifactStore
    from anyfem.io.result_artifact import result_artifact_payload, write_solution_artifact

    project = _plate()
    if batch:
        solver, analysis, kw = solve_linear_static_many, "Batch linear static", dict(
            target_size=0.5, load_cases=("default", "second"))
    else:
        solver, analysis, kw = solve_linear_static, "Linear static", dict(target_size=0.5)
    plain = _run_job(project, analysis, solver, False, **kw)
    axes = _run_job(project, analysis, solver, True, **kw)
    plain_payload, axes_payload = result_artifact_payload(plain), result_artifact_payload(axes)
    # ordinary content is bit-identical; only shell_axes_* tables are added
    assert not any(key.startswith("shell_axes_") for key in plain_payload.tables)
    plain_arrays, axes_arrays = _payload_arrays(plain_payload), _payload_arrays(axes_payload)
    assert plain_arrays.keys() == axes_arrays.keys()
    for key in plain_arrays:
        assert np.array_equal(plain_arrays[key], axes_arrays[key], equal_nan=True), key
        assert plain_payload.fields[key][0] == axes_payload.fields[key][0], key
    added = set(axes_payload.tables) - set(plain_payload.tables)
    assert added and all(key.startswith("shell_axes_") for key in added)
    for key in added:  # the archive accepts finite numbers only
        value = axes_payload.tables[key]
        if isinstance(value, np.ndarray) and value.dtype.kind == "f":
            assert np.isfinite(value).all(), key

    holders = axes.shapes if batch else (axes,)
    records = [holder._requested_shell_axes for holder in holders]
    store = ArtifactStore(tmp_path / "axes.anyfem")
    mesh = axes.built.mesh
    identity = dict(mesh_id="mesh", document_id=project.document_id, model_hash="model", mesh_hash="hash")
    store.write_mesh(mesh, **identity)
    artifact = write_solution_artifact(store, axes, job_id="axes", analysis_hash="analysis", **identity)
    dataset = store.open_result(artifact)
    meta = dataset.table("shell_axes_meta")
    assert [case["analysis"] for case in meta["cases"]] == [records[0]["provenance"]["analysis"]] * len(records)
    for key, value in records[0].items():
        if key in ("schema", "provenance"):
            continue
        stored = read_shell_axes_table(dataset, key)
        if key in STATIC_KEYS:
            expected = value
        elif isinstance(value, np.ndarray):
            expected = np.stack([record[key] for record in records])
        else:
            expected = [list(record[key]) for record in records]
        if isinstance(expected, np.ndarray):
            stored = np.asarray(stored)
            assert stored.dtype == expected.dtype and np.array_equal(stored, expected, equal_nan=True), key
        else:
            assert stored == expected, key


def test_partial_case_records_are_refused():
    from anyfem.io.result_artifact import result_artifact_payload

    axes = _run_job(_plate(), "Batch linear static", solve_linear_static_many, True,
                    target_size=0.5, load_cases=("default", "second"))
    del axes.shapes[1]._requested_shell_axes
    with pytest.raises(ValueError, match="missing for some cases"):
        result_artifact_payload(axes)


def test_refusal_type_is_the_solver_type():
    assert issubclass(ShellAxesRefusal, ValueError)
