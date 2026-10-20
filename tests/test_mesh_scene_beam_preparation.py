"""Build-local beam preparation preserves the original scalar sweep contract."""
from dataclasses import fields, is_dataclass, replace

import numpy as np
import pytest

from anyfem.mesh.mapped import Mesh
from anyfem.model.materials import steel
from anyfem.model.project import Project
from anyfem.model.sections import BeamSection
from anyfem.ui import scene as module


def section(profile="T-bar", **options):
    return BeamSection("beam", profile, "S355", web_height=.23,
                       web_thickness=.013, flange_width=.17,
                       flange_thickness=.021, **options)


def identity(value):
    if isinstance(value, np.ndarray):
        return value.dtype.str, value.shape, value.tobytes()
    if is_dataclass(value):
        return type(value), tuple((f.name, identity(getattr(value, f.name))) for f in fields(value))
    if isinstance(value, (list, tuple)):
        return type(value), tuple(map(identity, value))
    if isinstance(value, dict):
        return tuple((key, identity(item)) for key, item in value.items())
    return value


@pytest.mark.parametrize("profile", ["Flatbar", "T-bar", "Angle", "L-bulb"])
@pytest.mark.parametrize("reference", [(0., 0., 1.), (1., 0., 0.), (1e-15, 1., 1e-15)])
def test_prepared_sweep_matches_scalar_polygons_exactly(profile, reference):
    rng = np.random.default_rng(527)
    points = np.vstack((np.zeros((2, 3)), [1., 0., 0.], rng.normal(size=(5, 3))))
    shape = section(profile)
    expected = module._beam_section_polygons(shape, points, reference)
    actual = module._beam_section_polygons(module._PreparedBeamSection(shape), points, reference)
    assert identity(actual) == identity(expected)
    assert all(polygon.flags.owndata for polygon in actual)
    snapshots = [polygon.copy() for polygon in actual[1:]]
    actual[0][:] = 99.
    assert identity(actual[1:]) == identity(snapshots)
    assert not np.shares_memory(actual[0], points)


def fixture(project_type=Project):
    project = project_type()
    project.add_material(steel("S355", .01))
    project.add_beam_section(section(web_direction=(0., 0., 1.)))
    a, b, c = [project.geometry.add_point(x, 0., 0.) for x in (0., 2., 4.)]
    first = project.geometry.add_line(a, b)
    second = project.geometry.add_line(b, c)
    project.edge_sections.update({first: "beam", second: "beam"})
    mesh = Mesh(nodes={i + 1: np.array(point, float) for i, point in enumerate([
        (0., 0., 0.), (.5, .1, .2), (1., 0., 0.), (1.5, -.1, .1),
        (2., 0., 0.), (3., 0., 0.), (4., 0., 0.)])},
        beams={11: (1, 2, 3), 12: (3, 4, 5), 13: (5, 6, 7), 14: (1, 1)},
        elements_of_edge={first: [11, 12, 999], second: [13, 14]})
    return project, mesh, first


def legacy_scene(project, mesh, monkeypatch, **options):
    with monkeypatch.context() as patch:
        patch.setattr(module, "_can_prepare_mesh_beam_edge", lambda *args: False)
        return module.build_mesh_scene(project, mesh, **options)


def test_public_scene_matches_legacy_and_prepares_once_per_edge(monkeypatch):
    project, mesh, _ = fixture()
    made = []
    original = module._PreparedBeamSection.__init__

    def create(self, value):
        original(self, value)
        made.append(self)

    monkeypatch.setattr(module._PreparedBeamSection, "__init__", create)
    actual = module.build_mesh_scene(project, mesh)
    expected = legacy_scene(project, mesh, monkeypatch)
    assert identity(actual) == identity(expected)
    assert len(made) == 2
    assert len(actual.faces) == 3 and len(actual.lines) == 4
    assert actual.lines[-1].width == 4  # all-degenerate beam has no solid
    assert all(len(line.points) == 3 for line in actual.lines[:3])


