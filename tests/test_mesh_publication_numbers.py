from dataclasses import replace

import numpy as np
import pytest

from anyfem import Project, steel
from anyfem.application.controller import WorkbenchController
from anyfem.application.mesh_publication import check_ticket, publication_ticket
from anyfem.application.workflow import WorkbenchWorkflow
from anyfem.io.project_file import project_from_dict, project_to_dict
from anyfem.mesh_numbering import MAX_NUMBER, MeshNumbering, number_full_mesh, published_state
from anyfem.solve.build import build_fe_model
from anymesher.mesh import Mesh


class Workbench(WorkbenchWorkflow):
    """Use the normal command, session and worker infrastructure, without a viewport."""

    def __init__(self, project, **options):
        self.workbench = WorkbenchController(project, **options)
        self._inspection_mesh = None
        self._mesh_submissions = {}

    def set_status(self, message, **kwargs):
        self.status = message

    def show_mesh(self):
        pass

    def show_geometry(self):
        pass

    def refresh_panels(self):
        pass

    def refresh_all(self):
        pass

    def invalidate_result_requests(self, **kwargs):
        pass


def plates():
    project = Project("two disconnected plates")
    project.add_material(steel("S355", 0.01))
    project.add_plate_section("plate", thickness=0.01, material="S355")
    faces, edges = [], []
    for x in (0, 5):
        points = project.geometry.add_points([(x, 0, 0), (x + 1, 0, 0), (x + 1, 1, 0), (x, 1, 0)])
        boundary = project.geometry.add_polyline(points, close=True)
        face = project.geometry.add_face(boundary)
        project.assign_plate(face, "plate")
        faces.append(face)
        edges.append(boundary)
    return Workbench(project), faces, edges


def test_sparse_mixed_number_allocation_and_overflow():
    mesh = Mesh(nodes={9: np.zeros(3), 4: np.ones(3), 1: np.asarray([1., 0., 0.])},
                tris={4: (1, 4, 9)}, beams={8: (1, 9)})
    state = MeshNumbering(100, 800, 4, "old")
    candidate = number_full_mesh(mesh, state)
    assert set(candidate.nodes) == {101, 102, 103}
    assert candidate.tris == {801: (101, 102, 103)}
    assert candidate.beams == {802: (101, 103)}
    assert state == MeshNumbering(100, 800, 4, "old")
    assert published_state(state, candidate, "new") == MeshNumbering(103, 802, 5, "new")
    with pytest.raises(OverflowError):
        number_full_mesh(mesh, replace(state, element_high_water=MAX_NUMBER - 1))
    with pytest.raises(ValueError, match="retired|unproven"):
        published_state(state, mesh, "bad")


def test_full_publications_undo_branch_reopen_and_concurrent_ticket():
    app, _, _ = plates()
    first = app.generate_mesh(0.5, strategy="mapped")
    first_id = app.mesh_record_id
    old_ticket = publication_ticket(app)
    second = app.generate_mesh(0.5, strategy="mapped")
    assert min(second.nodes) > max(first.nodes)
    assert min(second.quads) > max(first.quads)
    with pytest.raises(ValueError, match="superseded"):
        check_ticket(app, old_ticket)
    app.undo()
    assert app.mesh_record_id == first_id
    assert app.mesh is first
    third = app.generate_mesh(0.5, strategy="mapped")
    assert min(third.nodes) > max(second.nodes)
    assert min(third.quads) > max(second.quads)
    reopened = project_from_dict(project_to_dict(app.project))
    assert reopened.mesh_numbering == app.project.mesh_numbering
    assert not app.session.commands.can_redo


def test_disconnected_update_preserves_solver_topology_after_save(tmp_path):
    from anyfem.io.artifacts import ArtifactStore
    from anyfem.io.project_file import load_project, save_project

    app, (changed, untouched), edges = plates()
    original = app.generate_mesh(0.5, strategy="mapped")
    old_elements = tuple(original.elements_of_face[untouched])
    old_nodes = {node for eid in old_elements for node in original.quads[eid]}
    old_connectivity = {eid: original.quads[eid] for eid in old_elements}
    app.seeding_overrides.update({int(edge): 4 for edge in edges[0]})
    updated = app.update_mesh(0.5, strategy="mapped")
    assert len(updated.elements_of_face[changed]) != len(original.elements_of_face[changed])
    assert tuple(updated.elements_of_face[untouched]) == old_elements
    path = tmp_path / "plate.anyfem"
    store = ArtifactStore(path)
    record = app.project.mesh_records[app.mesh_record_id]
    artifact = store.write_mesh(updated, mesh_id=record.id, model_hash=record.source_model_hash,
                                mesh_hash=record.mesh_hash)
    app.project.artifacts[artifact.id] = artifact
    record.artifact_id = artifact.id
    save_project(app.project, path)
    reopened = load_project(path)
    mesh = store.read_mesh(reopened.artifacts[reopened.mesh_records[record.id].artifact_id])
    built = build_fe_model(reopened, mesh, require_loads=False, require_supports=False)
    for eid, connectivity in old_connectivity.items():
        assert mesh.quads[eid] == connectivity
        assert tuple(built.fe_model.mesh.elements[eid].node_ids) == connectivity
    for node in old_nodes:
        np.testing.assert_array_equal(mesh.nodes[node], original.nodes[node])


