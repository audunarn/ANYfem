"""Entity mesh sizes: generator persistence/migration, controls and format guard."""
from copy import deepcopy

import pytest
from anygeometry import EntityRef

from anyfem import commands
from anyfem.application.controller import default_project
from anyfem.generator_properties import (
    generator_mesh_controls,
    properties,
    remove_generator_properties,
)
from anyfem.io.project_file import (
    ENTITY_SIZES_FORMAT,
    ProjectFileError,
    project_from_dict,
    project_to_dict,
)
from anyfem.mesh.refinement import Refinement
from anyfem.native_meshing import ControlScope, NativeMeshControl


def panel(project, **options):
    return commands.AddStiffenedPanel(4, 3, 1, **options).do(project)


def tagged(project, feature_id):
    return [item for item in project.refinements if item.name == f"generator:{feature_id}:mesh"]


# ------------------------------------------------------------------ generator
@pytest.mark.parametrize("size", [0.2, 0.4, 1.5])
def test_generator_sizes_above_and_below_global_are_entity_overrides(size):
    project = default_project()
    feature = panel(project, mesh_size=size)
    zones = tagged(project, feature.feature_id)
    assert len(zones) == 5
    assert all(item.mode == "override" and item.size == size for item in zones)
    assert project.generator_properties[feature.feature_id]["mesh_size_mode"] == "override"


def test_explicit_refine_mode_keeps_the_spatial_meaning():
    project = default_project()
    feature = commands.AddFeature(
        "generator.stiffened_panel",
        parameters={"length": 4, "width": 3, "longitudinal_spacing": 1},
        engineering={"mesh_size": 0.4, "mesh_size_mode": "refine"},
    ).do(project)
    assert all(item.mode == "refine" for item in tagged(project, feature.feature_id))
    loaded = project_from_dict(project_to_dict(project))
    assert all(item.mode == "refine" for item in tagged(loaded, feature.feature_id))
    assert loaded.generator_properties == project.generator_properties


def test_invalid_mode_is_rejected_atomically():
    with pytest.raises(ValueError):
        properties({"mesh_size": 0.4, "mesh_size_mode": "sideways"})
    project = default_project()
    before = deepcopy(project_to_dict(project))
    with pytest.raises(ValueError):
        commands.AddFeature(
            "generator.stiffened_panel",
            parameters={"length": 4, "width": 3, "longitudinal_spacing": 1},
            engineering={"mesh_size": 0.4, "mesh_size_mode": "sideways"},
        ).do(project)
    assert project_to_dict(project) == before


def test_override_project_round_trips_in_the_guarded_format():
    project = default_project()
    feature = panel(project, mesh_size=1.5)
    data = project_to_dict(project)
    assert data["anyfem"]["format"] == ENTITY_SIZES_FORMAT
    assert all(item.get("mode") == "override" for item in data["meshing"]["refinements"])
    loaded = project_from_dict(deepcopy(data))
    assert loaded.generator_properties == project.generator_properties
    assert [item.mode for item in loaded.refinements] == ["override"] * 5
    assert project_to_dict(loaded)["meshing"]["refinements"] == data["meshing"]["refinements"]
    assert loaded.regenerate_geometry_features().success
    assert len(tagged(loaded, feature.feature_id)) == 5
    assert all(item.mode == "override" for item in loaded.refinements)


def test_old_readers_cannot_silently_clamp_an_override():
    project = default_project()
    panel(project, mesh_size=1.5)
    data = project_to_dict(project)
    data["anyfem"]["format"] = ENTITY_SIZES_FORMAT - 1
    with pytest.raises(ProjectFileError, match="entity size overrides"):
        project_from_dict(data)


def test_unaffected_projects_keep_their_format_and_bytes():
    plain = project_to_dict(default_project())
    manual = default_project()
    manual.refinements.append(Refinement(size=0.2, center=(0.0, 0.0, 0.0), name="impact"))
    data = project_to_dict(manual)
    assert data["anyfem"]["format"] == plain["anyfem"]["format"]
    assert "mode" not in data["meshing"]["refinements"][0]
    loaded = project_from_dict(deepcopy(data))
    assert loaded.refinements[0].mode == "refine" and loaded.refinements[0].name == "impact"


