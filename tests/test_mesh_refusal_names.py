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
    project, sliver = _project_with_sliver(crossed=True)
    with pytest.raises(MeshError) as caught:
        project.generate_mesh(.5, strategy="quad_first")
    error = caught.value
    # The crossing plate splits the sliver; a sliver piece refuses under its own working id.
    assert error.source_face_ids == (sliver,)
    assert error.quad_first_face != sliver and error.quad_first_face not in project.geometry.faces
    assert error.builder_groups == ("all plates", "sliver plate")
    assert str(error).count("[quad-first face ") == 1
    assert str(error).endswith(f"; source face {sliver}; groups: all plates; sliver plate]")


def test_a_cache_hit_refusal_is_named(monkeypatch):
    import anyfem.model.project as project_module
    from anyfem import Project, steel

    project = Project("cache-hit refusal")
    project.add_material(steel("S355", .01))
    project.add_plate_section("plate", .01, "S355")
    _plate(project, ((0., 0., 0.), (2., 0., 0.), (2., 1., 0.), (0., 1., 0.)), (0, 1, 2, 3))
    skew = _plate(project, ((3., 0., 0.), (4.4, 0., 0.), (4.9, 1.2, 0.), (3.2, .8, 0.)), (0, 1, 2, 3))
    # A crossing plate splits the skew plate, so working and source face ids differ.
    _plate(project, ((3.9, -.5, -.5), (3.9, 1.5, -.5), (3.9, 1.5, .5), (3.9, -.5, .5)), (0, 1, 2, 3))
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
        assert caught.value.quad_first_face != skew and caught.value.source_face_ids == (skew,)
        assert text.endswith(f"; source face {skew}; groups: skew plate]"), text
    assert extracted == [], "both refusals must come from the cached preparation"


def test_strict_automatic_refusal_is_named_through_the_lineage():
    from anymesher import MeshAutomationOptions

    project, sliver = _project_with_sliver(crossed=True)
    with pytest.raises(MeshError) as caught:
        project.generate_mesh(.5, strategy="quad_first", automation=MeshAutomationOptions(strict_method=True))
    error = caught.value
    assert error.source_face_ids == (sliver,) and error.quad_first_face != sliver
    assert error.builder_groups == ("all plates", "sliver plate")
    assert str(error).count("[quad-first face ") == 1
    assert str(error).endswith(f"; source face {sliver}; groups: all plates; sliver plate]")


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


def test_a_refusal_raised_by_the_cancellation_callback_is_left_alone():
    from anyfem.mesh_refusal_names import call_naming_source_faces

    reused = MeshError("refused earlier")
    reused.quad_first_face, reused.quad_first_location = 9, None

    def action(*, cancellation_check):
        cancellation_check("stage")

    def check(stage):
        raise reused

    with pytest.raises(MeshError) as caught:
        call_naming_source_faces(action, {"face:2": ("face:9",)}, None, None, cancellation_check=check)
    assert caught.value is reused and str(reused) == "refused earlier"
    assert not hasattr(reused, "source_face_ids")


def test_a_typed_cancellation_signal_is_passed_on_unwrapped():
    from anygeometry.cancellation import CancellationSignal
    from anyfem.mesh_refusal_names import call_naming_source_faces

    signal, seen = CancellationSignal(), []
    call_naming_source_faces(lambda *, cancellation_check: seen.append(cancellation_check), {}, None, None,
                             cancellation_check=signal)
    assert seen == [signal] and seen[0] is signal


@pytest.mark.parametrize("explicit", (True, False))
def test_a_refusal_raised_from_a_callback_exception_is_left_alone(explicit):
    from anyfem.mesh_refusal_names import call_naming_source_faces

    def check(stage):
        raise RuntimeError("caller stop")

    def action(*, cancellation_check):
        try:
            cancellation_check("stage")
        except RuntimeError as stop:
            wrapper = MeshError("stage refused")
            wrapper.quad_first_face, wrapper.quad_first_location = 9, None
            if explicit:
                raise wrapper from stop
            raise wrapper

    with pytest.raises(MeshError) as caught:
        call_naming_source_faces(action, {"face:2": ("face:9",)}, None, None, cancellation_check=check)
    assert str(caught.value) == "stage refused"
    assert not hasattr(caught.value, "source_face_ids") and not hasattr(caught.value, "builder_groups")


def test_a_commit_between_the_revision_check_and_the_group_read_reports_unavailable(monkeypatch):
    import anyfem.mesh_refusal_names as names

    project, sliver = _project_with_sliver()
    geometry = project.geometry
    revision = geometry.revision

    class Moving:
        """The source geometry, whose revision moves on after the groups are read."""
        reads = 0

        @property
        def revision(self):
            return revision + (1 if Moving.reads else 0)

        @property
        def groups(self):
            Moving.reads += 1
            return geometry.groups

    error = MeshError("refused")
    error.quad_first_face, error.quad_first_location = 9, None
    names.name_source_of_face_refusal(error, {f"face:{sliver}": ("face:9",)}, Moving(), revision)
    assert error.builder_groups is None and Moving.reads == 1
    assert str(error).endswith("groups: unavailable (geometry changed)]")


def test_an_equal_but_distinct_refusal_is_still_named():
    """The callback-ownership walk matches by identity: an equal genuine refusal is still rendered."""
    from anyfem.mesh_refusal_names import call_naming_source_faces

    class EqualRefusal(MeshError):
        def __eq__(self, other):
            return True

        __hash__ = MeshError.__hash__

    def check(stage):
        raise EqualRefusal("caller stop")

    def action(*, cancellation_check):
        try:
            cancellation_check("stage")
        except EqualRefusal:
            pass  # handled; the stage then refuses on its own (no cause, no context)
        refusal = EqualRefusal("T3 cell 0 (0, 1, 2): corner angle 1.00 < 15 deg")
        refusal.quad_first_face, refusal.quad_first_location = 9, None
        raise refusal

    with pytest.raises(MeshError) as caught:
        call_naming_source_faces(action, {"face:2": ("face:9",)}, None, None, cancellation_check=check)
    assert caught.value.source_face_ids == (2,)
    assert str(caught.value).endswith("[quad-first face 9; source face 2]")
