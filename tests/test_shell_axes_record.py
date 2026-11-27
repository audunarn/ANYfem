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
    tables = {key: value for key, value in result_artifact_payload(axes).tables.items()
              if key.startswith("shell_axes_")}
    return tables, [shape._requested_shell_axes for shape in axes.shapes]


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


# ---------------------------------------------------------------- process pool (owner decision 2026-11-27, option A)
def _records_equal(left, right, ignore=("provenance",)):
    assert set(left) == set(right)
    for key, value in left.items():
        if key not in ignore:
            assert _same(value, right[key]), key


def test_pool_record_is_bit_identical_to_the_serial_record(monkeypatch):
    import anyfem.post.shell_axes_record as module
    from anyfem.post.shell_axes_record import ShellAxesPool

    monkeypatch.setattr(module, "POOL_MIN_SHELLS", 1)
    solution = solve_linear_static(_plate(pressure=1.0e3, in_plane=2.0e5), target_size=0.25)
    fe = solution.built.fe_model
    serial = recover_shell_axes_record(fe, solution.displacements)
    with ShellAxesPool(2) as pool:
        pooled = recover_shell_axes_record(fe, solution.displacements, pool=pool, carrier=solution)
        assert pool.used_by  # chunks ran in child processes
    _records_equal(serial, pooled)
    assert pooled["provenance"] == {"recovered_with": "pool:2", "serial_rechecked": 0}


def test_pool_rechecks_elements_whose_transported_copy_lost_solver_state(monkeypatch):
    """Codex r1 P1: the transport drops dynamic solver caches (here the batch
    recovery plan of an ordinary recovery), so a pooled copy could certify what
    the original refuses.  The fingerprint check recovers those serially."""

    import anyfem.post.shell_axes_record as module
    from anyfem.post.shell_axes_record import ShellAxesPool

    monkeypatch.setattr(module, "POOL_MIN_SHELLS", 1)
    solution = solve_linear_static(_plate(pressure=1.0e3, in_plane=2.0e5), target_size=0.08)
    fe = solution.built.fe_model
    solution.stresses()  # caches mesh._recovery_batch_plan (>= 100 elements)
    assert "_recovery_batch_plan" in vars(fe.mesh)
    serial = recover_shell_axes_record(fe, solution.displacements)
    assert set(serial["element_refusal"]) == {"AXES_OBJECT_NOT_PLAIN"}
    with ShellAxesPool(2) as pool:
        pooled = recover_shell_axes_record(fe, solution.displacements, pool=pool, carrier=solution)
    _records_equal(serial, pooled)
    # decided by the module's admission on the originals: nothing dispatched
    assert pooled["provenance"]["serial_rechecked"] == 0 and not pool.used_by


def _pool_vs_serial(monkeypatch, solution, fe=None):
    import anyfem.post.shell_axes_record as module
    from anyfem.post.shell_axes_record import ShellAxesPool

    monkeypatch.setattr(module, "POOL_MIN_SHELLS", 1)
    fe = solution.built.fe_model if fe is None else fe
    serial = recover_shell_axes_record(fe, solution.displacements)
    with ShellAxesPool(2) as pool:
        pooled = recover_shell_axes_record(fe, solution.displacements, pool=pool, carrier=solution)
    _records_equal(serial, pooled)
    return serial, pooled


def test_pool_matches_serial_when_the_transport_would_normalise_an_input(monkeypatch):
    """Codex r2 P1: Q4 pickle restore turns a float32 reference normal into
    float64.  The original is refused (AXES_ATTRIBUTE_TYPE) by the module's
    admission, run on the original before anything is dispatched."""

    solution = solve_linear_static(_plate(), target_size=0.25)
    fe = solution.built.fe_model
    eid = next(iter(fe.mesh.elements))
    element = fe.mesh.elements[eid]
    vars(element)["reference_normal"] = np.asarray(vars(element)["reference_normal"], dtype=np.float32)
    serial, pooled = _pool_vs_serial(monkeypatch, solution)
    row = list(serial["element_ids"]).index(eid)
    assert serial["element_refusal"][row] == "AXES_ATTRIBUTE_TYPE"


def test_pool_matches_serial_when_the_mesh_has_a_non_str_key(monkeypatch):
    """Codex r2 P1: an int key in the mesh's instance dict, which the transport
    drops; every original is refused, nothing certifies on the copies."""

    solution = solve_linear_static(_plate(), target_size=0.25)
    vars(solution.built.fe_model.mesh)[7] = "not a str key"
    serial, pooled = _pool_vs_serial(monkeypatch, solution)
    assert set(serial["element_refusal"]) == {"AXES_OBJECT_NOT_PLAIN"}


