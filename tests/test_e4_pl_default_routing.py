from __future__ import annotations

import ast
from types import SimpleNamespace
from pathlib import Path

import numpy as np

from anyfem import Project, steel
from anyfem.native_meshing import NativeMeshSettings
from anyfem.solve.build import build_fe_model
import anyfem.solve.build as build_module
from anymesher import Mesh
from anysolver import (
    LegacyShellElement,
    NativeParityE4PLS3V2DShellElement,
    QualifiedE4PLShellElement,
)


ROOT = Path(__file__).resolve().parents[1]


def test_anyfem_defaults_q4_and_admitted_s3_to_qualified_formulations() -> None:
    project = Project("E4-PL routing")
    project.add_material(steel("S355", 0.01))
    project.add_plate_section("plate", thickness=0.01, material="S355")
    points = project.geometry.add_points(
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0))
    )
    face = project.geometry.add_face(project.geometry.add_polyline(points, close=True))
    project.assign_plate(face, "plate")

    mesh = Mesh()
    mesh.nodes = {
        identifier: np.asarray(coordinates, dtype=float)
        for identifier, coordinates in {
            1: (0.0, 0.0, 0.0),
            2: (1.0, 0.0, 0.0),
            3: (1.0, 1.0, 0.0),
            4: (0.0, 1.0, 0.0),
            5: (0.5, 0.0, 0.0),
            6: (1.0, 0.5, 0.0),
            7: (0.5, 1.0, 0.0),
            8: (0.0, 0.5, 0.0),
            9: (2.0, 0.0, 0.0),
            10: (3.0, 0.0, 0.0),
            11: (3.0, 1.0, 0.0),
            12: (2.0, 1.0, 0.0),
            13: (4.0, 0.0, 0.0),
            14: (5.0, 0.0, 0.0),
            15: (5.0, 1.0, 0.0),
            16: (4.0, 1.0, 0.0),
            17: (4.5, 0.0, 0.0),
            18: (5.0, 0.5, 0.0),
            19: (4.5, 1.0, 0.0),
            20: (4.0, 0.5, 0.0),
        }.items()
    }
    mesh.quads = {
        10: (9, 10, 11, 12),
        11: (13, 14, 15, 16, 17, 18, 19, 20),
    }
    mesh.tris = {12: (1, 2, 3)}
    mesh.elements_of_face = {face: [10, 11, 12]}
    mesh.geometry_model_id = project.geometry.model_id
    mesh.geometry_revision = project.geometry.revision
    mesh.structural_preparation = {
        "qualified_s3": {
            "admission": {},
            "authority_model": {
                "prepared_revision": project.geometry.revision,
                "scope": "PREPARED_GEOMETRY_ORIENTED_SHEET_FACE_USE",
                "source_model_id": str(project.geometry.model_id),
                "source_revision": project.geometry.revision,
            },
            "contract_id": "ANYMESHER_QUALIFIED_S3_PRODUCTION_PREPARATION_V1",
            "element_ids": [12],
            "element_owner_normals": {
                str(element_id): [0.0, 0.0, 1.0]
                for element_id in (10, 11, 12)
            },
            "element_owner_sources": {
                str(element_id): {
                    "face_id": face,
                    "face_use_ids": [1],
                    "sheet_ids": [1],
                }
                for element_id in (10, 11, 12)
            },
            "formulation_id": "CANDIDATE_E4_PL_S3_V2D_NATIVE_PARITY_V1",
            "legacy_fallback": "FORBIDDEN",
            "nodal_normals": {
                str(node_id): [0.0, 0.0, 1.0]
                for node_id in {
                    node
                    for connectivity in mesh.shells.values()
                    for node in connectivity
                }
            },
            "quality_contract_id": "ANYMESHER_QUALIFIED_S3_ADMISSION_V1",
            "repair": {},
            "repair_contract_id": "ANYMESHER_QUALIFIED_S3_REPAIR_V1",
            "schema": "anymesher.qualified-s3-production-preparation-v1",
            "status": "ADMITTED",
        }
    }

    built = build_fe_model(
        project,
        mesh,
        require_loads=False,
        require_supports=False,
    )

    elements = built.fe_model.mesh.elements
    assert type(elements[10]) is QualifiedE4PLShellElement
    assert type(elements[11]) is LegacyShellElement
    assert type(elements[12]) is NativeParityE4PLS3V2DShellElement


def test_anyfem_retains_the_q4_only_transition_policy() -> None:
    from anyfem import ShellFormulationPolicy

    policy = ShellFormulationPolicy.qualified_q4_only()

    assert policy.q4 == "e4-pl"
    assert policy.s3 == "legacy-s3"
    assert Project().shell_formulation_policy == ShellFormulationPolicy.current_default()

    mesh = Mesh()
    mesh.nodes = {
        1: np.asarray((0.0, 0.0, 0.0)),
        2: np.asarray((1.0, 0.0, 0.0)),
        3: np.asarray((1.0, 1.0, 0.0)),
        4: np.asarray((0.0, 1.0, 0.0)),
    }
    mesh.quads = {10: (1, 2, 3, 4)}
    mesh.elements_of_face = {8: [10]}
    actual: dict[int, object] = {}
    build_module._add_shells(
        SimpleNamespace(
            shell_formulation_policy=policy,
            plate_section_of=lambda _face: SimpleNamespace(
                material="steel", thickness=0.01
            ),
        ),
        mesh,
        SimpleNamespace(add_element=lambda key, value: actual.__setitem__(key, value)),
    )
    assert type(actual[10]) is QualifiedE4PLShellElement


def test_anyfem_solver_adapter_has_no_direct_shell_element_bypass() -> None:
    source = ROOT / "src" / "anyfem" / "solve" / "build.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    direct = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "ShellElement"
    ]
    selector = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "create_shell_element"
    ]
    assert direct == []
    assert len(selector) == 1


def test_current_policy_meshes_and_builds_exact_v2d_s3() -> None:
    project = Project("V2D production path")
    project.add_material(steel("S355", 0.01))
    project.add_plate_section("plate", thickness=0.01, material="S355")
    points = project.geometry.add_points(
        (
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (1.0, 1.0, 0.0),
            (0.0, 1.0, 0.0),
        )
    )
    face = project.geometry.add_plate(points)
    project.geometry.add_sheet((face,), name="plate")
    project.assign_plate(face, "plate")
    project.native_mesh_settings = NativeMeshSettings.create(
        0.4,
        backend="native",
        parameters={"recombine": False},
    )

    mesh = project.generate_mesh()
    built = build_fe_model(
        project,
        mesh,
        require_loads=False,
        require_supports=False,
    )

    authority = mesh.structural_preparation["qualified_s3"]
    assert mesh.tris and not mesh.quads
    assert authority["status"] == "ADMITTED"
    assert authority["formulation_id"] == (
        "CANDIDATE_E4_PL_S3_V2D_NATIVE_PARITY_V1"
    )
    assert all(
        type(element) is NativeParityE4PLS3V2DShellElement
        for element in built.fe_model.mesh.elements.values()
    )
