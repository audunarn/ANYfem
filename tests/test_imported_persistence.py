"""Portable persistence for mesh-native imported projects."""

from __future__ import annotations

from pathlib import Path

import h5py
import pytest

from anygeometry.entities import EntityRef

from anyfem.io.artifacts import ArtifactError, ArtifactStore
from anyfem.io.project_file import (
    ProjectFileError,
    load_project,
    project_from_dict,
    project_to_dict,
    save_project,
)
from anyfem.io.sesam import import_sesam, import_sesam_artifact
from anyfem.model.attributes import Support
from anyfem.model.project import Project
from anyfem.model.records import ArtifactRef


def _sesam_record(name: str, *values: int | float) -> str:
    line = f"{name:<8}"
    for value in values:
        line += (
            f"{int(value):16d}"
            if isinstance(value, int)
            else f"{float(value):16.8E}"
        )
    return line


def _write_sesam_plate(path: Path) -> Path:
    lines = [
        _sesam_record("IDENT", 100, 1),
        _sesam_record("UNITS", 1, 1, 1),
        _sesam_record("MISOSEL", 1, 2.1e11, 0.3, 7850.0),
        _sesam_record("GELTH", 10, 0.012),
        _sesam_record("GCOORD", 1, 0.0, 0.0, 0.0),
        _sesam_record("GNODE", 1, 1, 6, 123456),
        _sesam_record("GCOORD", 2, 1.0, 0.0, 0.0),
        _sesam_record("GNODE", 2, 2, 6, 123456),
        _sesam_record("GCOORD", 3, 1.0, 1.0, 0.0),
        _sesam_record("GNODE", 3, 3, 6, 123456),
        _sesam_record("GCOORD", 4, 0.0, 1.0, 0.0),
        _sesam_record("GNODE", 4, 4, 6, 123456),
        _sesam_record("GELMNT1", 1, 0, 24, 0, 1, 2, 3, 4),
        _sesam_record("GELREF1", 1, 1, 10),
        _sesam_record("BNBCD", 1, 6, 1, 1, 1, 1, 1, 1),
        _sesam_record("BNBCD", 4, 6, 1, 1, 1, 1, 1, 1),
        _sesam_record("IEND", 0, 0, 0, 0),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="latin-1")
    return path