def test_pool_rechecks_an_element_whose_copy_differs_in_value(monkeypatch):
    """A recovery input the transport changes in value (simulated on the
    original after the copy is made) is caught by the input digest and the
    element is recovered on the original."""

    import anyfem.post.shell_axes_record as module

    solution = solve_linear_static(_plate(pressure=1.0e3, in_plane=2.0e5), target_size=0.25)
    fe = solution.built.fe_model
    original_entries = module.ShellAxesPool.entries
    target = next(iter(fe.mesh.elements))

    def entries(self, carrier, ids, cancelled):
        rows = original_entries(self, carrier, ids, cancelled)
        # the copy saw a different thickness than the original
        index = list(ids).index(target)
        rows[index] = ("0" * 64, rows[index][1])
        return rows

    monkeypatch.setattr(module.ShellAxesPool, "entries", entries)
    serial, pooled = _pool_vs_serial(monkeypatch, solution)
    assert pooled["provenance"]["serial_rechecked"] == 1


def test_lost_pool_worker_falls_back_to_the_serial_record(monkeypatch):
    """Codex r2 P1: a worker that dies (not a cancellation) must not leave the
    job waiting on the executor; the record is recovered serially."""

    import anyfem.post.shell_axes_record as module
    from anyfem.post.shell_axes_record import ShellAxesPool

    monkeypatch.setattr(module, "POOL_MIN_SHELLS", 1)
    solution = solve_linear_static(_plate(pressure=1.0e3, in_plane=2.0e5), target_size=0.05)
    fe = solution.built.fe_model
    serial = recover_shell_axes_record(fe, solution.displacements)
    pool = ShellAxesPool(2)
    for future in pool._warm:
        future.result(timeout=120)
    victims = list(pool._executor._processes.values())
    killed = []

    def kill_one():
        if not killed:
            victims[0].kill()
            victims[0].join(30)
            killed.append(True)

    try:
        pooled = recover_shell_axes_record(fe, solution.displacements, pool=pool, carrier=solution,
                                           cancelled=kill_one)
    finally:
        pool.close(terminate=True)
    assert killed and not pool.open
    assert pooled["provenance"] == {"recovered_with": "serial", "pool_lost": True}
    _records_equal(serial, pooled)
    for process in victims:
        assert not process.is_alive()


def test_pool_carries_v2d_elements_through_the_result_transport(monkeypatch):
    """S3 V2D elements refuse pickle; the pool copies the model through the
    verified result transport (to_dict/from_dict) instead."""

    import anyfem.post.shell_axes_record as module
    from anyfem.post.shell_axes_record import ShellAxesPool
    from result_transport_helpers import s3_linear_result

    monkeypatch.setattr(module, "POOL_MIN_SHELLS", 1)
    solution = s3_linear_result(project=None, progress=lambda message=None: None)
    fe = solution.built.fe_model
    serial = recover_shell_axes_record(fe, solution.displacements)
    assert serial["branch"] == ["v2d"]
    with ShellAxesPool(2) as pool:
        pooled = recover_shell_axes_record(fe, solution.displacements, pool=pool, carrier=solution)
    _records_equal(serial, pooled)


def test_pool_refuses_a_carrier_of_another_field(monkeypatch):
    import anyfem.post.shell_axes_record as module
    from anyfem.post.shell_axes_record import ShellAxesPool

    monkeypatch.setattr(module, "POOL_MIN_SHELLS", 1)
    solution = solve_linear_static(_plate(), target_size=0.5)
    with ShellAxesPool(2) as pool, pytest.raises(ValueError, match="pool carrier"):
        recover_shell_axes_record(solution.built.fe_model, 2.0 * solution.displacements,
                                  pool=pool, carrier=solution)


def test_cancelled_pool_terminates_its_workers(monkeypatch):
    import anyfem.post.shell_axes_record as module
    from anyfem.post.shell_axes_record import ShellAxesPool

    monkeypatch.setattr(module, "POOL_MIN_SHELLS", 1)
    solution = solve_linear_static(_plate(), target_size=0.25)

    class Cancelled(Exception):
        pass

    def cancel():
        raise Cancelled()

    pool = ShellAxesPool(2)
    processes = []
    try:
        for future in pool._warm:
            future.result(timeout=120)
        processes = list(pool._executor._processes.values())
        assert processes
        with pytest.raises(Cancelled):
            recover_shell_axes_record(solution.built.fe_model, solution.displacements,
                                      pool=pool, carrier=solution, cancelled=cancel)
        assert pool._executor is None
    finally:
        pool.close(terminate=True)
    for process in processes:
        process.join(timeout=30)
        assert not process.is_alive()


def test_default_pool_size_is_serial_outside_a_contained_worker(monkeypatch):
    """Codex r1 P2: thread jobs and library calls (the GUI process) never start
    an unowned pool; only a confirmed contained worker may."""

    import anyfem.job_process as job_process
    from anyfem.post.shell_axes_record import default_pool_workers

    monkeypatch.setenv("ANYFEM_SHELL_AXES_WORKERS", "4")
    assert job_process.contained_worker() is False
    assert default_pool_workers() == 0
    monkeypatch.setattr(job_process, "_CONTAINED_WORKER", True)
    assert default_pool_workers() == 4


