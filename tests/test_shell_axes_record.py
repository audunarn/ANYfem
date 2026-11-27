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

# A hard import: the pinned solver must provide the module (no silent skip).
from anyfesolver.shell_axes_outputs import ShellAxesRefusal, recover_shell_axes

from anyfem.post.shell_axes_record import FAMILIES, read_shell_axes_table, recover_shell_axes_record

THICKNESS = 0.008


def _same(left, right):
    """Bit identity (dtype, shape and bytes: -0.0 and +0.0 differ)."""

    if isinstance(left, np.ndarray):
        return (isinstance(right, np.ndarray) and left.dtype == right.dtype and left.shape == right.shape
                and left.tobytes() == right.tobytes())
    return left == right


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
        assert _same(np.ascontiguousarray(record["station_positions"][lo:hi]),
                     np.ascontiguousarray(direct.arrays["recovery_station_positions"]))
        for fam, key in (("membrane", "membrane_tensors"), ("bending", "bending_tensors"),
                         ("shear", "shear_vectors")):
            certified = bool(direct.certified[fam]) and fam not in direct.refusals
            assert bool(record[f"{fam}_certified"][row]) is certified
            stored = record[key][lo:hi]
            if certified:
                source = {"membrane": "global_membrane_resultant_tensors",
                          "bending": "global_bending_resultant_tensors",
                          "shear": "global_transverse_shear_resultants"}[fam]
                assert _same(np.ascontiguousarray(stored), np.ascontiguousarray(direct.arrays[source]))
            else:
                # a refused family carries no value: NaN rows plus its reason
                assert np.isnan(stored).all()
                assert np.isnan(record[f"{fam}_basis_normals"][lo:hi]).all()
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
def _run_job(project, analysis, solver, requested_shell_axes, target_size=0.5, job_flags=None, **kw):
    from anyfem.application.workflow import _execute_analysis_job
    from anyfem.solve.build import build_fe_model

    mesh = project.generate_mesh(target_size)
    build = {"load_case": None} if analysis == "Batch linear static" else {}
    built = build_fe_model(project, mesh, **build)
    return _execute_analysis_job(project=project, solver_function=solver, analysis_name=analysis,
                                 options=dict(kw, built=built), progress=lambda msg: None,
                                 requested_shell_axes=requested_shell_axes, **(job_flags or {}))


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


def test_axes_step_runs_before_the_ordinary_recoveries():
    """Owner decision 2026-11-27 (D2-1 (b)).  The solver's ordinary recovery of a
    model with >= 100 elements caches its batch plan on the mesh, which the axes
    module does not admit; with the old order (axes after the stress recovery)
    every shell was refused AXES_OBJECT_NOT_PLAIN."""

    from anyfesolver.shell_axes_outputs import recover_shell_axes

    flags = dict(requested_stress=True, requested_global_stress=True)
    solution = _run_job(_plate(pressure=1.0e3, in_plane=2.0e5), "Linear static", solve_linear_static, True,
                        target_size=0.08, job_flags=flags)
    fe = solution.built.fe_model
    assert len(fe.mesh.elements) >= 100
    record = solution._requested_shell_axes
    assert set(record["element_refusal"]) == {""}
    assert solution._requested_global_stress is not None
    # ... and the condition is real: after the ordinary recovery the module
    # refuses (fails closed), so the order matters.
    element = fe.mesh.elements[int(record["element_ids"][0])]
    u = np.asarray(solution.displacements, float)[np.asarray(element.get_dof_mapping(fe.mesh))]
    with pytest.raises(ShellAxesRefusal) as refused:
        recover_shell_axes(element, fe.mesh, u, fe.get_material(element.material_name))
    assert refused.value.code == "AXES_OBJECT_NOT_PLAIN"


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
    _records_equal(solution._requested_shell_axes, decoded._requested_shell_axes)


def _payload_arrays(payload):
    return {key: np.asarray(values) for key, (descriptor, values) in payload.fields.items()}


def _table_equal(left, right):
    if isinstance(left, np.ndarray) or isinstance(right, np.ndarray):
        return _same(np.asarray(left), np.asarray(right))
    return left == right


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
    flags = dict(requested_stress=True, requested_global_stress=True)  # what an axes request also sets
    plain = _run_job(project, analysis, solver, False, job_flags=flags, **kw)
    axes = _run_job(project, analysis, solver, True, job_flags=flags, **kw)
    plain_payload, axes_payload = result_artifact_payload(plain), result_artifact_payload(axes)
    # ordinary content is bit-identical (fields, descriptors and every ordinary
    # table); only shell_axes_* tables are added
    assert not any(key.startswith("shell_axes_") for key in plain_payload.tables)
    plain_arrays, axes_arrays = _payload_arrays(plain_payload), _payload_arrays(axes_payload)
    assert plain_arrays.keys() == axes_arrays.keys()
    for key in plain_arrays:
        assert _same(np.ascontiguousarray(plain_arrays[key]), np.ascontiguousarray(axes_arrays[key])), key
        assert plain_payload.fields[key][0] == axes_payload.fields[key][0], key
    for key, value in plain_payload.tables.items():
        assert key in axes_payload.tables and _table_equal(value, axes_payload.tables[key]), key
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
    assert [case["case_index"] for case in meta["cases"]] == list(range(len(records)))
    if batch:  # the load-dependent tensors differ between the two cases
        assert meta["entries"]["bending_tensors"]["layout"] == "per_case"
    assert meta["entries"]["element_ids"]["layout"] == "shared"
    for index, record in enumerate(records):
        for key, value in record.items():
            if key in ("schema", "provenance"):
                continue
            per_case = meta["entries"][key]["layout"] == "per_case"
            stored = read_shell_axes_table(dataset, key, case=index if per_case else None)
            if isinstance(value, np.ndarray):
                # NaN (absent) positions read back as NaN; values bit for bit
                assert stored.dtype == value.dtype and stored.shape == value.shape, key
                if value.dtype.kind == "f":
                    assert np.array_equal(np.isnan(stored), np.isnan(value)), key
                    assert _same(np.nan_to_num(stored, nan=0.0), np.nan_to_num(value, nan=0.0)), key
                else:
                    assert _same(stored, value), key
            else:
                assert stored == list(value), key


