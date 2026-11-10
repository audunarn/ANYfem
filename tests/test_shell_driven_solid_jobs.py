"""Shell-driven solid submodel child: definition record, reach tests, cut, staged run, artifacts, persistence.

Layout
------
* Record tests and the stub-based reach/direction tests need no solver stack.
* Numerical tests (cut, stage, artifacts, run/save/reopen) need the solid stack
  (ANYgeometry shell_detail, ANYmesher structured_box, ANYsolid, ANYsolver
  solid_submodel).  A build without it skips them -- unless the integration
  profile sets ``ANYFEM_REQUIRE_SOLID_STACK=1``, in which case an incomplete
  stack is a collection error for this whole module.
"""

from __future__ import annotations

import copy
import importlib
import json
import os
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

import anyfem.solid_submodel as solid
from anyfem.io import solid_submodel_artifact as artifact
from anyfem.model.solid_submodels import SolidSubmodelDefinition, solid_definition_hash
from anyfem.model.submodels import SourceReference, SubmodelDefinition, SubmodelRecordError, definition_hash
from anyfem.submodel import SubmodelError

_STACK = ("anygeometry.shell_detail", "anymesher.structured_box", "anysolid", "anysolver.solid_submodel",
          "anysolver.submodel_transfer")


def _missing():
    found = []
    for name in _STACK:
        try:
            importlib.import_module(name)
        except ImportError as error:
            found.append(f"{name}: {error}")
    return found


MISSING = _missing()
if MISSING and os.environ.get("ANYFEM_REQUIRE_SOLID_STACK") == "1":
    raise ImportError("ANYFEM_REQUIRE_SOLID_STACK=1 but the solid stack is incomplete: " + "; ".join(MISSING))
needs_stack = pytest.mark.skipif(bool(MISSING), reason="the solid stack is not installed: " + "; ".join(MISSING))

if not MISSING:
    import test_submodel_jobs
    from anyfem.document import DocumentSession
    from anyfem.io.artifacts import ArtifactError, ArtifactStore
    from anyfem.io.project_file import load_project, project_to_dict, save_project
    from anyfem.io.solid_submodel_artifact import (
        read_solid_mesh, read_solid_mesh_metadata, read_solid_result, write_solid_mesh, write_solid_result,
    )
    from anyfem.submodel import bind_source
    from anyfem.submodel_jobs import (
        _verify_child, add_submodel, discard_staged_run, job_source, run_submodel, submodel_run_staleness,
        submodel_snapshot,
    )
    from test_submodel_jobs import Setup, _files

FOOTPRINT = ((0.4, 0.4, 0.0), (0.6, 0.4, 0.0), (0.6, 0.6, 0.0), (0.4, 0.6, 0.0))


def make_definition(**changes):
    values = dict(
        name="detail", source=SourceReference("job", "default"), face_id=7, footprint=FOOTPRINT, target_size=0.05,
        layers=2, order=1, pressure_skin="end",
        geometry={"model_id": "m", "revision": 3, "checksum": "abc"},
        reconstruction={
            "director": [0.0, 0.0, 1.0], "thickness": 0.01, "reference_offset": 0.0,
            "section": {"id": "s", "name": "plate"},
            "material": {"name": "S355", "elastic_modulus": 2.1e11, "poisson_ratio": 0.3},
            "pressure": {"value": 1.0e4}, "boundary_tolerance": 1e-9})
    values.update(changes)
    return SolidSubmodelDefinition(**values)


# ----------------------------------------------------------------------
# the record (no optional dependency)
# ----------------------------------------------------------------------
def test_definition_round_trips_with_kind_and_schema():
    definition = make_definition()
    definition.validate()
    data = definition.to_dict()
    assert data["child_kind"] == "solid" and data["schema_version"] == 1
    assert data["child_case"] == "default" and data["source"] == {"job_id": "job", "case": "default"}
    restored = SolidSubmodelDefinition.from_dict(json.loads(json.dumps(data, allow_nan=False)), "submodel")
    assert restored == definition and restored.to_dict() == data
    assert isinstance(SubmodelDefinition.from_dict(data), SolidSubmodelDefinition)  # the shared reader dispatches
    assert solid_definition_hash(data) == solid_definition_hash({**data, "id": "other", "name": "renamed"})
    assert solid_definition_hash(data) != solid_definition_hash({**data, "layers": 3})


def test_solid_hash_is_the_canonical_definition_hash_and_refuses_other_kinds():
    data = make_definition().to_dict()
    assert solid_definition_hash(data) == definition_hash(data)
    with pytest.raises(SubmodelRecordError):
        solid_definition_hash({**data, "child_kind": "shell"})
    with pytest.raises(SubmodelRecordError):
        solid_definition_hash({key: value for key, value in data.items() if key != "child_kind"})


def test_definition_is_immutable():
    definition = make_definition()
    with pytest.raises(Exception):
        definition.layers = 5
    with pytest.raises(TypeError):
        definition.reconstruction["thickness"] = 1.0
    assert isinstance(definition.footprint, tuple) and all(isinstance(p, tuple) for p in definition.footprint)


@pytest.mark.parametrize("changes", [
    {"footprint": FOOTPRINT[:3]},
    {"footprint": ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0.2, 1, 0))},  # not a rectangle
    {"footprint": ((0, 0, 0), (0, 0, 0), (1, 1, 0), (0, 1, 0))},  # repeated corner
    {"footprint": ((0, 0, 0), (1, 0, 0), (1, 1, float("nan")), (0, 1, 0))},
    {"target_size": 0.0},
    {"target_size": -1.0},
    {"layers": 0},
    {"layers": True},
    {"order": 3},
    {"pressure_skin": "middle"},
    {"pressure_skin": None},  # a retained pressure value needs its skin
    {"face_id": -1},
    {"geometry": {"model_id": "m"}},
    {"schema_version": 2},
])
def test_definition_validation_refuses(changes):
    with pytest.raises(SubmodelRecordError):
        make_definition(**changes).validate()


def _reconstruction(**changes):
    return {**make_definition().to_dict()["reconstruction"], **changes}


@pytest.mark.parametrize("changes", [
    {"reference_offset": 0.001},  # the shell has no reference-offset model: the detail is centred on the plane
    {"director": [0.0, 0.0, 2.0]},  # not a unit vector
    {"invented": 1.0},  # fields that nothing produces or reads
])
def test_reconstruction_refuses_what_the_cut_never_produces(changes):
    with pytest.raises(SubmodelRecordError):
        make_definition(reconstruction=_reconstruction(**changes)).validate()


def test_from_dict_refuses_other_kinds_future_schemas_and_unknown_fields():
    data = make_definition().to_dict()
    for damage in ({"child_kind": "shell"}, {"schema_version": 2}, {"schema_version": 0}, {"id": ""},
                   {"source": {"job_id": "job"}}, {"pressure_skin": 3}, {"made_up": 1}):
        broken = copy.deepcopy(data)
        broken.update(damage)
        with pytest.raises(SubmodelRecordError):
            SolidSubmodelDefinition.from_dict(broken, "submodel")
    missing = copy.deepcopy(data)
    del missing["reconstruction"]["material"]
    with pytest.raises(SubmodelRecordError):
        SolidSubmodelDefinition.from_dict(missing, "submodel")


def test_no_pressure_definition_is_valid_without_skin():
    definition = make_definition(pressure_skin=None, reconstruction=_reconstruction(pressure={"value": None}))
    definition.validate()
    assert definition.to_dict()["pressure_skin"] is None


# ----------------------------------------------------------------------
# reach tests on a duck-typed geometry: proved separation vs unresolved overlap vs corruption
# ----------------------------------------------------------------------
class Geometry:
    """Only what the reach tests read; the owner's bounds and curve kind are substituted per test."""

    def __init__(self, vertices=None, samples=None):
        self.vertices = vertices or {}
        self.samples = samples
        self.edges = {1: SimpleNamespace(start=0, end=1, curve=object())}
        self.faces = {9: SimpleNamespace(loop=[SimpleNamespace(edge=1)], holes=())}
        self.calls = []

    def vertex_position(self, vertex):
        self.calls.append(("vertex", vertex))
        return self.vertices[vertex]

    def sample_edge(self, edge, t):
        self.calls.append(("sample", edge))
        if isinstance(self.samples, Exception):
            raise self.samples
        return self.samples(np.asarray(t, dtype=float))


def reach(geometry):
    volume, margin = solid._detail_volume(np.asarray(FOOTPRINT, dtype=float), np.array([0.0, 0.0, 1.0]), 0.01, 1e-9)
    return solid._Reach(geometry, volume, margin, 7)


def bounds(monkeypatch, low, high, *, straight):
    monkeypatch.setattr(solid, "_owner_edge_bounds",
                        lambda g, e, v: (np.asarray(low, dtype=float), np.asarray(high, dtype=float), True))
    monkeypatch.setattr(solid, "_is_straight", lambda g, e: straight)