def test_old_generator_sizes_migrate_on_load_but_manual_zones_do_not():
    project = default_project()
    feature = panel(project, mesh_size=0.4)
    data = deepcopy(project_to_dict(project))
    # Rewrite as a file from before entity sizes existed.
    data["anyfem"]["format"] = ENTITY_SIZES_FORMAT - 1
    for entry in data["meshing"]["refinements"]:
        entry.pop("mode", None)
    for options in data["generator_properties"].values():
        options.pop("mesh_size_mode", None)
    manual = deepcopy(data["meshing"]["refinements"][0])
    manual["name"] = "manual refine zone"
    data["meshing"]["refinements"].append(manual)
    loaded = project_from_dict(data)
    assert loaded.generator_properties[feature.feature_id]["mesh_size_mode"] == "override"
    owned = tagged(loaded, feature.feature_id)
    assert owned and all(item.mode == "override" for item in owned)
    zones = [item for item in loaded.refinements if item.name == "manual refine zone"]
    assert len(zones) == 1 and zones[0].mode == "refine"
    # Saving upgrades the file; the migration is then stable.
    again = project_to_dict(loaded)
    assert again["anyfem"]["format"] == ENTITY_SIZES_FORMAT


def test_old_file_without_override_keeps_a_manual_look_alike_unchanged():
    project = default_project()
    feature = panel(project, mesh_size=0.4)
    data = deepcopy(project_to_dict(project))
    data["anyfem"]["format"] = ENTITY_SIZES_FORMAT - 1
    for entry in data["meshing"]["refinements"]:
        entry.pop("mode", None)
    for options in data["generator_properties"].values():
        options.pop("mesh_size_mode", None)
    # Same tag but not the generator's recorded size: not generator provenance.
    impostor = deepcopy(data["meshing"]["refinements"][0])
    impostor["size"] = 0.123
    data["meshing"]["refinements"].append(impostor)
    loaded = project_from_dict(data)
    sizes = {item.size: item.mode for item in tagged(loaded, feature.feature_id)}
    assert sizes.get(0.4) == "override"
    if 0.123 in sizes:  # a later reapply may legitimately drop an impostor of its own tag
        assert sizes[0.123] == "refine"


def test_removal_clears_only_generator_owned_sizes():
    project = default_project()
    feature = panel(project, mesh_size=1.5)
    manual = Refinement(size=0.2, center=(0.0, 0.0, 0.0), name="impact")
    project.refinements.append(manual)
    remove_generator_properties(project, feature.feature_id)
    assert project.refinements == [manual]


# ----------------------------------------------------------- native controls
def test_sized_controls_default_to_override_and_refine_stays_explicit():
    default = NativeMeshControl.create("a", target_size=2.0)
    assert default.size_mode == "override"
    assert NativeMeshControl("e", ControlScope(), 1.0).size_mode == "override"
    # the default serializes exactly as sized controls always did
    assert "size_mode" not in default.to_dict()
    assert NativeMeshControl.from_dict(default.to_dict()) == default
    refine = NativeMeshControl.create("b", target_size=0.5, size_mode="refine")
    assert refine.to_dict()["size_mode"] == "refine"
    assert NativeMeshControl.from_dict(refine.to_dict()) == refine
    # parameters-only controls carry no size semantics
    params = NativeMeshControl.create("p", parameters={"recombine": True})
    assert params.size_mode == "refine" and "size_mode" not in params.to_dict()
    with pytest.raises(ValueError):
        NativeMeshControl.create("c", size_mode="override")  # no size to own
    with pytest.raises(ValueError):
        NativeMeshControl.create("d", target_size=1.0, size_mode="sideways")


def _counts(session, project, feature, other_face):
    assert session.runtime.wait_for_idle(180.0)
    published = session.runtime.publications()
    meshes = [item.mesh for item in published.values()]
    faces = [ref.id for ref in feature.outputs.values() if ref.kind == "face"]
    shells = sum(
        len(mesh.elements_of_face.get(face, ())) for mesh in meshes for face in faces
    )
    beams = sum(len(mesh.beams) for mesh in meshes)
    unrelated = sum(len(mesh.elements_of_face.get(other_face, ())) for mesh in meshes)
    return shells, beams, unrelated


def _unrelated_mesh(session, other_face):
    for item in session.runtime.publications().values():
        if item.mesh.elements_of_face.get(other_face):
            return item.mesh
    return None


