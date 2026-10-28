"""Authoritative DRY mass properties of the assembled FE model: ONE public owner call, nothing invented, rescaled or symmetrised.

``anysolver.calculate_mass_properties`` projects the same assembled mass matrix used by dynamics and validates the physical
spatial-inertia blocks, point masses and generalized sections; its validation is carried (a refusal propagates).  This module only
adds the sparse M and the local node->DOF map needed to apply ``-M a`` and ``M g`` to nodal vectors.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .floating_loads import NodalDofMap


@dataclass(frozen=True)
class DryMassProperties:
    mass: float
    cg: np.ndarray                 # project/body-frame centre of gravity
    inertia_cg: np.ndarray         # 3x3 about CG in project axes (owner tensor, as returned)
    node_ids: tuple
    points: np.ndarray             # (n,3) node coordinates, project frame
    dofmap: NodalDofMap
    mass_matrix: object            # assembled sparse M (full DOFs)
    owner: object = None           # the owner MassProperties record (validated)

    def apply(self, full):
        return self.mass_matrix @ full


def dry_mass_properties(fe_model) -> DryMassProperties:
    from anysolver import calculate_mass_properties
    from anysolver.matrix_assembly import assemble_mass_matrix
    owner = calculate_mass_properties(fe_model)
    m = float(owner.total_mass)
    if not np.isfinite(m) or m <= 0.0:
        raise ValueError('assembled dry FE mass must be positive and finite')
    M, _ = assemble_mass_matrix(fe_model)
    ids = tuple(sorted(fe_model.mesh.nodes))
    ndof = int(fe_model.mesh.dof_manager.total_dofs)
    index = -np.ones((len(ids), 6), dtype=np.intp)
    pts = np.zeros((len(ids), 3))
    for i, nid in enumerate(ids):
        node = fe_model.mesh.nodes[nid]
        d = list(node.dofs)
        index[i, :len(d)] = d[:6]
        pts[i] = node.coords()
    return DryMassProperties(m, np.array(owner.center_of_mass, dtype=float),
                             np.array(owner.inertia_tensor_center_of_mass, dtype=float), ids, pts,
                             NodalDofMap(index, ndof), M, owner)
