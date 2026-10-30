"""The 60 m free-floating barge example: closure, ballast, sea identity, roundtrip and safe output.

No real mesh, equilibrium or BEM is started here; ``--model`` runs with a mock mesh and mass model,
and the real ``--model`` and ``--prepare`` routes are parent-run.
"""
import importlib.util
import json
import math
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "free_floating_barge_60m.py"
STRUCTURE = 90000.


def _load():
    spec = importlib.util.spec_from_file_location("free_floating_barge_60m_example", EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def example():
    return _load()


@pytest.fixture(scope="module")
def built(example):
    return example.build_free_floating_project()


@pytest.fixture(scope="module")
def base(example, tmp_path_factory):
    directory = tmp_path_factory.mktemp("barge60")
    return directory, example.write_base(directory)


def _names(records):
    return sorted(records) if isinstance(records, dict) else sorted(getattr(r, "name", repr(r)) for r in records)


def _loads(project):
    return sum(len(getattr(case, kind)) for case in project.load_cases.values()
               for kind in ("point_loads", "pressures", "line_loads", "surface_tractions", "wet_pressures",
                            "connection_loads"))


def _corners(project, face_id):
    geometry = project.geometry
    return [tuple(float(x) for x in geometry.vertex_position(v)) for v in geometry.face_corner_vertices(face_id)]


def _mock_mass(project, mesh):
    """A small mass model: structure plus every project Mass, as a rebuilt FE model would report."""
    total = STRUCTURE + sum(mass.value for mass in project.masses)
    return SimpleNamespace(mass=total, cg=np.array([30., 0., 1.]), inertia_cg=np.eye(3))


def _mock_mesh():
    return SimpleNamespace(nodes={1: (0., 0., 0.)}, quads={1: (1, 2, 3, 4)}, tris={}, beams={})


def _model(example, directory, monkeypatch, **options):
    monkeypatch.setattr(example, "mesh_identity", lambda mesh: "mock-mesh")
    options = {"element_order": "linear", **options}  # publication fixture uses an explicit Q4 stub
    return example.write_model(directory, properties=_mock_mass, mesher=lambda project, check: _mock_mesh(), **options)


def test_structure_is_preserved_and_demonstration_loads_are_removed(example, built):
    from anyfem.application.examples import drawing_barge_60m
    project, faces = built
    original = drawing_barge_60m()
    transoms = {faces[name] for name in example.TRANSOMS}
    assert original.supports and original.load_cases and original.analyses       # what was removed existed
    assert set(project.geometry.faces) - transoms == set(original.geometry.faces)
    assert set(project.geometry.edges) == set(original.geometry.edges)
    assert set(project.geometry.vertices) == set(original.geometry.vertices)
    assert {k: v for k, v in dict(project.face_sections).items() if k not in transoms} == dict(original.face_sections)
    assert dict(project.edge_sections) == dict(original.edge_sections)
    for kind in ("materials", "plate_sections", "beam_sections"):
        assert _names(getattr(project, kind)) == _names(getattr(original, kind))
    assert len(project.masses) == len(original.masses)                            # the base model is unballasted
    assert not project.supports and not project.connections
    assert _loads(original) > 0 and _loads(project) == 0
    assert not project.load_cases and not project.analyses and not project.combinations


def test_original_generator_keeps_its_settings(example, built):
    from anyfem.application.examples import drawing_barge_60m
    project, _ = built
    stored, original = project.native_mesh_settings.to_dict(), drawing_barge_60m().native_mesh_settings.to_dict()
    assert (original["target_size"], original["element_order"]) == (.5, "quadratic")
    assert (stored["target_size"], stored["element_order"]) == (.5, "quadratic")
    assert stored["backend"] == original["backend"] == "quad_first"
    assert stored["parameters"] == original["parameters"]


def test_transoms_close_the_hull_on_shared_edges(example, built):
    from anyfem.application.floating_equilibrium_process import envelope_document
    project, faces = built
    geometry = project.geometry
    for name, x in zip(example.TRANSOMS, (0., 60.)):
        assert project.face_sections[faces[name]] == "Hull 6 mm"
        corners = _corners(project, faces[name])
        assert {c[0] for c in corners} == {x}
        assert {(c[1], c[2]) for c in corners} == {(-2.5, 0.), (2.5, 0.), (2.5, 2.), (-2.5, 2.)}
        for item in geometry.faces[faces[name]].loop:                             # every edge joins a hull plate
            assert set(geometry.faces_using_edge(item.edge)) & {faces[n] for n in ("Bottom", "Deck", "Port side",
                                                                                   "Starboard side")}
    envelope = envelope_document(project, [faces["Deck"]])
    assert envelope["volume"] == pytest.approx(600., rel=1.e-12)
    assert example.closed_envelope(project, faces)["volume_m3"] == envelope["volume"]


def test_the_open_hull_is_refused_by_the_owner(example, built):
    from anyfem.application.floating_equilibrium_process import FloatingEquilibriumError, envelope_document
    project, faces = built
    wet = project.wet_surfaces
    open_hull = SimpleNamespace(geometry=project.geometry,
                                wet_surfaces=[s for s in wet if int(s.ref.id) != faces[example.TRANSOMS[1]]])
    with pytest.raises(FloatingEquilibriumError, match="not closed"):
        envelope_document(open_hull, [faces["Deck"]])


def test_wet_hull_is_the_exterior_shell_only(example, built):
    project, faces = built
    assert len(set(faces.values())) == 6
    assert {round(c[2], 9) for c in _corners(project, faces["Bottom"])} == {0.}
    assert {round(c[1], 9) for c in _corners(project, faces["Port side"])} == {-2.5}
    assert {round(c[1], 9) for c in _corners(project, faces["Starboard side"])} == {2.5}
    assert {round(c[2], 9) for c in _corners(project, faces["Deck"])} == {2.}
    wet = {int(s.ref.id): s for s in project.wet_surfaces}
    assert set(wet) == {faces[name] for name in example.WET_FACES} and len(wet) == 5
    for surface in wet.values():
        assert getattr(surface.ref.kind, "value", surface.ref.kind) == "face"
        assert surface.fluid_side == "away_from_point"
        assert [float(x) for x in surface.interior_point] == [30., 0., 1.]
    # internal bulkheads, centreline bulkhead, deck and end houses are dry
    half_breadth = example.HULL_BREADTH / 2.
    planes = ((2, 0.), (1, -half_breadth), (1, half_breadth), (0, 0.), (0, example.HULL_LENGTH))
    for identifier in set(project.geometry.faces) - set(wet):
        corners = _corners(project, identifier)
        in_hull = all(0. <= c[0] <= example.HULL_LENGTH and abs(c[1]) <= half_breadth
                      and 0. <= c[2] <= example.HULL_DEPTH for c in corners)
        assert not (in_hull and any(all(c[axis] == value for c in corners) for axis, value in planes)), identifier
    assert len(project.geometry.faces) - len(wet) >= 1 + 4 + 10      # deck, 3+1 bulkheads, 2x5 end-house faces


def test_ambiguous_or_missing_hull_plates_refuse(example, built, monkeypatch):
    project, _ = built
    monkeypatch.setattr(example, "HULL_LENGTH", 61.)
    with pytest.raises(ValueError, match="not unique"):
        example.locate_hull_faces(project)


def test_ballast_reaches_the_target_through_a_rebuilt_mass_model(example):
    project, faces = example.build_free_floating_project()
    ballast = example.add_ballast(project, faces, lambda: _mock_mass(project, None))
    assert example.TARGET_DISPLACEMENT == 307500.
    assert ballast["ballast_mass_kg"] == 307500. - STRUCTURE and ballast["total_mass_kg"] == 307500.
    assert ballast["structural_mass_kg"] == STRUCTURE and ballast["distribution"] == "total_distributed"
    mass = project.masses[-1]
    assert getattr(mass.ref.kind, "value", mass.ref.kind) == "face" and int(mass.ref.id) == faces["Bottom"]
    assert mass.value == 307500. - STRUCTURE and mass.distribution_policy == "total_distributed"


def test_ballast_refuses_a_heavy_structure_and_an_unverified_total(example):
    project, faces = example.build_free_floating_project()
    heavy = SimpleNamespace(mass=4.e5, cg=(0., 0., 0.), inertia_cg=np.eye(3))
    with pytest.raises(ValueError, match="no positive ballast"):
        example.add_ballast(project, faces, lambda: heavy)
    assert not project.masses
    constant = SimpleNamespace(mass=STRUCTURE, cg=(0., 0., 0.), inertia_cg=np.eye(3))     # the mass was not assembled
    with pytest.raises(ValueError, match="misses"):
        example.add_ballast(project, faces, lambda: constant)


def test_mesh_summary_reports_the_real_order_and_refuses_a_mismatch(example, monkeypatch):
    monkeypatch.setattr(example, "mesh_identity", lambda mesh: "mock-mesh")
    assert example.mesh_summary(_mock_mesh(), "linear")["effective_element_order"] == "linear"
    with pytest.raises(ValueError, match="requested quadratic"):
        example.mesh_summary(_mock_mesh(), "quadratic")


def test_mean_pose_report_uses_the_owner_pose(example):
    trim = math.radians(.1)
    rotation = [[math.cos(trim), 0., math.sin(trim)], [0., 1., 0.], [-math.sin(trim), 0., math.cos(trim)]]
    report = example.mean_pose_report(dict(rotation_body_to_world=rotation, origin_z_m=-.9))
    assert report["keel_draft_m"]["X0"] == pytest.approx(.9)
    assert report["keel_draft_m"]["X60"] == pytest.approx(.9 + 60. * math.sin(trim))
    assert report["pitch_about_y_deg"] == pytest.approx(.1) and report["roll_about_x_deg"] == pytest.approx(0.)


def test_sea_defaults_and_overrides(example):
    wave = example.sea_input()
    assert (wave.significant_height, wave.peak_period, wave.gamma, wave.seed, wave.heading) == (1., 6., 3.3, 0, 0.)
    assert (wave.duration, wave.dt, wave.depth, wave.component_count) == (120., .1, 100., 32)
    assert not wave.follow_surface and not wave.include_hydrostatic
    assert len(wave.times) == 1201 and wave.times[-1] == 120.
    other = example.sea_input(significant_height=2., peak_period=8., seed=4, component_count=40)
    assert (other.significant_height, other.peak_period, other.seed, other.component_count) == (2., 8., 4, 40)


def test_realization_is_reproducible_and_seed_dependent(example, built):
    project, faces = built
    options = example.floating_options(faces)
    first = example.wave_request(project, faces, example.sea_input(), options)
    again = example.wave_request(project, faces, example.sea_input(), options)
    seeded = example.wave_request(project, faces, example.sea_input(seed=1), options)
    assert first["realization_digest"] == again["realization_digest"] != seeded["realization_digest"]
    assert len(first["components"]) == 32 and first["sample_count"] == 1201
    assert all(math.isclose(c["heading"], 0.) for c in first["components"])
    assert all(c["amplitude"] > 0. for c in first["components"])
    assert first["floating"]["envelope_extra_face_ids"] == [faces["Deck"]]
    assert first["floating"]["max_panels"] == 600 and "propagation" in first["heading_convention"]
    assert first["free_free_batch"] == {"constraint_mode": "nullspace", "inertia_relief": True,
                                        "allow_unbalanced_free_free": False}
    assert not {"equilibrium_python", "owner_wheel", "stability_source"} & set(first["floating"])


@pytest.mark.parametrize("bad", [
    dict(significant_height=-1.), dict(peak_period=0.), dict(gamma=.5), dict(depth=1.), dict(dt=0.),
    dict(duration=-1.), dict(seed=-1), dict(seed=1.5), dict(component_count=0), dict(component_count=201),
    dict(significant_height=float("nan")), dict(heading=float("inf")), dict(dt=200.), dict(spreading=1.),
])
def test_invalid_sea_values_refuse(example, bad):
    with pytest.raises(ValueError):
        example.sea_input(**bad)


def test_cli_parses_overrides_and_rejects_invalid_sea(example, tmp_path, capsys):
    arguments = example.parse_arguments([str(tmp_path), "--hs", "1.5", "--tp", "7", "--seed", "3",
                                         "--components", "16", "--depth", "80"])
    wave = example.sea_from_arguments(arguments)
    assert (wave.significant_height, wave.peak_period, wave.seed, wave.component_count, wave.depth) == (1.5, 7., 3, 16, 80.)
    assert not arguments.prepare and not arguments.model and not arguments.overwrite
    assert not arguments.allow_development
    assert example.main([str(tmp_path / "bad"), "--hs", "-1"]) == 2
    assert not (tmp_path / "bad").exists()
    with pytest.raises(SystemExit):
        example.parse_arguments([str(tmp_path), "--model", "--prepare"])


def test_runtime_defaults_come_only_from_the_documented_environment(example, tmp_path, monkeypatch):
    for key in ("EQUILIBRIUM_PYTHON", "OWNER_WHEEL", "STABILITY_SOURCE"):
        monkeypatch.delenv(f"ANYFEM_FLOATING_{key}", raising=False)
    arguments = example.parse_arguments([str(tmp_path)])
    assert (arguments.equilibrium_python, arguments.owner_wheel, arguments.stability_source) == ("", "", "")
    monkeypatch.setenv("ANYFEM_FLOATING_OWNER_WHEEL", "wheel.whl")
    assert example.parse_arguments([str(tmp_path)]).owner_wheel == "wheel.whl"


def test_base_project_roundtrip_and_pending_ballast(example, base):
    from anyfem.io.project_file import load_project
    directory, (project, faces, request, paths) = base
    assert {p.name for p in directory.iterdir()} == \
        {example.FILES[key] for key in ("base_project", "request", "status")}
    reopened = load_project(paths["base_project"])
    assert reopened.name == example.PROJECT_NAME
    assert not reopened.supports and not reopened.connections and _loads(reopened) == 0 and not reopened.analyses
    assert sorted((int(s.ref.id), s.fluid_side, [float(x) for x in s.interior_point]) for s in reopened.wet_surfaces) == \
        sorted((int(s.ref.id), s.fluid_side, [float(x) for x in s.interior_point]) for s in project.wet_surfaces)
    assert dict(reopened.face_sections) == dict(project.face_sections)
    assert not reopened.masses
    assert example.closed_envelope(reopened, faces)["volume_m3"] == pytest.approx(600., rel=1.e-12)
    saved = json.loads(paths["request"].read_text(encoding="utf-8"))
    assert saved == json.loads(json.dumps(request, default=str))
    assert saved["ballast"]["state"] == "pending" and saved["ballast"]["target_displacement_kg"] == 307500.
    rebuilt = example.sea_input(**{k: saved["wave"][k] for k in example.SEA_DEFAULTS})
    assert json.loads(json.dumps(asdict(rebuilt), default=str)) == saved["wave"]
    assert example.wave_request(project, faces, rebuilt, example.floating_options(faces))["realization_digest"] \
        == saved["realization_digest"]
    status = json.loads(paths["status"].read_text(encoding="utf-8"))
    assert status["state"] == "pending_ballast" and status["ballast_assigned"] is False
    assert status["floating_load_applied"] is False and status["solved"] is False


def test_model_save_reopen_and_manifest(example, tmp_path, monkeypatch):
    from anyfem.io.project_file import load_project
    model = _model(example, tmp_path, monkeypatch)
    assert model["status"]["state"] == "model_ready" and model["status"]["solved"] is False
    assert {p.name for p in tmp_path.iterdir()} == \
        {example.FILES[key] for key in ("model_project", "manifest", "request", "status")}
    reopened = load_project(tmp_path / example.FILES["model_project"])
    assert not reopened.supports and _loads(reopened) == 0 and not reopened.analyses
    mass = reopened.masses[-1]
    assert mass.name == example.BALLAST_NAME and mass.value == 307500. - STRUCTURE
    manifest = json.loads((tmp_path / example.FILES["manifest"]).read_text(encoding="utf-8"))
    assert manifest["ballast"]["ballast_mass_kg"] == mass.value and manifest["ballast"]["state"] == "assigned"
    assert manifest["ballast"]["target_displacement_kg"] == 307500. and manifest["reopened_total_mass_kg"] == 307500.
    assert manifest["envelope"]["volume_m3"] == pytest.approx(600., rel=1.e-12)
    assert manifest["mesh"]["effective_element_order"] == "linear"
    request = json.loads((tmp_path / example.FILES["request"]).read_text(encoding="utf-8"))
    assert request["ballast"]["state"] == "assigned" and request["project"] == example.FILES["model_project"]


def test_model_refusal_publishes_status_only(example, tmp_path, monkeypatch):
    monkeypatch.setattr(example, "mesh_identity", lambda mesh: "mock-mesh")

    def mesher(project, check):
        raise RuntimeError("mesh refused")

    result = example.write_model(tmp_path, properties=_mock_mass, mesher=mesher)
    assert result["status"]["state"] == "model_refused" and result["status"]["stage"] == "mesh"
    assert "mesh refused" in result["status"]["error"] and "project" not in result
    assert [p.name for p in tmp_path.iterdir()] == [example.FILES["status"]]


def test_existing_files_are_never_overwritten_by_default(example, base, tmp_path):
    directory, (_, _, _, paths) = base
    before = {p: p.read_bytes() for p in paths.values()}
    with pytest.raises(FileExistsError):
        example.write_base(directory)
    with pytest.raises(FileExistsError):
        example.write_model(directory)
    assert {p: p.read_bytes() for p in paths.values()} == before
    # one unrelated user file with an output name blocks everything; nothing else is written
    user = tmp_path / example.FILES["status"]
    user.write_text("user data", encoding="utf-8")
    assert example.main([str(tmp_path)]) == 3
    assert user.read_text(encoding="utf-8") == "user data"
    assert not (tmp_path / example.FILES["base_project"]).exists()


def test_overwrite_keeps_prior_evidence(example, tmp_path, monkeypatch):
    status = tmp_path / example.FILES["status"]
    status.write_text("prior refusal", encoding="utf-8")
    _model(example, tmp_path, monkeypatch, overwrite=True)
    assert json.loads(status.read_text(encoding="utf-8"))["state"] == "model_ready"
    assert (tmp_path / "superseded-001" / status.name).read_text(encoding="utf-8") == "prior refusal"
    example.write_base(tmp_path, overwrite=True)
    assert (tmp_path / "superseded-001" / status.name).read_text(encoding="utf-8") == "prior refusal"
    assert json.loads((tmp_path / "superseded-002" / status.name).read_text(encoding="utf-8"))["state"] == "model_ready"
    assert (tmp_path / "superseded-002" / example.FILES["model_project"]).is_file()
    assert not (tmp_path / example.FILES["model_project"]).exists()
    assert not list(tmp_path.glob(".example-publication-*"))


@pytest.mark.parametrize("move", ["_move_no_clobber", "_exclusive_replace"])
def test_moves_place_a_file_and_never_replace_one(example, tmp_path, move):
    source, destination = tmp_path / "staged", tmp_path / "published"
    source.write_text("new", encoding="utf-8")
    getattr(example, move)(source, destination)
    assert destination.read_text(encoding="utf-8") == "new" and not source.exists()
    source.write_text("newer", encoding="utf-8")
    with pytest.raises(FileExistsError):
        getattr(example, move)(source, destination)
    assert destination.read_text(encoding="utf-8") == "new"
    assert source.read_text(encoding="utf-8") == "newer"


def test_exclusive_replace_releases_its_claim_on_failure(example, tmp_path):
    destination = tmp_path / "published"
    with pytest.raises(FileNotFoundError):
        example._exclusive_replace(tmp_path / "missing", destination)
    assert not destination.exists()


def _rival_appears(example, monkeypatch, name):
    """Another writer takes the name just before its real no-clobber move."""
    move = example._move_no_clobber

    def racing(source, destination):
        if destination.name == name:
            destination.write_text("rival", encoding="utf-8")
        move(source, destination)

    monkeypatch.setattr(example, "_move_no_clobber", racing)


def test_a_failed_multi_file_commit_leaves_no_outputs_and_no_success(example, tmp_path, monkeypatch):
    rival = tmp_path / example.FILES["manifest"]
    _rival_appears(example, monkeypatch, rival.name)
    result = _model(example, tmp_path, monkeypatch)
    assert result["status"]["state"] == "model_refused" and result["status"]["stage"] == "publish"
    assert "FileExistsError" in result["status"]["error"] and "project" not in result
    assert rival.read_text(encoding="utf-8") == "rival"
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([rival.name, example.FILES["status"]])
    written = json.loads((tmp_path / example.FILES["status"]).read_text(encoding="utf-8"))
    assert written["state"] == "model_refused" and written["ballast_assigned"] is False


def test_a_lost_status_race_publishes_no_success(example, tmp_path, monkeypatch):
    status = tmp_path / example.FILES["status"]
    _rival_appears(example, monkeypatch, status.name)
    with pytest.raises(FileExistsError):
        _model(example, tmp_path, monkeypatch)
    assert status.read_text(encoding="utf-8") == "rival"
    assert [p.name for p in tmp_path.iterdir()] == [status.name]


def test_overwrite_keeps_prior_evidence_when_the_commit_fails(example, tmp_path, monkeypatch):
    status = tmp_path / example.FILES["status"]
    status.write_text("prior refusal", encoding="utf-8")
    _rival_appears(example, monkeypatch, example.FILES["manifest"])
    result = _model(example, tmp_path, monkeypatch, overwrite=True)
    assert result["status"]["state"] == "model_refused"
    assert (tmp_path / "superseded-001" / status.name).read_text(encoding="utf-8") == "prior refusal"
    assert (tmp_path / "superseded-002" / example.FILES["manifest"]).read_text(encoding="utf-8") == "rival"
    assert not (tmp_path / example.FILES["model_project"]).exists()
    assert json.loads(status.read_text(encoding="utf-8"))["state"] == "model_refused"


def test_supersede_moves_only_named_outputs_status_first(example, tmp_path, monkeypatch):
    for name in example.OUTPUTS:
        (tmp_path / name).write_text(name, encoding="utf-8")
    keep = tmp_path / "user-notes.txt"
    keep.write_text("mine", encoding="utf-8")
    moved, replace = [], example.os.replace

    def recording(source, destination):
        moved.append(Path(source).name)
        replace(source, destination)

    monkeypatch.setattr(example.os, "replace", recording)
    example._supersede(tmp_path)
    assert moved[0] == example.FILES["status"] and sorted(moved) == sorted(example.OUTPUTS)
    assert keep.read_text(encoding="utf-8") == "mine"
    assert sorted(p.name for p in (tmp_path / "superseded-001").iterdir()) == sorted(example.OUTPUTS)


def test_prepare_refuses_without_runtime_and_publishes_nothing_else(example, tmp_path):
    status = example.prepare(tmp_path)                    # no runtime: refused before any mesh or BEM
    assert status["state"] == "prepare_refused" and status["stage"] == "runtime"
    assert status["floating_load_applied"] is False and status["solved"] is False
    assert json.loads((tmp_path / example.FILES["status"]).read_text(encoding="utf-8")) == \
        json.loads(json.dumps(status, default=str))
    assert [p.name for p in tmp_path.iterdir()] == [example.FILES["status"]]


def test_prepare_refuses_an_existing_loaded_project(example, tmp_path):
    target = tmp_path / example.PREPARE_FILES["loaded_project"]
    target.write_text("user project", encoding="utf-8")
    with pytest.raises(FileExistsError):
        example.prepare(tmp_path)
    assert target.read_text(encoding="utf-8") == "user project"
    assert [p.name for p in tmp_path.iterdir()] == [target.name]
