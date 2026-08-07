"""Reading SESAM formatted FEM files.

An imported model has no geometry behind it: a SESAM file carries nodes and
elements, not the plates and lines someone drew.  Inventing a BRep to sit
underneath would be a guess dressed up as a model, so ANYfem does not.

What it does instead is give the imported mesh the same *association* an
ANYfem mesh has -- elements grouped into addressable sets -- so results stay
addressable, pickable and probeable through exactly the same code.  The groups
come from the file's own element properties rather than from geometry
inference, so they are what the file says, not what ANYfem guessed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ..geometry.entities import EntityRef
from ..mesh.mapped import Mesh
from ..model.project import Project

__all__ = [
    "ImportedModel",
    "SesamImportError",
    "import_sesam",
    "mesh_from_fe_model",
]


class SesamImportError(ValueError):
    """Raised when a SESAM file cannot be turned into a usable model."""


@dataclass
class ImportedModel:
    """A model that came from a file rather than from ANYfem geometry.

    It carries a solver model and an ANYfem mesh, but no geometry, and it says
    so: ``has_geometry`` is False and there is nothing to re-mesh.  Everything
    downstream -- analyses, fields, probes, contours -- works because those all
    go through the mesh association, not through the geometry.
    """

    name: str
    fe_model: Any
    mesh: Mesh
    source: Optional[Path] = None
    diagnostics: Tuple[Any, ...] = ()
    groups: Dict[str, EntityRef] = field(default_factory=dict)

    has_geometry: bool = False

    @property
    def num_nodes(self) -> int:
        return self.mesh.num_nodes

    @property
    def num_elements(self) -> int:
        return self.mesh.num_elements

    def project(self) -> Project:
        """A stand-in project, so the imported model can be displayed.

        It carries the name and nothing else; there is no geometry to put in
        it.  Anything that needs real geometry will find it empty rather than
        find something made up.
        """

        return Project(name=self.name)

    def load_case(self, name: str = "default"):
        """An empty load case to fill in against this model's groups.

        Loads reference the synthetic group IDs from :attr:`groups`, which is
        the same ``EntityRef`` vocabulary a modelled project uses -- so the
        same load types work, they just point at file-defined groups instead
        of at drawn plates.
        """

        from ..model.attributes import LoadCase

        return LoadCase(name=name)

    def add_support(self, support) -> None:
        """Restrain a group, on top of whatever the file already defined."""

        from anysolver import BoundaryCondition

        node_ids = self.mesh.nodes_on(support.ref)
        if not node_ids:
            raise SesamImportError(
                f"support {support.name!r} references {support.ref}, which is "
                "not a group in this imported model"
            )
        self.fe_model.add_boundary_condition(
            BoundaryCondition(
                name=support.name,
                node_ids=list(node_ids),
                dof_constraints=dict(support.constraints),
            )
        )

    def built(self, load_case: Any = None):
        """Wrap the imported model so the analyses and results accept it.

        An ANYfem load case is resolved against the mesh groups here, through
        the same accumulation a modelled project uses, so an imported model is
        loaded by exactly the same rules.
        """

        from anysolver import LoadCase as SolverLoadCase

        from ..solve.build import BuiltModel, _accumulate_case

        solver_case = None
        if load_case is not None:
            if isinstance(load_case, SolverLoadCase):
                solver_case = load_case
            else:
                solver_case = SolverLoadCase(name=load_case.name)
                _accumulate_case(
                    self.project(), self.mesh, load_case, 1.0, solver_case
                )
                solver_case.follower_pressure = bool(
                    getattr(load_case, "follower_pressure", False)
                )
            self.fe_model.add_load_case(solver_case)

        return BuiltModel(
            fe_model=self.fe_model,
            load_case=solver_case,
            mesh=self.mesh,
            project=self.project(),
        )

    def summary(self) -> str:
        groups = len(self.groups)
        note = "" if not self.diagnostics else f", {len(self.diagnostics)} diagnostic(s)"
        return (
            f"{self.name}: {self.num_nodes} nodes, "
            f"{len(self.mesh.quads) + len(self.mesh.tris)} shells, "
            f"{len(self.mesh.beams)} beams, {groups} group(s){note}"
        )


def import_sesam(
    path: str | Path, *, strict: bool = False, name: Optional[str] = None
) -> ImportedModel:
    """Read a SESAM FEM file into an imported model.

    ``strict`` is passed to ANYfileio's reader. It defaults to False here
    because a real file usually carries records outside the supported subset,
    and refusing the whole model for that would make the importer useless; the
    diagnostics are kept and reported instead.
    """

    from anyfileio import raise_if_errors, read_sesam_fem_document, read_sesam_semantics
    from anysolver.sesam_fem import build_fe_model_from_sesam_document

    source = Path(path)
    if not source.exists():
        raise SesamImportError(f"no such file: {source}")

    try:
        document = read_sesam_fem_document(source, strict=strict)
        semantics = read_sesam_semantics(document, strict=False)
        fe_model, adapter_diagnostics = build_fe_model_from_sesam_document(document)
        diagnostics = list(semantics.diagnostics)
        for diagnostic in adapter_diagnostics:
            if diagnostic not in diagnostics:
                diagnostics.append(diagnostic)
        if strict:
            raise_if_errors(diagnostics, "SESAM FEM import failed")
    except Exception as error:  # noqa: BLE001 - reported verbatim
        raise SesamImportError(f"{source.name}: {error}") from None

    mesh = semantics.mesh
    _ensure_groups(mesh, fe_model)
    if not mesh.shells and not mesh.beams:
        # The importer returns an empty model rather than None for a file that
        # parses but carries nothing usable, so emptiness is the real test.
        raise SesamImportError(
            f"{source.name} parsed, but no supported beam or shell topology was "
            "found, so there is no model to analyse. "
            f"{len(diagnostics)} diagnostic(s) were recorded."
        )
    groups = {
        f"group {index}": EntityRef("face", key)
        for index, key in enumerate(sorted(mesh.elements_of_face), start=1)
    }
    groups.update(
        {
            f"beams {index}": EntityRef("edge", key)
            for index, key in enumerate(sorted(mesh.elements_of_edge), start=1)
        }
    )

    return ImportedModel(
        name=name or source.stem,
        fe_model=fe_model,
        mesh=mesh,
        source=source,
        diagnostics=tuple(diagnostics),
        groups=groups,
    )


def _ensure_groups(mesh: Mesh, fe_model: Any) -> None:
    """Give unsectioned imported elements stable synthetic groups.

    ANYfileio preserves genuine SESAM section IDs. Older files often omit
    those references, while ANYfem still needs an addressable entity for loads,
    selection and probing. Group only the unclaimed elements so real IDs win.
    """

    claimed_shells = {
        element_id
        for element_ids in mesh.elements_of_face.values()
        for element_id in element_ids
    }
    claimed_beams = {
        element_id
        for element_ids in mesh.elements_of_edge.values()
        for element_id in element_ids
    }
    shell_groups: Dict[Any, int] = {}
    beam_groups: Dict[Any, int] = {}
    next_face = max(mesh.elements_of_face, default=0) + 1
    next_edge = max(mesh.elements_of_edge, default=0) + 1

    for element_id, element in sorted(fe_model.mesh.elements.items()):
        element_id = int(element_id)
        if element_id in mesh.shells and element_id not in claimed_shells:
            key = (
                getattr(element, "material_name", "default"),
                round(float(getattr(element, "thickness", 0.0)), 12),
            )
            if key not in shell_groups:
                shell_groups[key] = next_face
                next_face += 1
            mesh.elements_of_face.setdefault(shell_groups[key], []).append(element_id)
        elif element_id in mesh.beams and element_id not in claimed_beams:
            section = getattr(element, "cross_section", {}) or {}
            key = (
                getattr(element, "material_name", "default"),
                round(float(section.get("area", 0.0)), 12),
                round(float(section.get("Iy", 0.0)), 12),
            )
            if key not in beam_groups:
                beam_groups[key] = next_edge
                next_edge += 1
            mesh.elements_of_edge.setdefault(beam_groups[key], []).append(element_id)

    for group, element_ids in mesh.elements_of_edge.items():
        ordered: List[int] = []
        for element_id in element_ids:
            for node_id in mesh.beams[element_id]:
                if node_id not in ordered:
                    ordered.append(node_id)
        mesh.nodes_of_edge[group] = ordered


def mesh_from_fe_model(fe_model: Any) -> Mesh:
    """Build an ANYfem mesh from a solver model, grouping by section.

    The groups are synthetic entity IDs, not geometry: they exist so results
    can be addressed, selected and probed.  Shells are grouped by material and
    thickness, beams by material and section, because that is the distinction
    the file actually records.
    """

    mesh = Mesh()
    for node_id, node in fe_model.mesh.nodes.items():
        mesh.nodes[int(node_id)] = np.array(
            [float(node.x), float(node.y), float(node.z)], dtype=float
        )

    shell_groups: Dict[Any, int] = {}
    beam_groups: Dict[Any, int] = {}

    for element_id, element in sorted(fe_model.mesh.elements.items()):
        node_ids = [int(node) for node in getattr(element, "node_ids", ())]
        if len(node_ids) == 4 and hasattr(element, "thickness"):
            key = (
                getattr(element, "material_name", "default"),
                round(float(getattr(element, "thickness", 0.0)), 12),
            )
            group = shell_groups.setdefault(key, len(shell_groups) + 1)
            mesh.quads[int(element_id)] = tuple(node_ids)  # type: ignore[assignment]
            mesh.elements_of_face.setdefault(group, []).append(int(element_id))
        elif len(node_ids) == 2 and hasattr(element, "cross_section"):
            section = getattr(element, "cross_section", {}) or {}
            key = (
                getattr(element, "material_name", "default"),
                round(float(section.get("area", 0.0)), 12),
                round(float(section.get("Iy", 0.0)), 12),
            )
            group = beam_groups.setdefault(key, len(beam_groups) + 1)
            mesh.beams[int(element_id)] = (node_ids[0], node_ids[1])
            mesh.elements_of_edge.setdefault(group, []).append(int(element_id))
        # Anything else -- triangles, 8-node shells, couplings -- is left out
        # of the association rather than forced into a group it does not fit.

    for group, element_ids in mesh.elements_of_edge.items():
        ordered: List[int] = []
        for element_id in element_ids:
            for node_id in mesh.beams[element_id]:
                if node_id not in ordered:
                    ordered.append(node_id)
        mesh.nodes_of_edge[group] = ordered

    return mesh