def test_partial_case_records_are_refused():
    from anyfem.io.result_artifact import result_artifact_payload

    axes = _run_job(_plate(), "Batch linear static", solve_linear_static_many, True,
                    target_size=0.5, load_cases=("default", "second"))
    del axes.shapes[1]._requested_shell_axes
    with pytest.raises(ValueError, match="missing for some cases"):
        result_artifact_payload(axes)


def test_case_records_must_name_their_case():
    from anyfem.io.result_artifact import result_artifact_payload

    axes = _run_job(_plate(), "Batch linear static", solve_linear_static_many, True,
                    target_size=0.5, load_cases=("default", "second"))
    first, second = axes.shapes[0]._requested_shell_axes, axes.shapes[1]._requested_shell_axes
    axes.shapes[0]._requested_shell_axes, axes.shapes[1]._requested_shell_axes = second, first
    with pytest.raises(ValueError, match="does not name that case"):
        result_artifact_payload(axes)


def test_ordinary_artifact_does_not_import_the_axes_module(monkeypatch):
    import sys

    from anyfem.io.result_artifact import result_artifact_payload

    plain = _run_job(_plate(), "Linear static", solve_linear_static, False)
    monkeypatch.delitem(sys.modules, "anyfem.post.shell_axes_record")
    result_artifact_payload(plain)
    assert "anyfem.post.shell_axes_record" not in sys.modules


class _FakeDataset:
    """The reader's view of an archive: tables by name, slices, shapes."""

    def __init__(self, tables):
        self.tables = dict(tables)
        # the artifact's frame axis (one frame per case), as the payload carries it
        self.frames = np.asarray(self.tables.pop("__frames__", (0.0,)), dtype=float)
        self.identity = {"frame_kind": self.tables.pop("__frame_kind__", "static")}
        self.solution_type = self.tables.pop("__solution_type__", "LinearSolution")

    def metadata(self, name):
        return {"solution_type": self.solution_type} if name == "provenance" else None

    def table(self, name, rows=None):
        value = self.tables[name]
        return value if rows is None or not isinstance(value, np.ndarray) else value[rows]

    def table_shape(self, name):
        value = self.tables[name]
        return tuple(value.shape) if isinstance(value, np.ndarray) else ()

    @property
    def table_keys(self):
        return tuple(self.tables)


@pytest.fixture(scope="module")
def stored_tables():
    """The tables an axes batch run writes (two cases), and its records."""

    from anyfem.io.result_artifact import result_artifact_payload

    axes = _run_job(_plate(), "Batch linear static", solve_linear_static_many, True,
                    job_flags=dict(requested_stress=True, requested_global_stress=True),
                    target_size=0.5, load_cases=("default", "second"))
    return _axes_tables(axes), [shape._requested_shell_axes for shape in axes.shapes]


def _axes_tables(solution):
    """The axes tables of a solution's artifact payload, its batch case names
    and its frame axis (the reader's view of the written artifact)."""

    from anyfem.io.result_artifact import result_artifact_payload

    payload = result_artifact_payload(solution)
    tables = {key: value for key, value in payload.tables.items()
              if key.startswith("shell_axes_") or key == "load_case_names"}
    tables["__frames__"] = payload.frames
    tables["__frame_kind__"] = payload.frame_kind
    tables["__solution_type__"] = payload.provenance.get("solution_type")
    return tables


def test_reader_reads_each_case_exactly(stored_tables):
    tables, records = stored_tables
    dataset = _FakeDataset(tables)
    meta = tables["shell_axes_meta"]
    for index, record in enumerate(records):
        for key, value in record.items():
            if key in ("schema", "provenance"):
                continue
            per_case = meta["entries"][key]["layout"] == "per_case"
            stored = read_shell_axes_table(dataset, key, case=index if per_case else None)
            if isinstance(value, np.ndarray):
                assert stored.dtype == value.dtype and stored.shape == value.shape, key
                assert np.array_equal(np.isnan(stored), np.isnan(value)) if value.dtype.kind == "f" else True
                assert _same(np.nan_to_num(stored, nan=0.0), np.nan_to_num(value, nan=0.0)), key
            else:
                assert stored == list(value), key


@pytest.mark.parametrize("damage", ["missing_mask", "oversized", "rank_two", "int_mask", "string_not_list",
                                    "declared_dtype", "short_mask", "short_strings"])
def test_reader_refuses_malformed_tables(stored_tables, damage):
    """Codex r2 P1: the reader validates the schema and the stored table
    before reading; it never returns zeros, truncations or characters."""

    tables, _records = stored_tables
    tables = {key: (dict(value) if isinstance(value, dict) else value) for key, value in tables.items()}
    meta = dict(tables["shell_axes_meta"])
    meta["entries"] = {key: dict(value) for key, value in meta["entries"].items()}
    tables["shell_axes_meta"] = meta
    key, case = "thickness", None
    if damage == "missing_mask":
        del tables["shell_axes_thickness_present"]
    elif damage == "oversized":
        tables["shell_axes_thickness"] = np.concatenate([tables["shell_axes_thickness"], [1.0]])
    elif damage == "rank_two":
        tables["shell_axes_thickness"] = tables["shell_axes_thickness"].reshape(-1, 1)
    elif damage == "int_mask":
        key = "element_ids"
        tables["shell_axes_element_ids_present"] = np.ones_like(tables["shell_axes_element_ids"], dtype=bool)
    elif damage == "string_not_list":
        key = "branch"
        tables["shell_axes_branch"] = {"values": "legacy"}
    elif damage == "declared_dtype":
        meta["entries"]["thickness"]["dtype"] = "<i8"
    elif damage == "short_mask":
        tables["shell_axes_thickness_present"] = tables["shell_axes_thickness_present"][:-1]
    elif damage == "short_strings":
        key = "branch"
        tables["shell_axes_branch"] = {"values": list(tables["shell_axes_branch"]["values"])[:-1]}
    with pytest.raises(ValueError):
        read_shell_axes_table(_FakeDataset(tables), key, case=case)