@pytest.mark.parametrize("change", ["section", "nodes", "assignment"])
def test_build_local_preparation_observes_mutation_between_calls(change, monkeypatch):
    project, mesh, edge = fixture()
    before = module.build_mesh_scene(project, mesh)
    if change == "section":
        project.beam_sections["beam"] = replace(project.beam_sections["beam"],
                                                rotation_deg=33., attachment_side="back")
    elif change == "nodes":
        mesh.nodes[2][2] += .6
    else:
        project.edge_sections.pop(edge)
    after = module.build_mesh_scene(project, mesh)
    assert identity(after) != identity(before)
    assert identity(after) == identity(legacy_scene(project, mesh, monkeypatch))


def test_custom_frame_callback_keeps_per_element_calls(monkeypatch):
    project, mesh, _ = fixture()
    calls = []
    original = project.beam_section_frame

    def frame(edge):
        calls.append(edge)
        axis, y, z = original(edge)
        return axis, y, z if len(calls) % 2 else y

    project.beam_section_frame = frame
    actual = module.build_mesh_scene(project, mesh)
    assert len(calls) == 4
    calls.clear()
    assert identity(actual) == identity(legacy_scene(project, mesh, monkeypatch))
    assert len(calls) == 4


def test_custom_section_profile_keeps_segment_callback_order(monkeypatch):
    calls = []

    class CustomSection(BeamSection):
        def centered_profile_rectangles(self):
            calls.append(None)
            y, z, width, height = super().centered_profile_rectangles()[0]
            return ((y + len(calls) * .01, z, width, height),)

    project, mesh, _ = fixture()
    project.beam_sections["beam"] = CustomSection("beam", "Flatbar", "S355",
                                                 flange_width=.2, flange_thickness=.03,
                                                 web_direction=(0., 0., 1.))
    actual = module.build_mesh_scene(project, mesh)
    assert len(calls) == 6
    calls.clear()
    assert identity(actual) == identity(legacy_scene(project, mesh, monkeypatch))
    assert len(calls) == 6


@pytest.mark.parametrize("method", ["_sample_edge_object", "face_point"])
def test_transitive_geometry_callbacks_remain_per_element(method, monkeypatch):
    project, mesh, _ = fixture()
    if method == "face_point":
        project.beam_sections["beam"] = replace(project.beam_sections["beam"],
                                                web_direction=None)
        vertices = project.geometry.add_points(
            [(-1., -1., 0.), (5., -1., 0.), (5., 1., 0.), (-1., 1., 0.)])
        project.geometry.add_face(project.geometry.add_polyline(vertices, close=True))
    calls = []
    original = getattr(project.geometry, method)

    def callback(*args, **kwargs):
        calls.append(None)
        return original(*args, **kwargs)

    setattr(project.geometry, method, callback)
    actual = module.build_mesh_scene(project, mesh)
    count = len(calls)
    assert count > 0
    if method == "_sample_edge_object":
        assert count == 4
    calls.clear()
    assert identity(actual) == identity(legacy_scene(project, mesh, monkeypatch))
    assert len(calls) == count


@pytest.mark.parametrize("field", ["web_height", "rotation_deg", "profile", "attachment_side"])
def test_custom_section_scalars_keep_legacy_callbacks(field, monkeypatch):
    calls = []

    class Number(float):
        def __float__(self):
            calls.append(None)
            return super().__float__()

    class Text(str):
        def __eq__(self, other):
            calls.append(None)
            return super().__eq__(other)

        __hash__ = str.__hash__

    project, mesh, _ = fixture()
    shape = project.beam_sections["beam"]
    value = getattr(shape, field)
    object.__setattr__(shape, field, Text(value) if isinstance(value, str) else Number(value))
    actual = module.build_mesh_scene(project, mesh)
    count = len(calls)
    assert count > 0
    calls.clear()
    assert identity(actual) == identity(legacy_scene(project, mesh, monkeypatch))
    assert len(calls) == count


