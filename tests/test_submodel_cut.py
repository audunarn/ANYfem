"""Submodels cut from a parent project (workflow contract sections 4-5; slices S2a and S4a)."""

from __future__ import annotations

import numpy as np
import pytest

from anygeometry.volume_cut import BoxVolume
from anyfem.document import DocumentSession
from anyfem.io.artifacts import ArtifactStore
from anyfem.model import pinned, support
from anyfem.model.records import JobStatus
from anyfem.model.submodels import SourceReference
from anyfem.submodel import SubmodelError
from anyfem.submodel_cut import cut_submodel
from anyfem.submodel_jobs import (
    add_submodel,
    discard_staged_run,
    publish_submodel_run,
    stage_submodel_run,
    submodel_run_staleness,
    submodel_snapshot,
)
from test_submodel_jobs import make_job
from test_submodel_workflow import PRESSURE, SIZE, global_project, node_match

BOX = BoxVolume((0.2, 0.2, -0.1), (0.8, 0.8, 0.1))


class Parent:
    def __init__(self, tmp_path, project=None, gfaces=None, mesh=None):
        if project is None:
            project, gfaces = global_project()
        self.project, self.gfaces = project, gfaces
        self.session = DocumentSession(project)
        self.store = ArtifactStore(tmp_path / "parent.anyfem")
        self.job, self.record, self.mesh, self.solution = make_job(self.session, self.store, mesh=mesh)
        self.source = SourceReference(self.job.id, "default")

    def cut(self, volume=BOX, **kwargs):
        options = dict(source=self.source, policy="drive_all", target_size=SIZE)
        options.update(kwargs)
        return cut_submodel(self.session.snapshot(), volume, **options)

    def register(self, definition):
        with self.session.transaction("add submodel", solver_affecting=False):
            add_submodel(self.project, definition)


@pytest.fixture
def parent(tmp_path):
    return Parent(tmp_path)


# ----------------------------------------------------------------------
# end to end
# ----------------------------------------------------------------------
def test_box_cut_runs_end_to_end_and_reproduces_the_parent(parent):
    cut = parent.cut()
    definition, report = cut.definition, cut.report
    assert set(definition.cut_faces) == {"x0", "x1", "y0", "y1"}
    assert report.original_boundary_edges == 0 and not report.skipped_cases
    child = definition.child
    assert len(child.geometry.faces) == 9 and set(definition.source_faces) == set(child.geometry.faces)
    assert report.carried["pressures"] == 9 and report.carried["plate_assignments"] == 9
    assert report.dropped_outside["line_loads"] == 6 and report.dropped_outside["supports"] > 0

    parent.register(definition)
    staged = stage_submodel_run(submodel_snapshot(parent.session), definition.id, parent.store)
    try:
        result = staged.result
        mesh = result.solution.built.mesh
        match = node_match(mesh, parent.mesh)
        driven = set(result.transfer)
        errors = np.array([np.abs(result.solution.node_displacement(n) - parent.solution.node_displacement(match[n]))
                           for n in set(mesh.nodes) - driven])
        worst = errors.max(axis=0)
        assert np.max(worst[:3]) < 1e-12 and np.max(worst[3:]) < 1e-10
        body = result.diagnostics["free_body"]
        assert body["force_ratio"] < 1e-12 and body["moment_ratio"] < 1e-12
        assert abs(result.diagnostics["whole_cut_resultant"][2] + PRESSURE * 0.6 * 0.6) < 1e-3 * PRESSURE * 0.36
        published = publish_submodel_run(parent.session, staged)
    except BaseException:
        discard_staged_run(staged)
        raise
    assert published.notification_error is None
    assert submodel_run_staleness(parent.session.snapshot(), published.run, parent.store) == ()


def test_the_parent_is_not_modified(parent):
    from anyfem.io.project_file import project_to_dict

    before = project_to_dict(parent.project)
    revision = parent.session.revision.id
    parent.cut()
    assert project_to_dict(parent.project) == before and parent.session.revision.id == revision


def test_a_box_on_existing_edges_cuts_the_centre_face(parent):
    cut = parent.cut(BoxVolume((0.3, 0.3, -0.1), (0.7, 0.7, 0.1)))
    child = cut.definition.child
    assert len(child.geometry.faces) == 1
    assert set(cut.definition.cut_faces) == {"x0", "x1", "y0", "y1"}
    assert cut.definition.source_faces == {next(iter(child.geometry.faces)): (parent.gfaces[1, 1],)}