def test_detail_volume_is_an_oriented_box_about_the_footprint():
    ctx = reach(Geometry())
    assert np.allclose(ctx.volume.lower, (0.0, 0.0, -0.005)) and np.allclose(ctx.volume.upper, (0.2, 0.2, 0.005))
    assert np.allclose(ctx.volume.world(np.array([0.1, 0.1, 0.0]))[0], (0.5, 0.5, 0.0))
    assert ctx.margin >= 1e-9


def test_a_curved_edge_proved_separated_by_owner_bounds_is_not_reached_and_never_sampled(monkeypatch):
    geometry = Geometry(samples=RuntimeError("must not be sampled"))
    bounds(monkeypatch, (5, 5, 5), (6, 6, 6), straight=False)
    assert solid._entity_reaches(reach(geometry), "edge", 1) is False
    assert geometry.calls == []


def test_a_crossing_curve_reaches_and_a_tangent_one_within_the_margin_reaches(monkeypatch):
    bounds(monkeypatch, (-1, -1, -1), (1, 1, 1), straight=False)
    crossing = Geometry(samples=lambda t: np.column_stack([0.3 + 0.4 * t, 0.5 + 0 * t, 0 * t]))
    assert solid._entity_reaches(reach(crossing), "edge", 1) is True
    tangent = Geometry(samples=lambda t: np.column_stack([0.6 + 5e-7 + 0 * t, 0.2 + 0.6 * t, 0 * t]))  # grazes x = 0.6
    assert solid._entity_reaches(reach(tangent), "edge", 1) is True


def test_a_curve_whose_bounds_overlap_but_no_sample_is_inside_is_refused_not_dropped(monkeypatch):
    bounds(monkeypatch, (-1, -1, -1), (1, 1, 1), straight=False)
    elsewhere = Geometry(samples=lambda t: np.column_stack([0.9 + 0 * t, 0.9 + 0 * t, t * 0.0]))
    with pytest.raises(SubmodelError, match="CUT_CURVE_UNRESOLVED"):
        solid._entity_reaches(reach(elsewhere), "edge", 1)


def test_a_straight_edge_is_decided_exactly_not_by_its_bounding_box(monkeypatch):
    ctx_for = lambda a, b: reach(Geometry(vertices={0: a, 1: b}))  # noqa: E731
    bounds(monkeypatch, (-1, -1, -1), (1, 1, 1), straight=True)
    # x + y = 1.3 passes beyond the box corner (x + y <= 1.2) although the segment's bounding box overlaps the box
    assert solid._entity_reaches(ctx_for((0.3, 1.0, 0.0), (1.0, 0.3, 0.0)), "edge", 1) is False
    assert solid._entity_reaches(ctx_for((0.3, 0.8, 0.0), (0.8, 0.3, 0.0)), "edge", 1) is True


def test_vertices_are_exact_points(monkeypatch):
    geometry = Geometry(vertices={0: (0.5, 0.5, 0.004), 1: (0.5, 0.5, 0.02), 2: (0.61, 0.5, 0.0)})
    ctx = reach(geometry)
    assert solid._entity_reaches(ctx, "vertex", 0) is True
    assert solid._entity_reaches(ctx, "vertex", 1) is False
    assert solid._entity_reaches(ctx, "vertex", 2) is False


@pytest.mark.parametrize("failure", ["bounds_raise", "bounds_nonfinite", "samples_raise", "vertex_missing"])
def test_geometry_corruption_is_a_typed_refusal_never_not_reached(monkeypatch, failure):
    from anygeometry.errors import GeometryError

    geometry = Geometry(vertices={0: (0, 0, 0)}, samples=GeometryError("corrupt") if failure == "samples_raise" else None)
    if failure == "bounds_raise":
        def broken(g, e, v):
            raise GeometryError("corrupt")
        monkeypatch.setattr(solid, "_owner_edge_bounds", broken)
    elif failure == "bounds_nonfinite":
        monkeypatch.setattr(solid, "_owner_edge_bounds",
                            lambda g, e, v: (np.array([np.nan] * 3), np.array([1.0] * 3), True))
    else:
        bounds(monkeypatch, (-1, -1, -1), (1, 1, 1), straight=failure == "vertex_missing")
    with pytest.raises(SubmodelError, match="CUT_GEOMETRY_UNRESOLVED"):
        solid._entity_reaches(reach(geometry), "edge", 1)


def test_missing_entities_and_unsupported_kinds_refuse():
    ctx = reach(Geometry())
    with pytest.raises(SubmodelError, match="CUT_GEOMETRY_UNRESOLVED"):
        solid._entity_reaches(ctx, "vertex", 99)
    with pytest.raises(SubmodelError, match="CUT_REGION_UNSUPPORTED"):
        solid._entity_reaches(ctx, "solid", 1)


def test_faces_the_source_face_separated_planar_and_unresolved_surfaces(monkeypatch):
    geometry = Geometry(vertices={0: (0.0, 0.0, 0.0), 1: (0.1, 0.0, 0.0)})
    ctx = reach(geometry)
    assert solid._entity_reaches(ctx, "face", 7) is True  # the source face itself
    monkeypatch.setattr(solid, "_face_kind", lambda g, f: "other")
    with pytest.raises(SubmodelError, match="CUT_SURFACE_UNRESOLVED"):  # a boundary cannot bound a general surface
        solid._entity_reaches(ctx, "face", 9)
    monkeypatch.setattr(solid, "_face_kind", lambda g, f: "planar")
    bounds(monkeypatch, (5, 5, 5), (6, 6, 6), straight=True)
    assert solid._entity_reaches(ctx, "face", 9) is False  # separated by the extent of its boundary
    monkeypatch.setattr(solid, "_face_kind", lambda g, f: "cylinder")
    bounds(monkeypatch, (-1, -1, -1), (1, 1, 1), straight=True)  # overlapping extent, boundary edge misses the box
    with pytest.raises(SubmodelError, match="CUT_SURFACE_UNRESOLVED"):
        solid._entity_reaches(ctx, "face", 9)


# ----------------------------------------------------------------------
# the pressure direction and the skin tractions (ANYfem pressure is +p * n, n the signed element normal)
# ----------------------------------------------------------------------
NODES = {1: (0, 0, 0), 2: (1, 0, 0), 3: (2, 0, 0), 4: (0, 1, 0), 5: (1, 1, 0), 6: (2, 1, 0)}


class ShellMesh:
    def __init__(self, elements):
        self.nodes = {i: np.array(p, dtype=float) for i, p in NODES.items()}
        self.quads = dict(elements)
        self.elements_of_face = {5: list(elements)}

    def corners_of(self, element_id):
        return self.quads[element_id]


def built_source(elements, loads, fe_elements=None):
    return SimpleNamespace(mesh=ShellMesh(elements), fe_model=SimpleNamespace(mesh=SimpleNamespace(elements=fe_elements or {})),
                           load_case=SimpleNamespace(pressure_loads=loads))


def analysis_stub(pressure, normal=(0.0, 0.0, 1.0)):
    return SimpleNamespace(pressure=pressure, normal=np.asarray(normal, dtype=float))


UP = {10: [1, 2, 5, 4], 11: [2, 3, 6, 5]}  # counter-clockwise from +z: the pressure normal is +z


@needs_stack
@pytest.mark.parametrize("pressure", [2.0e3, -2.0e3])
def test_both_skins_carry_the_same_signed_traction(pressure):
    analysis = analysis_stub(pressure)
    direction = solid._source_pressure_direction(built_source(UP, {10: pressure, 11: pressure}), 5, analysis)
    assert np.allclose(direction, (0, 0, 1))
    start, end = (solid._skin_tractions(analysis, skin, direction)[skin] for skin in ("start", "end"))
    assert np.allclose(start, end) and np.allclose(start, (0.0, 0.0, pressure))
    assert np.linalg.norm(np.add(start, end)) == pytest.approx(2.0 * abs(pressure))  # a sign error would cancel here
    assert solid._skin_tractions(analysis, None, direction) is None


@needs_stack
def test_the_traction_follows_the_solved_element_normal_not_the_geometry_normal():
    flipped = {10: [4, 5, 2, 1], 11: [5, 6, 3, 2]}  # clockwise from +z: the element normal is -z
    analysis = analysis_stub(1.0e3, normal=(0.0, 0.0, 1.0))
    direction = solid._source_pressure_direction(built_source(flipped, {10: 1.0e3, 11: 1.0e3}), 5, analysis)
    assert np.allclose(direction, (0, 0, -1))
    for skin in ("start", "end"):
        assert np.allclose(solid._skin_tractions(analysis, skin, direction)[skin], (0.0, 0.0, -1.0e3))