def contained_axes_job(*, project, progress, cancellation_token=None):
    """Module-level job for the worker process: a 400-shell plate with an axes request."""

    from anyfem.application.workflow import _execute_analysis_job
    from anyfem.solve.build import build_fe_model

    plate = _plate(pressure=1.0e3, in_plane=2.0e5)
    built = build_fe_model(plate, plate.generate_mesh(0.05))
    return _execute_analysis_job(project=plate, solver_function=solve_linear_static, analysis_name="Linear static",
                                 options={"built": built}, progress=progress,
                                 cancellation_token=cancellation_token, requested_shell_axes=True)


def scratch_probe_job(*, project, progress, scratch_directory=None, cancellation_token=None):
    """Module-level job: reports the supervisor-owned scratch directory."""

    import os

    progress(f"scratch={scratch_directory} exists={bool(scratch_directory) and os.path.isdir(scratch_directory)}")
    return contained_axes_job(project=project, progress=progress, cancellation_token=cancellation_token)


def test_contained_job_scratch_directory_is_owned_and_removed_by_the_supervisor(monkeypatch):
    """Codex r2 P2: the payload lives in a per-job directory the supervisor
    removes after the worker tree is retired."""

    import os

    from anyfem.document import DocumentSession
    from anyfem.jobs import JobManager
    from anyfem.model.records import AnalysisDefinition

    monkeypatch.setenv("ANYFEM_SHELL_AXES_WORKERS", "2")
    project = Project("axes scratch")
    manager = JobManager(project, executor="process")
    record = manager.submit(AnalysisDefinition("Job"), DocumentSession(project).snapshot(), scratch_probe_job,
                            containment=True)
    manager.wait(record.id, timeout=600)
    line = next(str(entry.get("message", "")) for entry in manager.log(record.id)
                if str(entry.get("message", "")).startswith("scratch="))
    path, exists = line[len("scratch="):].rsplit(" exists=", 1)
    assert exists == "True" and os.path.basename(path).startswith("anyfem-job-")
    assert not os.path.exists(path)
    assert manager.result(record.id)._requested_shell_axes["provenance"]["recovered_with"] == "pool:2"


@pytest.mark.parametrize("containment", [True, False])
def test_axes_job_in_a_worker_process_uses_the_pool_only_when_contained(monkeypatch, containment):
    from anyfem.document import DocumentSession
    from anyfem.jobs import JobManager
    from anyfem.model.records import AnalysisDefinition

    monkeypatch.setenv("ANYFEM_SHELL_AXES_WORKERS", "2")
    project = Project("axes worker")
    manager = JobManager(project, executor="process")
    record = manager.submit(AnalysisDefinition("Job"), DocumentSession(project).snapshot(), contained_axes_job,
                            containment=containment)
    manager.wait(record.id, timeout=600)
    messages = [str(entry.get("message", "")) for entry in manager.log(record.id)]
    assert record.status.value in ("completed", "COMPLETED") or str(record.status).lower().endswith("completed"), messages
    result = manager.result(record.id)
    axes = result._requested_shell_axes
    assert len(axes["element_ids"]) >= 400 and set(axes["element_refusal"]) == {""}
    pooled = any(message.startswith("shell result-axes pool: 2 workers") for message in messages)
    assert pooled is containment, messages
    assert axes["provenance"]["recovered_with"] == ("pool:2" if containment else "serial")
    assert axes["provenance"].get("serial_rechecked", 0) == 0


def test_thread_job_stays_serial(monkeypatch):
    from anyfem.document import DocumentSession
    from anyfem.jobs import JobManager
    from anyfem.model.records import AnalysisDefinition

    monkeypatch.setenv("ANYFEM_SHELL_AXES_WORKERS", "2")
    project = Project("axes thread")
    manager = JobManager(project, executor="thread")
    record = manager.submit(AnalysisDefinition("Job"), DocumentSession(project).snapshot(), contained_axes_job,
                            containment=True)
    manager.wait(record.id, timeout=600)
    axes = manager.result(record.id)._requested_shell_axes
    assert axes["provenance"]["recovered_with"] == "serial"


def _scope(keys, basis="global", recovery="native"):
    return {"request": {"quantity_keys": list(keys), "basis": basis, "recovery": recovery}}


def test_submission_flags_change_only_for_axes_requests():
    from anyfem.application.workflow import _requested_recovery

    ordinary = (_scope(("stress_von_mises",)), _scope(("displacement.uz",)), _scope(("stress_xx",), recovery="patch"))
    flags, submission = _requested_recovery(ordinary)
    assert flags == {"requested_stress": True, "requested_global_stress": True, "requested_patch_stress": True}
    assert submission == {}
    flags, submission = _requested_recovery((_scope(("displacement.uz",)),))
    assert flags == {"requested_stress": False, "requested_global_stress": False, "requested_patch_stress": False}
    assert submission == {}
    flags, submission = _requested_recovery((_scope(("axes_membrane_xx",)),))
    # design rev 4: an axes request also sets both ordinary stress flags
    assert flags == {"requested_stress": True, "requested_global_stress": True, "requested_patch_stress": False,
                     "requested_shell_axes": True}
    assert submission == {"containment": True}
