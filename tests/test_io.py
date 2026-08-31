"""Project files, SESAM import and deck export.

A saved model has to come back *identical*, not merely equivalent: loads and
sections reference geometry by ID, so a round trip that renumbered anything
would silently re-target them.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pytest

from anyfem import Project, pinned, solve_linear_static, solve_modal, steel
from anyfem.io import (
    DeckExportError,
    ProjectFileError,
    SesamImportError,
    export_calculix_deck,
    export_sesam,
    import_sesam,
    load_project,
    project_from_dict,
    project_to_dict,
    save_project,
)
from anyfem.model import BeamSection, Mass, plate_mode, prescribed
from anyfem.post import evaluate_field, probe
from anyfem.solve.build import build_fe_model


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as directory:
        yield Path(directory)


def rich_project() -> Project:
    """A model using every attribute the file format has to carry."""

    project = Project(name="deck")
    project.add_material(steel("S355", 0.010))
    project.add_plate_section("plate", thickness=0.010, material="S355")
    project.add_beam_section(
        BeamSection(
            name="stiffener", profile="T-bar", material="S355",
            web_height=0.2, web_thickness=0.01,
            flange_width=0.1, flange_thickness=0.012,
            web_direction=(0.0, 0.0, 1.0),
        )
    )

    geometry = project.geometry
    points = geometry.add_points([(0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0)])
    edges = geometry.add_polyline(points, close=True)
    face = geometry.add_face(edges)
    # An arc as well, so the curve type has to survive.
    start = geometry.add_point(3.0, 0.0, 0.0)
    via = geometry.add_point(2.7, 0.7, 0.0)
    arc = geometry.add_arc(start, via, points[2])

    project.assign_plate(face, "plate")
    project.assign_beam(edges[0], "stiffener")
    for edge in edges:
        project.add_support(pinned(project.edge(edge)))
    project.add_support(prescribed(project.point(points[0]), uz=0.002))
    project.add_mass(Mass(ref=project.face(face), value=500.0, name="equipment"))

    dead = project.load_case("dead")
    dead.add_pressure(project.face(face), 10_000.0)
    dead.set_gravity()
    live = project.load_case("live")
    live.add_point_load(project.point(points[1]), force=(0, 0, -900.0))
    live.add_line_load(project.edge(edges[1]), (0, 0, -300.0))
    live.add_surface_traction(project.face(face), (100.0, 0, 0))

    project.add_combination("ULS", {"dead": 1.2, "live": 1.5})
    project.add_imperfection(
        plate_mode(project.face(face), amplitude=0.004, waves=(1, 2))
    )
    return project, face, edges, points, arc


# ----------------------------------------------------------------------
# project files
# ----------------------------------------------------------------------
def test_a_round_trip_preserves_every_id(workspace):
    project, _face, _edges, _points, _arc = rich_project()
    reloaded = load_project(save_project(project, workspace / "model"))

    assert sorted(reloaded.geometry.entity_keys()) == sorted(
        project.geometry.entity_keys()
    )
    # The counters too, or a new entity could collide with a saved one.
    assert reloaded.geometry.id_state() == project.geometry.id_state()


def test_a_round_trip_preserves_curve_types(workspace):
    project, _face, _edges, _points, arc = rich_project()
    reloaded = load_project(save_project(project, workspace / "model"))

    from anyfem.geometry.curves import Arc

    assert isinstance(reloaded.geometry.edges[arc].curve, Arc)
    assert reloaded.geometry.edge_length(arc) == pytest.approx(
        project.geometry.edge_length(arc)
    )


def test_a_round_trip_preserves_every_attribute(workspace):
    project, face, edges, _points, _arc = rich_project()
    reloaded = load_project(save_project(project, workspace / "model"))

    assert reloaded.name == "deck"
    assert reloaded.face_sections == project.face_sections
    assert reloaded.edge_sections == project.edge_sections
    assert len(reloaded.supports) == len(project.supports)
    assert reloaded.masses[0].value == 500.0
    assert reloaded.masses[0].name == "equipment"
    assert sorted(reloaded.load_cases) == ["dead", "live"]
    assert reloaded.combinations["ULS"].factors == {"dead": 1.2, "live": 1.5}
    assert reloaded.imperfections[0].waves == (1, 2)
    assert reloaded.imperfections[0].axes == (0, 1)
    assert reloaded.imperfections[0].direction == (0.0, 0.0, 1.0)
    assert reloaded.imperfections[0].amplitude == pytest.approx(0.004)
    assert reloaded.imperfections[0].id == project.imperfections[0].id

    dead = reloaded.load_case("dead")
    assert dead.pressures[0].value == 10_000.0
    assert np.allclose(dead.gravity, [0, 0, -9.81])
    live = reloaded.load_case("live")
    assert np.allclose(live.point_loads[0].force, [0, 0, -900.0])
    assert np.allclose(live.line_loads[0].force_per_length, [0, 0, -300.0])
    assert np.allclose(live.surface_tractions[0].traction, [100.0, 0, 0])

    # Beam section geometry survives, not just its name.
    section = reloaded.beam_sections["stiffener"]
    assert section.profile == "T-bar"
    assert section.web_height == pytest.approx(0.2)
    assert np.allclose(section.web_direction, [0, 0, 1])


def test_beam_attachment_and_rotation_round_trip():
    project = Project("section placement")
    project.add_material(steel("S355", 0.010))
    project.add_beam_section(
        BeamSection(
            "tee",
            "T-bar",
            "S355",
            web_height=0.2,
            web_thickness=0.01,
            flange_width=0.1,
            flange_thickness=0.012,
            offset_mode="automatic",
            attachment_side="back",
            rotation_deg=37.5,
        )
    )

    restored = project_from_dict(project_to_dict(project))
    section = restored.beam_sections["tee"]
    assert section.offset_mode == "automatic"
    assert section.attachment_side == "back"
    assert section.rotation_deg == pytest.approx(37.5)


def test_a_prescribed_displacement_keeps_its_value(workspace):
    project, _face, _edges, _points, _arc = rich_project()
    reloaded = load_project(save_project(project, workspace / "model"))
    values = [
        support.constraints
        for support in reloaded.supports
        if any(abs(v) > 0 for v in support.constraints.values())
    ]
    assert values == [{"uz": 0.002}]


def test_a_reloaded_model_solves_identically(workspace):
    project = Project(name="plate")
    project.add_material(steel("S355", 0.010))
    project.add_plate_section("plate", thickness=0.010, material="S355")
    geometry = project.geometry
    points = geometry.add_points([(0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0)])
    edges = geometry.add_polyline(points, close=True)
    face = geometry.add_face(edges)
    project.assign_plate(face, "plate")
    for edge in edges:
        project.add_support(pinned(project.edge(edge)))
    project.load_case("dead").add_pressure(project.face(face), 10_000.0)
    project.load_case("live").add_pressure(project.face(face), 4_000.0)
    project.add_combination("ULS", {"dead": 1.2, "live": 1.5})

    reloaded = load_project(save_project(project, workspace / "model"))
    first = solve_linear_static(
        project, target_size=0.25, combination="ULS"
    ).max_translation()[1]
    second = solve_linear_static(
        reloaded, target_size=0.25, combination="ULS"
    ).max_translation()[1]
    assert first == second


def test_new_entities_after_a_reload_do_not_collide(workspace):
    project, _face, _edges, _points, _arc = rich_project()
    reloaded = load_project(save_project(project, workspace / "model"))

    existing = set(reloaded.geometry.vertices)
    fresh = reloaded.geometry.add_point(9.0, 9.0, 0.0)
    assert fresh not in existing


def test_geometry_groups_tags_and_lineage_survive_project_round_trip(workspace):
    from anyfem import commands as cmd
    from anygeometry.serialization import to_dict as geometry_to_dict

    project, face, _edges, _points, _arc = rich_project()
    reference = project.face(face)
    project.geometry.add_to_group("shell", [reference])
    project.geometry.tag(reference, "deck", "primary")

    faces = cmd.CommandStack(project).run(
        cmd.SplitFace(face, axis=0, fraction=0.5)
    )
    encoded = project_to_dict(project)
    assert encoded["geometry"] == geometry_to_dict(project.geometry)
    reloaded = load_project(save_project(project, workspace / "lineage"))

    assert {item.id for item in reloaded.geometry.group("shell")} == set(faces)
    assert all(
        reloaded.geometry.tags_for(reloaded.face(item)) == ("deck", "primary")
        for item in faces
    )
    assert {
        item.id for item in reloaded.geometry.replacement_history()[reference]
    } == set(faces)
    assert geometry_to_dict(reloaded.geometry) == encoded["geometry"]


def test_format_two_geometry_remains_readable():
    legacy = {
        "anyfem": {"format": 2},
        "name": "legacy geometry",
        "geometry": {
            "vertices": [
                {"id": 1, "position": [0.0, 0.0, 0.0]},
                {"id": 2, "position": [1.0, 0.0, 0.0]},
            ],
            "edges": [
                {"id": 1, "start": 1, "end": 2, "curve": {"kind": "line"}}
            ],
            "faces": [],
            "next_id": {"vertex": 3, "edge": 2, "face": 1},
        },
    }

    project = project_from_dict(legacy)

    assert project.geometry.entity_keys() == {
        ("vertex", 1), ("vertex", 2), ("edge", 1)
    }
    assert project.geometry.id_state() == {"vertex": 3, "edge": 2, "face": 1}


def test_the_suffix_is_added_when_missing(workspace):
    project, _face, _edges, _points, _arc = rich_project()
    written = save_project(project, workspace / "noextension")
    assert written.suffix == ".anyfem"


def test_a_file_without_a_header_is_refused(workspace):
    path = workspace / "bogus.anyfem"
    path.write_text(json.dumps({"name": "x"}), encoding="utf-8")
    with pytest.raises(ProjectFileError, match="format header"):
        load_project(path)


def test_a_newer_format_is_refused_with_advice(workspace):
    path = workspace / "future.anyfem"
    path.write_text(json.dumps({"anyfem": {"format": 99}}), encoding="utf-8")
    with pytest.raises(ProjectFileError, match="upgrade ANYfem"):
        load_project(path)


def test_invalid_json_is_refused(workspace):
    path = workspace / "broken.anyfem"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ProjectFileError, match="not valid JSON"):
        load_project(path)


def test_a_missing_file_is_refused(workspace):
    with pytest.raises(ProjectFileError, match="cannot read"):
        load_project(workspace / "nothing.anyfem")


def test_a_dangling_serialized_entity_reference_is_refused():
    project, _face, _edges, _points, _arc = rich_project()
    data = project_to_dict(project)
    data["supports"][0]["ref"]["id"] = 999

    with pytest.raises(ProjectFileError, match=r"support\.ref: no edge 999"):
        project_from_dict(data)


def test_a_serialized_section_cannot_name_an_undefined_material():
    project, _face, _edges, _points, _arc = rich_project()
    data = project_to_dict(project)
    data["plate_sections"][0]["material"] = "missing"

    with pytest.raises(ProjectFileError, match="no material named 'missing'"):
        project_from_dict(data)


def test_malformed_serialized_geometry_reports_the_data_path():
    project, _face, _edges, _points, _arc = rich_project()
    data = project_to_dict(project)
    data["geometry"]["vertices"][0]["position"] = [0.0, 0.0]

    with pytest.raises(
        ProjectFileError,
        match=r"geometry: .*vertex position.*shape \(3,\)",
    ):
        project_from_dict(data)


def test_the_file_holds_the_model_not_its_consequences(workspace):
    project, _face, _edges, _points, _arc = rich_project()
    data = project_to_dict(project)
    # A mesh and results are regenerable, so they are not stored.
    assert "mesh" not in data
    assert "results" not in data
    assert data["geometry"]["schema"] == "anygeometry"
    assert data["geometry"]["version"] == 4
    assert "id_state" in data["geometry"]
    assert "groups" in data["geometry"]
    assert "replacement_history" in data["geometry"]


# ----------------------------------------------------------------------
# SESAM
# ----------------------------------------------------------------------
def sesam_record(name: str, *values) -> str:
    """One fixed-width SESAM record: name in 8 columns, fields in 16."""

    line = f"{name:<8}"
    for value in values:
        line += (
            f"{int(value):16d}"
            if isinstance(value, int)
            else f"{float(value):16.8E}"
        )
    return line


def write_sesam_plate(
    path: Path, nx: int = 3, ny: int = 3, side: float = 1.0, thickness: float = 0.01
) -> Path:
    """A quad-shell plate clamped along x = 0."""

    lines = [
        sesam_record("IDENT", 100, 1),
        sesam_record("UNITS", 1, 1, 1),
        sesam_record("MISOSEL", 1, 2.1e11, 0.3, 7850.0),
        sesam_record("GELTH", 10, thickness),
    ]
    ids = {}
    node = 0
    for j in range(ny + 1):
        for i in range(nx + 1):
            node += 1
            ids[(i, j)] = node
            lines.append(
                sesam_record("GCOORD", node, i * side / nx, j * side / ny, 0.0)
            )
            lines.append(sesam_record("GNODE", node, node, 6, 123456))
    element = 0
    for j in range(ny):
        for i in range(nx):
            element += 1
            corners = [
                ids[(i, j)], ids[(i + 1, j)], ids[(i + 1, j + 1)], ids[(i, j + 1)]
            ]
            lines.append(sesam_record("GELMNT1", element, 0, 24, 0, *corners))
            lines.append(sesam_record("GELREF1", element, 1, 10))
    for j in range(ny + 1):
        lines.append(sesam_record("BNBCD", ids[(0, j)], 6, 1, 1, 1, 1, 1, 1))
    lines.append(sesam_record("IEND", 0, 0, 0, 0))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_a_sesam_plate_imports_cleanly(workspace):
    model = import_sesam(write_sesam_plate(workspace / "plate.FEM"))

    assert model.num_nodes == 16
    assert len(model.mesh.quads) == 9
    assert not model.diagnostics
    assert model.groups


def test_an_imported_model_admits_it_has_no_geometry(workspace):
    model = import_sesam(write_sesam_plate(workspace / "plate.FEM"))

    assert model.has_geometry is False
    # The stand-in project is empty rather than invented.
    assert not model.project().geometry.faces
    assert not model.project().geometry.edges


def test_imported_elements_are_grouped_and_addressable(workspace):
    model = import_sesam(write_sesam_plate(workspace / "plate.FEM"))
    group = model.groups["group 1"]

    assert model.mesh.elements_on(group) == sorted(model.mesh.quads)
    assert model.mesh.nodes_on(group) == sorted(model.mesh.nodes)


def test_an_imported_model_solves_and_postprocesses(workspace):
    model = import_sesam(write_sesam_plate(workspace / "plate.FEM"))
    group = model.groups["group 1"]
    case = model.load_case()
    case.add_pressure(group, 20_000.0)

    solution = solve_linear_static(built=model.built(case))
    _node, magnitude = solution.max_translation()
    assert magnitude > 0.0

    # The same postprocessing works, because it goes through the association.
    stress = evaluate_field(solution, "von_mises")
    assert len(stress) == len(model.mesh.quads)
    assert probe(solution, group).stresses


def test_an_imported_model_runs_other_analyses(workspace):
    model = import_sesam(write_sesam_plate(workspace / "plate.FEM"))
    solution = solve_modal(built=model.built(), num_modes=3)
    assert solution.status == "ok"
    assert len(solution) == 3


def test_a_support_can_be_added_to_an_imported_group(workspace):
    from anyfem.model.attributes import fixed

    model = import_sesam(write_sesam_plate(workspace / "plate.FEM"))
    before = len(model.fe_model.boundary_conditions)
    model.add_support(fixed(model.groups["group 1"], name="clamp"))
    assert len(model.fe_model.boundary_conditions) == before + 1


def test_a_support_on_an_unknown_group_is_refused(workspace):
    from anyfem.geometry.entities import EntityRef
    from anyfem.model.attributes import fixed

    model = import_sesam(write_sesam_plate(workspace / "plate.FEM"))
    with pytest.raises(SesamImportError, match="not a group"):
        model.add_support(fixed(EntityRef("face", 99), name="nowhere"))


def test_a_missing_sesam_file_is_refused(workspace):
    with pytest.raises(SesamImportError, match="no such file"):
        import_sesam(workspace / "absent.FEM")


def test_a_sesam_file_with_no_supported_topology_is_refused(workspace):
    path = workspace / "empty.FEM"
    path.write_text(
        "\n".join(
            [
                sesam_record("IDENT", 100, 1),
                sesam_record("UNITS", 1, 1, 1),
                sesam_record("IEND", 0, 0, 0, 0),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(SesamImportError, match="no supported beam or shell"):
        import_sesam(path)


def test_triangular_and_quadrilateral_shells_stay_in_the_imported_groups(workspace):
    """ANYfileio's neutral mesh preserves both supported shell topologies."""

    path = workspace / "mixed.FEM"
    lines = [
        sesam_record("IDENT", 100, 1),
        sesam_record("UNITS", 1, 1, 1),
        sesam_record("MISOSEL", 1, 2.1e11, 0.3, 7850.0),
        sesam_record("GELTH", 10, 0.02),
    ]
    for index, (x, y) in enumerate(((0, 0), (1, 0), (0, 1), (1, 1)), start=1):
        lines.append(sesam_record("GCOORD", index, float(x), float(y), 0.0))
        lines.append(sesam_record("GNODE", index, index, 6, 123456))
    lines.append(sesam_record("GELMNT1", 100, 0, 25, 0, 1, 2, 3))
    lines.append(sesam_record("GELREF1", 100, 1, 10))
    lines.append(sesam_record("GELMNT1", 200, 0, 24, 0, 1, 2, 4, 3))
    lines.append(sesam_record("GELREF1", 200, 1, 10))
    lines.append(sesam_record("IEND", 0, 0, 0, 0))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    model = import_sesam(path)
    grouped = {e for ids in model.mesh.elements_of_face.values() for e in ids}
    assert grouped == {100, 200}
    assert model.mesh.tris[100] == (1, 2, 3)
    assert model.mesh.quads[200] == (1, 2, 4, 3)
    assert 100 not in model.mesh.quads