@needs_stack
@pytest.mark.parametrize("case", ["mixed_normals", "wrong_pressure", "missing_pressure", "director_disagrees"])
def test_a_source_face_that_does_not_match_the_definition_refuses(case):
    elements, loads, fe = dict(UP), {10: 1.0e3, 11: 1.0e3}, None
    if case == "mixed_normals":
        elements[11] = [5, 6, 3, 2]
    elif case == "wrong_pressure":
        loads[11] = -1.0e3
    elif case == "missing_pressure":
        del loads[11]
    else:
        fe = {10: SimpleNamespace(reference_normal=(0.0, 0.0, 1.0), director_polarity=-1.0)}
    code = "SOLID_SOURCE_LOADING" if case in ("wrong_pressure", "missing_pressure") else "SOLID_SOURCE_DIRECTOR"
    with pytest.raises(SubmodelError, match=code):
        solid._source_pressure_direction(built_source(elements, loads, fe), 5, analysis_stub(1.0e3))


# ----------------------------------------------------------------------
# cut, stage, artifacts (need the solid stack)
# ----------------------------------------------------------------------
@pytest.fixture
def setup(tmp_path):
    return Setup(tmp_path)


def centre_face(setup):
    return setup.gfaces[1, 1]


def cut(setup, **kwargs):
    arguments = dict(source=SourceReference(setup.job.id, "default"), face_id=centre_face(setup), footprint=FOOTPRINT,
                     target_size=0.05, layers=2, order=1, pressure_skin="end")
    arguments.update(kwargs)
    return solid.cut_solid_submodel(setup.snapshot(), **arguments)


def stage(setup, definition, token=None):
    snap = submodel_snapshot(setup.session)
    snapshot = snap.snapshot
    parent = snapshot.thaw()
    source = job_source(parent, definition.source, setup.store, document=snapshot.document,
                        model_hash=snapshot.revision.model_hash)
    digest = solid_definition_hash(definition.to_dict())
    return solid.stage_solid_submodel_run(snap, parent, definition, source, bind_source(source.solution), digest,
                                          setup.store, cancellation_token=token)


def register(setup, definition):
    with setup.session.transaction("add solid submodel", solver_affecting=False):
        add_submodel(setup.project, definition)
    return definition


def project_state(project):
    return json.dumps(project_to_dict(project), sort_keys=True, default=str)


@needs_stack
def test_cut_returns_a_valid_definition_and_ignores_remote_entities(setup):
    result = cut(setup)
    definition = result.definition
    definition.validate()
    assert definition.to_dict()["child_kind"] == "solid" and definition.pressure_skin == "end"
    assert definition.reconstruction["pressure"]["value"] == pytest.approx(test_submodel_jobs.PRESSURE)
    assert definition.reconstruction["material"]["name"] == "S355"
    assert result.report.ignored_remote.get("supports", 0) > 0  # boundary supports are remote, not refused
    assert definition.id not in setup.project.submodels  # nothing registered


@needs_stack
def test_pressure_must_be_named_and_must_exist(setup):
    with pytest.raises(SubmodelError, match="CUT_PRESSURE_SKIN"):
        cut(setup, pressure_skin=None)
    setup.project.load_case("alt")
    with pytest.raises(SubmodelError, match="CUT_PRESSURE_SKIN"):
        cut(setup, source=SourceReference(setup.job.id, "alt"), pressure_skin="start")
    with pytest.raises(SubmodelError, match="CUT_PRESSURE_SKIN"):
        cut(setup, pressure_skin="middle")


@needs_stack
@pytest.mark.parametrize("footprint", [
    ((0.2, 0.4, 0.0), (0.6, 0.4, 0.0), (0.6, 0.6, 0.0), (0.2, 0.6, 0.0)),  # crosses the face rim
    ((0.4, 0.4, 0.1), (0.6, 0.4, 0.1), (0.6, 0.6, 0.1), (0.4, 0.6, 0.1)),  # off the reference plane
    ((0.4, 0.4, 0.0), (0.6, 0.4, 0.0), (0.6, 0.6, 0.0)),  # three points
])
def test_footprints_that_are_not_strictly_interior_refuse(setup, footprint):
    with pytest.raises((SubmodelError, ValueError)):
        cut(setup, footprint=footprint)


@needs_stack
def test_an_intersecting_unsupported_load_refuses(setup):
    case = setup.project.load_case()
    case.add_surface_traction(setup.project.face(centre_face(setup)), (0.0, 0.0, 1.0))
    with pytest.raises(SubmodelError, match="CUT_LOAD_UNSUPPORTED"):
        cut(setup)


@needs_stack
def test_a_remote_unsupported_load_is_not_refused(setup):
    case = setup.project.load_case()
    case.add_surface_traction(setup.project.face(setup.gfaces[0, 0]), (0.0, 0.0, 1.0))
    cut(setup)  # the traction is on another face and does not reach the footprint


@needs_stack
def test_staged_run_has_the_shared_run_schema_and_a_separate_mesh_codec(setup):
    definition = cut(setup).definition
    before = list(_files(setup.store))
    staged = stage(setup, definition)
    try:
        run = staged.run
        assert run.child["child_kind"] == "solid" and run.definition_id == definition.id
        assert run.definition_hash == solid_definition_hash(definition.to_dict()) == run.child["model_hash"]
        assert run.source["binding"]["field_fingerprint"] and run.source["revalidation"]["eta_scaled"] <= 1e-10
        assert run.id not in setup.project.submodel_runs and not staged.published
        assert {p.name for p in staged.files} <= set(_files(setup.store))
        mesh_ref, result_ref = staged.artifacts
        assert (mesh_ref.kind, result_ref.kind) == ("mesh", "result")
        _verify_child(setup.store, mesh_ref, "mesh", run, staged.document_id)  # the shared verifier reads the solid codec
        _verify_child(setup.store, result_ref, "result", run, staged.document_id)
        with pytest.raises(ArtifactError):
            setup.store.read_mesh(mesh_ref)  # the shell codec refuses the solid schema
        meta = read_solid_mesh_metadata(setup.store, mesh_ref)
        assert meta["artifact_id"] == f"{run.id}-mesh" and meta["mesh_hash"] == run.child["mesh_fingerprint"]
        assert "imported_model" not in meta and "embedded_source" not in meta  # shell-only fields are not invented
        diagnostics = run.diagnostics
        assert diagnostics["solver"]["certification"]["certified"] is True
        assert diagnostics["source_mesh"]["locations"] > 0 and diagnostics["source_mesh"]["element_kinds"]
    finally:
        discard_staged_run(staged)
    assert _files(setup.store) == before


@needs_stack
def test_solid_mesh_and_result_round_trip_on_a_reopened_store(setup, tmp_path):
    staged = stage(setup, cut(setup).definition)
    try:
        mesh_ref, result_ref = staged.artifacts
        memory = staged.result
        reopened = ArtifactStore(tmp_path / "parent.anyfem")  # a fresh store object on the same sidecars
        mesh = read_solid_mesh(reopened, mesh_ref)
        assert np.array_equal(mesh.coordinates, np.asarray(memory.mesh.coordinates))
        assert np.array_equal(mesh.cells, np.asarray(memory.mesh.cells))
        assert np.array_equal(mesh.cell_ids, np.asarray(memory.mesh.cell_ids))
        assert np.array_equal(mesh.boundary_facets, np.asarray(memory.mesh.boundary_facets))
        assert np.array_equal(mesh.boundary_ids, np.asarray(memory.mesh.boundary_ids))
        assert set(mesh.face_owners) == set(map(int, mesh.boundary_ids))
        assert len({model for model, _face in mesh.face_owners.values()}) == 1
        assert {"start", "end", "side_0", "side_1", "side_2", "side_3"} <= set(mesh.boundary_groups)
        assert mesh.boundary_ids is mesh["boundary_ids"]
        result = read_solid_result(reopened, result_ref)
        assert np.array_equal(result["coefficients"], memory.coefficients)
        assert np.array_equal(result["displacement"], memory.nodal_displacements)
        assert np.array_equal(result["recovery_weights"], np.asarray(memory.solved.recovery.weights))
        # true integration weights integrate to the box volume; points lie in the box
        assert result["recovery_weights"].sum() == pytest.approx(0.2 * 0.2 * 0.01, rel=1e-9)
        low, high = np.array([0.4, 0.4, -0.005]) - 1e-9, np.array([0.6, 0.6, 0.005]) + 1e-9
        points = result["recovery_coordinates"].reshape(-1, 3)
        assert np.all(points >= low) and np.all(points <= high)
        assert result["solid_fields"]["recovery_weights"]["location"] == "integration_point"
        assert result["identity"]["analysis_hash"] == staged.run.definition_hash
    finally:
        discard_staged_run(staged)