# ---------------------------------------------------------------- solver output edge cases (synthetic)
def _patched_recovery(monkeypatch, mutate):
    """Wrap the solver call so one element's output takes a shape the module can
    produce but the fixture does not (refusals with emitted normals etc.)."""

    import dataclasses

    import anyfesolver.shell_axes_outputs as solver_module

    original = solver_module.recover_shell_axes

    def wrapped(*args, **kwargs):
        result = original(*args, **kwargs)
        axes = result.axes
        arrays, refusals, certified = dict(axes.arrays), dict(axes.refusals), dict(axes.certified)
        mutate(arrays, refusals, certified)
        return dataclasses.replace(result, axes=dataclasses.replace(
            axes, arrays=arrays, refusals=refusals, certified=certified))

    monkeypatch.setattr(solver_module, "recover_shell_axes", wrapped)


def test_refused_family_keeps_no_basis_normal_even_when_emitted(monkeypatch, plate_solution):
    from anyfesolver.shell_axes_outputs import Refusal

    def mutate(arrays, refusals, certified):
        # the module emits station normals when bending certified the station
        # frame, also while membrane is unresolvable
        refusals["membrane"] = Refusal("AXES_FRAME_RULE_MISMATCH", "unresolvable_family", "synthetic")
        certified["membrane"] = False
        arrays.pop("global_membrane_resultant_tensors", None)

    _patched_recovery(monkeypatch, mutate)
    record = recover_shell_axes_record(plate_solution.built.fe_model, plate_solution.displacements)
    assert not record["membrane_certified"].any() and record["bending_certified"].all()
    assert np.isnan(record["membrane_basis_normals"]).all()
    assert np.isfinite(record["bending_basis_normals"]).all()
    assert set(record["membrane_refusal_reason"]) == {"unresolvable_family"}


def test_refused_positions_refuse_the_element_and_metadata_refusals_are_kept(monkeypatch, plate_solution):
    from anyfesolver.shell_axes_outputs import Refusal

    calls = []

    def mutate(arrays, refusals, certified):
        calls.append(1)
        if len(calls) == 1:
            arrays.pop("recovery_station_positions")
            refusals["positions"] = Refusal("AXES_FRAME_RULE_MISMATCH", "nonfinite", "synthetic positions")
        elif len(calls) == 2:
            refusals["metadata"] = Refusal("AXES_FRAME_RULE_MISMATCH", "nonfinite", "synthetic director")
            arrays.pop("physical_reference_director", None)

    _patched_recovery(monkeypatch, mutate)
    record = recover_shell_axes_record(plate_solution.built.fe_model, plate_solution.displacements)
    assert record["element_refusal"][0] == "AXES_FRAME_RULE_MISMATCH"
    assert "positions" in record["element_refusal_reason"][0]
    assert record["station_offsets"][1] == 0
    assert record["metadata_refusal"][1] == "nonfinite: synthetic director"
    assert np.isnan(record["physical_reference_director"][1]).all()


def test_shadowed_mesh_method_and_bad_material_name_get_typed_refusals(plate_solution):
    """Codex r2 P2: nothing of the element or mesh is used before the module's
    admission; malformed lookups are typed refusals, not job failures."""

    import copy

    fe = copy.deepcopy(plate_solution.built.fe_model)
    fe.mesh.get_node = lambda *args, **kwargs: None
    record = recover_shell_axes_record(fe, plate_solution.displacements)
    assert set(record["element_refusal"]) == {"AXES_INSTANCE_SHADOW"}
    fe = copy.deepcopy(plate_solution.built.fe_model)
    eid = next(iter(fe.mesh.elements))
    vars(fe.mesh.elements[eid])["material_name"] = ("not", "hashable", ["list"])
    record = recover_shell_axes_record(fe, plate_solution.displacements)
    row = list(record["element_ids"]).index(eid)
    assert record["element_refusal"][row].startswith("AXES_")


def test_shadowed_instance_method_gets_the_solver_refusal(plate_solution):
    import copy

    fe = copy.deepcopy(plate_solution.built.fe_model)
    eid = next(iter(fe.mesh.elements))
    fe.mesh.elements[eid].get_dof_mapping = lambda *args, **kwargs: []
    record = recover_shell_axes_record(fe, plate_solution.displacements)
    row = list(record["element_ids"]).index(eid)
    # dispatched through the class, so the solver admission decides (typed)
    assert record["element_refusal"][row] not in ("", "AXES_MISSING_GLOBAL_TENSORS")
    assert record["element_refusal"][row].startswith("AXES_")


def _records_equal(left, right, ignore=("provenance",)):
    assert set(left) == set(right)
    for key, value in left.items():
        if key not in ignore:
            assert _same(value, right[key]), key


def _scope(keys, basis="global", recovery="native"):
    return {"request": {"quantity_keys": list(keys), "basis": basis, "recovery": recovery}}


def test_submission_flags_change_only_for_axes_requests():
    from anyfem.application.workflow import _requested_recovery

    ordinary = (_scope(("stress_von_mises",)), _scope(("displacement.uz",)), _scope(("stress_xx",), recovery="patch"))
    flags = _requested_recovery(ordinary)
    assert flags == {"requested_stress": True, "requested_global_stress": True, "requested_patch_stress": True}
    flags = _requested_recovery((_scope(("displacement.uz",)),))
    assert flags == {"requested_stress": False, "requested_global_stress": False, "requested_patch_stress": False}
    flags = _requested_recovery((_scope(("axes_membrane_xx",)),))
    # design rev 4: an axes request also sets both ordinary stress flags
    assert flags == {"requested_stress": True, "requested_global_stress": True, "requested_patch_stress": False,
                     "requested_shell_axes": True}


@pytest.mark.parametrize("damage", ["offsets_order", "offsets_start", "inf_present", "absent_not_zero",
                                    "empty_mask_dtype"])