def test_incremental_session_follows_generator_sizes_up_down_and_away():
    """Real session: .5 then 2 then none gives fresh counts; other plates unchanged."""
    from anyfem.generator_properties import reapply_generator_properties
    from anyfem.native_meshing import NativeMeshSettings
    from anyfem.native_meshing_backend import create_native_meshing_session

    project = default_project()
    # 8 x 4 panel, stiffener spacing 2: its stations are not pinned at the
    # global size, so a local size of 2 genuinely reduces it (the small 4 x 3,
    # spacing-1 panel keeps 12 shells at 2 and at an equivalent global 2).
    feature = commands.AddStiffenedPanel(
        8, 4, 2, beam_section="stiffener"
    ).do(project)
    other_face = project.geometry.add_plate(
        project.geometry.add_points(
            ((0.0, 10.0, 0.0), (2.0, 10.0, 0.0), (2.0, 12.0, 0.0), (0.0, 12.0, 0.0))
        )
    )
    with create_native_meshing_session(
        project, NativeMeshSettings.create(1.0, backend="mapped")
    ) as session:
        session.request_remesh()
        base = _counts(session, project, feature, other_face)
        assert base[0] > 0 and base[1] > 0 and base[2] > 0

        def set_size(size):
            options = project.generator_properties.setdefault(feature.feature_id, {})
            if size is None:
                options.pop("mesh_size", None)
            else:
                options["mesh_size"] = size
            reapply_generator_properties(project)
            session.refresh_generator_controls()

        original = _unrelated_mesh(session, other_face)
        assert original is not None

        set_size(0.5)
        fine = _counts(session, project, feature, other_face)
        assert fine[0] > base[0] and fine[1] > base[1] and fine[2] == base[2]
        # the unrelated plate's actual mesh payload is the very object published
        # before: a generator edit does not regenerate it
        assert _unrelated_mesh(session, other_face) is original
        set_size(2.0)
        coarse = _counts(session, project, feature, other_face)
        assert coarse[0] < base[0] and coarse[1] < base[1] and coarse[2] == base[2]
        assert _unrelated_mesh(session, other_face) is original
        set_size(None)
        assert _counts(session, project, feature, other_face) == base
        assert _unrelated_mesh(session, other_face) is original


def test_small_constrained_panel_local_size_equals_the_equivalent_global_size():
    """Local 2 on the 4 x 3 / spacing-1 panel equals a global 2 (not smaller)."""
    from anyfem.native_meshing import NativeMeshSettings
    from anyfem.native_meshing_backend import create_native_meshing_session

    def run(global_size, local_size):
        project = default_project()
        feature = panel(project, beam_section="stiffener", mesh_size=local_size)
        face = _plate_at(project, 10.0)
        with create_native_meshing_session(
            project, NativeMeshSettings.create(global_size, backend="mapped")
        ) as session:
            session.request_remesh()
            return _counts(session, project, feature, face)

    local, equivalent = run(1.0, 2.0), run(2.0, None)
    # the panel itself (shells, beams) is what the equivalent global gives
    assert local[:2] == equivalent[:2]


def test_ordinary_scoped_target_size_control_is_honoured_above_global():
    from anyfem.native_meshing import NativeMeshSettings
    from anyfem.native_meshing_backend import create_native_meshing_session

    project = default_project()
    feature = panel(project, beam_section="stiffener")
    other_face = project.geometry.add_plate(
        project.geometry.add_points(
            ((0.0, 10.0, 0.0), (2.0, 10.0, 0.0), (2.0, 12.0, 0.0), (0.0, 12.0, 0.0))
        )
    )
    handle = project.geometry.handle("face", other_face)
    control = NativeMeshControl.create(
        "coarse-plate", scope=ControlScope.create((handle,)), target_size=2.0
    )
    with create_native_meshing_session(
        project, NativeMeshSettings.create(1.0, backend="mapped", controls=(control,))
    ) as session:
        session.request_remesh()
        shells, beams, unrelated = _counts(session, project, feature, other_face)
    plain_project = default_project()
    plain_feature = panel(plain_project, beam_section="stiffener")
    plain_face = plain_project.geometry.add_plate(
        plain_project.geometry.add_points(
            ((0.0, 10.0, 0.0), (2.0, 10.0, 0.0), (2.0, 12.0, 0.0), (0.0, 12.0, 0.0))
        )
    )
    with create_native_meshing_session(
        plain_project, NativeMeshSettings.create(1.0, backend="mapped")
    ) as plain:
        plain.request_remesh()
        base = _counts(plain, plain_project, plain_feature, plain_face)
    assert unrelated < base[2]  # the scoped plate is coarser than global
    assert (shells, beams) == (base[0], base[1])  # nothing else moved


