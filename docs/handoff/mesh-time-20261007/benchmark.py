"""Reproducible source-snapshot meshing and fixed-load response evidence.

Use run_checked.py for process limits/accounting. This script never calibrates
the example and writes only to the explicitly selected evidence directory.
"""
from __future__ import annotations

import argparse
from collections import Counter
import functools
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import time

REPOSITORIES = ("ANYfem", "ANYmesh", "ANYgeometry", "ANYsolver", "ANYloads",
                "ANYsection", "ANYmaterial", "ANYfileIO")
PACKAGES = ("anyfem", "anymesher", "anygeometry", "anysolver", "anyloads",
            "anysection", "anymaterial", "anyfileio")


def configure(tree, evidence):
    paths = []
    artifact = evidence / "artifacts" / tree / "installed"
    for name in REPOSITORIES:
        if artifact.is_dir() and name in REPOSITORIES[:3]:
            continue
        if tree == "installed" and name in REPOSITORIES[:3]:
            continue
        if tree == "live" and name in REPOSITORIES[:3]:
            root = Path("C:/Github")
        else:
            root = evidence / ("candidate" if tree == "candidate" and name in REPOSITORIES[:3] else "baseline")
        paths.append(str(root / name / "src"))
    if tree == "installed":
        paths.insert(0, str(evidence / "installed"))
    elif artifact.is_dir():
        paths.insert(0, str(artifact))
    sys.path[:0] = paths
    os.environ["PYTHONPATH"] = os.pathsep.join(paths)
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["MKL_NUM_THREADS"] = "1"
    return paths


def dump(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False), encoding="utf-8")


def make_project(case):
    from anyfem.application import examples as ex
    if case == "basic5":
        return ex.floating_barge_section()
    if case == "large_arcs":
        return ex.floating_barge_section(
            length=15., bulkheads=(5., 10.), girders=(0., 4., 8., 12.),
            openings=ex.BARGE_LARGE_OPENINGS, stress_scale=ex.BARGE_LARGE_STRESS_SCALE,
            target_size=.25,
        )
    return ex.floating_barge_large()


class Timings:
    def __init__(self):
        self.seconds = Counter()
        self.calls = Counter()
        self.depth = Counter()

    def wrap(self, fn, label):
        @functools.wraps(fn)
        def measured(*args, **kwargs):
            self.depth[label] += 1
            started = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                self.calls[label] += 1
                self.depth[label] -= 1
                if not self.depth[label]:
                    self.seconds[label] += time.perf_counter() - started
        return measured

    def install(self):
        from anygeometry import GeometryModel
        import anygeometry.attachment_remapping as attachment_remapping
        from anymesher.structural_pipeline import StructuralMeshingPipeline
        from anymesher.mesh_bvh import MeshElementBVH
        self.calls.update({'bvh_builds': 0, 'bvh_lookups': 0})
        if hasattr(MeshElementBVH, '_ensure_built'):
            original_build = MeshElementBVH._ensure_built
            measured_build = self.wrap(original_build, 'bvh_builds')
            def ensure_built(bvh):
                return (measured_build(bvh) if bvh._records is None
                        else original_build(bvh))
            MeshElementBVH._ensure_built = ensure_built
        else:
            MeshElementBVH.__init__ = self.wrap(MeshElementBVH.__init__, 'bvh_builds')
        lookup_name = ('_candidate_ids' if hasattr(MeshElementBVH, '_candidate_ids')
                       else 'candidates')
        setattr(MeshElementBVH, lookup_name, self.wrap(
            getattr(MeshElementBVH, lookup_name), 'bvh_lookups'))
        # This alias is specific to attachment remapping: do not count unrelated
        # arrangement clips through every module sharing the original function.
        attachment_remapping._clip_intervals = self.wrap(
            attachment_remapping._clip_intervals, "attachment_clip_calls")
        original_bounds = GeometryModel.bounds
        measured_bounds = self.wrap(original_bounds, "planner_bounds")

        def bounds(model, *args, **kwargs):
            if self.depth["plan_independent_components"]:
                return measured_bounds(model, *args, **kwargs)
            return original_bounds(model, *args, **kwargs)

        GeometryModel.bounds = bounds
        for name in ("apply_connectivity", "_add_attachment_coupling"):
            setattr(StructuralMeshingPipeline, name, self.wrap(getattr(StructuralMeshingPipeline, name), name))
        for module, name in (
            ("anyfem.structural_preparation", "prepare_structural_connectivity"),
            ("anygeometry.component_partition", "plan_independent_components"),
            ("anygeometry.attachment_remapping", "remap_face_attachments"),
            ("anygeometry.trimmed_charts", "query_trimmed_surface_charts"),
            ("anygeometry.trimmed_charts", "query_trimmed_surface_charts_by_face"),
            ("anymesher.hybrid", "generate_hybrid_mesh_result"),
        ):
            original = getattr(importlib.import_module(module), name)
            wrapped = self.wrap(original, name)
            for loaded in list(sys.modules.values()):
                if getattr(loaded, name, None) is original:
                    setattr(loaded, name, wrapped)