def test_reader_refuses_misaligned_or_invalid_values(stored_tables, damage):
    """Codex r3 P1: offsets must be zero-based and nondecreasing; a present
    value must be finite; an absent one is stored as 0.0; dtypes are checked
    even for empty spans."""

    tables, _records = stored_tables
    tables = dict(tables)
    key, case = "station_positions", None
    meta = tables["shell_axes_meta"]
    per_case = {k: v["layout"] == "per_case" for k, v in meta["entries"].items()}
    if damage == "offsets_order":
        offsets = tables["shell_axes_station_offsets"].copy()
        offsets[1], offsets[2] = offsets[2], offsets[1]
        tables["shell_axes_station_offsets"] = offsets
        key = "thickness"
        key = "station_offsets"
    elif damage == "offsets_start":
        offsets = tables["shell_axes_station_offsets"].copy()
        offsets[0] = 1
        tables["shell_axes_station_offsets"] = offsets
        key = "station_offsets"
    elif damage == "inf_present":
        values = tables["shell_axes_station_positions"].copy()
        values[0] = np.inf
        tables["shell_axes_station_positions"] = values
    elif damage == "absent_not_zero":
        values = tables["shell_axes_thickness"].copy()
        mask = tables["shell_axes_thickness_present"].copy()
        mask[0] = False
        values[0] = 5.0
        tables["shell_axes_thickness"], tables["shell_axes_thickness_present"] = values, mask
        key = "thickness"
    elif damage == "empty_mask_dtype":
        # an empty element set: dtype of the (empty) mask must still be bool
        meta = dict(meta)
        meta["entries"] = {k: dict(v) for k, v in meta["entries"].items()}
        for name in list(tables):
            if name.startswith("shell_axes_") and isinstance(tables[name], np.ndarray):
                tables[name] = tables[name][:0]
        tables["shell_axes_station_offsets"] = np.zeros(1, dtype=np.int64)
        meta["entries"]["element_ids"]["shape"] = [0]
        meta["entries"]["station_offsets"]["shape"] = [1]
        meta["entries"]["thickness"]["shape"] = [0]
        tables["shell_axes_thickness_present"] = np.zeros(0, dtype=np.int8)
        tables["shell_axes_meta"] = meta
        key = "thickness"
    if per_case.get(key):
        case = 0
    with pytest.raises(ValueError):
        read_shell_axes_table(_FakeDataset(tables), key, case=case)


@pytest.mark.parametrize("damage", ["offsets_overflow", "case_slot", "duplicate_ids", "unknown_entry"])
def test_reader_refuses_overflowing_offsets_and_inconsistent_identities(stored_tables, damage):
    """Codex r4: offsets compared as Python ints (no wrapping difference); each
    case slot names its own index; element ids unique; only known entries."""

    tables, _records = stored_tables
    tables = dict(tables)
    meta = dict(tables["shell_axes_meta"])
    meta["entries"] = {k: dict(v) for k, v in meta["entries"].items()}
    meta["cases"] = [dict(case) for case in meta["cases"]]
    tables["shell_axes_meta"] = meta
    key, case = "station_offsets", None
    if damage == "offsets_overflow":
        offsets = tables["shell_axes_station_offsets"].copy()
        last = int(offsets[-1])
        offsets[1:4] = np.array([np.iinfo(np.int64).max, np.iinfo(np.int64).min, -1])
        offsets[-1] = last
        tables["shell_axes_station_offsets"] = offsets
    elif damage == "case_slot":
        meta["cases"][0]["case_index"] = 1
        key = "thickness"
    elif damage == "duplicate_ids":
        ids = tables["shell_axes_element_ids"].copy()
        ids[1] = ids[0]
        tables["shell_axes_element_ids"] = ids
        key = "thickness"
    elif damage == "unknown_entry":
        meta["entries"]["bogus"] = {"layout": "shared", "kind": "numeric", "dtype": "<f8", "shape": [1]}
        key = "thickness"
    if meta["entries"][key]["layout"] == "per_case":
        case = 0
    with pytest.raises(ValueError):
        read_shell_axes_table(_FakeDataset(tables), key, case=case)


@pytest.mark.parametrize("damage", ["other_declaration", "missing_table", "missing_other_mask", "bool_shape",
                                    "refused_family_value"])
def test_reader_validates_the_whole_artifact(stored_tables, damage):
    """Codex r5 P2: any malformed entry, inventory or family/certification
    disagreement refuses every read, not only reads of that entry."""

    tables, _records = stored_tables
    tables = dict(tables)
    meta = dict(tables["shell_axes_meta"])
    meta["entries"] = {k: dict(v) for k, v in meta["entries"].items()}
    tables["shell_axes_meta"] = meta
    if damage == "other_declaration":
        meta["entries"]["shear_vectors"]["dtype"] = "<f4"
    elif damage == "missing_table":
        del tables["shell_axes_bending_tensors"]
    elif damage == "missing_other_mask":
        del tables["shell_axes_station_positions_present"]
    elif damage == "bool_shape":
        meta["entries"]["thickness"]["shape"] = [True]
    elif damage == "refused_family_value":
        certified = tables["shell_axes_membrane_certified"].copy()
        certified[:] = False
        tables["shell_axes_membrane_certified"] = certified
    with pytest.raises(ValueError):
        read_shell_axes_table(_FakeDataset(tables), "thickness")


# ---------------------------------------------------------------- serial edge cases
def test_v2d_shells_are_recorded():
    from result_transport_helpers import s3_linear_result

    solution = s3_linear_result(project=None, progress=lambda message=None: None)
    record = recover_shell_axes_record(solution.built.fe_model, solution.displacements)
    assert record["branch"] == ["v2d"] and record["element_refusal"] == [""]


def test_a_float32_reference_normal_is_refused_by_the_module_admission():
    solution = solve_linear_static(_plate(), target_size=0.25)
    fe = solution.built.fe_model
    eid = next(iter(fe.mesh.elements))
    element = fe.mesh.elements[eid]
    vars(element)["reference_normal"] = np.asarray(vars(element)["reference_normal"], dtype=np.float32)
    record = recover_shell_axes_record(fe, solution.displacements)
    row = list(record["element_ids"]).index(eid)
    assert record["element_refusal"][row] == "AXES_ATTRIBUTE_TYPE"
    assert set(record["element_refusal"][:row] + record["element_refusal"][row + 1:]) == {""}


def test_a_non_str_mesh_key_refuses_every_shell():
    solution = solve_linear_static(_plate(), target_size=0.25)
    vars(solution.built.fe_model.mesh)[7] = "not a str key"
    record = recover_shell_axes_record(solution.built.fe_model, solution.displacements)
    assert set(record["element_refusal"]) == {"AXES_OBJECT_NOT_PLAIN"}