def _write_imported_artifact(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = _write_sesam_plate(tmp_path / "source.FEM")
    imported = import_sesam(source)
    metadata, source_contents = imported.artifact_embedding()
    store = ArtifactStore(tmp_path / "portable.anyfem")
    artifact = store.write_mesh(
        imported.mesh,
        mesh_id="imported-mesh",
        document_id="document-1",
        model_hash="model-hash",
        mesh_hash="mesh-hash",
        imported_model=metadata,
        embedded_source=source_contents,
    )
    return source, imported, store, artifact


def test_v4_mesh_only_project_round_trips_provenance_and_group_refs():
    artifact = ArtifactRef(
        id="imported-mesh",
        kind="mesh",
        uri="meshes/imported-mesh.anymesh.h5",
    )
    project = Project(
        "imported",
        mesh_only=True,
        imported_format="sesam_fem",
        imported_semantics_artifact_id=artifact.id,
    )
    project.artifacts[artifact.id] = artifact
    project.supports.append(
        Support("deck restraint", EntityRef("edge", 71), {"uz": 0.0})
    )
    project.load_case("sea").add_pressure(EntityRef("face", 42), 12_500.0)

    encoded = project_to_dict(project)
    restored = project_from_dict(encoded)

    assert encoded["mesh_only"] is True
    assert restored.mesh_only is True
    assert restored.imported_format == "sesam_fem"
    assert restored.imported_semantics_artifact_id == artifact.id
    assert not restored.geometry.entity_keys()
    assert restored.supports[0].ref == EntityRef("edge", 71)
    assert restored.load_cases["sea"].pressures[0].ref == EntityRef("face", 42)


@pytest.mark.parametrize(
    "reference, message",
    [
        ({"kind": "element", "id": 1}, "unknown entity kind"),
        ({"kind": "face", "id": 0}, "positive integer"),
        ({"kind": "face", "id": True}, "positive integer"),
    ],
)
def test_mesh_only_refs_relax_existence_but_not_syntax(reference, message):
    data = project_to_dict(Project("imported", mesh_only=True))
    data["supports"] = [
        {"name": "bad", "ref": reference, "constraints": {"uz": 0.0}}
    ]

    with pytest.raises(ProjectFileError, match=message):
        project_from_dict(data)


def test_v3_files_default_to_modelled_geometry_semantics():
    data = project_to_dict(Project("legacy"))
    data["anyfem"]["format"] = 3
    data.pop("mesh_only")
    data.pop("imported_format")
    data.pop("imported_semantics_artifact_id")

    restored = project_from_dict(data)

    assert restored.mesh_only is False
    assert restored.imported_format is None
    assert restored.imported_semantics_artifact_id is None


def test_deleted_source_restores_full_sesam_semantics_from_sidecar(tmp_path):
    source, original, store, artifact = _write_imported_artifact(tmp_path)
    original_boundary_count = len(original.fe_model.boundary_conditions)
    original_thickness = next(iter(original.fe_model.mesh.elements.values())).thickness
    source.unlink()

    metadata = store.read_mesh_metadata(artifact)
    restored = import_sesam_artifact(store, artifact)

    assert not source.exists()
    assert metadata["imported_model"]["format"] == "sesam_fem"
    assert metadata["embedded_source"]["name"] == "source.FEM"
    assert metadata["embedded_source"]["byte_size"] > 0
    assert restored.has_geometry is False
    assert not restored.project().geometry.entity_keys()
    assert restored.project().mesh_only is True
    assert restored.mesh.quads == original.mesh.quads
    assert restored.groups == original.groups
    assert len(restored.fe_model.boundary_conditions) == original_boundary_count
    assert next(iter(restored.fe_model.mesh.elements.values())).thickness == pytest.approx(
        original_thickness
    )


def test_project_and_sidecar_can_be_relocated_together(tmp_path):
    source, imported, store, artifact = _write_imported_artifact(tmp_path / "old")
    project = imported.project()
    project.artifacts[artifact.id] = artifact
    project.imported_semantics_artifact_id = artifact.id
    project_path = save_project(project, store.project_path)

    destination = tmp_path / "relocated"
    destination.mkdir()
    relocated_project = destination / project_path.name
    project_path.replace(relocated_project)
    store.root.replace(destination / store.root.name)
    source.unlink()

    relocated = ArtifactStore(relocated_project)
    loaded = load_project(relocated_project)
    loaded_artifact = loaded.artifacts[loaded.imported_semantics_artifact_id]
    restored = import_sesam_artifact(relocated, loaded_artifact)

    assert loaded.mesh_only is True
    assert restored.num_nodes == imported.num_nodes
    assert restored.num_elements == imported.num_elements


def test_outer_and_embedded_checksums_are_both_enforced(tmp_path):
    _source, _imported, store, artifact = _write_imported_artifact(tmp_path)
    path = store.resolve(artifact.uri)
    with h5py.File(path, "r+") as handle:
        handle.attrs["model_hash"] = "tampered"
    # Keep the size assertion current so this specifically exercises the
    # stronger content checksum rather than stopping at the earlier size gate.
    artifact.byte_size = path.stat().st_size

    with pytest.raises(ArtifactError, match="checksum mismatch"):
        store.read_mesh_metadata(artifact)

    # URI-only reads intentionally lack an outer ArtifactRef checksum, so this
    # second artifact proves the embedded source has its own integrity check.
    _source2, _imported2, store2, artifact2 = _write_imported_artifact(
        tmp_path / "embedded"
    )
    path2 = store2.resolve(artifact2.uri)
    with h5py.File(path2, "r+") as handle:
        dataset = handle["imported_model/source_bytes"]
        dataset[0] = int(dataset[0]) ^ 1

    with pytest.raises(ArtifactError, match="embedded source checksum"):
        store2.read_embedded_source(artifact2.uri)


def test_embedded_source_names_and_artifact_paths_cannot_escape(tmp_path):
    source = _write_sesam_plate(tmp_path / "source.FEM")
    imported = import_sesam(source)
    metadata, contents = imported.artifact_embedding()
    metadata["source"]["name"] = "../outside.FEM"
    store = ArtifactStore(tmp_path / "model.anyfem")

    with pytest.raises(ArtifactError, match="plain file name"):
        store.write_mesh(
            imported.mesh,
            imported_model=metadata,
            embedded_source=contents,
        )
    with pytest.raises(ArtifactError, match="inside the project data root"):
        store.read_mesh_metadata("../outside.h5")