def test_headless_session_updates_reopened_mesh_and_keeps_sparse_ids(tmp_path):
    from anyfem.io.project_file import load_project
    from anyfem.mesh_jobs import MeshSettings

    app, (_, untouched), edges = plates()
    settings = MeshSettings.create(.5, strategy="mapped", element_order="linear")
    session = app.project.mesh_session()
    original = session.generate_mesh(settings)
    source = session.mesh_record_id
    destination = session.save(tmp_path / "saved.anyfem")
    reopened = load_project(destination).mesh_session(destination)
    changed = replace(settings, overrides=tuple((int(edge), 4) for edge in edges[0]))
    assert "regenerate" in reopened.preview_update(changed).description
    mesh = reopened.update_mesh(changed)
    assert reopened.mesh_record_id != source
    assert mesh.elements_of_face[untouched] == original.elements_of_face[untouched]
    path = reopened.save()
    final = load_project(path).mesh_session(path)
    assert final.mesh_record_id == reopened.mesh_record_id
    built = build_fe_model(final.project, final.mesh, require_loads=False, require_supports=False)
    for eid in original.elements_of_face[untouched]:
        assert tuple(built.fe_model.mesh.elements[eid].node_ids) == tuple(original.quads[eid])


@pytest.mark.parametrize("executor", ["thread", "process"])
def test_background_update_publishes_retained_numbers(executor):
    from time import monotonic, sleep
    from anyfem.mesh_jobs import MeshTaskManager

    app, (_, untouched), edges = plates()
    app = Workbench(app.project, mesh_task_manager_factory=lambda: MeshTaskManager(executor=executor))
    original = app.generate_mesh(.5, strategy="mapped")
    parent = app.mesh_record_id
    try:
        app.seeding_overrides.update({int(edge): 4 for edge in edges[0]})
        job = app.update_mesh_async(.5, strategy="mapped")
        assert app.mesh_record_id == parent
        deadline = monotonic() + 90
        while app.mesh_task_manager.busy and monotonic() < deadline:
            app._poll_mesh_jobs()
            sleep(.015)
        assert not app.mesh_task_manager.busy
        record = app.project.mesh_records[job.id]
        assert record.status == "completed", record.diagnostics
        assert app.mesh_record_id == job.id
        assert app.mesh.elements_of_face[untouched] == original.elements_of_face[untouched]
        for eid in original.elements_of_face[untouched]:
            assert app.mesh.quads[eid] == original.quads[eid]
            for node in original.quads[eid]:
                np.testing.assert_array_equal(app.mesh.nodes[node], original.nodes[node])
    finally:
        app.mesh_task_manager.shutdown()


def test_sparse_deck_numbers_and_output_range(tmp_path):
    from anyfem.io import DeckExportError, export_calculix_deck
    from anymesher.regional_update import remap_mesh_ids

    app, _, _ = plates()
    mesh = app.project.generate_mesh(.5, strategy="mapped")
    for base, width in ((100, 32), (2**31, 64)):
        ns = {key: base + 3 * key for key in mesh.nodes}
        es = {key: base + 7 * key for key in mesh.quads}
        sparse = remap_mesh_ids(mesh, ns, es)
        built = build_fe_model(app.project, sparse, require_loads=False, require_supports=False)
        path = tmp_path / f"ccx-{width}.inp"
        if width == 64:
            with pytest.raises(DeckExportError, match="32-bit"):
                export_calculix_deck(built, path)
            assert not path.exists()
        export_calculix_deck(built, path, integer_bits=width)
        deck = path.read_text().upper().splitlines()
        blocks = {}
        block = ""
        for line in deck:
            if line.startswith("*"):
                block = line.split(",")[0]
            elif block in ("*NODE", "*ELEMENT") and line.strip():
                blocks.setdefault(block, set()).add(int(line.split(",")[0]))
        assert blocks["*NODE"] == set(ns.values())
        assert blocks["*ELEMENT"] == set(es.values())


def test_numbered_solver_input_refuses_stale_geometry():
    app, _, _ = plates()
    original = app.generate_mesh(.5, strategy="mapped")
    app.project.geometry.move_point(1, -.1, 0)
    with pytest.raises(ValueError, match="stale.*geometry"):
        build_fe_model(app.project, original, require_loads=False, require_supports=False)