def test_without_the_admission_gate_every_shell_is_refused(monkeypatch):
    """Temporary dependency on the private ``shell_axes_outputs._admit``
    (coordinator 2026-11-27): without it nothing of an unadmitted element is
    used; every shell carries a typed refusal (fail closed)."""

    import sys
    import types

    import anyfesolver.shell_axes_outputs as solver_module

    import anyfem.post.shell_axes_record as module

    solution = solve_linear_static(_plate(pressure=1.0e3, in_plane=2.0e5), target_size=0.25)
    fe = solution.built.fe_model
    stand_in = types.ModuleType(solver_module.__name__)
    stand_in.__dict__.update({k: v for k, v in vars(solver_module).items() if k != "_admit"})
    monkeypatch.setitem(sys.modules, solver_module.__name__, stand_in)
    assert module._admission_gate() is None
    record = recover_shell_axes_record(fe, solution.displacements)
    assert set(record["element_refusal"]) == {"AXES_ADMISSION_UNAVAILABLE"}
    assert record["station_offsets"][-1] == 0


# ---------------------------------------------------------------- Codex r6 data-layer items
def _read_all(tables, records):
    dataset = _FakeDataset(tables)
    meta = tables["shell_axes_meta"]
    for index, record in enumerate(records):
        for key, value in record.items():
            if key in ("schema", "provenance"):
                continue
            per_case = meta["entries"][key]["layout"] == "per_case"
            stored = read_shell_axes_table(dataset, key, case=index if per_case else None)
            if isinstance(value, np.ndarray):
                assert stored.dtype == value.dtype and stored.shape == value.shape, key
                if value.dtype.kind == "f":  # presence first, then the bytes of present values
                    assert np.array_equal(np.isnan(stored), np.isnan(value)), key
                assert _same(np.nan_to_num(stored, nan=0.0), np.nan_to_num(value, nan=0.0)), key
            else:
                assert stored == list(value), key


@pytest.mark.parametrize("which", ["all_refused", "mixed"])
def test_records_with_refused_shells_round_trip(which):
    """Codex r6 P1: a refused shell has zero stations; that must not make the
    stored record unreadable (all refused, or refused next to admitted)."""

    if which == "all_refused":
        project = _plate(origin=(1.0e7, 0.0, 0.0), tension=False)
        solution = _run_job(project, "Linear static", solve_linear_static, True, target_size=0.25)
    else:
        import anyfem.post.shell_axes_record as module

        solution = _run_job(_plate(tension=False), "Linear static", solve_linear_static, False, target_size=0.25)
        fe = solution.built.fe_model
        element = fe.mesh.elements[next(iter(fe.mesh.elements))]
        vars(element)["reference_normal"] = np.asarray(vars(element)["reference_normal"], dtype=np.float32)
        solution._requested_shell_axes = module.recover_shell_axes_record(
            fe, solution.displacements, provenance={"analysis": "linear_static", "case_index": 0, "case_label": "static"})
    record = solution._requested_shell_axes
    refused = [code for code in record["element_refusal"] if code]
    assert refused and (which == "mixed") == (len(refused) < len(record["element_refusal"]))
    _read_all(_axes_tables(solution), [record])


@pytest.mark.parametrize("damage", ["position_coordinate", "normal_coordinate"])
def test_reader_refuses_an_incomplete_station_row(stored_tables, damage):
    """Codex r6 P2: every admitted station has all three coordinates, and a
    basis normal, where present, is a whole row (the writer emits whole rows)."""

    tables, _records = stored_tables
    tables = dict(tables)
    key = "station_positions" if damage == "position_coordinate" else "bending_basis_normals"
    values = tables[f"shell_axes_{key}"].copy()
    mask = tables[f"shell_axes_{key}_present"].copy()
    index = int(np.flatnonzero(mask)[1])  # one coordinate of a present row
    mask[index] = False
    values[index] = 0.0
    tables[f"shell_axes_{key}"], tables[f"shell_axes_{key}_present"] = values, mask
    with pytest.raises(ValueError):
        read_shell_axes_table(_FakeDataset(tables), "thickness")


def _identical_cases_batch():
    project = _plate(tension=False)
    project.load_case("again").add_pressure(project.face(next(iter(project.geometry.faces))), 1.0e4)
    return _run_job(project, "Batch linear static", solve_linear_static_many, True, target_size=0.5,
                    load_cases=("default", "again"))


@pytest.mark.parametrize("damage", ["dropped_case", "label_mismatch"])
def test_reader_refuses_axes_cases_that_do_not_match_the_artifact(damage):
    """Codex r6 P2: the axes case list must match the artifact's own cases
    (frame axis; batch case names), also when every entry is shared."""

    batch = _identical_cases_batch()
    tables = _axes_tables(batch)
    meta = dict(tables["shell_axes_meta"])
    assert all(entry["layout"] == "shared" for entry in meta["entries"].values())
    if damage == "dropped_case":
        meta["cases"] = meta["cases"][:1]
    else:
        meta["cases"] = [dict(case) for case in meta["cases"]]
        meta["cases"][1]["case_name"] = "default"
    tables["shell_axes_meta"] = meta
    with pytest.raises(ValueError):
        read_shell_axes_table(_FakeDataset(tables), "thickness")


# ---------------------------------------------------------------- Codex PR-1 round 1
def _damaged(tables):
    tables = dict(tables)
    meta = dict(tables["shell_axes_meta"])
    meta["entries"] = {k: dict(v) for k, v in meta["entries"].items()}
    meta["cases"] = [dict(case) for case in meta["cases"]]
    tables["shell_axes_meta"] = meta
    return tables, meta


@pytest.mark.parametrize("damage", ["no_case_names", "reversed_frames", "imported_analysis", "frame_kind",
                                    "one_case_batch_without_names"])
def test_reader_checks_case_identity_against_the_artifact(stored_tables, damage):
    tables, meta = _damaged(stored_tables[0])
    if damage == "no_case_names":
        del tables["load_case_names"]
    elif damage == "reversed_frames":
        tables["__frames__"] = np.asarray(tables["__frames__"])[::-1]
    elif damage == "imported_analysis":
        meta["cases"][1]["analysis"] = "imported"
    elif damage == "frame_kind":
        tables["__frame_kind__"] = "static"
    else:
        one = _run_job(_plate(), "Batch linear static", solve_linear_static_many, True, load_cases=("default",))
        tables, meta = _damaged(_axes_tables(one))
        del tables["load_case_names"]
    with pytest.raises(ValueError):
        read_shell_axes_table(_FakeDataset(tables), "thickness")