# ----------------------------------------------------------------------
# pressure: signed scalar on both named skins against the assembled parent
# ----------------------------------------------------------------------
def signed_project(sign):
    real = test_submodel_jobs.global_project

    def make():
        project, gfaces = real()
        for case in project.load_cases.values():
            case.pressures[:] = [replace(p, value=sign * p.value) for p in case.pressures]
        return project, gfaces

    return make


def assembled_parent(setup, face):
    """``(traction, first element normal)``: the parent's own assembled ``sum(p A n) / sum(A)`` on ``face``."""

    built = setup.solution.built
    mesh = built.mesh
    force, area, first = np.zeros(3), 0.0, None
    for element_id in mesh.elements_of_face[face]:
        corners = np.array([mesh.nodes[n] for n in mesh.corners_of(element_id)])
        cross = (np.cross(corners[2] - corners[0], corners[3] - corners[1]) if len(corners) == 4
                 else np.cross(corners[1] - corners[0], corners[2] - corners[0]))
        size = float(np.linalg.norm(cross))
        normal = cross / size
        first = normal if first is None else first
        force += built.load_case.pressure_loads[element_id] * 0.5 * size * normal
        area += 0.5 * size
    return force / area, first


@needs_stack
@pytest.mark.parametrize("sign", [1.0, -1.0])
def test_both_named_skins_carry_the_parent_traction_resultant_and_moment(tmp_path, monkeypatch, sign):
    monkeypatch.setattr(test_submodel_jobs, "global_project", signed_project(sign))
    setup = Setup(tmp_path)
    traction, normal = assembled_parent(setup, centre_face(setup))
    assert np.linalg.norm(traction) > 0.0 and sign * float(traction @ normal) > 0.0  # +p along the element normal
    area, centre = 0.2 * 0.2, np.array([0.5, 0.5, 0.0])
    force, moment, retained = {}, {}, {}
    for skin in ("start", "end"):
        staged = stage(setup, cut(setup, pressure_skin=skin).definition)
        try:
            info = staged.run.diagnostics["retained_pressure"]
            retained[skin] = np.asarray(info["traction"][skin])
            assert np.allclose(info["direction"], normal) and info["skin"] == skin
            force[skin] = np.asarray(staged.result.solved.skin_force)
            moment[skin] = np.asarray(staged.result.solved.skin_moment)
        finally:
            discard_staged_run(staged)
    scale = np.linalg.norm(traction) * area
    for skin in ("start", "end"):
        assert np.allclose(retained[skin], traction, rtol=1e-9, atol=1e-9 * np.linalg.norm(traction))
        assert np.allclose(force[skin], traction * area, rtol=1e-7, atol=1e-9 * scale)
        # the skins lie at +-t/2 along n, parallel to the force, so the moment about the origin is the same
        assert np.allclose(moment[skin], np.cross(centre, traction) * area, rtol=1e-7, atol=1e-9 * scale)
    assert np.allclose(force["start"], force["end"]) and np.allclose(moment["start"], moment["end"])
    assert np.linalg.norm(force["start"] + force["end"]) == pytest.approx(2.0 * scale, rel=1e-6)  # no cancellation


@needs_stack
def test_each_run_reverifies_the_source(setup):
    definition = cut(setup).definition
    setup.edit(lambda p: p.load_case().add_pressure(p.face(centre_face(setup)), 1.0), solver_affecting=True)
    with pytest.raises(SubmodelError):  # loading changed: the job source no longer certifies
        stage(setup, definition)


# ----------------------------------------------------------------------
# solver and result verification, cancellation, writer failures
# ----------------------------------------------------------------------
@needs_stack
def test_solver_refusals_and_cancellation_map_to_submodel_errors_and_leave_no_files(setup, monkeypatch):
    import anysolver.solid_submodel as solver

    definition = cut(setup).definition
    before = _files(setup.store)
    real = solver.solve_shell_driven_solid

    def refuses(*args, **kwargs):
        raise solver.SolidSubmodelError("LOCATE_MISS", "no element")

    monkeypatch.setattr(solver, "solve_shell_driven_solid", refuses)
    with pytest.raises(SubmodelError, match="SOLID_LOCATE_MISS"):
        stage(setup, definition)

    token = SimpleNamespace(cancelled=False)

    def cancels_inside_the_solver(*args, **kwargs):
        token.cancelled = True
        return real(*args, **kwargs)

    monkeypatch.setattr(solver, "solve_shell_driven_solid", cancels_inside_the_solver)
    with pytest.raises(SubmodelError, match="SUBMODEL_CANCELLED"):
        stage(setup, definition, token)
    assert _files(setup.store) == before


@needs_stack
@pytest.mark.parametrize("damage, code", [("flip_skin_load", "SOLID_RESULT_LOADS"),
                                          ("nan_coefficients", "SOLID_RESULT_COEFFICIENTS"),
                                          ("uncertified", "SOLID_RESULT_UNCERTIFIED")])
def test_a_solver_result_that_fails_verification_is_refused_before_anything_is_written(setup, monkeypatch, damage, code):
    import anysolver.solid_submodel as solver

    real = solver.solve_shell_driven_solid

    def damaged(*args, **kwargs):
        result = real(*args, **kwargs)
        if damage == "flip_skin_load":
            return replace(result, skin_force=-result.skin_force)
        if damage == "nan_coefficients":
            return replace(result, coefficients=np.full_like(result.coefficients, np.nan))
        return replace(result, diagnostics={**result.diagnostics, "certification": {"certified": False}})

    definition = cut(setup).definition
    before = _files(setup.store)
    monkeypatch.setattr(solver, "solve_shell_driven_solid", damaged)
    with pytest.raises(SubmodelError, match=code):
        stage(setup, definition)
    assert _files(setup.store) == before


@needs_stack
def test_cancellation_and_writer_failures_leave_no_files(setup, monkeypatch):
    from anysolver import CancellationToken

    definition = cut(setup).definition
    before = _files(setup.store)
    token = CancellationToken()
    token.cancel()
    with pytest.raises(SubmodelError, match="SUBMODEL_CANCELLED"):
        stage(setup, definition, token)
    assert _files(setup.store) == before

    import anyfem.io.solid_submodel_artifact as artifacts

    real = artifacts.write_solid_result

    def writes_then_fails(*args, **kwargs):
        real(*args, **kwargs)
        raise RuntimeError("descriptor failed")

    monkeypatch.setattr(artifacts, "write_solid_result", writes_then_fails)
    with pytest.raises(RuntimeError, match="descriptor failed"):
        stage(setup, definition)
    assert _files(setup.store) == before