def _plate_at(project, y):
    return project.geometry.add_plate(
        project.geometry.add_points(
            ((0.0, y, 0.0), (2.0, y, 0.0), (2.0, y + 2.0, 0.0), (0.0, y + 2.0, 0.0))
        )
    )


def test_scoped_runtime_update_keeps_other_components_and_cancels_the_affected_one():
    import threading
    from dataclasses import replace
    from anyfem.native_meshing import (
        NativeMeshResult, NativeMeshSettings, NativeMeshingRuntime,
    )

    project = default_project()
    a, b = (project.geometry.handle("face", _plate_at(project, y)) for y in (10.0, 20.0))
    held, release = threading.Event(), threading.Event()
    calls: dict = {a: 0, b: 0}

    def generate(request):
        calls[request.component] += 1
        sizes = tuple(sorted((c.control_id, c.target_size) for c in request.controls))
        if request.component == a and calls[a] == 2:
            held.set()
            release.wait(30.0)
        request.cancellation.raise_if_cancelled("test")
        return NativeMeshResult({"component": request.component, "sizes": sizes})

    settings = NativeMeshSettings.create(1.0, backend="mapped")
    with NativeMeshingRuntime(
        settings, resolve_dirty_components=lambda changes: (),
        generate_component=generate, capture_component=lambda handle: handle,
    ) as runtime:
        runtime.request_remesh((a, b))
        assert runtime.wait_for_idle(30.0)
        untouched = runtime.published_mesh(b)
        generation = runtime.control_generation
        runtime.request_remesh(a)  # this A job (call 2) is held in flight
        assert held.wait(30.0)
        scoped = replace(settings, controls=(
            NativeMeshControl.create("c", scope=ControlScope.create((a,)), target_size=0.5),
        ))
        assert runtime.set_settings_scoped(scoped, [a]) == (a,)
        assert runtime.control_generation == generation  # no global invalidation
        release.set()
        assert runtime.wait_for_idle(30.0)
        # the in-flight old-target job never published; the re-issued one did
        assert runtime.published_mesh(a)["sizes"] == (("c", 0.5),)
        # the unaffected component kept its publication and was not regenerated
        assert runtime.published_mesh(b) is untouched and calls[b] == 1
        # a scoped update may not smuggle in a non-control change
        with pytest.raises(ValueError, match="only the controls"):
            runtime.set_settings_scoped(replace(scoped, target_size=2.0), [a])
        # the public global route keeps its contract: new generation, everything remeshed
        newer = replace(scoped, controls=(
            NativeMeshControl.create("c", scope=ControlScope.create((a,)), target_size=2.0),
        ))
        assert runtime.set_settings(newer) == generation + 1
        assert runtime.wait_for_idle(30.0)
        assert calls[b] == 2 and runtime.published_mesh(a)["sizes"] == (("c", 2.0),)


def test_scoped_update_invalidates_affected_jobs_atomically_with_the_settings_change():
    """An old affected job that finishes between the settings update and the
    deferred scheduling must not publish (deterministic, no sleeps)."""
    import threading
    from dataclasses import replace
    from anyfem.native_meshing import (
        NativeMeshResult, NativeMeshSettings, NativeMeshingRuntime,
    )

    project = default_project()
    a = project.geometry.handle("face", _plate_at(project, 10.0))
    held, release = threading.Event(), threading.Event()
    calls = {"a": 0}

    def generate(request):
        calls["a"] += 1
        sizes = tuple(sorted((c.control_id, c.target_size) for c in request.controls))
        if calls["a"] == 2:
            held.set()
            assert release.wait(30.0)
            # Deliberately NO cancellation check here: the old job returns a
            # result, so only the runtime's generation guard can stop it.
        return NativeMeshResult({"sizes": sizes})

    settings = NativeMeshSettings.create(1.0, backend="mapped")
    with NativeMeshingRuntime(
        settings, resolve_dirty_components=lambda changes: (),
        generate_component=generate, capture_component=lambda handle: handle,
    ) as runtime:
        runtime.request_remesh(a)
        assert runtime.wait_for_idle(30.0)
        original = runtime.published_mesh(a)
        runtime.request_remesh(a)  # call 2: held in flight with the OLD settings
        assert held.wait(30.0)

        schedule = runtime._schedule_components
        seen: dict = {}

        def intercepted(components, **options):
            # The boundary between the settings update and the (slow) scheduling:
            # let the old job finish and publish if it is allowed to, and wait
            # until the runtime has processed it.
            release.set()
            assert runtime.wait_for_idle(30.0)
            seen["published"] = runtime.published_mesh(a)
            return schedule(components, **options)

        runtime._schedule_components = intercepted
        scoped = replace(settings, controls=(
            NativeMeshControl.create("c", scope=ControlScope.create((a,)), target_size=0.5),
        ))
        assert runtime.set_settings_scoped(scoped, [a]) == (a,)
        # nothing from the old target was published in the gap
        assert seen["published"] is original
        runtime._schedule_components = schedule
        assert runtime.wait_for_idle(30.0)
        assert runtime.published_mesh(a)["sizes"] == (("c", 0.5),)


