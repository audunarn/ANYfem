"""Build the native solved project and ZIP used by the Qt fatigue example."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
from anyfatigue.extract import extract
from anyfatigue.measures import StressMeasure
from anyfatigue.sn_curves import get_curve
from anyfatigue.timeseries import LoadBlock, TimeSeriesSettings, assess_time_series
from anyfesolver import RecoveryConfig

from anyfem.application.barge_fatigue import barge_fatigue_detail, bracketed_barge_fatigue
from anyfem.application.fatigue import load_fatigue_input, prepare_fatigue_location
from anyfem.application.mesh_publication import mesh_source_digests
from anyfem.document import DocumentSession, canonical_hash
from anyfem.io.artifacts import ArtifactStore
from anyfem.io.project_file import load_project, save_project
from anyfem.io.result_artifact import write_solution_artifact
from anyfem.jobs import analysis_hash
from anyfem.mesh_jobs import mesh_semantic_hash
from anyfem.mesh_numbering import number_full_mesh, published_state
from anyfem.model.records import JobRecord, JobStatus, MeshRecord
from anyfem.solve.run import solve_linear_static_many


def mesh_project(path):
    project = bracketed_barge_fatigue()
    print("Generating bracketed 5 m mesh", flush=True)
    mesh = project.generate_mesh(project.target_size, _publication_allocation=project.mesh_numbering)
    if mesh.hybrid_diagnostics.get("automation", {}).get("status") == "inspection_only":
        raise ValueError("The generated mesh was marked inspection only")
    mesh = number_full_mesh(mesh, project.mesh_numbering, source_geometry=project.geometry)
    preparation = dict(project._last_mesh_preparation)
    snapshot = DocumentSession(project).snapshot()
    model_hash = snapshot.revision.model_hash
    settings_hash = canonical_hash(snapshot.document["meshing"])
    mesh_hash = mesh_semantic_hash(mesh, model_hash=model_hash, mesh_input_hash=settings_hash,
                                  structural_preparation=preparation)
    record = MeshRecord(name="Hull and refined internal brackets", source_model_hash=model_hash,
                        mesh_input_hash=settings_hash, mesh_hash=mesh_hash,
                        structural_preparation=preparation,
                        summary={"active_mesh": True, "nodes": mesh.num_nodes, "elements": mesh.num_elements,
                                 "mesh_sources": mesh_source_digests(project)})
    ref = ArtifactStore(path).write_mesh(mesh, mesh_id=record.id, document_id=project.document_id,
                                         model_hash=model_hash, mesh_hash=mesh_hash,
                                         structural_preparation=preparation)
    record.artifact_id = ref.id
    project.add_artifact(ref)
    project.add_mesh_record(record)
    project.mesh_numbering = published_state(project.mesh_numbering, mesh, record.id)
    save_project(project, path)
    print(f"Saved {mesh.num_nodes} nodes and {mesh.num_elements} elements", flush=True)
    return project, mesh, record


def solve_history(project, mesh, record, path):
    cases = [case.name for case in project.load_cases.values() if case.parent and case.time is not None]
    cases.sort(key=lambda name: project.load_cases[name].time)
    definition, = project.analyses.values()
    if not cases or len({project.load_cases[name].time for name in cases}) != len(cases):
        raise ValueError("The example needs distinct timed load cases")
    snapshot = DocumentSession(project).snapshot()
    digest = analysis_hash(definition, snapshot.document["output_requests"], document=snapshot.document)
    identity = dict(mesh_hash=record.mesh_hash, model_hash=snapshot.revision.model_hash, analysis_hash=digest)
    job = JobRecord(analysis_id=definition.id, name=definition.name, **identity,
                    input_hash=canonical_hash(identity), created_utc=datetime.now(timezone.utc).isoformat())
    print(f"Solving {len(cases)} signed load cases", flush=True)
    job.started_utc = datetime.now(timezone.utc).isoformat()
    solution = solve_linear_static_many(project, mesh=mesh, load_cases=cases, load_tolerance=0, progress=print)
    if solution.status != "converged" or tuple(solution.case_names) != tuple(cases):
        raise ValueError(f"Incomplete bending history: {solution.status}")
    # Keep both signed surfaces at the original integration points. A beam recovery
    # or a von Mises envelope cannot supply this shell fatigue history.
    components = tuple(f"global_{c}_{s}" for s in ("top", "bot") for c in ("xx","yy","zz","xy","yz","xz"))
    scope = RecoveryConfig(element_ids=tuple(mesh.shells), components=(*components, "physical_director"),
                           include_displacements=False, include_reactions=False)
    for shape in solution.shapes:
        print(f"Recovering global shell surfaces for {shape.label}", flush=True)
        shape._requested_global_stress = shape.stresses(return_global=True, recovery_config=scope)
    result = write_solution_artifact(ArtifactStore(path), solution, job_id=job.id, document_id=project.document_id,
                                     mesh_id=record.id, **identity)
    job.result_artifact_id = result.id
    job.finished_utc = datetime.now(timezone.utc).isoformat()
    job.status = JobStatus.COMPLETED
    project.add_artifact(result)
    project.add_job(job)
    save_project(project, path)
    print("Saved solved project and signed history", flush=True)


def _assess_terminal_weld(project, mesh, data, side, leg):
    face, edge, direction = barge_fatigue_detail(project, side, leg)
    chain = mesh.nodes_of_edge[edge]
    middle = chain[len(chain) // 2]
    elements = mesh.elements_of_face[face]
    element = next(eid for eid in elements if middle in mesh.shells[eid])
    thickness = data.thickness_mm_for((element,))
    # The weld is on the underside of the deck flange or on the +X side of
    # the bulkhead flange. Select that skin using the submitted shell directors.
    toward_bracket = (0., 0., -1.) if leg == "deck" else (1., 0., 0.)
    surface_mesh = data.source.mesh
    rows = [surface_mesh.element_row(eid) for eid in elements]
    facing = surface_mesh.normals_for(rows) @ toward_bracket
    if np.allclose(facing, 1.):
        surface = "top"
    elif np.allclose(facing, -1.):
        surface = "bottom"
    else:
        raise ValueError(f"{side} {leg} flange has inconsistent weld-side directors")
    detail = prepare_fatigue_location(data, (element,), target=project.edge(edge),
                                      thickness_mm=thickness, direction=direction, surface=surface)
    source = detail.source
    settings = TimeSeriesSettings(curve=get_curve("D", "air"), thickness_mm=thickness,
                                  design_life_years=25., dff=1.,
                                  blocks=(LoadBlock("Illustrative bending", source.case_labels),),
                                  measure=StressMeasure.parse("normal_to_weld"))
    assessment = assess_time_series(source, (detail.location,), settings)
    signals = np.asarray([settings.measure.signed_series(pt.tensors, pt.normal, pt.weld_direction)
                          for pt in extract(source, detail.location)])
    if not np.isfinite(signals).all() or not np.any((signals.min(axis=1) < 0) & (signals.max(axis=1) > 0)):
        raise ValueError(f"{side} {leg} toe needs a finite reversing stress history")
    if not np.isfinite(assessment.critical.damage) or assessment.critical.damage <= 0:
        raise ValueError(f"{side} {leg} toe needs a finite positive damage")
    print(f"{side} {leg} toe: {len(signals)} stations, {len(source.case_labels)} frames, "
          f"{thickness:g} mm {surface} flange, range {signals.min():.6g} to {signals.max():.6g} MPa; "
          f"governing Miner damage {assessment.critical.damage:.8g} for 25 example years", flush=True)
    return {"element_id": int(element), "face_id": int(face), "edge_id": int(edge),
            "surface": surface, "side": "auto", "direction": list(direction),
            "field": f"stress_global_{'xx' if leg == 'deck' else 'zz'}_{'top' if surface == 'top' else 'bot'}",
            "frame": int(np.argmax(np.max(np.abs(signals), axis=0)))}


def verify_and_pack(path):
    project = load_project(path)
    job, = project.jobs.values()
    record, = project.mesh_records.values()
    store = ArtifactStore(path)
    mesh_ref = project.artifacts[record.artifact_id]
    result_ref = project.artifacts[job.result_artifact_id]
    mesh = store.read_mesh(mesh_ref, mesh_id=record.id, document_id=project.document_id, mesh_hash=record.mesh_hash)
    dataset = store.open_result(result_ref)
    try:
        data = load_fatigue_input(dataset=dataset, mesh=mesh, project=project)
        toes = {(side, leg): _assess_terminal_weld(project, mesh, data, side, leg)
                for side in ("Port", "Starboard") for leg in ("deck", "bulkhead")}
        selected = toes["Port", "deck"]
        regions = {r.name: r for r in project.regions}
        focus = {ref.id for ref in regions["Port knee detail"].definition.anchors}
        context = {ref.id for ref in regions["Port bracket with shell stiffeners"].definition.anchors}
        preset = {**selected, "job_id": job.id, "context_face_ids": sorted(context),
                  "focus_face_ids": sorted(focus | {selected["face_id"]}),
                  "camera_direction": [0.8, -1.3, -0.9]}
    finally:
        dataset.close()
    archive = path.parent / "barge5_fatigue.zip"
    with ZipFile(archive, "w", compression=ZIP_DEFLATED, compresslevel=6) as package:
        for file in (path, store.resolve(mesh_ref.uri), store.resolve(result_ref.uri)):
            package.write(file, file.relative_to(path.parent))
        package.writestr("preset.json", json.dumps(preset, indent=2))
    print(f"Prepared {archive} ({archive.stat().st_size:,} bytes)", flush=True)
    return preset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="output directory for the new example")
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    path = args.directory / "Barge5Fatigue.anyfem"
    if path.exists() or ArtifactStore(path).root.exists():
        parser.error("Choose a fresh output directory")
    project, mesh, record = mesh_project(path)
    solve_history(project, mesh, record, path)
    verify_and_pack(path)


if __name__ == "__main__":
    main()