def test_nondefault_numpy_error_callbacks_keep_scalar_profile_visits(monkeypatch):
    project, mesh, _ = fixture()
    project.beam_sections["beam"] = BeamSection(
        "beam", "Flatbar", "S355", flange_width=1e-200,
        flange_thickness=1e-200, web_direction=(0., 0., 1.))
    events = []
    previous = np.geterrcall()
    try:
        np.seterrcall(lambda error, flag: events.append((error, flag)))
        with np.errstate(all="call"):
            actual = module.build_mesh_scene(project, mesh)
            expected_events = list(events)
            assert expected_events
            events.clear()
            expected = legacy_scene(project, mesh, monkeypatch)
            assert events == expected_events
            assert identity(actual) == identity(expected)
    finally:
        np.seterrcall(previous)


def test_custom_assignment_key_keeps_per_element_lookups(monkeypatch):
    calls = []

    class Key(str):
        def __hash__(self):
            calls.append(None)
            return super().__hash__()

    project, mesh, _ = fixture()
    project.edge_sections = {edge: Key(name) for edge, name in project.edge_sections.items()}
    actual = module.build_mesh_scene(project, mesh)
    count = len(calls)
    assert count > 0
    calls.clear()
    assert identity(actual) == identity(legacy_scene(project, mesh, monkeypatch))
    assert len(calls) == count


@pytest.mark.parametrize("kind", ["section", "edge"])
@pytest.mark.parametrize("fail_at", [None, 4])
def test_stored_mapping_key_callbacks_and_late_errors_keep_order(kind, fail_at, monkeypatch):
    outcomes = []
    for legacy in (False, True):
        calls = []

        def equal(value, other):
            calls.append(other)
            if len(calls) == fail_at:
                raise RuntimeError("stored key callback failed")
            return str.__eq__(value, other) if kind == "section" else int.__eq__(value, other)

        class SectionKey(str):
            __eq__ = equal
            __hash__ = str.__hash__

        class EdgeKey(int):
            __eq__ = equal
            __hash__ = int.__hash__

        project, mesh, _ = fixture()
        if kind == "section":
            project.beam_sections = {
                SectionKey(name): value for name, value in project.beam_sections.items()}
        else:
            project.edge_sections = {
                EdgeKey(edge): value for edge, value in project.edge_sections.items()}
        if fail_at is None:
            scene = (legacy_scene(project, mesh, monkeypatch) if legacy
                     else module.build_mesh_scene(project, mesh))
            outcomes.append((identity(scene), list(calls)))
        else:
            with pytest.raises(RuntimeError, match="stored key callback failed"):
                (legacy_scene(project, mesh, monkeypatch) if legacy
                 else module.build_mesh_scene(project, mesh))
            outcomes.append(list(calls))
        assert calls
    assert outcomes[0] == outcomes[1]


def test_hidden_and_missing_beams_do_not_prepare_sections(monkeypatch):
    project, mesh, _ = fixture()
    project.beam_section_frame = lambda edge: pytest.fail("unexpected frame preparation")
    module.build_mesh_scene(project, mesh, show_beam_sections=False)
    mesh.beams.clear()
    module.build_mesh_scene(project, mesh)


def test_missing_geometry_edge_keeps_refusal(monkeypatch):
    project, mesh, _ = fixture()
    mesh.elements_of_edge = {9999: [11]}
    project.edge_sections[9999] = "beam"
    errors = []
    for legacy in (False, True):
        with pytest.raises(Exception) as caught:
            (legacy_scene(project, mesh, monkeypatch) if legacy
             else module.build_mesh_scene(project, mesh))
        errors.append((type(caught.value), str(caught.value)))
    assert errors[0] == errors[1]


def test_zero_length_segments_do_not_evaluate_profile():
    class Never:
        def centered_profile_rectangles(self):
            pytest.fail("zero-length segment evaluated profile")

    prepared = module._PreparedBeamSection(Never())
    assert module._beam_section_polygons(prepared, np.zeros((3, 3)), (0., 0., 1.)) == []
    assert prepared.corners is None