@pytest.mark.parametrize("damage", ["polarity", "partial_director", "reason_without_code", "surplus_table",
                                    "refused_shell_with_branch"])
def test_reader_refuses_impossible_metadata_and_surplus_tables(damage):
    """Codex PR-1 r1: metadata domains and refused-shell rules; the stored
    inventory must equal the declared one."""

    import anyfem.post.shell_axes_record as module
    from result_transport_helpers import s3_linear_result

    v2d = s3_linear_result(project=None, progress=lambda message=None: None)
    v2d._requested_shell_axes = module.recover_shell_axes_record(
        v2d.built.fe_model, v2d.displacements,
        provenance={"analysis": "linear_static", "case_index": 0, "case_label": "static"})
    tables, meta = _damaged(_axes_tables(v2d))
    read_shell_axes_table(_FakeDataset(dict(tables)), "thickness")  # undamaged: reads
    if damage == "polarity":
        values = tables["shell_axes_director_polarity"].copy()
        values[0] = 7
        tables["shell_axes_director_polarity"] = values
    elif damage == "partial_director":
        values = tables["shell_axes_physical_reference_director"].copy()
        mask = tables["shell_axes_physical_reference_director_present"].copy()
        mask[0], values[0] = False, 0.0
        tables["shell_axes_physical_reference_director"] = values
        tables["shell_axes_physical_reference_director_present"] = mask
    elif damage == "reason_without_code":
        tables["shell_axes_element_refusal_reason"] = {"values": ["no code but a reason"]}
    elif damage == "surplus_table":
        tables["shell_axes_branch__2"] = {"values": ["v2d"]}
    else:
        far = _run_job(_plate(origin=(1.0e7, 0.0, 0.0), tension=False), "Linear static", solve_linear_static, True,
                       target_size=0.5)
        tables, meta = _damaged(_axes_tables(far))
        tables["shell_axes_branch"] = {"values": ["legacy"] * len(tables["shell_axes_branch"]["values"])}
    with pytest.raises(ValueError):
        read_shell_axes_table(_FakeDataset(tables), "thickness")


@pytest.mark.parametrize("damage", ["bool_case_index", "different_element_ids"])
def test_writer_refuses_records_that_would_not_read_back(damage):
    from anyfem.io.result_artifact import result_artifact_payload

    axes = _run_job(_plate(), "Batch linear static", solve_linear_static_many, True,
                    target_size=0.5, load_cases=("default", "second"))
    record = dict(axes.shapes[0]._requested_shell_axes)
    if damage == "bool_case_index":
        record["provenance"] = {**record["provenance"], "case_index": False}
    else:
        record["element_ids"] = record["element_ids"] + 1000
    axes.shapes[0]._requested_shell_axes = record
    with pytest.raises(ValueError):
        result_artifact_payload(axes)


def test_unknown_material_name_and_short_dof_map_are_typed_refusals(monkeypatch, plate_solution):
    """Codex PR-1 r1: get_material falls back to a default for unknown names;
    a short DOF map must not reach the recovery."""

    import copy

    fe = copy.deepcopy(plate_solution.built.fe_model)
    eid = next(iter(fe.mesh.elements))
    vars(fe.mesh.elements[eid])["material_name"] = "no such material"
    record = recover_shell_axes_record(fe, plate_solution.displacements)
    row = list(record["element_ids"]).index(eid)
    assert record["element_refusal"][row] == "AXES_RECOVERY_MALFORMED"
    fe = copy.deepcopy(plate_solution.built.fe_model)
    kind = type(next(iter(fe.mesh.elements.values())))
    original = kind.get_dof_mapping
    monkeypatch.setattr(kind, "get_dof_mapping", lambda self, mesh: list(original(self, mesh))[:3])
    record = recover_shell_axes_record(fe, plate_solution.displacements)
    assert set(record["element_refusal"]) == {"AXES_MISSING_GLOBAL_TENSORS"}


def test_validation_reads_in_bounded_shell_spans(monkeypatch, stored_tables):
    """Codex PR-1 r1: validation keeps only a bounded span of shells."""

    import anyfem.post.shell_axes_record as module

    monkeypatch.setattr(module, "VALIDATE_SHELLS", 2)
    tables = dict(stored_tables[0])
    reads = []

    class Counting(_FakeDataset):
        def table(self, name, rows=None):
            value = super().table(name, rows)
            if name.startswith("shell_axes_") and "tensors" in name and isinstance(value, np.ndarray):
                reads.append(value.size)
            return value

    read_shell_axes_table(Counting(tables), "thickness")
    max_stations = int(np.max(np.diff(tables["shell_axes_station_offsets"])))
    assert reads and max(reads) <= 2 * max_stations * 9


_PROBE = """
import sys
from anyfem import Project, pinned, solve_linear_static, steel
from anyfem.application.workflow import _execute_analysis_job
from anyfem.io.result_artifact import result_artifact_payload
from anyfem.result_transport import decode_result, encode_result
from anyfem.solve.build import build_fe_model
project = Project(name="probe")
project.add_material(steel("S355", 0.008))
project.add_plate_section("plate", thickness=0.008, material="S355")
points = project.geometry.add_points([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
edges = project.geometry.add_polyline(points, close=True)
face = project.geometry.add_face(edges)
project.assign_plate(face, "plate")
for edge in edges:
    project.add_support(pinned(project.edge(edge)))
project.load_case().add_pressure(project.face(face), 1.0e4)
built = build_fe_model(project, project.generate_mesh(0.5))
solution = _execute_analysis_job(project=project, solver_function=solve_linear_static, analysis_name="Linear static",
                                 options={"built": built}, progress=lambda message: None,
                                 requested_stress=True, requested_global_stress=True)
result_artifact_payload(decode_result(encode_result(solution)))
print([name for name in ("anyfem.post.shell_axes_record", "anyfesolver.shell_axes_outputs") if name in sys.modules])
"""


def test_ordinary_run_never_imports_an_axes_module(tmp_path):
    """Codex PR-1 r1 P3: a fresh process runs an ordinary job through the
    worker step, the result transport and the artifact payload; neither axes
    module is imported."""

    import pathlib
    import subprocess
    import sys

    script = tmp_path / "probe.py"
    script.write_text(_PROBE)
    out = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=600)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1] == "[]"