def test_a_box_inside_one_face_and_attributes_carry(parent):
    cut = parent.cut(BoxVolume((0.4, 0.4, -0.1), (0.6, 0.6, 0.1)))
    child = cut.definition.child
    (face,) = child.geometry.faces
    assert child.face_sections[face] == parent.project.face_sections[parent.gfaces[1, 1]]
    assert [p.value for p in child.load_cases["default"].pressures] == [PRESSURE]
    assert child.materials.keys() == parent.project.materials.keys()
    assert cut.definition.volume["lower"] == [0.4, 0.4, -0.1]
    assert child.target_size == SIZE and cut.definition.policy == "drive_all"


def assembled_totals(child, case):
    """Sum of the assembled nodal force and moment of ``case`` on the child's own mesh."""

    from anyfem.solve.build import build_fe_model
    from anysolver.matrix_assembly import assemble_load_vector

    mesh = child.generate_mesh(SIZE)
    built = build_fe_model(child, mesh, case, require_loads=False, require_supports=False)
    load, _info = assemble_load_vector(built.fe_model, built.load_case)
    six = np.asarray(load, dtype=float).reshape(-1, 6)
    return {"force": six[:, :3].sum(axis=0), "moment": six[:, 3:].sum(axis=0)}


def child_mass_total(child):
    from anyfem.solve.build import build_fe_model

    built = build_fe_model(child, child.generate_mesh(SIZE), "default", require_loads=False, require_supports=False)
    return float(sum(built.fe_model.mesh.point_masses.values()))


def vertex_at(project, x, y):
    return next(v for v in project.geometry.vertices
                if np.allclose(project.geometry.vertex_position(v), (x, y, 0.0)))


def point_group(project, name, vertices):
    """A multi-point selection (a region), the target of per_target / total_distributed point loads."""

    project.geometry.add_to_group(name, [project.geometry.entity_ref("vertex", v) for v in vertices])
    return project._geometry_group_region(name, "vertex")


def test_supports_loads_and_masses_inside_the_box_are_carried(tmp_path):
    from anyfem.model.attributes import Mass

    project, gfaces = global_project()
    project.add_support(pinned(project.face(gfaces[1, 1])))  # a whole-face restraint inside the box
    project.add_mass(Mass(project.face(gfaces[1, 1]), 5.0, "block", distribution_policy="total_distributed"))
    case = project.load_case("extra")
    first, second = vertex_at(project, 0.3, 0.3), vertex_at(project, 0.7, 0.7)
    case.add_point_load(project.point(first), force=(0, 0, -10.0), distribution_policy="per_target",
                        region=point_group(project, "pair", [first, second]))
    edge = project.geometry.faces[gfaces[1, 1]].loop[0].edge
    case.add_line_load(project.edge(edge), (0.0, 0.0, -50.0))
    parent = Parent(tmp_path, project, gfaces)
    cut = parent.cut()  # the box [0.2, 0.8]^2 holds the whole centre face and the pair of points
    child = cut.definition.child
    assert len(child.supports) == 1 and len(child.masses) == 1 and child.masses[0].value == 5.0
    extra = child.load_cases["extra"]
    assert len(extra.point_loads) == 1 and len(extra.line_loads) == 1  # one record, one region of two points
    assert cut.report.skipped_cases == {}
    # per_target: -10 at each of two points, plus the line load -50 N/m over the 0.4 m edge
    assert assembled_totals(child, "extra")["force"][2] == pytest.approx(-20.0 - 20.0)
    assert child_mass_total(child) == pytest.approx(5.0)


# ----------------------------------------------------------------------
# refusals and skipped cases
# ----------------------------------------------------------------------
@pytest.mark.parametrize("change, code", [
    (lambda p: setattr(p, "name", p.name), None),  # control: nothing refuses
])
def test_control(parent, change, code):
    assert parent.cut().definition.id


