"""Editable small barge ownership, physical loading and retained-result requests."""

import json

import numpy as np
import pytest

from anyfem.application.barge_fatigue import (
    ANALYSIS, BRACKET, MOMENT_FACTORS, SERIES, TIMES, barge_fatigue_detail, bracketed_barge_fatigue,
)
from anyfem.io.project_file import project_from_dict, project_to_dict


@pytest.fixture(scope="module")
def project():
    return bracketed_barge_fatigue()


def test_model_roundtrip_keeps_named_welds_and_the_small_hull(project):
    restored = project_from_dict(project_to_dict(project))
    assert barge_fatigue_detail(restored) == barge_fatigue_detail(project)
    assert restored.shell_formulation_policy.s3_reference_surface == "faceted"
    geometry = restored.geometry
    xyz = np.array([geometry.vertex_position(v) for v in geometry.vertices])
    np.testing.assert_allclose(xyz.max(axis=0) - xyz.min(axis=0), (5, 11, 2.5))
    assert not geometry.validate_topology()
    assert restored.plate_sections[BRACKET].thickness == pytest.approx(.010)
    assert restored.plate_sections[BRACKET].material == "S355"
    regions = {region.name: region for region in restored.regions}
    assert restored.target_size == pytest.approx(.7)
    scoped = {r.ref.id for r in restored.refinements if r.mode == "override" and r.ref.kind == "face"}
    for base in ("Deck", "Bottom", "Port side", "Starboard side", "Transverse bulkhead"):
        originals = regions[base].definition.anchors
        assert all(not geometry.faces[ref.id].holes for ref in originals)
        if base == "Bottom":
            assert not any(ref.id in scoped for ref in originals)
        else:
            # Central, distant panels inherit the original coarse hull target.
            assert any(ref.id not in scoped for ref in originals)
    expected = {"deck stiffener web": .008, "deck stiffener flange": .010,
                "bulkhead stiffener web": .006, "bulkhead stiffener flange": .008,
                "knee bracket": .010, "knee face flat": .008}
    for side, y in (("Port",-4.8), ("Starboard",5.0)):
        host_faces = {}
        for name, t in expected.items():
            sheet, = [s for s in geometry.sheets.values() if s.name == f"{side} {name}"]
            host_faces[name] = {geometry.face_uses[u].face_id for u in sheet.face_use_ids}
            assert all(restored.plate_sections[restored.face_sections[f]].thickness == pytest.approx(t)
                       for f in host_faces[name])
        for leg, axis, limit, half_width in (("deck",2,2.295,.050), ("bulkhead",0,2.654,.0375)):
            flange_vertices = [geometry.vertex_position(v)
                               for f in host_faces[f"{leg} stiffener flange"] for u in geometry.faces[f].loop
                               for v in (geometry.edges[u.edge].start, geometry.edges[u.edge].end)]
            xyz = np.asarray(flange_vertices)
            np.testing.assert_allclose(xyz[:,axis], limit)
            np.testing.assert_allclose([xyz[:,1].min(), xyz[:,1].max()], [y-half_width, y+half_width])
            web_vertices = [geometry.vertex_position(v)
                            for f in host_faces[f"{leg} stiffener web"] for u in geometry.faces[f].loop
                            for v in (geometry.edges[u.edge].start, geometry.edges[u.edge].end)]
            web = np.asarray(web_vertices)
            np.testing.assert_allclose(web[:,1], y)
            np.testing.assert_allclose([min(web[:,axis]),max(web[:,axis])], sorted((2.5,limit)))
        assert {r.id for r in regions[f"{side} bracket with shell stiffeners"].definition.anchors
                } == set.union(*host_faces.values())
        flat = host_faces["knee face flat"]
        knee = host_faces["knee bracket"]
        assert {r.id for r in regions[f"{side} knee detail"].definition.anchors} == (flat | knee)
        # A second beam axis at either replaced T would double its stiffness.
        for eid in restored.edge_sections:
            axis = geometry.edges[eid]
            a, b = (geometry.vertex_position(v) for v in (axis.start, axis.end))
            if np.isclose(a[1], y) and np.isclose(b[1], y):
                assert not (a[2] == b[2] == 2.5 and a[0] != b[0])
                assert not (a[0] == b[0] == 2.5 and a[2] != b[2])
        fine = {r.ref.id for r in restored.refinements if r.mode == "override" and r.size <= .020}
        for leg in ("deck","bulkhead"):
            fid, toe, direction = barge_fatigue_detail(restored, side, leg)
            assert fid in host_faces[f"{leg} stiffener flange"] and fid in fine
            assert toe in {u.edge for u in geometry.faces[fid].loop}
            assert any(toe in {u.edge for u in geometry.faces[f].loop} for f in flat & fine)
            axis = geometry.edges[toe]
            ends = [geometry.vertex_position(v) for v in (axis.start, axis.end)]
            np.testing.assert_allclose(np.sort([a[1] for a in ends]), [y, y + (.05 if leg == "deck" else .0375)])
            tip = next(a for a in ends if np.isclose(a[1], y))
            kneecorners = {v for f in knee & fine for u in geometry.faces[f].loop
                           for v in (geometry.edges[u.edge].start,geometry.edges[u.edge].end)}
            assert any(np.allclose(geometry.vertex_position(v),tip) for v in kneecorners)
            assert direction == ((1.,0.,0.) if leg == "deck" else (0.,0.,-1.))
    restored.validate()


