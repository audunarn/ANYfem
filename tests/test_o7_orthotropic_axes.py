"""O7 (option A): orthotropic shell materials are refused until a material direction can be set.

docs/direction-programme/O7-orthotropic-axes.md.  Before the refusal such a plate
built with material axes following node order (+10.6 % deflection when half the
quads were turned).  Isotropic node-order independence stays as the control.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "docs" / "direction-programme" / "scripts"))
import o7_ortho_axes_probe as probe  # noqa: E402


def _max_translation(symmetry: str, rotate: bool) -> float:
    return probe.run(symmetry, rotate, 0.25)[2].max_translation()[1]  # iso only: ortho is refused


def test_isotropic_plate_is_independent_of_node_order_control():
    assert _max_translation("iso", True) == pytest.approx(_max_translation("iso", False), rel=1e-12)


def test_orthotropic_shell_material_is_refused_at_build():
    """Fails on the old code, which built it with axes following node order."""
    from anyfem.model import ProjectError
    from anyfem.solve.build import build_fe_model

    project = probe.make("ortho")
    mesh = project.generate_mesh(0.25)
    with pytest.raises(ProjectError, match="SHELL_ORTHOTROPIC_AXES_UNDEFINED") as raised:
        build_fe_model(project, mesh)
    assert raised.value.code == "SHELL_ORTHOTROPIC_AXES_UNDEFINED"
    assert type(raised.value).__name__ == "OrthotropicShellAxesError"


def test_orthotropic_refusal_reaches_the_solve_entry_point():
    from anyfem.model import ProjectError
    from anyfem.solve.run import solve_linear_static

    with pytest.raises(ProjectError, match="SHELL_ORTHOTROPIC_AXES_UNDEFINED"):
        solve_linear_static(probe.make("ortho"), target_size=0.25)


def test_an_unused_orthotropic_material_does_not_block_an_isotropic_model():
    from anyfem.model.materials import Material
    from anyfem.solve.build import build_fe_model

    project = probe.make("iso")
    project.add_material(Material("spare", symmetry="orthotropic", constants=dict(
        elastic_modulus_1=40e9, elastic_modulus_2=200e9, elastic_modulus_3=100e9,
        poisson_ratio_12=0.05, poisson_ratio_13=0.25, poisson_ratio_23=0.25,
        shear_modulus_12=30e9, shear_modulus_13=30e9, shear_modulus_23=30e9)))
    build_fe_model(project, project.generate_mesh(0.25))


def test_a_face_without_shell_elements_does_not_refuse():
    from anyfem.solve import build as B

    project = probe.make("ortho")
    mesh = project.generate_mesh(0.25)
    mesh.elements_of_face = {face: [] for face in mesh.elements_of_face}
    fe = B.FEModel(name="x")
    for spec in project.materials.values():
        fe.register_material(spec.build())
    B._add_shells(project, mesh, fe)  # no orthotropic shell element is created
    assert not fe.mesh.elements


def test_the_registered_solver_material_decides_not_the_project_key():
    """A spec registered under another name must not hide an orthotropic solver material."""
    from anyfem.solve import build as B
    from anyfem.solve.build import FEModel

    fe = FEModel(name="x")

    class NoSymmetry:
        name = "S355"

    fe.materials["S355"] = NoSymmetry()
    with pytest.raises(B.OrthotropicShellAxesError):
        B._refuse_orthotropic_shell_material(fe, 1, "S355")
    with pytest.raises(B.OrthotropicShellAxesError):
        B._refuse_orthotropic_shell_material(fe, 1, "missing")