def test_unknown_job_case_and_volume_refuse(parent):
    with pytest.raises(SubmodelError, match="CUT_SOURCE_JOB_MISSING"):
        parent.cut(source=SourceReference("no-such-job", "default"))
    with pytest.raises(SubmodelError, match="CUT_CASE_MISSING"):
        parent.cut(source=SourceReference(parent.job.id, "nope"))
    with pytest.raises(SubmodelError, match="VOLUME_EMPTY"):
        parent.cut(BoxVolume((5, 5, 5), (6, 6, 6)))
    with pytest.raises(SubmodelError, match="CUT_NO_CUT_EDGES|VOLUME_"):
        parent.cut(BoxVolume((-1, -1, -1), (2, 2, 1)))
    with pytest.raises(SubmodelError, match="CUT_VOLUME_INVALID"):
        parent.cut({"kind": "box"})


def test_a_point_load_on_the_cut_skips_other_cases_and_refuses_the_chosen_one(tmp_path):
    project, gfaces = global_project()
    vertex = vertex_at(project, 0.3, 0.3)
    project.load_case("on_cut").add_point_load(project.point(vertex), force=(0, 0, 100.0))
    parent = Parent(tmp_path, project, gfaces)
    box = BoxVolume((0.3, 0.3, -0.1), (0.7, 0.7, 0.1))
    cut = parent.cut(box)
    assert set(cut.report.skipped_cases) == {"on_cut"} and "default" in cut.definition.child.load_cases
    with pytest.raises(SubmodelError, match="CUT_LOAD_UNSUPPORTED"):
        parent.cut(box, source=SourceReference(parent.job.id, "on_cut"))


def test_a_clipped_distributed_load_skips_its_case(tmp_path):
    project, gfaces = global_project()
    pair = [vertex_at(project, 0.0, 0.0), vertex_at(project, 0.3, 0.3)]
    project.load_case("clipped").add_point_load(
        project.point(pair[0]), force=(0, 0, -30.0), distribution_policy="total_distributed",
        region=point_group(project, "pair", pair))
    parent = Parent(tmp_path, project, gfaces)
    # The box holds (0.3, 0.3) but not (0, 0): the distributed load would be clipped.
    cut = parent.cut(BoxVolume((0.2, 0.2, -0.1), (0.8, 0.8, 0.1)))
    assert "clipped" in cut.report.skipped_cases and "default" in cut.definition.child.load_cases
    # A box holding both points carries it.
    whole = parent.cut(BoxVolume((-0.1, -0.1, -0.1), (0.35, 0.35, 0.1)))
    assert "clipped" in whole.definition.child.load_cases and not whole.report.skipped_cases
    carried = whole.definition.child
    assert len(carried.load_cases["clipped"].point_loads) == 1  # the total stays one record
    assert assembled_totals(carried, "clipped")["force"][2] == pytest.approx(-30.0)


def test_imperfections_beams_wet_surfaces_and_masses_on_the_cut_refuse(tmp_path):
    from anyfem.model import plate_mode
    from anyfem.model.attributes import Mass, WetSurface

    project, gfaces = global_project()
    project.add_imperfection(plate_mode(project.face(gfaces[2, 2]), amplitude=0.001))
    parent = Parent(tmp_path, project, gfaces)
    with pytest.raises(SubmodelError, match="CUT_IMPERFECTIONS_UNSUPPORTED"):
        parent.cut()

    project, gfaces = global_project()
    project.wet_surfaces.append(WetSurface(project.face(gfaces[1, 1]), name="hull"))
    with pytest.raises(SubmodelError, match="CUT_WET_UNSUPPORTED"):
        Parent(tmp_path / "w", project, gfaces).cut()

    project, gfaces = global_project()
    project.add_mass(Mass(project.face(gfaces[0, 0]), 3.0, "clipped", distribution_policy="total_distributed"))
    with pytest.raises(SubmodelError, match="CUT_MASS_CLIPPED"):
        Parent(tmp_path / "m", project, gfaces).cut()