# ----------------------------------------------------------------------
# decks
# ----------------------------------------------------------------------
def solved_project():
    project = Project(name="deck")
    project.add_material(steel("S355", 0.010))
    project.add_plate_section("plate", thickness=0.010, material="S355")
    geometry = project.geometry
    points = geometry.add_points([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
    edges = geometry.add_polyline(points, close=True)
    face = geometry.add_face(edges)
    project.assign_plate(face, "plate")
    for edge in edges:
        project.add_support(pinned(project.edge(edge)))
    project.load_case().add_pressure(project.face(face), 10_000.0)
    return project


def test_a_calculix_deck_is_written(workspace):
    project = solved_project()
    built = build_fe_model(project, project.generate_mesh(0.25))
    export_calculix_deck(built, workspace / "model")

    deck = workspace / "model.inp"
    assert deck.exists()
    text = deck.read_text(encoding="utf-8")
    assert "*NODE" in text
    assert "*ELEMENT" in text.upper()


def test_the_deck_carries_metadata(workspace):
    project = solved_project()
    built = build_fe_model(project, project.generate_mesh(0.5))
    export_calculix_deck(built, workspace / "model", metadata={"note": "hello"})

    sidecar = workspace / "model.json"
    assert sidecar.exists()
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    text = json.dumps(data)
    assert "ANYfem" in text
    assert "hello" in text


def test_sesam_export_is_refused_with_a_reason():
    with pytest.raises(DeckExportError) as excinfo:
        export_sesam(object(), "anything.fem")
    message = str(excinfo.value)
    assert "outside its supported gate" in message
    # It says what to do instead rather than just refusing.
    assert "CalculiX" in message or "project file" in message


# ----------------------------------------------------------------------
# solving something already built
# ----------------------------------------------------------------------
def test_an_analysis_accepts_an_already_built_model():
    project = solved_project()
    built = build_fe_model(project, project.generate_mesh(0.25))

    from_built = solve_linear_static(built=built).max_translation()[1]
    from_project = solve_linear_static(
        project, target_size=0.25
    ).max_translation()[1]
    assert from_built == pytest.approx(from_project)


def test_an_analysis_needs_a_project_or_a_built_model():
    with pytest.raises(ValueError, match="project or an already-built model"):
        solve_linear_static()