def solve(project, mesh, output):
    import numpy as np
    from anyfem.solve.run import solve_linear_static
    from anysolver.assembly import assemble_system, compute_reactions
    started = time.perf_counter()
    solution = solve_linear_static(project, mesh=mesh)
    print("linear solve complete", flush=True)
    stiffness, force, _ = assemble_system(solution.built.fe_model, solution.built.load_case)
    energy = .5 * float(solution.displacements @ (stiffness @ solution.displacements))
    external_work = float(solution.displacements @ force)
    reactions = compute_reactions(solution.built.fe_model, solution.displacements, solution.built.load_case)
    print("reaction recovery complete", flush=True)
    stresses = solution.stresses().element_stresses
    node_ids = np.array(sorted(mesh.nodes), dtype=np.int64)
    displacements = np.array([solution.node_displacement(int(node)) for node in node_ids])
    coordinates = np.array([mesh.nodes[int(node)] for node in node_ids])
    reaction_ids = np.array(sorted(reactions), dtype=np.int64)
    reaction_values = np.array([reactions[int(node)] for node in reaction_ids])
    stress_ids = np.array(sorted(e for e, row in stresses.items() if row.get("von_mises") is not None), dtype=np.int64)
    stress_peak = np.array([float(np.max(np.abs(stresses[int(e)]["von_mises"]))) for e in stress_ids])
    stress_samples = [np.asarray(stresses[int(e)]["von_mises"], dtype=float).ravel()
                      for e in stress_ids]
    stress_offsets = np.cumsum([0] + [values.size for values in stress_samples])
    np.savez_compressed(output / "response.npz", node_ids=node_ids, coordinates=coordinates,
                        displacements=displacements, reaction_ids=reaction_ids,
                        reactions=reaction_values, stress_ids=stress_ids, stress_peak=stress_peak,
                        stress_offsets=stress_offsets, stress_von_mises=np.concatenate(stress_samples))
    result = {"solve_and_recovery_seconds": time.perf_counter() - started,
              "strain_energy": energy, "external_work": external_work,
              "max_translation": float(np.max(np.linalg.norm(displacements[:, :3], axis=1))),
              "peak_von_mises": float(stress_peak.max()),
              "peak_stress_element": int(stress_ids[stress_peak.argmax()]),
              "reaction_resultant_components": reaction_values.sum(axis=0).tolist(),
              "displacement_count": len(displacements), "stress_count": len(stress_ids)}
    dump(output / "response.json", result)
    print(json.dumps(result), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tree", choices=("baseline", "candidate", "live", "installed"), required=True)
    parser.add_argument("--evidence", type=Path, default=Path("C:/ANYworkspaceWork/meshing-speedup-20261007"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policy", choices=("off", "auto"), default="auto")
    parser.add_argument("--case", choices=("basic5", "large_arcs", "large_full"), default="large_full")
    parser.add_argument("--project", type=Path)
    parser.add_argument("--save-mesh", action="store_true")
    parser.add_argument("--process-job", action="store_true")
    parser.add_argument("--solve-from", type=Path)
    args = parser.parse_args()
    import_roots = configure(args.tree, args.evidence)
    args.output.mkdir(parents=True, exist_ok=False)
    import anymesher.native_cpp as native
    from anyfem.io.project_file import project_to_dict, project_from_dict
    from anymesher.serialize import mesh_to_dict, mesh_from_dict
    from anyfem.mesh_execution import MeshExecutionPolicy
    import numpy as np
    capabilities = {name: bool(getattr(native, name)) for name in (
        "NATIVE_CPP_AVAILABLE", "COMPILED_NATIVE_V2_AVAILABLE",
        "COMPILED_QUALITY_PIPELINE_AVAILABLE", "COMPILED_TRIANGULATION_AVAILABLE")}
    modules = {}
    for name in PACKAGES:
        module = importlib.import_module(name)
        modules[name] = str(Path(module.__file__).resolve())
        expected = next(Path(path) / name for path in import_roots
                        if (Path(path) / name / "__init__.py").exists())
        if Path(module.__file__).resolve().parent != expected.resolve():
            raise RuntimeError(f"wrong import origin for {name}: {module.__file__}")
    native_path = Path(native._compiled.__file__).resolve() if native.NATIVE_CPP_AVAILABLE else None
    identity = {"tree": args.tree, "python": sys.executable, "python_version": sys.version,
                "modules": modules, "native": capabilities,
                "native_extension": None if native_path is None else {
                    "path": str(native_path), "sha256": hashlib.sha256(native_path.read_bytes()).hexdigest()},
                "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "thread_environment": {key: os.environ.get(key) for key in
                                       ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")}}
    manifest = args.evidence / f"{args.tree}-manifest.json"
    if manifest.exists():
        identity["source_manifest"] = {"path": str(manifest),
                                       "sha256": hashlib.sha256(manifest.read_bytes()).hexdigest()}
    artifact_manifest = args.evidence / "artifacts" / args.tree / "manifest.json"
    if artifact_manifest.exists():
        identity["artifact_manifest"] = {
            "path": str(artifact_manifest),
            "sha256": hashlib.sha256(artifact_manifest.read_bytes()).hexdigest(),
        }
    try:
        from threadpoolctl import threadpool_info
        identity["threadpools"] = threadpool_info()
    except ImportError:
        identity["threadpools"] = "threadpoolctl unavailable"
    dump(args.output / "runtime.json", identity)
    if not all(capabilities.values()):
        raise RuntimeError("compiled baseline capabilities are required")
    if args.solve_from:
        project = project_from_dict(json.loads((args.solve_from / "project.json").read_text()))
        mesh = mesh_from_dict(json.loads((args.solve_from / "mesh.json").read_text()))
        solve(project, mesh, args.output)
        return
    project = project_from_dict(json.loads(args.project.read_text())) if args.project else make_project(args.case)
    dump(args.output / "project.json", project_to_dict(project))
    from anygeometry import to_dict as geometry_to_dict
    authored_geometry = geometry_to_dict(project.geometry)
    timing = Timings()
    timing.install()
    policy = MeshExecutionPolicy.off() if args.policy == "off" else MeshExecutionPolicy.auto()
    started = time.perf_counter()
    if args.process_job:
        from anyfem.document import DocumentSession
        from anyfem.mesh_jobs import MeshTaskManager, MeshSettings
        manager = MeshTaskManager(executor="process")
        try:
            future = manager.submit("speedup", DocumentSession(project).snapshot(),
                                    MeshSettings.create(project.target_size, element_order="linear", execution_policy=policy))
            future.result(timeout=280)
            events = []
            deadline = time.monotonic() + 5.
            while time.monotonic() < deadline:
                events.extend(manager.poll())
                if any(event.kind in ("completed", "failed", "cancelled") for event in events):
                    break
                time.sleep(.02)
            completed = [event for event in events if event.kind == "completed"]
            if len(completed) != 1:
                raise RuntimeError(f"mesh job did not complete: {[(e.kind, e.message) for e in events]}")
            mesh = completed[0].payload.mesh
            execution = dict(completed[0].payload.execution)
        finally:
            manager.shutdown()
    else:
        mesh = project.generate_mesh(project.target_size, order="linear", execution_policy=policy)
        execution = dict(project._last_mesh_execution)
    elapsed = time.perf_counter() - started
    if geometry_to_dict(project.geometry) != authored_geometry:
        raise RuntimeError("public meshing mutated authored geometry")
    payload = mesh_to_dict(mesh)
    material = {key: value for key, value in payload.items() if key not in
                ("geometry_model_id", "geometry_revision", "structural_preparation", "hybrid_diagnostics")}
    material_hash = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    result = {"tree": args.tree, "policy": args.policy, "case": args.case,
              "process_job": args.process_job, "mesh_wall_seconds": elapsed,
              "nodes": len(mesh.nodes), "shells": len(mesh.shells), "beams": len(mesh.beams),
              "couplings": len(mesh.couplings), "execution": execution,
              "seconds": dict(timing.seconds), "calls": dict(timing.calls),
              "material_hash": material_hash,
              "source_geometry_hash": hashlib.sha256(json.dumps(
                  authored_geometry, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest(),
              "runtime": mesh.hybrid_diagnostics.get("runtime", {})}
    dump(args.output / "measurement.json", result)
    if args.save_mesh:
        dump(args.output / "mesh.json", payload)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