# ---------------------------------------------------------------- Codex PR-1 round 2
@pytest.mark.parametrize("damage", ["float32_column", "list_column", "none_string"])
def test_writer_refuses_mistyped_record_columns(damage):
    """Codex PR-1 r2: nothing is coerced before it is written."""

    from anyfem.io.result_artifact import result_artifact_payload

    axes = _run_job(_plate(), "Batch linear static", solve_linear_static_many, True,
                    target_size=0.5, load_cases=("default", "second"))
    record = dict(axes.shapes[1]._requested_shell_axes)
    if damage == "float32_column":
        record["thickness"] = record["thickness"].astype(np.float32)
    elif damage == "list_column":
        record["thickness"] = record["thickness"].tolist()
    else:
        record["branch"] = [None] * len(record["branch"])
    axes.shapes[1]._requested_shell_axes = record
    with pytest.raises(ValueError):
        result_artifact_payload(axes)


@pytest.mark.parametrize("damage", ["imported_solution_type", "linear_case_name"])
def test_records_bind_to_the_artifact_solution_type(damage):
    """Codex PR-1 r2: a static frame alone does not qualify a result; the
    artifact's own solution type does; a linear record names no batch case."""

    from anyfem.io.result_artifact import result_artifact_payload

    solution = _run_job(_plate(), "Linear static", solve_linear_static, True)
    tables, meta = _damaged(_axes_tables(solution))
    if damage == "imported_solution_type":
        tables["__solution_type__"] = "ImportedSolution"
    else:
        meta["cases"][0]["case_name"] = "default"
        record = dict(solution._requested_shell_axes)
        record["provenance"] = {**record["provenance"], "case_name": "default"}
        solution._requested_shell_axes = record
        with pytest.raises(ValueError):
            result_artifact_payload(solution)
    with pytest.raises(ValueError):
        read_shell_axes_table(_FakeDataset(tables), "thickness")


@pytest.mark.parametrize("damage", ["station_mask_int8", "element_ids_float32"])
def test_an_empty_record_still_checks_stored_dtypes(damage):
    """Codex PR-1 r2: zero shells; only one stored dtype is changed."""

    from test_analyses import strut_project

    project, _section, _start, _end = strut_project()
    solution = _run_job(project, "Linear static", solve_linear_static, True, target_size=0.25)
    tables = _axes_tables(solution)
    assert tables["shell_axes_element_ids"].size == 0
    read_shell_axes_table(_FakeDataset(dict(tables)), "station_offsets")  # valid: reads
    if damage == "station_mask_int8":
        tables["shell_axes_station_positions_present"] = tables["shell_axes_station_positions_present"].astype(np.int8)
    else:
        tables["shell_axes_element_ids"] = tables["shell_axes_element_ids"].astype(np.float32)
    with pytest.raises(ValueError):
        read_shell_axes_table(_FakeDataset(tables), "station_offsets")


def test_offsets_are_streamed_in_spans(monkeypatch, stored_tables):
    """Codex PR-1 r2: validation does not hold a whole offsets array per case."""

    import anyfem.post.shell_axes_record as module

    monkeypatch.setattr(module, "VALIDATE_SHELLS", 2)
    reads = []

    class Counting(_FakeDataset):
        def table(self, name, rows=None):
            value = super().table(name, rows)
            if name == "shell_axes_station_offsets":
                reads.append(np.asarray(value).size)
            return value

    read_shell_axes_table(Counting(dict(stored_tables[0])), "thickness")
    assert reads and max(reads) <= 3


def test_certified_metadata_and_normals_are_the_solver_values_bit_for_bit(plate_solution):
    """Codex PR-1 r2 P3: every copied column, not only positions and tensors."""

    fe = plate_solution.built.fe_model
    u = np.asarray(plate_solution.displacements, float)
    record = recover_shell_axes_record(fe, u)
    offsets = record["station_offsets"]
    checked = 0
    for row, eid in enumerate(record["element_ids"]):
        element = fe.mesh.elements[int(eid)]
        direct = recover_shell_axes(element, fe.mesh, u[np.asarray(element.get_dof_mapping(fe.mesh))],
                                    fe.get_material(element.material_name)).axes
        lo, hi = offsets[row], offsets[row + 1]
        for fam in FAMILIES:
            key = f"{fam}_basis_normals"
            if bool(record[f"{fam}_certified"][row]) and key in direct.arrays:
                assert _same(np.ascontiguousarray(record[key][lo:hi]), np.ascontiguousarray(direct.arrays[key]))
                checked += 1
        if record["shear_certified"][row] and "shear_normal_components" in direct.arrays:
            assert _same(np.ascontiguousarray(record["shear_normal_components"][lo:hi]),
                         np.ascontiguousarray(direct.arrays["shear_normal_components"]))
        if "physical_reference_director" in direct.arrays:
            assert _same(np.ascontiguousarray(record["physical_reference_director"][row]),
                         np.ascontiguousarray(direct.arrays["physical_reference_director"]))
        if "director_polarity" in direct.arrays:
            assert int(record["director_polarity"][row]) == int(np.asarray(direct.arrays["director_polarity"]))
        for key in ("reference_surface_offset", "section_origin_offset_from_reference",
                    "physical_bottom_offset_from_reference", "physical_top_offset_from_reference"):
            if key in direct.arrays:
                assert _same(np.asarray(record[key][row]), np.asarray(direct.arrays[key]).astype(np.float64))
    assert checked > 0


