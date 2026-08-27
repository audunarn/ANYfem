from __future__ import annotations

import ast
import warnings
from pathlib import Path

import numpy as np

from anyfem import Project, steel
from anyfem.solve.build import build_fe_model
from anymesher import Mesh
from anysolver import (
    LegacyQ4DeprecationWarning,
    QualifiedE4PLS3ShellElement,
    QualifiedE4PLShellElement,
    ShellElement,
)


ROOT = Path(__file__).resolve().parents[1]


def test_anyfem_routes_qualified_q4_and_s3_and_preserves_higher_order() -> None:
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
            5: (1.5, 0.0, 0.0),
            6: (2.5, 0.0, 0.0),
            7: (2.5, 1.0, 0.0),
            8: (1.5, 1.0, 0.0),
            9: (2.0, 0.0, 0.0),
            10: (2.5, 0.5, 0.0),
            11: (2.0, 1.0, 0.0),
            12: (1.5, 0.5, 0.0),
            13: (3.0, 0.0, 0.0),
            14: (4.0, 0.0, 0.0),
            15: (3.5, np.sqrt(3.0) / 2.0, 0.0),
        }.items()
    }
    mesh.quads = {20: (1, 2, 3, 4), 21: (5, 6, 7, 8, 9, 10, 11, 12)}
    mesh.tris = {22: (13, 14, 15)}
    mesh.elements_of_face = {face: [20, 21, 22]}

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        built = build_fe_model(
            project,
            mesh,
            require_loads=False,
            require_supports=False,
        )

    elements = built.fe_model.mesh.elements
    assert type(elements[20]) is QualifiedE4PLShellElement
    assert type(elements[21]) is ShellElement
    assert type(elements[22]) is QualifiedE4PLS3ShellElement
    assert [item for item in caught if item.category is LegacyQ4DeprecationWarning] == []


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