def test_no_change_and_deleting_latest_publication_preserve_high_water():
    from anyfem.commands import DeleteProjectRecord
    from anyfem.mesh_jobs import MeshSettings
    app, _, _ = plates()
    session = app.project.mesh_session()
    settings = MeshSettings.create(.5, strategy="mapped", element_order="linear")
    prior = session.generate_mesh(settings)
    state = app.project.mesh_numbering
    assert session.update_mesh(settings) is prior
    assert app.project.mesh_numbering == state
    with session.session.transaction("delete published mesh"):
        session.session.commands.run(DeleteProjectRecord("mesh", state.active_mesh_id))
    assert session.mesh is None
    assert app.project.mesh_numbering.element_high_water == state.element_high_water
    candidate = session.generate_mesh(settings)
    assert min(candidate.quads) > state.element_high_water


def test_cancelling_prepared_update_consumes_no_numbers():
    from anyfem.mesh_jobs import MeshSettings

    app, _, boundaries = plates()
    session = app.project.mesh_session()
    settings = MeshSettings.create(.5, strategy="mapped", element_order="linear")
    parent = session.generate_mesh(settings)
    original = app.project.mesh_numbering
    changed = replace(settings, overrides=tuple((edge, 4) for edge in boundaries[0]))

    class CancelAtPublication:
        is_cancelled = False

        def raise_if_cancelled(self, stage=""):
            if stage == "mesh publication":
                raise RuntimeError("cancelled before publication")

    with pytest.raises(RuntimeError, match="cancelled before publication"):
        session.update_mesh(changed, cancellation=CancelAtPublication())
    assert session.mesh is parent
    assert app.project.mesh_numbering == original
    assert set(app.project.mesh_records) == {original.active_mesh_id}
    completed = session.update_mesh(changed)
    assert min(set(completed.quads) - set(parent.quads)) == original.element_high_water + 1


def test_racing_session_candidates_only_publish_one_number_range(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from anyfem import mesh_session
    from anyfem.mesh_jobs import MeshSettings

    app, _, boundaries = plates()
    session = app.project.mesh_session()
    settings = MeshSettings.create(.5, strategy="mapped", element_order="linear")
    parent = session.generate_mesh(settings)
    prior = app.project.mesh_numbering
    changed = replace(settings, overrides=tuple((edge, 4) for edge in boundaries[0]))
    both_prepared = Barrier(2, timeout=30)
    generate = mesh_session.run_mesh_job

    def race(*args, **kwargs):
        result = generate(*args, **kwargs)
        both_prepared.wait()
        return result

    monkeypatch.setattr(mesh_session, "run_mesh_job", race)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(session.update_mesh, changed) for _ in range(2)]
    refused = [job.exception() for job in jobs if job.exception() is not None]
    assert len(refused) == 1 and "superseded" in str(refused[0])
    winner, = [job.result() for job in jobs if job.exception() is None]
    assert session.mesh is winner
    assert len(app.project.mesh_records) == 2
    state = app.project.mesh_numbering
    assert state.generation == prior.generation + 1
    assert state.element_high_water == prior.element_high_water + len(set(winner.quads) - set(parent.quads))
    assert state.node_high_water == prior.node_high_water + len(set(winner.nodes) - set(parent.nodes))


