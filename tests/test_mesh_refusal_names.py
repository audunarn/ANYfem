"""A quad-first refusal names the working face, a location, the source face and its builder groups.

Probe diagnostics (tripod variants, 2026-11-24).  ANYmesher records the working face without changing
its message; ANYfem renders it at its outer meshing boundaries only.
"""

import pytest
from anygeometry import EntityRef, OrientedEdge

from anymesher.errors import MeshError


def _plate(project, points, corners=None):
    g = project.geometry
    edges = g.add_polyline(g.add_points(points), close=True)
    loop = tuple(OrientedEdge(e, True) for e in edges)
    face = g.add_face_from_loop(loop, corners=corners) if corners else g.add_face_from_loop(loop)
    g.add_sheet((face,))
    project.assign_plate(face, "plate")
    return face


def _project_with_sliver(*, crossed=False):
    """A rectangle plus a sliver triangle; ``crossed`` adds a vertical plate that splits the sliver."""
    from anyfem import Project, steel

    project = Project("sliver refusal")
    project.add_material(steel("S355", .01))
    project.add_plate_section("plate", .01, "S355")
    rectangle = _plate(project, ((0., 0., 0.), (2., 0., 0.), (2., 1., 0.), (0., 1., 0.)), (0, 1, 2, 3))
    sliver = _plate(project, ((5., 0., 0.), (6., 0., 0.), (6., .05, 0.)))
    faces = [rectangle, sliver]
    if crossed:
        faces.append(_plate(project, ((5.5, -.2, -.5), (5.5, .3, -.5), (5.5, .3, .5), (5.5, -.2, .5)), (0, 1, 2, 3)))
    g = project.geometry
    g.add_to_group("rectangle plate", [EntityRef("face", rectangle)])
    g.add_to_group("sliver plate", [EntityRef("face", sliver)])
    g.add_to_group("all plates", [EntityRef("face", item) for item in faces])
    return project, sliver


def _assert_named(error, sliver):
    text = str(error)
    assert text.startswith("T3 cell "), text
    assert "[quad-first face " in text and " near (" in text, text
    assert f"; source face {sliver}; groups: all plates; sliver plate]" in text, text
    assert error.source_face_ids == (sliver,)
    assert error.builder_groups == ("all plates", "sliver plate")
    x, y, _z = error.quad_first_location
    assert 5. <= x <= 6. and 0. <= y <= .05


def test_project_generate_mesh_refusal_names_source_face_and_groups():
    project, sliver = _project_with_sliver()
    with pytest.raises(MeshError) as caught:
        project.generate_mesh(.5, strategy="quad_first")
    _assert_named(caught.value, sliver)


def test_a_split_source_face_is_named_through_the_lineage():
    project, _sliver = _project_with_sliver(crossed=True)
    with pytest.raises(MeshError) as caught:
        project.generate_mesh(.5, strategy="quad_first")
    error = caught.value
    (source,) = error.source_face_ids
    assert error.quad_first_face not in project.geometry.faces, "the crossing must split the refusing face"
    expected = tuple(sorted(name for name, items in project.geometry.groups.items()
                            if EntityRef("face", source) in items))
    assert error.builder_groups == expected and expected
    assert str(error).count("[quad-first face ") == 1
    assert f"; source face {source}; groups: {'; '.join(expected)}]" in str(error)


def test_a_cache_hit_refusal_is_named(monkeypatch):
    import anyfem.model.project as project_module
    from anyfem import Project, steel

    project = Project("cache-hit refusal")
    project.add_material(steel("S355", .01))
    project.add_plate_section("plate", .01, "S355")
    _plate(project, ((0., 0., 0.), (2., 0., 0.), (2., 1., 0.), (0., 1., 0.)), (0, 1, 2, 3))
    skew = _plate(project, ((3., 0., 0.), (4.4, 0., 0.), (4.9, 1.2, 0.), (3.2, .8, 0.)), (0, 1, 2, 3))
    project.geometry.add_to_group("skew plate", [EntityRef("face", skew)])
    project.generate_mesh(.5, strategy="quad_first")  # succeeds and stores the prepared-model cache
    assert project._mesh_preparation_cache is not None
    extracted = []
    real_extract = project_module.extract_model_closure
    monkeypatch.setattr(project_module, "extract_model_closure",
                        lambda *a, **k: extracted.append(1) or real_extract(*a, **k))
    for _attempt in range(2):
        with pytest.raises(MeshError) as caught:
            project.generate_mesh(.5, strategy="quad_first", quad_t3_minimum_angle=59.0)
        text = str(caught.value)
        assert text.startswith("T3 cell ") and text.count("[quad-first face ") == 1, text
        assert text.endswith(f"; source face {skew}; groups: skew plate]"), text
    assert extracted == [], "both refusals must come from the cached preparation"


def test_native_meshing_session_refusal_names_source_face_and_groups():
    from anyfem.native_meshing import ComponentUpdateKind, NativeMeshSettings

    project, sliver = _project_with_sliver()
    with project.native_meshing_session(NativeMeshSettings.create(.5, backend="quad_first")) as session:
        session.request_remesh()
        assert session.runtime.wait_for_idle(timeout=300)
        failed = [event for event in session.runtime.poll_events() if event.kind is ComponentUpdateKind.FAILED]
    named = [event.message for event in failed if "[quad-first face " in event.message]
    assert named, [event.message for event in failed]
    assert f"; source face {sliver}; groups: all plates; sliver plate]" in named[0]


def test_changed_geometry_reports_groups_unavailable():
    from anyfem.mesh_refusal_names import name_source_of_face_refusal

    project, sliver = _project_with_sliver()
    error = MeshError("T3 cell 0 (0, 1, 2): corner angle 1.00 < 15 deg")
    error.quad_first_face, error.quad_first_location = 9, None
    name_source_of_face_refusal(error, {f"face:{sliver}": ("face:9",)}, project.geometry,
                                project.geometry.revision - 1)
    assert error.builder_groups is None
    assert str(error).endswith(f"[quad-first face 9; source face {sliver}; groups: unavailable (geometry changed)]")


def test_a_rerendered_refusal_shows_the_current_face_once():
    from anyfem.mesh_refusal_names import name_source_of_face_refusal

    error = MeshError("refused")
    error.quad_first_face, error.quad_first_location = 7, None
    name_source_of_face_refusal(error, {"face:1": ("face:7",)}, None)
    error.quad_first_face = 9
    name_source_of_face_refusal(error, {"face:2": ("face:9",)}, None)
    assert str(error) == "refused [quad-first face 9; source face 2]"
    assert error.source_face_ids == (2,)


def test_refusals_without_a_working_face_and_other_exceptions_are_untouched():
    from anyfem.mesh_refusal_names import call_naming_source_faces, name_source_of_face_refusal

    def refuse():
        raise MeshError("quad-first requires at least one selected face")

    with pytest.raises(MeshError) as caught:
        call_naming_source_faces(refuse, {"face:1": ("face:2",)}, None, None)
    assert str(caught.value) == "quad-first requires at least one selected face"
    assert not hasattr(caught.value, "source_face_ids")

    class Hostile(ValueError):
        def __getattr__(self, name):
            raise RuntimeError("no attributes here")

    other = Hostile("not a mesher refusal")
    name_source_of_face_refusal(other, {"face:1": ("face:2",)}, None)
    assert str(other) == "not a mesher refusal"