def test_every_child_face_needs_exactly_one_section(tmp_path):
    project, gfaces = global_project()
    parent = Parent(tmp_path, project, gfaces)
    snapshot = parent.session.snapshot()
    document = {key: value for key, value in snapshot.document.items()}
    # Drop one face's assignment from the document, as a half-edited parent would be.
    document["assignments"] = {**document["assignments"], "sections": document["assignments"]["sections"][1:]}
    for key in ("face_sections",):
        document.pop(key, None)
    from types import SimpleNamespace

    broken = SimpleNamespace(document=document, revision=snapshot.revision, thaw=lambda: __import__(
        "anyfem.io.project_file", fromlist=["project_from_dict"]).project_from_dict(__import__("copy").deepcopy(document)))
    with pytest.raises(SubmodelError, match="CUT_SECTION_UNRESOLVED"):
        cut_submodel(broken, BOX, source=parent.source, policy="drive_all", target_size=SIZE)


# ----------------------------------------------------------------------
# a cylinder parent (planes and cylinders are the supported surfaces)
# ----------------------------------------------------------------------
@pytest.mark.parametrize("policy", ["drive_all", "release_normal_rotation"])
def test_a_cylinder_patch_is_cut_and_solved_end_to_end(tmp_path, policy):
    from test_submodel_cylinder import R, W_EXACT, radial_at, tube

    project = tube(0.25)[0]
    parent = Parent(tmp_path, project, {}, mesh=project.generate_mesh(0.25))
    cut = parent.cut(BoxVolume((0.5, -0.5, 1.5), (1.5, 0.5, 2.5)), policy=policy, target_size=0.05)
    assert set(cut.definition.cut_faces) == {"y0", "y1", "z0", "z1"}
    assert len(cut.definition.child.geometry.faces) == 2  # the patch straddles the seam of two cylinder faces
    assert cut.report.carried["pressures"] == 2 and cut.report.dropped_outside["supports"] > 0
    # The generator's four seam members carry no beam section: the one reaching the box is left out, reported.
    assert cut.report.dropped_outside["unassigned_members"] == 1 and cut.report.notes
    parent.register(cut.definition)
    staged = stage_submodel_run(submodel_snapshot(parent.session), cut.definition.id, parent.store)
    try:
        result = staged.result
        cmesh = result.solution.built.mesh
        assert abs(radial_at(result, cmesh, 0.0, 2.0) / W_EXACT - 1.0) <= 0.005
        body = result.diagnostics["free_body"]
        assert body["force_ratio"] <= 1e-12 and body["moment_ratio"] <= 1e-12
        assert result.diagnostics["cylindrical"]["nodes"] == len(result.hits)
    finally:
        discard_staged_run(staged)


def test_total_distributed_masses_and_loads_keep_their_totals_with_shared_nodes(tmp_path):
    """Review round 1: one record per parent record; overlapping scopes keep builder semantics."""

    from anyfem.model.attributes import Mass

    project, gfaces = global_project()
    face = gfaces[1, 1]
    project.add_mass(Mass(project.face(face), 6.0, "face", distribution_policy="total_distributed"))
    edge = project.geometry.faces[face].loop[0].edge
    project.add_mass(Mass(project.edge(edge), 2.0, "edge", distribution_policy="total_distributed"))  # shares nodes
    parent = Parent(tmp_path, project, gfaces)
    cut = parent.cut()
    child = cut.definition.child
    assert len(child.masses) == 2 and child_mass_total(child) == pytest.approx(8.0)
    # The parent's own build assembles the same total.
    from anyfem.solve.build import build_fe_model

    built = build_fe_model(project, parent.mesh, "default", require_loads=False, require_supports=False)
    assert float(sum(built.fe_model.mesh.point_masses.values())) == pytest.approx(8.0)


def test_a_retained_physical_boundary_vertex_is_not_on_the_cut(tmp_path):
    """Review round 1: (0, 0.3) lies on the box plane x = 0 but on the plate's own free edge."""

    from anyfem.model.attributes import Mass

    project, gfaces = global_project()
    vertex = vertex_at(project, 0.0, 0.3)
    project.load_case("edge_load").add_point_load(project.point(vertex), force=(0, 0, 100.0))
    project.add_mass(Mass(project.point(vertex), 1.5, "boundary", distribution_policy="per_target"))
    parent = Parent(tmp_path, project, gfaces)
    cut = parent.cut(BoxVolume((0.0, 0.2, -0.1), (0.8, 0.8, 0.1)))
    assert "edge_load" in cut.definition.child.load_cases and not cut.report.skipped_cases
    assert len(cut.definition.child.masses) == 1
    assert "x0" not in cut.definition.cut_faces  # the plate's own edge, not a cut


