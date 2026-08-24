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
    QualifiedE4PLShellElement,
    ShellElement,
)


ROOT = Path(__file__).resolve().parents[1]


def test_anyfem_routes_q4_through_default_and_preserves_other_shell_topologies() -> None:
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
        }.items()
    }
    mesh.quads = {10: (1, 2, 3, 4), 11: (1, 2, 3, 4, 5, 6, 7, 8)}
    mesh.tris = {12: (1, 2, 3)}
    mesh.elements_of_face = {face: [10, 11, 12]}

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        built = build_fe_model(
            project,
            mesh,
            require_loads=False,
            require_supports=False,
        )

    elements = built.fe_model.mesh.elements
    assert type(elements[10]) is QualifiedE4PLShellElement
    assert type(elements[11]) is ShellElement
    assert type(elements[12]) is ShellElement
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
        and node.func.id == "create_element"
    ]
    assert direct == []
    assert len(selector) == 1
