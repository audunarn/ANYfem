"""Opt-in consumer checks for exact, owner-supplied schema-6 documents.

Set ANYFEM_SCHEMA6_FIXTURES to the owner export directory. These checks do not
raise the supported geometry floor or claim broad meshing/solver acceptance.
"""
from pathlib import Path
import json
import os

import numpy as np
import pytest

ROOT=os.environ.get("ANYFEM_SCHEMA6_FIXTURES")
if not ROOT:
    pytest.skip("set ANYFEM_SCHEMA6_FIXTURES for exact owner-candidate checks",allow_module_level=True)
ROOT=Path(ROOT)
MANIFEST={item["name"]:item for item in json.loads((ROOT/"manifest.json").read_text())}
CASES=[(name,state) for name in MANIFEST for state in ("authored","prepared")]

from anygeometry import from_dict,to_dict
from anygeometry.entities import EntityRef
from anyfem import Project,commands
from anyfem.document import DocumentSession
from anyfem.io import save_project,load_project,project_to_dict,project_from_dict
from anyfem.io.recovery import write_autosave,load_recovery
from anyfem.model.attributes import Support
from anyfem.ui.scene import build_geometry_scene


def encoded(geometry):
    # Owner codecs contain immutable tuples; compare the actual JSON contract.
    return json.loads(json.dumps(to_dict(geometry)))


def design_document(project):
    """Compare design; undo/redo advance revisions and reserve allocated IDs."""
    document = project_to_dict(project)
    document["geometry"].pop("revision")
    document["geometry"].pop("checksum")
    document["geometry"].pop("id_state")
    return document


def owner_project(name,state):
    data=json.loads((ROOT/f"{name}-{state}.json").read_text())
    key="source_checksum" if state=="authored" else "prepared_checksum"
    assert data["checksum"]["value"]==MANIFEST[name][key]
    project=Project(name,geometry=from_dict(data))
    assert encoded(project.geometry)==data
    return project,data


@pytest.mark.parametrize("name,state",CASES)
def test_schema6_save_snapshot_recovery_and_reopen(name,state,tmp_path):
    project,data=owner_project(name,state)
    session=DocumentSession(project);document=project_to_dict(project)
    saved=save_project(project,tmp_path/"consumer.anyfem")
    assert encoded(load_project(saved).geometry)==data
    assert encoded(session.snapshot().thaw().geometry)==data
    revision=session.revision
    candidate=write_autosave(document,document_id=project.document_id,
        revision_id=revision.id,revision_sequence=revision.sequence,
        document_hash=revision.document_hash,model_hash=revision.model_hash,root=tmp_path/"recovery")
    restored=project_from_dict(load_recovery(candidate))
    assert encoded(restored.geometry)==data
    assert encoded(project.geometry)==data


@pytest.mark.parametrize("name,state",CASES)
def test_schema6_references_undo_redo_and_failed_transaction(name,state):
    project,data=owner_project(name,state);session=DocumentSession(project)
    edge=next(iter(project.geometry.edges));vertex=next(iter(project.geometry.vertices))
    edge_ref=project.geometry.entity_ref("edge",edge)
    vertex_ref=project.geometry.entity_ref("vertex",vertex)
    session.execute(commands.AddLineLoad(edge_ref,(0,0,-3)))
    session.execute(commands.AddSupport(Support("fixed",vertex_ref,{"ux":0})))
    before=design_document(project)
    identifier=session.execute(commands.AddPoint(20,30,40));after=design_document(project)
    allocated_ids=encoded(project.geometry)["id_state"]
    edited_revision=project.geometry.revision
    assert session.undo() and design_document(project)==before
    undo_revision=project.geometry.revision
    assert undo_revision>edited_revision
    assert encoded(project.geometry)["id_state"]==allocated_ids
    assert session.redo() and design_document(project)==after
    assert project.geometry.revision>undo_revision
    assert encoded(project.geometry)["id_state"]==allocated_ids
    assert identifier in project.geometry.vertices
    restored=project_from_dict(project_to_dict(project))
    assert restored.load_cases["default"].line_loads[0].ref==edge_ref
    assert tuple(restored.resolve_geometry_attachment(edge_ref))==tuple(project.resolve_geometry_attachment(edge_ref))
    session.mesh_cache["marker"]=object();marker=session.mesh_cache["marker"]
    with pytest.raises(RuntimeError,match="deliberate rollback"):
        with session.transaction("refused consumer edit"):
            project.geometry.add_point(99,98,97)
            raise RuntimeError("deliberate rollback")
    assert design_document(project)==after
    assert session.mesh_cache["marker"] is marker
    assert encoded(project.geometry)["edges"]==data["edges"]


@pytest.mark.parametrize("name,state",CASES)
def test_schema6_scene_keeps_exact_owner_tags_and_finite_samples(name,state):
    project,data=owner_project(name,state)
    scene=build_geometry_scene(project)
    assert scene.faces and scene.lines
    assert {item.ref.id for item in scene.faces} == set(project.geometry.faces)
    assert {item.ref.id for item in scene.lines} == set(project.geometry.edges)
    for item in scene.faces:
        assert item.ref.id in project.geometry.faces
        assert item.polygons and all(np.isfinite(polygon).all() for polygon in item.polygons)
    for item in scene.lines:
        assert item.ref.id in project.geometry.edges
        assert np.isfinite(item.points).all()
    assert encoded(project.geometry)==data


def test_schema6_second_cut_attachment_coordinate_oracles_survive_project_codec(tmp_path):
    project,data=owner_project("second-cut-attachments","prepared")
    project=load_project(save_project(project,tmp_path/"attachments.anyfem"))
    geometry=project.geometry
    for oracle in MANIFEST["second-cut-attachments"]["expected"]["attachment_stations"]:
        attachment=geometry.attachments[oracle["attachment_id"]]
        assert attachment.source_kind==oracle["source_kind"]
        parameter=attachment.target_parameters[0].start
        point=geometry.sample_edge(attachment.target_id,np.array([parameter]))[0]
        tolerance=attachment.tolerance_used
        assert tolerance>0
        assert np.linalg.norm(point-np.asarray(oracle["position"]))<=tolerance
        if attachment.source_kind=="vertex":
            assert np.linalg.norm(geometry.vertex_position(attachment.source_id)-point)<=tolerance
        else:
            assert attachment.member_id in geometry.members
    assert encoded(geometry)==data


@pytest.mark.parametrize("name", MANIFEST)
def test_schema6_shell_preparation_preserves_source_and_face_lineage(name):
    from anygeometry.closure import extract_model_closure
    from anyfem.structural_preparation import prepare_structural_connectivity

    project, document = owner_project(name, "authored")
    source = project.geometry
    handles = tuple(source.handle(kind, identifier)
                    for kind in ("face", "sheet")
                    for identifier in getattr(source, kind + "s"))
    closure = extract_model_closure(source, handles, include_structural_closure=True,
                                    include_features=False)
    working = closure.working_model
    report = prepare_structural_connectivity(
        working, source_model_id=str(closure.source_model_id),
        source_revision=closure.source_revision, member_ids=(),
    )
    assert encoded(source) == document
    assert bool(report.connections) == (name != "plate-pipe-separated")
    for identifier in source.faces:
        initial = closure.source_to_work[source.handle("face", identifier)]
        # The original face may now be deleted; resolve its durable reference.
        refs = working.resolve_ref(EntityRef("face", initial.id))
        assert refs and all(ref.id in working.faces for ref in refs)