def test_signed_series_is_a_pure_variable_bending_couple(project):
    assert "default" not in project.load_cases
    series = project.load_cases[SERIES]
    assert len(series.line_loads) == 2
    steps = project.series_children(SERIES)
    assert tuple(case.time for case in steps) == TIMES
    assert len(project.load_cases) == len(steps) + 1
    reference = None
    moments = []
    for index, step in enumerate(steps):
        case = project.resolve_case(step)
        resultant, moment = np.zeros(3), np.zeros(3)
        for load in case.line_loads:
            factor = case.envelope_factor(load.envelope)
            assert factor == pytest.approx(MOMENT_FACTORS[index])
            axis = project.geometry.edges[load.ref.id]
            a, b = [project.geometry.vertex_position(v) for v in (axis.start, axis.end)]
            assert a[0] == b[0] == 5.
            force = np.asarray(load.force_per_length) * np.linalg.norm(b - a) * factor
            resultant += force
            moment += np.cross((a + b) / 2., force)
        np.testing.assert_allclose(resultant, 0., atol=1.e-8)
        np.testing.assert_allclose(moment[[0, 2]], 0., atol=1.e-8)
        if reference is None and MOMENT_FACTORS[index]:
            reference = moment[1] / MOMENT_FACTORS[index]
        moments.append(moment[1])
    # Same end couple as the original 5 m beam-stiffened example.
    from anyfem.application.examples import floating_barge_section
    original = floating_barge_section().load_case().line_loads[0]
    assert reference == pytest.approx(original.force_per_length[0] * 11. * 2.5)
    np.testing.assert_allclose(moments, np.asarray(MOMENT_FACTORS) * reference)


def test_rerun_requests_every_signed_surface_component_and_displacement(project):
    analysis, = project.analyses.values()
    assert (analysis.name, analysis.type) == (ANALYSIS, "batch_linear_static")
    requests = [project.output_requests[r] for r in analysis.output_request_ids]
    stress, = [r for r in requests if r.location == "element"]
    assert stress.basis == "global" and stress.frame_policy == "all"
    assert set(stress.quantity_keys) == {
        f"stress.global_{component}_{side}" for component in ("xx", "yy", "zz", "xy", "yz", "xz")
        for side in ("top", "bot")}
    assert {r.id for r in project.regions[stress.region.id].definition.anchors} == set(project.geometry.faces)
    assert any(r.quantity_keys == ("displacement",) and r.location == "node" for r in requests)


def test_mount_chart_coordinates_are_restricted_to_the_small_hull():
    from anyfem.application.examples import floating_barge_section
    with pytest.raises(ValueError,match="5 × 11 × 2.5"):
        floating_barge_section(length=15.,shell_mount_charts=True)