# ----------------------------------------------------------------------
# malformed artifacts: typed errors, nothing half-written
# ----------------------------------------------------------------------
@needs_stack
def test_a_corrupted_or_inconsistent_solid_artifact_is_a_typed_error(setup):
    staged = stage(setup, cut(setup).definition)
    try:
        mesh_ref, result_ref = staged.artifacts
        memory = staged.result
        # an inconsistent writer input is refused and leaves no file
        owners_missing = SimpleNamespace(**{name: getattr(memory.mesh, name) for name in (
            "coordinates", "node_ids", "cells", "cell_ids", "boundary_facets", "boundary_ids", "node_groups",
            "cell_groups", "boundary_groups", "provenance", "units", "geometry_order")}, face_owners={})
        with pytest.raises(ArtifactError):
            write_solid_mesh(setup.store, owners_missing, mesh_id="x-mesh", document_id="d", model_hash="m",
                             mesh_hash="h")
        assert not setup.store.resolve("meshes/x-mesh.anymesh.h5").exists()
        short = SimpleNamespace(**{**vars(memory.solved), "nodal_displacements": np.zeros((1, 3))}) \
            if hasattr(memory.solved, "__dict__") else replace(memory.solved, nodal_displacements=np.zeros((1, 3)))
        with pytest.raises(ArtifactError):
            write_solid_result(setup.store, short, memory.prepared, memory.mesh, run_id="x", document_id="d",
                               mesh_id="x-mesh", model_hash="m", mesh_hash="h", analysis_hash="a")
        # a flipped byte fails the checksum in the reader and in the shared child verifier
        path = setup.store.resolve(mesh_ref.uri)
        data = bytearray(path.read_bytes())
        data[len(data) // 2] ^= 0xFF
        path.write_bytes(bytes(data))
        with pytest.raises(ArtifactError):
            read_solid_mesh_metadata(setup.store, mesh_ref)
        with pytest.raises(ArtifactError):
            read_solid_mesh(setup.store, mesh_ref)
        with pytest.raises(SubmodelError, match="SUBMODEL_ARTIFACT"):
            _verify_child(setup.store, mesh_ref, "mesh", staged.run, staged.document_id)
    finally:
        discard_staged_run(staged)


# ----------------------------------------------------------------------
# the normal route: run_submodel, save, read, format 20, reopen; the legacy shell route; cancellation through it
# ----------------------------------------------------------------------
@needs_stack
def test_run_submodel_save_read_and_reopen_a_solid_project_in_format_20(setup):
    definition = register(setup, cut(setup).definition)
    published = run_submodel(setup.session, definition.id, setup.store)
    run = published.run
    assert published.notification_error is None and run.id in setup.project.submodel_runs
    assert run.child["child_kind"] == "solid" and run.definition_hash == solid_definition_hash(definition.to_dict())
    assert run.source["revalidation"]["eta_scaled"] <= 1e-10
    assert {run.child["mesh_artifact"]["id"], run.child["result_artifact"]["id"]} <= set(setup.project.artifacts)
    assert submodel_run_staleness(setup.snapshot(), run, setup.store) == ()
    assert project_to_dict(setup.project)["anyfem"]["format"] == 20

    path = save_project(setup.project, setup.path)
    first = path.read_bytes()
    reopened = load_project(path)
    restored = reopened.submodels[definition.id]
    assert isinstance(restored, SolidSubmodelDefinition) and restored.to_dict() == definition.to_dict()
    assert reopened.submodel_runs[run.id].to_dict() == run.to_dict()
    assert reopened.submodels[setup.definition.id].to_dict() == setup.definition.to_dict()  # the shell entry survives
    save_project(reopened, path)
    assert path.read_bytes() == first  # byte-stable re-save

    store = ArtifactStore(path)
    session = DocumentSession(reopened)
    assert submodel_run_staleness(session.snapshot(), reopened.submodel_runs[run.id], store) == ()
    for key in ("mesh_artifact", "result_artifact"):
        assert store.verify(reopened.artifacts[run.child[key]["id"]])
    assert read_solid_mesh(store, reopened.artifacts[run.child["mesh_artifact"]["id"]]).cells.shape[1] == 4
    result = read_solid_result(store, reopened.artifacts[run.child["result_artifact"]["id"]])
    assert np.isfinite(result["recovery_stress"]).all()


@needs_stack
def test_a_shell_only_project_is_unchanged_by_solid_support(setup):
    run = setup.run()
    assert project_to_dict(setup.project)["anyfem"]["format"] == 12
    path = save_project(setup.project, setup.path)
    reopened = load_project(path)
    assert type(reopened.submodels[setup.definition.id]) is SubmodelDefinition
    assert reopened.submodel_runs[run.id].to_dict() == run.to_dict()
    assert "child_kind" not in reopened.submodel_runs[run.id].child  # the shell run manifest is as before


class CountdownToken:
    """Not cancelled for ``polls`` polls, then cancelled (every stage poll and every in-library poll counts)."""

    def __init__(self, polls):
        self.left = polls

    @property
    def cancelled(self):
        self.left -= 1
        return self.left < 0


@needs_stack
@pytest.mark.parametrize("polls", [0, 1, 2])
def test_cancelling_run_submodel_changes_neither_the_project_nor_the_store(setup, polls):
    definition = register(setup, cut(setup).definition)
    before_files, before_state = _files(setup.store), project_state(setup.project)
    with pytest.raises(SubmodelError, match="SUBMODEL_CANCELLED"):
        run_submodel(setup.session, definition.id, setup.store, cancellation_token=CountdownToken(polls))
    assert _files(setup.store) == before_files and project_state(setup.project) == before_state
    assert not setup.project.submodel_runs and definition.id in setup.project.submodels


# ----------------------------------------------------------------------
# the persisted cut interface: a small synthetic record, recomputed independently (no solver stack)
# ----------------------------------------------------------------------
def synthetic_interface():
    """4 locations x 3 components; group ``a`` owns locations 0-2, ``b`` owns 2-3, so location 2 is shared."""

    rng = np.random.default_rng(3)
    coords = np.repeat(rng.uniform(0.0, 1.0, (4, 3)), 3, axis=0)
    comps = np.tile(np.arange(3), 4)
    q, r = rng.normal(size=12), rng.normal(size=12)
    names = ["a", "b"]
    pairs = sorted((3 * loc + c, g) for g, locs in enumerate(([0, 1, 2], [2, 3])) for loc in locs for c in range(3))
    multiplicity = {d: sum(1 for p, _ in pairs if p == d) for d, _ in pairs}
    force, moment = np.zeros((2, 3)), np.zeros((2, 3))
    for d, g in pairs:
        v = np.zeros(3)
        v[comps[d]] = r[d] / multiplicity[d]  # the documented equal split
        force[g] += v
        moment[g] += np.cross(coords[d], v)
    arrays = {
        "prescribed_dofs": np.arange(12, dtype=np.int64), "prescribed_values": q.copy(), "reactions": r.copy(),
        "ownership_dofs": np.array([d for d, _ in pairs], dtype=np.int64),
        "ownership_groups": np.array([g for _, g in pairs], dtype=np.int64),
        "group_force": force, "group_moment": moment, "group_dof_count": np.array([9, 6], dtype=np.int64),
        "group_shared_dof_count": np.array([3, 3], dtype=np.int64), "interface_force": force.sum(axis=0),
        "interface_moment": moment.sum(axis=0), "skin_force": -force.sum(axis=0), "skin_moment": -moment.sum(axis=0),
        "moment_origin": np.zeros(3)}
    meta = {"version": 2, "group_names": names, "attribution_rule": artifact.ATTRIBUTION_RULE, "moment_frame": "world"}
    return arrays, meta, q, coords, comps


def interface_problems(arrays, meta, q, coords, comps):
    return artifact.interface_problems(arrays, meta, q, coords, comps)


def test_the_attribution_convention_is_the_solvers():
    pytest.importorskip("anysolver.solid_submodel")
    import anysolver.solid_submodel as solver

    assert artifact.ATTRIBUTION_RULE == solver.ATTRIBUTION_RULE


def test_a_sound_interface_record_has_no_problems():
    assert interface_problems(*synthetic_interface()) == []


def _set(key, fn):
    def damage(arrays, meta):
        arrays[key] = fn(arrays[key])
        return arrays, meta
    return damage


def _meta(key, value):
    def damage(arrays, meta):
        return arrays, {**meta, key: value}
    return damage


def _shift(array, delta):
    array = array.copy()
    array[0] += delta
    return array


def _put(index, value):
    def apply(array):
        array = array.copy()
        array[index] = value
        return array
    return apply


DAMAGE = {
    "repeated_dof": _set("prescribed_dofs", _put(1, 0)),
    "dof_out_of_range": _set("prescribed_dofs", _put(0, 99)),
    "negative_dof": _set("prescribed_dofs", _put(0, -1)),
    "imposed_value_differs": _set("prescribed_values", lambda a: _shift(a, 1.0)),
    "nan_reaction": _set("reactions", _put(0, np.nan)),
    "short_reactions": _set("reactions", lambda a: a[:-1]),
    "float_dofs": _set("prescribed_dofs", lambda a: a.astype(float)),
    "unowned_dof": _set("ownership_dofs", lambda a: a[:-1]),
    "duplicate_ownership_pair": _set("ownership_dofs", _put(1, 0)),
    "group_index_out_of_range": _set("ownership_groups", _put(0, 7)),
    "wrong_group_count": _set("group_dof_count", lambda a: _shift(a, 1)),
    "wrong_shared_count": _set("group_shared_dof_count", lambda a: _shift(a, 1)),
    "unequal_allocation": lambda arrays, meta: (
        {**arrays, "group_force": arrays["group_force"] + np.array([[0.1, 0, 0], [-0.1, 0, 0]])}, meta),
    "shared_dof_counted_in_full": lambda arrays, meta: (
        {**arrays, "group_force": arrays["group_force"] * np.array([[1.0], [1.5]])}, meta),
    "interface_resultant_flipped": _set("interface_force", lambda a: -a),
    "interface_moment_wrong": _set("interface_moment", lambda a: _shift(a, 1.0)),
    "skin_sign_flipped": _set("skin_force", lambda a: -a),
    "skin_moment_dropped": _set("skin_moment", lambda a: np.zeros(3)),
    "moment_origin_moved": _set("moment_origin", _put(0, 1.0)),
    "moment_frame": _meta("moment_frame", "body"),
    "attribution_rule": _meta("attribution_rule", "first_owner"),
    "duplicate_group_names": _meta("group_names", ["a", "a"]),
    "empty_group_name": _meta("group_names", ["a", ""]),
}


@pytest.mark.parametrize("label", sorted(DAMAGE))
def test_a_damaged_interface_record_is_refused(label):
    arrays, meta, q, coords, comps = synthetic_interface()
    arrays, meta = DAMAGE[label]({k: v.copy() for k, v in arrays.items()}, dict(meta))
    assert interface_problems(arrays, meta, q, coords, comps), label


def test_unique_virtual_work_counts_a_shared_dof_once_and_the_groups_add_up_to_it():
    arrays, meta, q, _coords, _comps = synthetic_interface()
    result = {"coefficients": q, "interface": {**arrays, "group_names": meta["group_names"]}}
    unique = float(np.sum(arrays["reactions"] * q))
    assert artifact.interface_virtual_work(result) == pytest.approx(unique, rel=1e-12)
    by_group = artifact.group_virtual_work(result)
    assert sum(by_group.values()) == pytest.approx(unique, rel=1e-12)
    naive = float(sum(arrays["reactions"][d] * q[d] for d in arrays["ownership_dofs"]))  # shared dofs twice
    assert naive != pytest.approx(unique, rel=1e-6)


# ----------------------------------------------------------------------
# the persisted interface of a real run, after reopen
# ----------------------------------------------------------------------
def region_for(setup, definition):
    """An independently re-derived region snapshot and tolerance: a legitimate but *distinct* owner model."""

    snap = setup.snapshot()
    analysis = solid._verify_source(getattr(snap, "snapshot", snap).thaw(), definition)
    return analysis.detail.solid_region_snapshot(analysis.region_id), float(analysis.reconstruction["boundary_tolerance"])


def stored_region(store, ref):
    """``(record, region, tolerance)`` of the exact detached source stored in a solid mesh artifact."""

    record = read_solid_mesh(store, ref)
    return record, record.source_geometry.solid_region_snapshot(record.region_id), record.boundary_tolerance


@needs_stack
@pytest.mark.parametrize("order", [1, 2])
def test_reopened_interface_matches_the_solved_values(setup, tmp_path, order):
    staged = stage(setup, cut(setup, order=order).definition)
    try:
        solved = staged.result.solved
        reopened = ArtifactStore(tmp_path / "parent.anyfem")
        result = read_solid_result(reopened, staged.artifacts[1])
        interface = result["interface"]
        assert np.array_equal(interface["prescribed_dofs"], solved.prescribed_dofs)
        assert np.array_equal(interface["prescribed_values"], solved.prescribed_values)
        assert np.array_equal(interface["reactions"], solved.reactions)
        assert np.array_equal(result["coefficients"][interface["prescribed_dofs"]], solved.prescribed_values)
        names = list(interface["group_names"])
        assert names == list(solved.group_resultants)
        for g, name in enumerate(names):
            group = solved.group_resultants[name]
            assert np.array_equal(interface["group_force"][g], group.force)
            assert np.array_equal(interface["group_moment"][g], group.moment)
            assert interface["group_dof_count"][g] == group.dof_count == int(np.sum(interface["ownership_groups"] == g))
            assert interface["group_shared_dof_count"][g] == group.shared_dof_count
        owners = {}
        for dof, g in zip(interface["ownership_dofs"], interface["ownership_groups"]):
            owners.setdefault(int(dof), []).append(names[int(g)])
        assert {d: tuple(v) for d, v in owners.items() if len(v) > 1} == dict(solved.shared_attribution) != {}
        assert interface["meta"]["attribution_rule"] == solved.attribution_rule
        # force and moment (world frame, about the origin) from the reopened reactions, against the solved ones
        coords = result["coefficient_coordinates"][interface["prescribed_dofs"]]
        unit = np.zeros((len(coords), 3))
        unit[np.arange(len(coords)), result["coefficient_components"][interface["prescribed_dofs"]]] = interface["reactions"]
        scale = float(np.abs(unit).sum())
        assert np.allclose(unit.sum(axis=0), solved.interface_force, rtol=0, atol=1e-9 * scale)
        assert np.allclose(np.cross(coords, unit).sum(axis=0), solved.interface_moment, rtol=0, atol=1e-9 * scale)
        assert np.array_equal(result["interface_force"], solved.interface_force)
        # unique virtual work from the reopened record equals the solver's
        expected = float(np.dot(solved.reactions, solved.coefficients[solved.prescribed_dofs]))
        assert artifact.interface_virtual_work(result) == pytest.approx(expected, rel=1e-12)
        assert sum(artifact.group_virtual_work(result).values()) == pytest.approx(expected, rel=1e-9)
        # the recorded skin resultant is the signed traction over the footprint, and balances the interface
        info = staged.run.diagnostics["retained_pressure"]
        traction = np.asarray(info["traction"]["end"])
        area = 0.2 * 0.2
        assert np.allclose(interface["skin_force"], traction * area, rtol=1e-7, atol=1e-9 * np.linalg.norm(traction))
        assert np.allclose(interface["skin_moment"], np.cross([0.5, 0.5, 0.0], traction) * area, rtol=1e-7,
                           atol=1e-9 * np.linalg.norm(traction))
        assert np.allclose(interface["skin_force"] + result["interface_force"], 0.0, atol=1e-6 * scale)
        assert np.all(interface["moment_origin"] == 0.0) and interface["meta"]["moment_frame"] == "world"
        if order == 2:  # P2 edge midpoints are cut DOFs too, shared between adjacent sides
            midpoints = [d for d, v in owners.items() if len(v) == 2 and set(v) <= {"side_0", "side_1", "side_2", "side_3"}]
            assert midpoints and all(len(owners[d]) == 2 for d in midpoints)
            vertices = {tuple(p) for p in np.asarray(staged.result.mesh.coordinates, dtype=float)}
            assert any(tuple(c) not in vertices for c in coords)  # a cut DOF that is not a mesh vertex: an edge midpoint
    finally:
        discard_staged_run(staged)


@needs_stack
def test_a_solver_result_with_a_damaged_interface_is_refused_before_anything_is_written(setup):
    staged = stage(setup, cut(setup).definition)
    try:
        memory = staged.result
        solved = memory.solved
        before = _files(setup.store)
        first = next(iter(solved.group_resultants))
        group = solved.group_resultants[first]
        cases = {
            "reaction_sign": replace(solved, reactions=_put(0, -solved.reactions[0] - 1.0)(solved.reactions)),
            "imposed_value": replace(solved, prescribed_values=_shift(solved.prescribed_values, 1.0)),
            "repeated_dof": replace(solved, prescribed_dofs=_put(1, int(solved.prescribed_dofs[0]))(solved.prescribed_dofs)),
            "group_allocation": replace(solved, group_resultants={
                **solved.group_resultants, first: replace(group, force=group.force + 1.0)}),
            "shared_attribution": replace(solved, shared_attribution={}),
            "skin_sign": replace(solved, skin_moment=-solved.skin_moment - 1.0),
        }
        for label, damaged in cases.items():
            with pytest.raises(ArtifactError):
                write_solid_result(setup.store, damaged, memory.prepared, memory.mesh, run_id=f"bad-{label}",
                                   document_id="d", mesh_id="m", model_hash="h", mesh_hash="h", analysis_hash="a")
        assert _files(setup.store) == before
    finally:
        discard_staged_run(staged)


@needs_stack
def test_the_previous_result_variant_without_an_interface_still_writes_and_reads(setup, tmp_path):
    staged = stage(setup, cut(setup).definition)
    try:
        memory = staged.result
        stripped = SimpleNamespace(**{name: getattr(memory.solved, name) for name in (
            "coefficients", "nodal_displacements", "recovery", "interface_force", "interface_moment")})
        with pytest.raises(ArtifactError):  # the default refuses a result that lacks the record
            write_solid_result(setup.store, stripped, memory.prepared, memory.mesh, run_id="old-0", document_id="d",
                               mesh_id="m", model_hash="h", mesh_hash="h", analysis_hash="a")
        stripped.skin_force = stripped.skin_moment = np.zeros(3)
        ref = write_solid_result(setup.store, stripped, memory.prepared, memory.mesh, run_id="old-1", document_id="d",
                                 mesh_id="m", model_hash="h", mesh_hash="h", analysis_hash="a", require_interface=False)
        old = read_solid_result(ArtifactStore(tmp_path / "parent.anyfem"), ref)
        assert old["interface"] is None and np.array_equal(old["coefficients"], memory.coefficients)
        with pytest.raises(ArtifactError):
            artifact.interface_virtual_work(old)
    finally:
        discard_staged_run(staged)


# ----------------------------------------------------------------------
# owner handles and topology of the stored mesh
# ----------------------------------------------------------------------
def mesh_arrays(record, **changes):
    values = dict(x=record.coordinates, node_ids=record.node_ids, cells=record.cells, cell_ids=record.cell_ids,
                  facets=record.boundary_facets, boundary_ids=record.boundary_ids,
                  owner_faces=np.array([record.face_owners[int(b)][1] for b in record.boundary_ids]))
    values.update(changes)
    return values


def mesh_problems(**values):
    return artifact._mesh_problems(values["x"], values["node_ids"], values["cells"], values["cell_ids"],
                                   values["facets"], values["boundary_ids"], values["owner_faces"], "m", 1)


@needs_stack
def test_a_reopened_mesh_passes_the_full_checks_with_and_without_its_region(setup, tmp_path):
    definition = cut(setup).definition
    staged = stage(setup, definition)
    try:
        reopened = ArtifactStore(tmp_path / "parent.anyfem")
        record, region, tolerance = stored_region(reopened, staged.artifacts[0])
        assert mesh_problems(**mesh_arrays(record)) == []
        assert record.region_id and tolerance > 0.0
        assert str(record.source_geometry.model_id) == str(region.model_id)
        assert np.asarray(region.coordinates).shape == (8, 3) and len(region.face_refs) == 6
        assert read_solid_mesh_metadata(reopened, staged.artifacts[0])["bound"] is True
        again = read_solid_mesh(reopened, staged.artifacts[0], region=region, boundary_tolerance=tolerance)
        assert dict(again.face_owners) == dict(record.face_owners)
        with pytest.raises(ArtifactError):  # region and tolerance travel together
            read_solid_mesh(reopened, staged.artifacts[0], region=region)
        # a model re-derived from the parent is a legitimate but distinct owner: its fresh id differs and the
        # staged owners are rightly not its faces
        derived, derived_tolerance = region_for(setup, definition)
        assert str(derived.model_id) != str(region.model_id)
        with pytest.raises(ArtifactError):
            read_solid_mesh(reopened, staged.artifacts[0], region=derived, boundary_tolerance=derived_tolerance)
        handles = {name: {record.face_owners[int(b)] for b in ids} for name, ids in record.boundary_groups.items()
                   if name in artifact.BOX_GROUPS}
        assert all(len(v) == 1 for v in handles.values()) and len({next(iter(v)) for v in handles.values()}) == 6
        assert {pair[0] for v in handles.values() for pair in v} == {str(region.model_id)}
    finally:
        discard_staged_run(staged)


@needs_stack
@pytest.mark.parametrize("mode", ["groups_and_handles", "groups_only", "handles_only", "foreign_model"])
def test_swapped_or_foreign_owners_are_refused_against_the_region(setup, mode):
    from anygeometry.identity import EntityHandle

    staged = stage(setup, cut(setup).definition)
    try:
        _record, region, tolerance = stored_region(setup.store, staged.artifacts[0])
        memory = staged.result.mesh
        owners = {int(b): h for b, h in memory.face_owners.items()}
        groups = {k: [int(b) for b in v] for k, v in memory.boundary_groups.items()}
        start, end = groups["start"], groups["end"]
        start_handle, end_handle = owners[start[0]], owners[end[0]]
        if mode in ("groups_and_handles", "handles_only"):
            owners.update({b: end_handle for b in start})
            owners.update({b: start_handle for b in end})
        if mode in ("groups_and_handles", "groups_only"):
            groups["start"], groups["end"] = end, start
        if mode == "foreign_model":
            foreign = str(__import__("uuid").uuid4())
            owners = {b: EntityHandle(foreign, "face", h.id) for b, h in owners.items()}
        damaged = SimpleNamespace(**{n: getattr(memory, n) for n in (
            "coordinates", "cells", "boundary_facets", "boundary_ids")}, face_owners=owners, boundary_groups=groups)
        check = artifact.check_solid_mesh_against_region
        check(memory, region, boundary_tolerance=tolerance)  # the genuine mesh is accepted
        with pytest.raises(ArtifactError):
            check(damaged, region, boundary_tolerance=tolerance)
    finally:
        discard_staged_run(staged)


@needs_stack
@pytest.mark.parametrize("mode", ["groups_and_handles", "groups_only", "handles_only", "foreign_model"])
def test_staging_refuses_a_swapped_mesh_before_any_load_is_applied(setup, monkeypatch, mode):
    import anymesher.structured_box as box
    import anysolver.solid_submodel as solver
    from anygeometry.identity import EntityHandle

    real = box.generate_structured_box_mesh

    def swapped(*args, **kwargs):
        mesh = real(*args, **kwargs)
        owners = {int(b): h for b, h in mesh.face_owners.items()}
        groups = {k: [int(b) for b in v] for k, v in mesh.boundary_groups.items()}
        start, end = groups["start"], groups["end"]
        start_handle, end_handle = owners[start[0]], owners[end[0]]
        if mode in ("groups_and_handles", "handles_only"):
            owners.update({b: end_handle for b in start})
            owners.update({b: start_handle for b in end})
        if mode in ("groups_and_handles", "groups_only"):
            groups["start"], groups["end"] = end, start
        if mode == "foreign_model":
            owners = {b: EntityHandle(str(__import__("uuid").uuid4()), "face", h.id) for b, h in owners.items()}
        fields = {n: getattr(mesh, n) for n in ("coordinates", "node_ids", "cells", "cell_ids", "boundary_facets",
                                                "boundary_ids", "node_groups", "cell_groups", "provenance", "units",
                                                "geometry_order")}
        return SimpleNamespace(**fields, face_owners=owners, boundary_groups=groups)

    def never(*args, **kwargs):
        raise AssertionError("a load was applied to a mesh with wrong ownership")

    definition = cut(setup).definition
    before = _files(setup.store)
    monkeypatch.setattr(box, "generate_structured_box_mesh", swapped)
    monkeypatch.setattr(solver, "solve_shell_driven_solid", never)
    with pytest.raises(SubmodelError, match="SOLID_MESH_OWNERSHIP"):
        stage(setup, definition)
    assert _files(setup.store) == before


@needs_stack
def test_stored_topology_and_owner_faults_are_refused_on_reopen(setup, tmp_path):
    staged = stage(setup, cut(setup).definition)
    try:
        record = read_solid_mesh(ArtifactStore(tmp_path / "parent.anyfem"), staged.artifacts[0])
        base = mesh_arrays(record)
        assert mesh_problems(**base) == []
        facets, ids, owners = base["facets"], base["boundary_ids"], base["owner_faces"]
        groups = record.boundary_groups
        faults = {
            "missing_facet": dict(facets=facets[1:], boundary_ids=ids[1:], owner_faces=owners[1:]),
            "extra_facet": dict(facets=np.vstack([facets, facets[:1, ::-1]]), boundary_ids=np.append(ids, ids.max() + 1),
                                owner_faces=np.append(owners, owners[0])),
            "duplicate_facet": dict(facets=np.vstack([facets, facets[:1]]), boundary_ids=np.append(ids, ids.max() + 1),
                                    owner_faces=np.append(owners, owners[0])),
            "bad_winding": dict(facets=np.vstack([facets[:1, ::-1], facets[1:]])),
            "all_winding_reversed": dict(facets=facets[:, ::-1].copy()),
            "one_owner_for_two_planes": dict(owner_faces=np.full_like(owners, owners[0])),
            "negative_owner": dict(owner_faces=_put(0, -1)(owners)),
            "one_plane_split_over_two_faces": dict(owner_faces=np.where(
                np.isin(ids, groups["start"][:1]), owners.max() + 1, owners)),
        }
        for label, change in faults.items():
            assert mesh_problems(**{**base, **change}), label
        nonsense = np.array(facets)
        nonsense[0] = (nonsense[0, 0], nonsense[0, 0], nonsense[0, 1])  # a repeated vertex
        assert mesh_problems(**{**base, "facets": nonsense})
    finally:
        discard_staged_run(staged)


def writer_inputs(memory):
    return {name: getattr(memory.mesh, name) for name in (
        "coordinates", "node_ids", "cells", "cell_ids", "boundary_facets", "boundary_ids", "node_groups",
        "cell_groups", "boundary_groups", "provenance", "units", "geometry_order", "face_owners")}


@needs_stack
def test_a_stored_mesh_is_revalidated_against_its_own_source_after_the_parent_changes(setup, tmp_path):
    staged = stage(setup, cut(setup).definition)
    try:
        setup.edit(lambda p: p.load_case().add_pressure(p.face(centre_face(setup)), 1.0), solver_affecting=True)
        reopened = ArtifactStore(tmp_path / "parent.anyfem")
        record = read_solid_mesh(reopened, staged.artifacts[0])  # no region: the stored source decides
        assert record.source_geometry is not None
        assert {owner[0] for owner in record.face_owners.values()} == {str(record.source_geometry.model_id)}
        assert read_solid_mesh_metadata(reopened, staged.artifacts[0])["region_id"] == record.region_id
    finally:
        discard_staged_run(staged)


@needs_stack
@pytest.mark.parametrize("mode", ["groups_only", "handles_only", "groups_and_handles", "foreign_model"])
def test_a_genuinely_swapped_mesh_is_refused_by_the_writer_and_by_the_historical_reader(setup, monkeypatch, mode):
    from anygeometry.identity import EntityHandle

    staged = stage(setup, cut(setup).definition)
    try:
        record, _region, tolerance = stored_region(setup.store, staged.artifacts[0])
        memory = staged.result
        inputs = writer_inputs(memory)
        owners = {int(b): h for b, h in inputs["face_owners"].items()}
        groups = {k: [int(b) for b in v] for k, v in inputs["boundary_groups"].items()}
        start, end = groups["start"], groups["end"]
        start_handle, end_handle = owners[start[0]], owners[end[0]]
        if mode in ("groups_and_handles", "handles_only"):
            owners.update({b: end_handle for b in start})
            owners.update({b: start_handle for b in end})
        if mode in ("groups_and_handles", "groups_only"):
            groups["start"], groups["end"] = end, start
        if mode == "foreign_model":
            foreign = str(__import__("uuid").uuid4())
            owners = {b: EntityHandle(foreign, "face", h.id) for b, h in owners.items()}
        swapped = SimpleNamespace(**{**inputs, "face_owners": owners, "boundary_groups": groups})
        arguments = dict(mesh_id="swap-mesh", document_id="d", model_hash="m", mesh_hash="h",
                         detail=record.source_geometry, region_id=record.region_id, boundary_tolerance=tolerance)
        with pytest.raises(ArtifactError):
            write_solid_mesh(setup.store, swapped, **arguments)
        assert not setup.store.resolve("meshes/swap-mesh.anymesh.h5").exists()
        # write it anyway (the writer's source check disabled) and read it back as a historical caller
        with monkeypatch.context() as patch:
            patch.setattr(artifact, "_check_against_source", lambda *a, **k: None)
            ref = write_solid_mesh(setup.store, swapped, **arguments)
        with pytest.raises(ArtifactError):
            read_solid_mesh(setup.store, ref)
    finally:
        discard_staged_run(staged)


@needs_stack
def test_unbound_version_1_meshes_still_write_and_read_and_partial_source_is_refused(setup, tmp_path):
    staged = stage(setup, cut(setup).definition)
    try:
        record, _region, tolerance = stored_region(setup.store, staged.artifacts[0])
        inputs = writer_inputs(staged.result)
        old = write_solid_mesh(setup.store, SimpleNamespace(**inputs), mesh_id="v1-mesh", document_id="d",
                               model_hash="m", mesh_hash="h")
        reopened = ArtifactStore(tmp_path / "parent.anyfem")
        unbound = read_solid_mesh(reopened, old)
        assert unbound.source_geometry is None and unbound.region_id is None and unbound.boundary_tolerance is None
        meta = read_solid_mesh_metadata(reopened, old)
        assert meta["version"] == 1 and meta["bound"] is False and meta["region_id"] is None
        assert read_solid_mesh_metadata(reopened, staged.artifacts[0])["version"] == 2
        for partial in ({"detail": record.source_geometry}, {"region_id": record.region_id},
                        {"boundary_tolerance": tolerance},
                        {"detail": record.source_geometry, "region_id": record.region_id}):
            with pytest.raises(ArtifactError):
                write_solid_mesh(setup.store, SimpleNamespace(**inputs), mesh_id="p-mesh", document_id="d",
                                 model_hash="m", mesh_hash="h", **partial)
        assert not setup.store.resolve("meshes/p-mesh.anymesh.h5").exists()
    finally:
        discard_staged_run(staged)


@needs_stack
def test_malformed_source_metadata_is_a_typed_error(setup):
    staged = stage(setup, cut(setup).definition)
    try:
        record, _region, tolerance = stored_region(setup.store, staged.artifacts[0])
        text, region_id, _ = artifact._source_text(record.source_geometry, record.region_id, tolerance)
        model, region, found = artifact._load_source(text, region_id, tolerance)  # the genuine metadata loads
        assert found == tolerance and str(model.model_id) == str(record.source_geometry.model_id)
        document = json.loads(text)
        tampered = json.dumps({**document, "revision": int(document["revision"]) + 1}, sort_keys=True)
        cases = {
            "unknown_region": (text, str(__import__("uuid").uuid4()), tolerance),
            "empty_region": (text, "", tolerance),
            "numeric_region": (text, 7, tolerance),
            "zero_tolerance": (text, region_id, 0.0),
            "negative_tolerance": (text, region_id, -1e-9),
            "nan_tolerance": (text, region_id, float("nan")),
            "text_tolerance": (text, region_id, "tight"),
            "bool_tolerance": (text, region_id, True),
            "not_text": (None, region_id, tolerance),
            "truncated_json": (text[: len(text) // 2], region_id, tolerance),
            "not_a_document": ("[]", region_id, tolerance),
            "checksum_mismatch": (tampered, region_id, tolerance),
        }
        for label, arguments in cases.items():
            with pytest.raises(ArtifactError):
                artifact._load_source(*arguments)
            assert label
        with pytest.raises(ArtifactError):  # owners that name another model than the stored source
            artifact._check_against_source({"face_owners": {1: ("not-the-source-model", 1)}}, model, region, tolerance)
        bad = dict(boundary_tolerance=float("inf"), detail=record.source_geometry, region_id=record.region_id)
        with pytest.raises(ArtifactError):
            write_solid_mesh(setup.store, staged.result.mesh, mesh_id="bad-mesh", document_id="d", model_hash="m",
                             mesh_hash="h", **bad)
        assert not setup.store.resolve("meshes/bad-mesh.anymesh.h5").exists()
    finally:
        discard_staged_run(staged)


@needs_stack
def test_read_solid_result_closes_the_dataset_it_opened(setup, monkeypatch):
    staged = stage(setup, cut(setup).definition)
    try:
        closed = []
        real = setup.store.open_result

        class Tracked:
            def __init__(self, inner):
                self.inner = inner
                self.identity = inner.identity

            def close(self):
                closed.append(True)

        monkeypatch.setattr(setup.store, "open_result", lambda ref: Tracked(real(ref)))
        read_solid_result(setup.store, staged.artifacts[1])
        assert closed == [True]
        path = setup.store.resolve(staged.artifacts[1].uri)
        data = bytearray(path.read_bytes())
        data[len(data) // 2] ^= 0xFF
        path.write_bytes(bytes(data))
        with pytest.raises(ArtifactError):
            read_solid_result(setup.store, staged.artifacts[1])
    finally:
        discard_staged_run(staged)


# ----------------------------------------------------------------------
# curved edges on a real GeometryModel (the stub tests above substitute the owner's bounds)
# ----------------------------------------------------------------------
def real_edge(kind, points):
    """``(model, edge id)`` of one curved edge on a real ``GeometryModel``.

    ``arc`` takes ``[start, via, end]`` (``add_arc(start, via, end)``); ``bezier`` takes
    ``[start, control, control, end]`` (a cubic Bezier through ``add_spline(start, controls, end)``).
    """

    from anygeometry import GeometryModel

    model = GeometryModel()
    ids = [model.add_point(*p) for p in points]
    if kind == "arc":
        edge = model.add_arc(ids[0], ids[1], ids[2])
    else:
        edge = model.add_spline(ids[0], ids[1:3], ids[3])
    return model, edge


def classify(model, edge):
    ctx = reach(model)
    try:
        return "reaches" if solid._entity_reaches(ctx, "edge", edge) else "separated"
    except SubmodelError as error:
        assert "CUT_CURVE_UNRESOLVED" in str(error), error
        return "unresolved"


def circle_points(centre, radius, degrees):
    return [(centre[0] + radius * np.cos(np.radians(a)), centre[1] + radius * np.sin(np.radians(a)), 0.0)
            for a in degrees]


@needs_stack
@pytest.mark.parametrize("kind, points, expected", [
    ("arc", [(0.3, 0.5, 0.0), (0.5, 0.58, 0.0), (0.7, 0.5, 0.0)], {"reaches"}),  # crosses the box
    ("arc", circle_points((0.8, 0.5), 0.2, (150, 180, 210)), {"reaches"}),  # grazes the side x = 0.6 at its mid-point
    ("arc", circle_points((0.3, 0.3), 0.45, (20, 45, 70)), {"unresolved", "separated"}),  # bounds overlap, curve misses
    ("arc", [(0.8, 0.8, 0.0), (0.9, 0.85, 0.0), (1.0, 0.8, 0.0)], {"separated"}),
    ("bezier", [(0.3, 0.5, 0.0), (0.4, 0.7, 0.0), (0.6, 0.3, 0.0), (0.7, 0.5, 0.0)], {"reaches"}),
    ("bezier", [(0.3, 0.7, 0.0), (0.4, 0.58, 0.0), (0.6, 0.58, 0.0), (0.7, 0.7, 0.0)], {"unresolved", "separated"}),
    ("bezier", [(0.8, 0.8, 0.0), (0.85, 0.9, 0.0), (0.95, 0.9, 0.0), (1.0, 0.8, 0.0)], {"separated"}),
])
def test_real_curved_edges_are_decided_never_silently_dropped(kind, points, expected):
    model, edge = real_edge(kind, points)
    outcome = classify(model, edge)
    assert outcome in expected, (kind, outcome)
    if "reaches" not in expected:
        assert outcome != "reaches"  # a curve that misses the box is never reported as reaching it