def test_mapped_automatic_mesh_update_matches_full_static_plate_solution(tmp_path):
    from anyfem import pinned, solve_linear_static
    from anyfem.io.artifacts import ArtifactStore
    from anyfem.io.result_artifact import write_solution_artifact
    from anyfem.mesh_jobs import MeshSettings
    from anyfem.ui.scene import build_mesh_scene
    from anymesher import MeshAutomationOptions

    app, faces, boundaries = plates()
    project = app.project
    # Adjacent public labels beyond exact double precision must stay distinct
    # through scene picking, numerical assembly and persisted result lookup.
    project.mesh_numbering = MeshNumbering(2**53, 2**53)
    for face, edges in zip(faces, boundaries):
        for edge in edges:
            project.add_support(pinned(project.edge(edge)))
        project.load_case().add_pressure(project.face(face), 10000.)
    session = project.mesh_session()
    settings = MeshSettings.create(.25, strategy="auto", element_order="linear",
                                   automation=MeshAutomationOptions())
    prior = session.generate_mesh(settings)
    old_solution = solve_linear_static(built=build_fe_model(project, prior))
    changed = replace(settings, overrides=tuple((edge, 8) for edge in boundaries[0]))
    local = session.update_mesh(changed)
    local_solution = solve_linear_static(built=build_fe_model(project, local))
    scene = build_mesh_scene(project, local)
    picks = {owner for patch in scene.faces for owners in patch.polygon_owners
             for owner in owners if owner.kind == "element"}
    assert {(pick.mesh_id, pick.id) for pick in picks} == {(session.mesh_record_id, eid) for eid in local.quads}
    record = project.mesh_records[session.mesh_record_id]
    store = ArtifactStore(tmp_path / "result.anyfem")
    artifact = write_solution_artifact(store, local_solution, job_id="static", document_id=project.document_id,
                                       mesh_id=record.id, model_hash=record.source_model_hash,
                                       mesh_hash=record.mesh_hash, analysis_hash="plate-static")
    result = store.open_result(artifact)
    try:
        ids = result.table("displacement_node_ids").tolist()
        assert set(ids) == set(local.nodes) and len(ids) == len(local.nodes)
        displacements = result.field("displacement").read(0)
        for row, node in enumerate(ids):
            np.testing.assert_array_equal(displacements[row], local_solution.node_displacement(node))
    finally:
        result.close()
    fixed_face = project.face(faces[1])
    assert local.elements_of_face[faces[1]] == prior.elements_of_face[faces[1]]
    for node, displacement in old_solution.displacement_at(fixed_face).items():
        np.testing.assert_allclose(local_solution.node_displacement(node), displacement, rtol=1e-9, atol=1e-12)
    rebuilt = session.generate_mesh(changed)
    full_solution = solve_linear_static(built=build_fe_model(project, rebuilt))
    full_nodes = {tuple(xyz): n for n, xyz in rebuilt.nodes.items()}
    assert len(full_nodes) == len(local.nodes)
    for node, xyz in local.nodes.items():
        np.testing.assert_allclose(local_solution.node_displacement(node),
                                   full_solution.node_displacement(full_nodes[tuple(xyz)]),
                                   rtol=1e-9, atol=1e-12)


def test_load_only_edit_reuses_publication_and_rejects_mutated_parent(tmp_path):
    from anyfem.mesh_jobs import MeshSettings

    app, faces, _ = plates()
    project = app.project
    session = project.mesh_session()
    settings = MeshSettings.create(.5, strategy="mapped", element_order="linear")
    parent = session.generate_mesh(settings)
    prior = project.mesh_numbering
    project.load_case().add_pressure(project.face(faces[0]), 500.)
    assert session.update_mesh(settings) is parent
    assert project.mesh_numbering == prior
    node = min(parent.nodes)
    parent.nodes[node] = parent.nodes[node] + np.array([0., 0., 1.])
    with pytest.raises(ValueError, match="content differs"):
        session.update_mesh(settings)
    with pytest.raises(ValueError, match="content differs"):
        session.save(tmp_path / "mutated.anyfem")
    assert not (tmp_path / "mutated.anyfem").exists()
    assert project.mesh_numbering == prior


@pytest.mark.parametrize("first_number", [0, 41])
def test_legacy_artifact_numbers_survive_open_and_explicit_rebuild(tmp_path, first_number):
    from anyfem.io.artifacts import ArtifactStore
    from anyfem.io.project_file import load_project, save_project
    from anyfem.mesh_jobs import MeshSettings, mesh_semantic_hash
    from anyfem.model.records import MeshRecord

    app, _, _ = plates()
    project = app.project
    path = tmp_path / "legacy.anyfem"
    nodes = {first_number + i: np.asarray(xyz, float)
             for i, xyz in enumerate(((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)))}
    old = Mesh(nodes=nodes, quads={first_number: tuple(nodes)})
    record = MeshRecord(name="Legacy mesh", source_model_hash=app.session.revision.model_hash,
                        mesh_input_hash="old mesh inputs", mesh_hash="")
    record.mesh_hash = mesh_semantic_hash(old, model_hash=record.source_model_hash,
                                          mesh_input_hash=record.mesh_input_hash)
    artifact = ArtifactStore(path).write_mesh(old, mesh_id=record.id, mesh_hash=record.mesh_hash)
    record.artifact_id = artifact.id
    project.artifacts[artifact.id] = artifact
    project.mesh_records[record.id] = record
    save_project(project, path)
    session = load_project(path).mesh_session(path)
    assert session.mesh_record_id == record.id
    assert set(session.mesh.quads) == {first_number}
    settings = MeshSettings.create(.5, strategy="mapped", element_order="linear")
    with pytest.raises(ValueError, match="baseline"):
        session.update_mesh(settings)
    rebuilt = session.generate_mesh(settings)
    assert min(rebuilt.nodes) > max(nodes)
    assert min(rebuilt.quads) > first_number
    session.save()
    assert set(ArtifactStore(path).read_mesh(artifact).quads) == {first_number}