def test_bundled_example_is_current_with_the_live_model(project, tmp_path):
    """The packaged ZIP opens without stale meshes and carries the live couple."""
    from importlib import resources
    from zipfile import ZipFile

    from anyfem.beam_offset_rule import CURRENT_RULE, HEADER_KEY, has_rule_stale_records, is_rule_stale
    from anyfem.io.project_file import load_project

    resource = resources.files("anyfem").joinpath("example_data", "barge5_fatigue.zip")
    with resource.open("rb") as stream, ZipFile(stream) as archive:
        archive.extract("Barge5Fatigue.anyfem", tmp_path)
        header = json.loads(archive.read("Barge5Fatigue.anyfem"))["anyfem"]
    assert header.get(HEADER_KEY) == CURRENT_RULE
    bundled = load_project(tmp_path / "Barge5Fatigue.anyfem")
    assert not has_rule_stale_records(bundled)
    record, = bundled.mesh_records.values()
    assert record.status != "stale" and not is_rule_stale(record)
    job, = bundled.jobs.values()
    assert job.mesh_hash == record.mesh_hash
    # The saved signed history is the one the live model applies now: the end
    # couple of every step, including its time factor.
    def history(p):
        steps = []
        for step in p.series_children(SERIES):
            case = p.resolve_case(step)
            steps.append((step.time, [(load.ref.id, tuple(load.force_per_length), case.envelope_factor(load.envelope))
                                      for load in case.line_loads]))
        return steps
    assert history(bundled) == history(project)


def _extract_example(tmp_path, *, results=False):
    from importlib import resources
    from zipfile import ZipFile

    resource = resources.files("anyfem").joinpath("example_data", "barge5_fatigue.zip")
    with resource.open("rb") as stream, ZipFile(stream) as archive:
        names = [n for n in archive.namelist()
                 if n == "Barge5Fatigue.anyfem" or n.endswith(".anymesh.h5") or (results and n.endswith(".anyres.h5"))]
        archive.extractall(tmp_path, members=names)
    return tmp_path / "Barge5Fatigue.anyfem"


def _unused_plate_section(project):
    from anyfem.model import PlateSection

    material = next(iter(project.plate_sections.values())).material
    return PlateSection(name="Unused 12 mm", thickness=.012, material=material)


def test_bundled_example_mesh_stays_current_after_an_edit_that_keeps_every_offset(tmp_path):
    """The packaged mesh records its beam offsets, so an unrelated edit does not make it stale."""
    from anyfem.application.mesh_publication import (
        BEAM_OFFSETS_KEY, beam_offsets_changed, current_mesh_sources, recorded_beam_offsets,
    )
    from anyfem.io.artifacts import ArtifactStore
    from anyfem.io.project_file import load_project
    from anyfem.solve.build import build_fe_model

    path = _extract_example(tmp_path)
    bundled = load_project(path)
    record, = bundled.mesh_records.values()
    assert record.summary.get(BEAM_OFFSETS_KEY) == recorded_beam_offsets(bundled)
    mesh = ArtifactStore(path).read_mesh(bundled.artifacts[record.artifact_id], mesh_id=record.id,
                                         document_id=bundled.document_id, mesh_hash=record.mesh_hash)
    # An unused plate section changes the model but no mesh input and no beam offset.
    sources = record.summary["mesh_sources"]
    section = _unused_plate_section(bundled)
    bundled.add_plate_section(section.name, thickness=section.thickness, material=section.material)
    model_hash, digests = current_mesh_sources(bundled)
    assert model_hash != record.source_model_hash and digests == sources
    assert not beam_offsets_changed(bundled, record, model_hash)
    build_fe_model(bundled, mesh, require_loads=False, require_supports=False)


def test_workbench_keeps_the_bundled_mesh_current_after_an_edit_that_keeps_every_offset(project, tmp_path):
    """Through the real edit command the saved mesh record stays completed, not stale."""
    from anyfem.commands import AddPlateSection
    from test_mesh_publication_lifecycle import FileWorkbench

    path = _extract_example(tmp_path, results=True)
    editor = FileWorkbench(project)
    editor.open_project(str(path))
    try:
        record = editor.project.mesh_records[editor.project.mesh_numbering.active_mesh_id]
        assert editor.mesh_record_state(record) == "completed"
        before = editor.session.snapshot().revision.model_hash
        editor.run(AddPlateSection(_unused_plate_section(editor.project)))
        assert editor.session.snapshot().revision.model_hash != before
        assert editor.mesh_record_state(record) == "completed"
    finally:
        editor._project_lock.release()