def test_missing_override_entity_is_refused_by_the_project_before_meshing():
    from anyfem.model.project import ProjectError
    from anyfem.native_meshing import NativeMeshSettings

    project = default_project()
    face = _plate_at(project, 10.0)
    project.set_native_mesh_settings(NativeMeshSettings.create(1.0, backend="mapped"))
    revision = project.geometry.revision
    project.refinements.append(Refinement(
        size=0.5, ref=EntityRef("face", face), name="valid", mode="override"))
    project.refinements.append(Refinement(
        size=0.5, ref=EntityRef("face", 999999), name="ghost", mode="override"))
    with pytest.raises(ProjectError, match="999999"):
        project.generate_mesh()
    assert project.geometry.revision == revision  # source untouched


def test_generator_controls_fail_closed_on_malformed_sizes():
    from anyfem.generator_properties import GeneratorControlError

    project = default_project()
    feature = panel(project, mesh_size=1.5)
    assert len(generator_mesh_controls(project)) == 1
    project.generator_properties[feature.feature_id]["mesh_size"] = -1.0
    with pytest.raises(GeneratorControlError):
        generator_mesh_controls(project)


def test_sized_override_controls_select_the_guarded_format_and_refine_stays_old():
    from anyfem.native_meshing import NativeMeshSettings

    project = default_project()
    plain = project_to_dict(project)["anyfem"]["format"]

    def with_control(**options):
        item = default_project()
        item.set_native_mesh_settings(NativeMeshSettings.create(
            1.0, controls=(NativeMeshControl.create("c", **options),)))
        return item

    sized = with_control(target_size=2.0)  # default override
    data = project_to_dict(sized)
    assert data["anyfem"]["format"] == ENTITY_SIZES_FORMAT
    reloaded = project_from_dict(deepcopy(data))
    assert reloaded.native_mesh_settings.controls[0].size_mode == "override"
    assert project_to_dict(reloaded)["anyfem"]["format"] == ENTITY_SIZES_FORMAT
    refine = with_control(target_size=0.5, size_mode="refine")
    refine_data = project_to_dict(refine)
    assert refine_data["anyfem"]["format"] == plain
    assert project_from_dict(refine_data).native_mesh_settings.controls[0].size_mode == "refine"
    params = with_control(parameters={"recombine": True})
    assert project_to_dict(params)["anyfem"]["format"] == plain
    # an older file with a sized control loads under the corrected intent and
    # its next save advertises the guarded format
    old = deepcopy(data)
    old["anyfem"]["format"] = plain
    migrated = project_from_dict(old)
    assert migrated.native_mesh_settings.controls[0].size_mode == "override"
    assert project_to_dict(migrated)["anyfem"]["format"] == ENTITY_SIZES_FORMAT


def test_generator_sizes_are_mirrored_as_scoped_override_controls():
    project = default_project()
    feature = panel(project, mesh_size=1.5)
    controls = generator_mesh_controls(project)
    assert [item.control_id for item in controls] == [f"generator:{feature.feature_id}:mesh"]
    control = controls[0]
    assert control.target_size == 1.5 and control.size_mode == "override"
    assert {handle.kind for handle in control.scope.handles} == {"face", "edge"}
    assert len(control.scope.handles) == 5
    refs = {EntityRef(handle.kind, handle.id) for handle in control.scope.handles}
    assert refs == {item.ref for item in tagged(project, feature.feature_id)}