def test_unresolved_lineage_cannot_make_a_ready_definition(parent, monkeypatch):
    import anygeometry.volume_cut as volume_cut

    real = volume_cut.apply_volume_cut

    def with_unresolved(model, plan):
        result = real(model, plan)
        from dataclasses import replace

        return replace(result, unresolved=(("vertex", 1),))

    monkeypatch.setattr(volume_cut, "apply_volume_cut", with_unresolved)
    with pytest.raises(SubmodelError, match="CUT_LINEAGE_UNRESOLVED"):
        parent.cut()


def test_the_report_matches_the_returned_child_and_counts_outside_records(tmp_path):
    project, gfaces = global_project()
    on_cut = vertex_at(project, 0.3, 0.3)
    case = project.load_case("fails_late")
    case.add_pressure(project.face(gfaces[1, 1]), 1000.0)  # carried before the failure below
    case.add_point_load(project.point(on_cut), force=(0, 0, 1.0))
    far = project.geometry.faces[gfaces[2, 2]].loop[0].edge
    project.seeding_overrides[far] = 3
    parent = Parent(tmp_path, project, gfaces)
    cut = parent.cut(BoxVolume((0.3, 0.3, -0.1), (0.7, 0.7, 0.1)))
    report, child = cut.report, cut.definition.child
    assert set(report.skipped_cases) == {"fails_late"} and "fails_late" not in child.load_cases
    used = {load.region.id for case in child.load_cases.values() for load in case.point_loads if load.region}
    assert {r.id for r in child.regions if r.hidden and "selection" in r.name} <= used  # no orphaned scope
    assert report.carried["pressures"] == len(child.load_cases["default"].pressures) == 1
    assert report.carried.get("supports", 0) == len(child.supports)
    assert report.dropped_outside["seeding_overrides"] == 1


def test_a_skipped_case_leaves_no_orphaned_scope_region(tmp_path):
    """Review round 2: the region made for an earlier multi-point load is removed with its case."""

    project, gfaces = global_project()
    inside = [vertex_at(project, 0.3, 0.3), vertex_at(project, 0.7, 0.7)]
    case = project.load_case("late_failure")
    case.add_point_load(project.point(inside[0]), force=(0, 0, -5.0), region=point_group(project, "two", inside))
    on_cut = vertex_at(project, 0.3, 0.7)
    case.add_point_load(project.point(on_cut), force=(0, 0, 1.0))
    parent = Parent(tmp_path, project, gfaces)
    cut = parent.cut(BoxVolume((0.3, 0.3, -0.1), (0.7, 0.7, 0.1)))  # (0.3, 0.7) is on the cut
    assert set(cut.report.skipped_cases) == {"late_failure"}
    assert not [r for r in cut.definition.child.regions if "selection" in r.name]


# ----------------------------------------------------------------------
# stiffeners (slices S2b and S4b)
# ----------------------------------------------------------------------
def stiffened_project(*, free_beam=False):
    """The global plate with flat-bar stiffeners along y = 0.3 and x = 0.7 (plate seams).

    The section follows the plate normal and carries its automatic neutral-axis offset,
    so a stiffener that lost its frame or offset in the child would change the answer.
    With ``free_beam`` a beam also runs free above the plate, across the box.
    """

    from anyfem.model import BeamSection

    project, gfaces = global_project()
    project.add_beam_section(BeamSection("flat bar", "Flatbar", "S355", flange_width=0.08, flange_thickness=0.01,
                                         offset_mode="automatic"))
    g = project.geometry
    stiffeners = []
    for edge_id, edge in g.edges.items():
        a, b = (np.asarray(g.vertex_position(v)) for v in (edge.start, edge.end))
        if (abs(a[1] - 0.3) < 1e-12 and abs(b[1] - 0.3) < 1e-12) or (abs(a[0] - 0.7) < 1e-12 and abs(b[0] - 0.7) < 1e-12):
            stiffeners.append(edge_id)
    assert len(stiffeners) == 6
    project.assign_beams(stiffeners, "flat bar")
    if free_beam:
        project.add_beam_section(BeamSection("free bar", "Flatbar", "S355", flange_width=0.08, flange_thickness=0.01,
                                             offset_mode="centerline"))
        line = g.add_line(g.add_point(0.0, 0.5, 0.05), g.add_point(1.0, 0.5, 0.05))
        project.assign_beam(line, "free bar")
    return project, gfaces, stiffeners


