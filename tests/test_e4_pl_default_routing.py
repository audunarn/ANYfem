from __future__ import annotations

import ast
from types import SimpleNamespace
from pathlib import Path

import numpy as np

from anyfem import Project, steel
from anyfem.solve.build import build_fe_model
import anyfem.solve.build as build_module
from anymesher import Mesh
from anysolver import (
    LegacyShellElement,
    QualifiedE4PLShellElement,
)


ROOT = Path(__file__).resolve().parents[1]


def test_anyfem_defaults_every_shell_topology_to_explicit_legacy() -> None:
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

    built = build_fe_model(
        project,
        mesh,
        require_loads=False,
        require_supports=False,
    )

    elements = built.fe_model.mesh.elements
    assert type(elements[10]) is LegacyShellElement
    assert type(elements[11]) is LegacyShellElement
    assert type(elements[12]) is LegacyShellElement


def test_anyfem_retains_an_explicit_q4_switch_without_selecting_it_by_default() -> None:
    from anyfem import ShellFormulationPolicy

    policy = ShellFormulationPolicy.qualified_q4_only()

    assert policy.q4 == "e4-pl"
    assert policy.s3 == "legacy-s3"
    assert Project().shell_formulation_policy == ShellFormulationPolicy.legacy_compatible()

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