_SUBMIT_PROBE = """
import sys
from anyfem import Project, pinned, steel
from anyfem.application.workflow import _execute_analysis_job, _requested_recovery
from anyfem.document import DocumentSession
from anyfem.io.result_artifact import result_artifact_payload
from anyfem.jobs import JobManager
from anyfem.model.records import AnalysisDefinition
from anyfem.result_transport import decode_result, encode_result
from anyfem.solve.build import build_fe_model
from anyfem.solve.run import solve_linear_static
project = Project(name="probe")
project.add_material(steel("S355", 0.008))
project.add_plate_section("plate", thickness=0.008, material="S355")
points = project.geometry.add_points([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
edges = project.geometry.add_polyline(points, close=True)
face = project.geometry.add_face(edges)
project.assign_plate(face, "plate")
for edge in edges:
    project.add_support(pinned(project.edge(edge)))
project.load_case().add_pressure(project.face(face), 1.0e4)
built = build_fe_model(project, project.generate_mesh(0.5))
scopes = [{"request": {"quantity_keys": ["stress_von_mises"], "basis": "global", "recovery": "native"}}]
flags = _requested_recovery(scopes)
manager = JobManager(project, executor="thread")
record = manager.submit(AnalysisDefinition("Job"), DocumentSession(project).snapshot(), _execute_analysis_job,
                        kwargs={"solver_function": solve_linear_static, "analysis_name": "Linear static",
                                "options": {"built": built}, **flags})
manager.wait(record.id, timeout=600)
result_artifact_payload(decode_result(encode_result(manager.result(record.id))))
print([name for name in ("anyfem.post.shell_axes_record", "anyfesolver.shell_axes_outputs") if name in sys.modules])
"""


def test_ordinary_submission_never_imports_an_axes_module(tmp_path):
    """Codex PR-1 r2 P3: through the submission flags and the JobManager."""

    import subprocess
    import sys

    script = tmp_path / "submit_probe.py"
    script.write_text(_SUBMIT_PROBE)
    out = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=600)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1] == "[]"


# ---------------------------------------------------------------- Codex PR-1 round 3
def test_writer_refuses_a_solution_type_override_with_axes_records():
    from anyfem.io.result_artifact import result_artifact_payload

    solution = _run_job(_plate(), "Linear static", solve_linear_static, True)
    with pytest.raises(ValueError, match="solution_type"):
        result_artifact_payload(solution, provenance={"solution_type": "ImportedSolution"})


@pytest.mark.parametrize("damage", ["impostor_class", "batch_case_not_linear", "record_on_batch_wrapper"])
def test_writer_checks_exact_result_classes(damage):
    """Codex PR-1 r3: exact wrapper and case classes; no record on a batch wrapper."""

    import dataclasses

    from anyfem.io.result_artifact import result_artifact_payload
    from anyfem.post.results import LinearSolution

    if damage == "impostor_class":
        solution = _run_job(_plate(), "Linear static", solve_linear_static, True)
        Impostor = type("LinearSolution", (LinearSolution,), {})
        fields = {field.name: getattr(solution, field.name) for field in dataclasses.fields(solution) if field.init}
        impostor = Impostor(**fields)
        impostor._requested_shell_axes = solution._requested_shell_axes
        target = impostor
    else:
        batch = _run_job(_plate(), "Batch linear static", solve_linear_static_many, True,
                         target_size=0.5, load_cases=("default", "second"))
        if damage == "batch_case_not_linear":
            Impostor = type("LinearSolution", (LinearSolution,), {})
            case = batch.shapes[1]
            fields = {field.name: getattr(case, field.name) for field in dataclasses.fields(case) if field.init}
            replacement = Impostor(**fields)
            replacement._requested_shell_axes = case._requested_shell_axes
            batch.shapes[1] = replacement
        else:
            batch._requested_shell_axes = batch.shapes[0]._requested_shell_axes
        target = batch
    with pytest.raises(ValueError):
        result_artifact_payload(target)


def test_worker_step_refuses_a_batch_case_that_is_not_linear():
    import dataclasses

    from anyfem.application.workflow import _attach_shell_axes_records
    from anyfem.post.results import LinearSolution

    batch = solve_linear_static_many(_plate(), target_size=0.5, load_cases=("default", "second"))
    Impostor = type("LinearSolution", (LinearSolution,), {})
    case = batch.shapes[0]
    batch.shapes[0] = Impostor(**{field.name: getattr(case, field.name)
                                  for field in dataclasses.fields(case) if field.init})
    with pytest.raises(ValueError, match="AXES_ANALYSIS_NOT_QUALIFIED"):
        _attach_shell_axes_records(batch, lambda message: None)


def test_validation_bounds_stations_per_span_and_per_shell(monkeypatch, stored_tables):
    """Codex PR-1 r3: spans are bounded by stations too; a shell's own station
    count is capped (a structural limit, not the solver rule)."""

    import anyfem.post.shell_axes_record as module

    tables = dict(stored_tables[0])
    stations = int(np.max(np.diff(tables["shell_axes_station_offsets"])))
    monkeypatch.setattr(module, "VALIDATE_STATIONS", stations)
    reads = []

    class Counting(_FakeDataset):
        def table(self, name, rows=None):
            value = super().table(name, rows)
            if name.startswith("shell_axes_") and "tensors" in name and isinstance(value, np.ndarray):
                reads.append(value.size)
            return value

    read_shell_axes_table(Counting(dict(tables)), "thickness")
    assert reads and max(reads) <= stations * 9
    monkeypatch.setattr(module, "MAX_STATIONS_PER_SHELL", stations - 1)
    with pytest.raises(ValueError, match="more than"):
        read_shell_axes_table(_FakeDataset(dict(tables)), "thickness")


def test_v2d_director_polarity_and_offsets_are_copied_bit_for_bit():
    """Codex PR-1 r3 P3: the fixture that emits the director, polarity and the
    four offsets (V2D), with their presence asserted before comparing."""

    from result_transport_helpers import s3_linear_result

    solution = s3_linear_result(project=None, progress=lambda message=None: None)
    fe = solution.built.fe_model
    u = np.asarray(solution.displacements, float)
    record = recover_shell_axes_record(fe, u)
    eid = int(record["element_ids"][0])
    element = fe.mesh.elements[eid]
    direct = recover_shell_axes(element, fe.mesh, u[np.asarray(element.get_dof_mapping(fe.mesh))],
                                fe.get_material(element.material_name)).axes
    keys = ("physical_reference_director", "director_polarity", "reference_surface_offset",
            "section_origin_offset_from_reference", "physical_bottom_offset_from_reference",
            "physical_top_offset_from_reference")
    assert all(key in direct.arrays for key in keys)
    assert _same(np.ascontiguousarray(record["physical_reference_director"][0]),
                 np.ascontiguousarray(direct.arrays["physical_reference_director"]))
    assert int(record["director_polarity"][0]) == int(np.asarray(direct.arrays["director_polarity"]))
    for key in keys[2:]:
        assert _same(np.asarray(record[key][0]), np.asarray(direct.arrays[key]).astype(np.float64)), key