def test_a5_stiffeners_are_carried_and_the_child_reproduces_the_parent(tmp_path):
    project, gfaces, stiffeners = stiffened_project()
    parent = Parent(tmp_path, project, gfaces)
    cut = parent.cut()
    definition, report = cut.definition, cut.report
    child = definition.child
    # Each stiffener line has three parent edges; the box keeps a piece of each (0.2-0.3, 0.3-0.7, 0.7-0.8).
    assert report.carried["beam_assignments"] == 6 and "beam_assignments" not in report.dropped_outside
    assert len(child.edge_sections) == 6 and set(child.edge_sections.values()) == {"flat bar"}
    # assign_beams owns each line by its own member; every one is partitioned, none dropped.
    assert len(child.geometry.members) == 6 and not cut.report.cut.dropped_members
    for child_edge in child.edge_sections:
        (source,) = cut.report.cut.lineage[("edge", child_edge)]
        assert source in stiffeners
        assert np.allclose(child.beam_offset_vector(child_edge), project.beam_offset_vector(source))

    parent.register(definition)
    staged = stage_submodel_run(submodel_snapshot(parent.session), definition.id, parent.store)
    try:
        result = staged.result
        mesh = result.solution.built.mesh
        # 0.1 elements: 2 x 10 stiffener beams in the parent, the 0.6 inside each line in the child
        assert (len(parent.mesh.beams), len(mesh.beams)) == (20, 12)
        match = node_match(mesh, parent.mesh)
        driven = set(result.transfer)
        # Slave/master structure at the cut: each stiffener end on the cut is an offset beam node
        # coupled to a driven plate node; the plate node is driven, the beam node follows it.
        at_cut = [c for c in mesh.couplings.values() if set(c.plate_nodes) <= driven]
        assert len(at_cut) == 4 and not {c.beam_node for c in at_cut} & driven
        assert not {c.beam_node for c in mesh.couplings.values()} & driven
        errors = np.array([np.abs(result.solution.node_displacement(n) - parent.solution.node_displacement(match[n]))
                           for n in set(mesh.nodes) - driven])
        worst = errors.max(axis=0)
        scale = max(np.abs(parent.solution.node_displacement(m)).max() for m in parent.mesh.nodes)
        assert np.max(worst) < 1e-9 * max(scale, 1e-12)
        body = result.diagnostics["free_body"]
        assert body["force_ratio"] < 1e-12 and body["moment_ratio"] < 1e-12
        published = publish_submodel_run(parent.session, staged)
    except BaseException:
        discard_staged_run(staged)
        raise
    assert published.notification_error is None


def test_a_free_standing_beam_across_the_box_refuses(tmp_path):
    project, gfaces, _stiffeners = stiffened_project(free_beam=True)
    with pytest.raises(SubmodelError, match="CUT_BEAMS_UNSUPPORTED"):
        Parent(tmp_path, project, gfaces).cut()


def test_a_beam_outside_the_box_is_reported_not_carried(tmp_path):
    project, gfaces, _stiffeners = stiffened_project()
    cut = Parent(tmp_path, project, gfaces).cut(BoxVolume((0.0, 0.5, -0.1), (0.5, 1.0, 0.1)))
    assert not cut.definition.child.edge_sections and not cut.definition.child.geometry.members
    assert cut.report.dropped_outside["beam_assignments"] == 6


def test_a_beam_whose_frame_would_change_refuses(tmp_path):
    from dataclasses import replace as replaced

    from anyfem.submodel_cut import _require_same_beam_frame

    project, gfaces, _stiffeners = stiffened_project()
    cut = Parent(tmp_path, project, gfaces).cut()
    child = cut.definition.child
    child_edge = next(iter(child.edge_sections))
    (source,) = cut.report.cut.lineage[("edge", child_edge)]
    _require_same_beam_frame(project, child, source, child_edge)  # as carried
    child.beam_sections["flat bar"] = replaced(child.beam_sections["flat bar"], attachment_side="back")
    with pytest.raises(SubmodelError, match="CUT_BEAM_FRAME"):
        _require_same_beam_frame(project, child, source, child_edge)
