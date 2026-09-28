"""One supervised RA1 application operation; invoked by runner only."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
import sys
import time
import traceback
from types import SimpleNamespace

import numpy as np


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False), encoding="utf-8")


def _stage(folder: Path, name: str) -> None:
    _write(folder / "progress.json", {"stage": name, "started_utc": time.time()})


def _source_project(case: str, h: float, refined: bool):
    from anyfem import Project, steel
    from anyfem.commands import AddCylinder, AddFeature, CommandStack
    from anymesher.refinement import Refinement
    from anystruct.fem_integration import (
        RuntimeFEMOptions, active_line_snapshot, example_runtime_app,
        runtime_geometry_summary, runtime_geometry_projection,
    )
    from anystruct.quad_first_integration import build_quad_first_project

    if case == "fem_deck":
        project = Project("RA1 cylinder deck")
        from anyfem.model.formulations import ShellFormulationPolicy
        project.shell_formulation_policy = ShellFormulationPolicy.legacy_compatible()
        commands = CommandStack(project)
        commands.run(AddCylinder(
            kind="generator.cylinder", name="Cylinder", label="add cylinder",
            parameters={
                "radius": 0.5, "height": 2.0, "circumferential_segments": 12,
                "origin": (0.0, 0.0, 0.0), "axis": (0.0, 0.0, 1.0),
                "radial_direction": (1.0, 0.0, 0.0),
                "longitudinal_spacing": 0.5, "ring_spacing": 1.0,
            },
        ))
        commands.run(AddFeature(
            "generator.plate", name="Plate", parameters={
                "length": 2.0, "width": 2.0,
                "origin": (-1.0, -1.0, 1.0),
                "u_direction": (1.0, 0.0, 0.0),
                "v_direction": (0.0, 1.0, 0.0),
                "semantic_group": "shell",
            },
        ))
        project.add_material(steel("S355", 0.02))
        project.add_plate_section("shell", 0.02, "S355")
        project.assign_plates(tuple(sorted(project.geometry.faces)), "shell")
        from anyfem.model.attributes import fixed
        for edge_id in sorted(project.geometry.edges):
            if len(project.geometry.faces_using_edge(edge_id)) == 1:
                project.add_support(fixed(project.edge(edge_id)))
        for face_id in sorted(project.geometry.faces):
            project.load_case().add_pressure(project.face(face_id), 1000.0)
        center = (0.5, 0.0, 1.0)
    else:
        name = "girder_panel" if case == "structure_panel" else "cylinder"
        snapshot = active_line_snapshot(example_runtime_app(name))
        opts = RuntimeFEMOptions(
            include_stiffeners=False, include_girders=False,
            include_end_lids=False,
        )
        summary = runtime_geometry_summary(snapshot, opts)
        projection = runtime_geometry_projection(summary, opts)
        if case == "structure_panel":
            # A declared interior source point placed on an existing h=1 m
            # control edge permits a causal local-size comparison at radius
            # 0.35 m without changing the plate's boundary or dimensions.
            center = (float(summary["length_m"]) / 2 + .5,
                      float(summary["width_m"]) / 2, 0.0)
        else:
            center = tuple(float(v) for v in projection.geometry_model.vertex_position(min(projection.geometry_model.vertices)))
        zone = Refinement(size=h / 3, radius=0.35, center=center, growth=1.5) if refined else None
        project = build_quad_first_project(
            projection, thickness_m=float(summary["thickness_m"]),
            pressure_pa=1000.0, refinement=zone,
        )
        if case == "structure_panel":
            project.geometry.add_point(*center)
        return project, center, summary
    if refined:
        project.refinements.append(Refinement(size=h / 3, radius=0.35, center=center, growth=1.5))
    return project, center, {"geometry": "cylinder deck", "thickness_m": 0.02}


def freeze(case: str, destination: Path) -> None:
    """Write one exact source project reused by all resolutions and orders."""

    from anyfem.io.project_file import project_to_dict

    coarse = {"fem_deck": .5, "structure_panel": 2., "structure_cylinder": 1.}[case]
    project, center, summary = _source_project(case, coarse, False)
    _write(destination, {
        "project": project_to_dict(project),
        "center": tuple(center),
        "summary": summary,
    })


class _OwnerPatch:
    def __init__(self, geometry, face):
        self.geometry, self.face = geometry, face

    def point(self, u, v):
        return self.geometry.face_point(self.face, u, v)

    def uv(self, point):
        return np.asarray(self.geometry.face_local_uv(self.face, point))

    def derivatives(self, u, v):
        du, dv = self.geometry.face_derivatives_many(self.face, ((u, v),))
        return du[0], dv[0]

    def normal(self, u, v):
        du, dv = self.derivatives(u, v)
        normal = np.cross(du, dv)
        return normal / np.linalg.norm(normal)


def _fixture(project, center, case):
    from anymesher.refinement import SizeField

    geometry = project.geometry
    fixture = SimpleNamespace(
        case=case,
        geometry=geometry,
        patches={face: _OwnerPatch(geometry, face) for face in geometry.faces},
        face_edges={
            face: tuple((item.edge, item.forward) for item in geometry.faces[face].loop)
            for face in geometry.faces
        },
        center=np.asarray(center, dtype=float),
        sheet=None,
        members=(),
        member_end=None,
    )
    fixture.field = lambda size, graded: SizeField(geometry, size, project.refinements if graded else ())
    return fixture


def _mesh_digest(mesh):
    from benchmarks.sg1.measure import mesh_signature
    return mesh_signature(mesh)


def _direct_owner_mesh(project, h, order):
    """Construct the same owner closure independently of Project.generate_mesh."""

    from anygeometry.closure import extract_model_closure
    from anymesher.hybrid import generate_hybrid_mesh
    from anyfem.quad_first import sg1_quad_options
    from anyfem.structural_preparation import (
        prepare_structural_connectivity, remap_mesh_to_source,
    )

    source = project.geometry
    handles = tuple(
        [source.handle("face", face) for face in sorted(source.faces)]
        + [source.handle("sheet", sheet) for sheet in sorted(source.sheets)]
    )
    closure = extract_model_closure(
        source, handles, include_structural_closure=True, include_features=False,
    )
    working = closure.working_model
    prepare_structural_connectivity(
        working, source_model_id=str(closure.source_model_id),
        source_revision=closure.source_revision, member_ids=(),
    )
    mesh = generate_hybrid_mesh(
        working, target_size=h, strategy="native", native_backend="python",
        order=order, quad_options=sg1_quad_options(),
        face_ids=tuple(sorted(working.faces)), member_ids=(),
        refinements=project.refinements, certification_mode="strict",
        structural_preparation={
            "automatic_face_connections": False,
            "automatic_member_connections": False,
            "automatic_member_sheet_connections": False,
            "declare_missing_owners": True,
        },
        mutation_policy="working_copy",
    )
    remap_mesh_to_source(mesh, closure)
    return mesh


def _structure_runtime_entrypoint_checks(folder, case, h):
    """Exercise the actual ANYstructure runtime before direct-owner comparison."""

    from unittest.mock import patch

    from anystruct import quad_first_integration as integration
    from anystruct.fem_integration import (
        RuntimeFEMOptions, active_line_snapshot, example_runtime_app,
        run_runtime_fem,
    )
    from anyfem.solve.build import build_fe_model
    from anyfem.solve.run import solve_linear_static
    from anysolver import ResourceConfig, assemble_load_vector

    snapshot = active_line_snapshot(example_runtime_app(
        "girder_panel" if case == "structure_panel" else "cylinder"
    ))
    options = RuntimeFEMOptions(
        mesh_method="quad-first", analysis_type="linear static",
        runtime_solver="static only", include_stiffeners=False,
        include_girders=False, include_end_lids=False,
        shell_element_order="S8", mesh_size_m=h, pressure_pa=1000.0,
    )
    captured = []
    original = integration.run_quad_first_application

    def capture(*args, **kwargs):
        run = original(*args, **kwargs)
        captured.append(run)
        return run

    def progress(message):
        if message in (
            "quad-first mesh start", "quad-first assembly start",
            "quad-first solve start",
        ):
            _stage(folder, "runtime-" + message.removeprefix("quad-first ").replace(" ", "-"))

    _stage(folder, "runtime-entrypoint")
    with patch.object(integration, "run_quad_first_application", side_effect=capture):
        response = run_runtime_fem(snapshot, options, status_callback=progress)
    if len(captured) != 1 or captured[0].solution is None:
        raise RuntimeError("ANYstructure runtime did not consume one quadratic application mesh")
    application = captured[0]
    _stage(folder, "runtime-direct-owner-mesh")
    owner = _direct_owner_mesh(application.project, h, "quadratic")
    same_mesh = (
        set(owner.nodes) == set(application.mesh.nodes)
        and all(np.array_equal(owner.nodes[node], application.mesh.nodes[node]) for node in owner.nodes)
        and owner.quads == application.mesh.quads and owner.tris == application.mesh.tris
        and owner.elements_of_face == application.mesh.elements_of_face
        and owner.elements_of_sheet == application.mesh.elements_of_sheet
        and owner.couplings == application.mesh.couplings
    )
    _stage(folder, "runtime-direct-owner-assembly")
    built = build_fe_model(application.project, owner, load_case="default")
    _stage(folder, "runtime-direct-owner-solve")
    reference = solve_linear_static(
        built=built, resource_config=ResourceConfig(solver_threads=1, assembly_threads=1),
    )
    force = assemble_load_vector(built.fe_model, built.load_case)
    if isinstance(force, tuple):
        force = force[0]
    reference_energy = 0.5 * float(np.dot(reference.displacements, force))
    displacement_error = float(
        np.linalg.norm(application.solution.displacements - reference.displacements)
        / max(np.linalg.norm(reference.displacements), 1e-12)
    )
    energy_error = abs(float(response.summary["strain_energy_j"]) - reference_energy) / max(abs(reference_energy), 1e-12)
    checks = [
        {"name": "structure-runtime-mesh-equality", "status": "passed" if same_mesh else "failed"},
        {"name": "structure-runtime-solver-parity", "status": "passed" if max(displacement_error, energy_error) <= 1e-8 else "failed",
         "displacement_error": displacement_error, "energy_error": energy_error},
        {"name": "structure-runtime-options-and-certificate", "status": "passed" if (
            response.summary["sg1_compatible_options"]
            and response.summary["strict_validation_status"] == "CERTIFIED_POSITIVE"
            and response.summary["mesh_info"]["shells"] == len(application.mesh.shells)
            and response.summary["analysis_state"] == "linear-static solved"
        ) else "failed"},
    ]
    _write(folder / "structure-runtime-parity.json", {
        "checks": checks, "application_source_model_id": application.source_model_id,
        "mesh_hash": _mesh_digest(application.mesh),
        "direct_owner_mesh_hash": _mesh_digest(owner),
        "application_energy_j": response.summary["strain_energy_j"],
        "direct_owner_energy_j": reference_energy,
        "application_stages_seconds": dict(application.stage_seconds),
    })
    return checks


def _junction_checks(mesh, checks):
    """Extend SG1's two-face oracle for declared deck/cylinder four-sector seams."""

    declared = {tuple(sorted(pair)) for pair in mesh.declared_plate_junction_edges}
    if not declared:
        return checks
    expected_names = {f"global-edge/{edge}/incidence" for edge in declared}
    kept = [check for check in checks if check["name"] not in expected_names]
    seen: dict[tuple[int, int], list[tuple[int, int | None]]] = {edge: [] for edge in declared}
    for face, element_ids in mesh.elements_of_face.items():
        for element_id in element_ids:
            body = mesh.shells[element_id]
            corners = 4 if len(body) in (4, 8) else 3
            for index in range(corners):
                edge = tuple(sorted((body[index], body[(index + 1) % corners])))
                if edge in seen:
                    midpoint = body[corners + index] if len(body) > corners else None
                    seen[edge].append((face, midpoint))
    for edge in sorted(declared):
        records = seen[edge]
        ownership = sorted([face for face, _ in records])
        valid = (
            len(records) == 4
            and len(set(ownership)) == 3
            and sorted(ownership.count(face) for face in set(ownership)) == [1, 1, 2]
        )
        valid = valid and len({midpoint for _, midpoint in records}) == 1
        kept.append({
            "name": f"declared-junction/{edge}/four-sector-incidence-and-midside",
            "status": "passed" if valid else "failed",
            "faces": [face for face, _ in records],
            "midsides": [midpoint for _, midpoint in records],
        })
    return kept


def run(spec, folder: Path):
    from anygeometry.serialization import to_dict as geometry_to_dict
    from anymesher.serialize import mesh_to_dict, mesh_from_dict
    from anyfem.quad_first import sg1_quad_options
    from benchmarks.sg1.measure import audit, promotion_checks, mesh_signature
    from anyfem.solve.build import build_fe_model
    from anyfem.solve.run import solve_linear_static
    from anysolver import assemble_load_vector
    from anysolver.assembly import compute_reactions
    from anysolver import ResourceConfig
    from anysolver.matrix_assembly import assemble_stiffness_matrix
    from anysolver.assembly import build_constraint_transformation
    from benchmarks.sg1.consumer import probe

    case, h, order, refined = spec["case"], float(spec["h"]), spec["order"], bool(spec["refined"])
    if spec.get("fixture"):
        from anyfem.io.project_file import project_from_dict
        from anymesher.refinement import Refinement

        frozen = json.loads(Path(spec["fixture"]).read_text(encoding="utf-8"))
        project = project_from_dict(frozen["project"])
        center = tuple(frozen["center"])
        summary = frozen["summary"]
        if refined:
            project.refinements.append(
                Refinement(size=h / 3, radius=.35, center=center, growth=1.5)
            )
    else:
        project, center, summary = _source_project(case, h, refined)
    source = geometry_to_dict(project.geometry)
    _write(folder / "source.json", source)
    source_digest = hashlib.sha256(json.dumps(source, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    _stage(folder, "mesh")
    started = time.perf_counter()
    mesh = project.generate_mesh(h, order=order, strategy="quad_first", quad_options=sg1_quad_options(), certification_mode="strict")
    mesh_seconds = time.perf_counter() - started
    if mesh_seconds > 120:
        raise TimeoutError("mesh exceeded 120 seconds")
    if len(mesh.shells) > 1500 or 6 * len(mesh.nodes) > 15000:
        raise RuntimeError("RA1 shell or DOF resource cap exceeded")
    _write(folder / "mesh.json", mesh_to_dict(mesh))
    checks = []
    checks.append({"name": "source-unchanged", "status": "passed" if geometry_to_dict(project.geometry) == source else "failed"})
    checks.append({"name": "serialization", "status": "passed" if mesh_signature(mesh_from_dict(mesh_to_dict(mesh))) == mesh_signature(mesh) else "failed"})
    _stage(folder, "validation")
    started = time.perf_counter()
    metrics = audit(_fixture(project, center, case), mesh, h, refined)
    validation_seconds = time.perf_counter() - started
    _write(folder / "mesh-metrics.json", metrics)
    checks.extend(_junction_checks(mesh, metrics["checks"]))
    if order == "quadratic":
        high = mesh.hybrid_diagnostics.get("high_order_geometry", {})
        checks.append({"name": "strict-certificate", "status": "passed" if high.get("status") == "CERTIFIED_POSITIVE" else "failed", "value": high.get("status")})
    if order == "quadratic" and not refined:
        _stage(folder, "linear-promotion-control")
        linear = project.generate_mesh(h, order="linear", strategy="quad_first", quad_options=sg1_quad_options(), certification_mode="strict")
        checks.extend(promotion_checks(linear, mesh))
    if spec.get("direct_parity"):
        _stage(folder, "direct-owner-parity")
        owner = _direct_owner_mesh(project, h, order)
        equal = (
            set(owner.nodes) == set(mesh.nodes)
            and all(np.array_equal(owner.nodes[n], mesh.nodes[n]) for n in mesh.nodes)
            and owner.quads == mesh.quads and owner.tris == mesh.tris
            and owner.elements_of_face == mesh.elements_of_face
            and owner.elements_of_sheet == mesh.elements_of_sheet
            and owner.couplings == mesh.couplings
        )
        checks.append({"name": "direct-owner-mesh-equality", "status": "passed" if equal else "failed"})
        _write(folder / "direct-owner-mesh.json", mesh_to_dict(owner))
    solver = None
    if spec.get("solve"):
        _stage(folder, "assembly")
        started = time.perf_counter()
        built = build_fe_model(project, mesh, load_case="default")
        assembly_seconds = time.perf_counter() - started
        _stage(folder, "solve")
        started = time.perf_counter()
        resource_config = ResourceConfig(solver_threads=1, assembly_threads=1)
        result = solve_linear_static(built=built, resource_config=resource_config)
        solve_seconds = time.perf_counter() - started
        if solve_seconds > 120:
            raise TimeoutError("solve exceeded 120 seconds")
        f = assemble_load_vector(built.fe_model, built.load_case)
        if isinstance(f, tuple):
            f = f[0]
        energy = 0.5 * float(np.dot(result.displacements, f))
        convergence = result.info.get("convergence_info", {})
        constraints = result.info.get("constraint_postcheck", {})
        stiffness, _ = assemble_stiffness_matrix(built.fe_model)
        _, _, transformation, _, _, constraint_info = build_constraint_transformation(
            stiffness, f, built.fe_model
        )
        free_residual = float(
            np.linalg.norm(transformation.T @ (stiffness @ result.displacements - f))
            / max(np.linalg.norm(transformation.T @ f), 1e-30)
        )
        fixed_probes = probe(_fixture(project, center, case), mesh, built.fe_model, result.displacements)
        checks.extend((
            {"name": "solver-finite", "status": "passed" if np.all(np.isfinite(result.displacements)) else "failed"},
            {"name": "solver-energy", "status": "passed" if energy > 0 and np.isfinite(energy) else "failed", "value": energy},
            {"name": "solver-free-residual", "status": "passed" if free_residual <= 1e-8 else "failed", "value": free_residual},
            {"name": "solver-constraint-residual", "status": "passed" if float(constraints.get("max_relative_residual", float("inf"))) <= 1e-8 else "failed", "value": constraints.get("max_relative_residual")},
        ))
        reactions = compute_reactions(built.fe_model, result.displacements, built.load_case)
        force = np.zeros(3); moment = np.zeros(3); scale = np.linalg.norm(np.ptp(np.asarray(list(mesh.nodes.values())), axis=0))
        load_scale = 0.0; moment_scale = 0.0
        dofs = built.fe_model.mesh.dof_manager
        for node_id, xyz in mesh.nodes.items():
            ids = dofs.get_node_dofs(node_id)
            applied = np.asarray(f[ids], dtype=float)
            reaction = np.asarray(reactions.get(node_id, np.zeros(6)), dtype=float)
            xyz = np.asarray(xyz)
            force += applied[:3] + reaction[:3]
            moment += np.cross(xyz, applied[:3] + reaction[:3]) + applied[3:] + reaction[3:]
            load_scale += np.linalg.norm(applied[:3])
            moment_scale += np.linalg.norm(np.cross(xyz, applied[:3]) + applied[3:])
        force_error = float(np.linalg.norm(force) / max(load_scale, 1.0))
        moment_error = float(np.linalg.norm(moment) / max(moment_scale, load_scale * scale, 1.0))
        checks.extend((
            {"name": "force-equilibrium", "status": "passed" if force_error <= 1e-6 else "failed", "value": force_error},
            {"name": "moment-equilibrium", "status": "passed" if moment_error <= 1e-6 else "failed", "value": moment_error},
        ))
        solver = {
            "energy_j": energy,
            "max_translation_m": float(result.max_translation()[1]),
            "force_error": force_error, "moment_error": moment_error,
            "free_residual": free_residual,
            "constraint_residual": constraints.get("max_relative_residual"),
            "fixed_source_probes_m": fixed_probes,
            "constraint_equations": int(constraint_info.get("equation_count", 0)) if isinstance(constraint_info, dict) else None,
            "assembly_seconds": assembly_seconds, "solve_seconds": solve_seconds,
            "formulation": "legacy Q8/T6",
            "thread_policy": result.info.get("thread_policy"),
            "assembly_kernels": result.info.get("assembly", {}).get("stiffness", {}).get("diagnostics", {}).get("vectorized_shell_groups", ()),
        }
        _write(folder / "solver.json", solver)
        if spec.get("direct_parity"):
            _stage(folder, "direct-owner-solve")
            owner_built = build_fe_model(project, owner, load_case="default")
            owner_result = solve_linear_static(
                built=owner_built, resource_config=resource_config
            )
            owner_force = assemble_load_vector(owner_built.fe_model, owner_built.load_case)
            if isinstance(owner_force, tuple):
                owner_force = owner_force[0]
            owner_energy = 0.5 * float(np.dot(owner_result.displacements, owner_force))
            displacement_error = float(
                np.linalg.norm(result.displacements - owner_result.displacements)
                / max(np.linalg.norm(owner_result.displacements), 1e-12)
            )
            energy_error = abs(energy - owner_energy) / max(abs(owner_energy), 1e-12)
            owner_probes = probe(
                _fixture(project, center, case), owner,
                owner_built.fe_model, owner_result.displacements,
            )
            probe_error = max(
                np.linalg.norm(np.asarray(fixed_probes[key]) - np.asarray(value))
                / max(np.linalg.norm(value), 1e-12)
                for key, value in owner_probes.items()
            )
            checks.append({
                "name": "direct-owner-solver-parity",
                "status": "passed" if max(displacement_error, energy_error, probe_error) <= 1e-8 else "failed",
                "displacement_error": displacement_error,
                "energy_error": energy_error,
                "probe_error": float(probe_error),
            })
    if spec.get("direct_parity") and case.startswith("structure_"):
        checks.extend(_structure_runtime_entrypoint_checks(folder, case, h))
    return {
        "status": "passed" if all(item["status"] == "passed" for item in checks) else "failed",
        "case": case, "h": h, "order": order, "refined": refined,
        "source_hash": source_digest, "mesh_hash": _mesh_digest(mesh),
        "counts": metrics["counts"], "geometry_family": mesh.hybrid_diagnostics.get("geometry_family_by_face"),
        "checks": checks, "mesh_seconds": mesh_seconds,
        "validation_seconds": validation_seconds,
        "solver": solver, "fixture": summary,
    }


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--freeze":
        freeze(sys.argv[2], Path(sys.argv[3]))
        return
    folder = Path(sys.argv[1]); spec = json.loads((folder / "input.json").read_text(encoding="utf-8"))
    try:
        result = run(spec, folder)
    except BaseException as error:
        (folder / "failure.log").write_text(traceback.format_exc(), encoding="utf-8")
        result = {"status": "failed", "exception": type(error).__name__, "message": str(error)}
    _write(folder / "result.json", result)
    print(result["status"], flush=True)


if __name__ == "__main__":
    main()
