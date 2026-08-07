"""Resolve a meshed project into an ``anysolver`` FEModel.

This is the only place ANYfem speaks the solver's vocabulary.  Everything
upstream talks about plates, lines and points; everything downstream talks
about nodes, elements and DOFs.  Attribute resolution -- geometry entity to
node set -- happens here through the mesh association map.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import numpy as np
from anysolver import (
    BeamElement,
    BoundaryCondition,
    CoupledBeamShellElement,
    FEModel,
    QuadraticBeamElement,
    ShellElement,
)
from anysolver import LoadCase as SolverLoadCase

from ..geometry.entities import EntityRef
from ..mesh.mapped import Mesh
from ..model.attributes import LoadCase
from ..model.project import Project, ProjectError

__all__ = ["BuiltModel", "build_fe_model"]


@dataclass
class BuiltModel:
    """A solver-ready model, with the mesh that produced it."""

    fe_model: FEModel
    load_case: SolverLoadCase | None
    mesh: Mesh
    project: Project
    combination: str | None = None

    def node_dofs(self, node_id: int) -> List[int]:
        return self.fe_model.mesh.dof_manager.get_node_dofs(node_id)


def build_fe_model(
    project: Project,
    mesh: Mesh,
    load_case: str | LoadCase | None = "default",
    *,
    combination: str | None = None,
    apply_imperfections: bool = True,
    require_loads: bool = True,
    require_supports: bool = True,
) -> BuiltModel:
    """Build the solver model for one load case or combination."""

    project.validate(
        require_loads=require_loads, require_supports=require_supports
    )

    fe_model = FEModel(name=project.name)
    for specification in project.materials.values():
        # ANYmaterial owns validation and construction. Registering the live
        # object supports both isotropic and orthotropic specifications.
        fe_model.register_material(specification.build())

    for node_id in sorted(mesh.nodes):
        x, y, z = mesh.nodes[node_id]
        fe_model.add_node(node_id, float(x), float(y), float(z))

    _add_shells(project, mesh, fe_model)
    _add_beams(project, mesh, fe_model)
    _add_couplings(project, mesh, fe_model)
    _add_supports(project, mesh, fe_model)
    _add_masses(project, mesh, fe_model)

    # Imperfections move the stress-free reference geometry, so they are
    # applied to the finished model before anything is assembled from it.
    if apply_imperfections and project.imperfections:
        fe_model = _apply_imperfections(project, mesh, fe_model)

    if combination is not None:
        cases = _build_combination(project, mesh, fe_model, combination)
        return BuiltModel(
            fe_model=fe_model,
            load_case=cases,
            mesh=mesh,
            project=project,
            combination=combination,
        )

    # A load-free build is used for deck handoff and neutral-mesh inspection.
    # The historical default name should not manufacture a requirement after
    # the caller explicitly disabled load validation.
    if (
        not require_loads
        and load_case == "default"
        and "default" not in project.load_cases
    ):
        resolved = None
    else:
        resolved = _resolve_load_case(project, load_case)
    solver_case = (
        None if resolved is None else _build_load_case(project, mesh, resolved)
    )
    if solver_case is not None:
        fe_model.add_load_case(solver_case)

    return BuiltModel(
        fe_model=fe_model, load_case=solver_case, mesh=mesh, project=project
    )


def _build_combination(
    project: Project, mesh: Mesh, fe_model: FEModel, name: str
) -> SolverLoadCase:
    """Fold a factored set of cases into one equivalent load case.

    The solver can assemble a combination directly, but folding it here keeps
    every analysis path taking a single load case, and keeps the factored
    result addressable in exactly the same way.
    """

    try:
        combination = project.combinations[name]
    except KeyError:
        raise ProjectError(f"no load combination named {name!r}") from None

    folded = SolverLoadCase(name=name)
    follower = False
    for case_name, factor in combination.factors.items():
        try:
            case = project.load_cases[case_name]
        except KeyError:
            raise ProjectError(
                f"combination {name!r} references undefined load case "
                f"{case_name!r}"
            ) from None
        follower = follower or case.follower_pressure
        _accumulate_case(project, mesh, case, float(factor), folded)

    if follower and any(
        not project.load_cases[case_name].follower_pressure
        for case_name in combination.factors
    ):
        raise ProjectError(
            f"combination {name!r} mixes follower and dead pressure. The "
            "solver assembles a case in either the reference or the current "
            "configuration, not both; split the combination."
        )
    folded.follower_pressure = follower

    fe_model.add_load_case(folded)
    return folded


def _add_shells(project: Project, mesh: Mesh, fe_model: FEModel) -> None:
    for face_id, element_ids in mesh.elements_of_face.items():
        section = project.plate_section_of(face_id)
        for element_id in element_ids:
            if element_id in mesh.quads:
                node_ids = mesh.quads[element_id]
            elif element_id in mesh.tris:
                node_ids = mesh.tris[element_id]
            else:
                raise ProjectError(
                    f"face {face_id} references missing shell element {element_id}"
                )
            fe_model.add_element(
                element_id,
                ShellElement(
                    element_id,
                    list(node_ids),
                    material_name=section.material,
                    thickness=section.thickness,
                ),
            )


def _add_beams(project: Project, mesh: Mesh, fe_model: FEModel) -> None:
    """Beam elements, linear or quadratic according to the mesh's order.

    Shells need no such branch: the solver's ``ShellElement`` reads its own
    topology from the node count.  The beam classes are separate, so the choice
    has to be made here.
    """

    element_class = QuadraticBeamElement if mesh.is_quadratic else BeamElement
    for edge_id, element_ids in mesh.elements_of_edge.items():
        section = project.beam_section_of(edge_id)
        properties = section.properties()
        for element_id in element_ids:
            fe_model.add_element(
                element_id,
                element_class(
                    element_id,
                    list(mesh.beams[element_id]),
                    material_name=section.material,
                    cross_section=dict(properties),
                ),
            )


def _add_couplings(project: Project, mesh: Mesh, fe_model: FEModel) -> None:
    """Tie eccentric stiffener nodes back to the plating.

    A kinematic constraint, not a stiff spring: the solver derives the offset
    from the node positions and enforces ``u_beam = u_shell + theta_shell x r``
    exactly, so there is no penalty parameter to get wrong.
    """

    material = next(iter(project.materials), "default")
    for element_id, coupling in mesh.couplings.items():
        if hasattr(coupling, "beam_node"):
            beam_node = int(coupling.beam_node)
            plate_nodes = tuple(int(node) for node in coupling.plate_nodes)
            weights = tuple(float(value) for value in coupling.weights)
            eccentricity = tuple(float(value) for value in coupling.eccentricity)
        else:
            # Wheels from the pre-extraction ANYfem line stored a pair. Keep
            # accepting one so an application-provided legacy Mesh still builds.
            beam_node, shell_node = coupling
            beam_node = int(beam_node)
            plate_nodes = (int(shell_node),)
            weights = (1.0,)
            eccentricity = tuple(
                float(value)
                for value in (mesh.nodes[beam_node] - mesh.nodes[int(shell_node)])
            )

        if len(plate_nodes) == 1:
            element = CoupledBeamShellElement(
                element_id,
                beam_node_id=beam_node,
                shell_node_id=plate_nodes[0],
                material_name=material,
            )
        else:
            # This remains solver functionality: ANYmesher describes the
            # interpolation, while ANYsolver turns it into exact MPC equations.
            from anysolver import InterpolatedBeamShellMPCElement

            element = InterpolatedBeamShellMPCElement(
                element_id,
                beam_node_id=beam_node,
                shell_node_ids=list(plate_nodes),
                shape_weights=np.asarray(weights, dtype=float),
                eccentricity=np.asarray(eccentricity, dtype=float),
                material_name=material,
            )
        fe_model.add_element(element_id, element)


def _add_supports(project: Project, mesh: Mesh, fe_model: FEModel) -> None:
    for support in project.supports:
        # A restraint holds a physical location, so it reaches the offset
        # stiffener nodes as well as the plating.
        node_ids = mesh.constrained_nodes_on(support.ref)
        if not node_ids:
            raise ProjectError(
                f"support {support.name!r} references {support.ref}, which has "
                "no nodes in the mesh"
            )
        fe_model.add_boundary_condition(
            BoundaryCondition(
                name=support.name,
                node_ids=list(node_ids),
                dof_constraints=dict(support.constraints),
            )
        )


def _resolve_load_case(
    project: Project, load_case: str | LoadCase | None
) -> LoadCase | None:
    if load_case is None:
        return None
    if isinstance(load_case, LoadCase):
        return load_case
    try:
        return project.load_cases[load_case]
    except KeyError:
        raise ProjectError(f"no load case named {load_case!r}") from None


def _build_load_case(
    project: Project, mesh: Mesh, case: LoadCase
) -> SolverLoadCase:
    solver_case = SolverLoadCase(name=case.name)
    _accumulate_case(project, mesh, case, 1.0, solver_case)
    solver_case.follower_pressure = bool(case.follower_pressure)
    return solver_case


def _accumulate_case(
    project: Project,
    mesh: Mesh,
    case: LoadCase,
    factor: float,
    target: SolverLoadCase,
) -> None:
    """Add one case's loads into a solver load case, scaled by ``factor``.

    Accumulating rather than assigning matters: the solver's
    ``add_pressure_load`` overwrites an element's pressure, so a combination
    that puts two pressures on the same plate would otherwise keep only the
    last one.
    """

    for load in case.point_loads:
        node_id = mesh.node_of_vertex.get(load.ref.id)
        if node_id is None:
            raise ProjectError(
                f"point load references {load.ref}, which has no node in the mesh"
            )
        _add_nodal(target, node_id, factor * load.force, factor * load.moment)

    for load in case.pressures:
        element_ids = mesh.elements_of_face.get(load.ref.id)
        if not element_ids:
            raise ProjectError(
                f"pressure references {load.ref}, which has no elements in the mesh"
            )
        for element_id in element_ids:
            target.pressure_loads[element_id] = (
                target.pressure_loads.get(element_id, 0.0) + factor * load.value
            )

    for load in case.line_loads:
        for node_id, force in _line_load_to_nodes(
            mesh, load.ref, load.force_per_length
        ):
            _add_nodal(target, node_id, factor * force, np.zeros(3))

    for load in case.surface_tractions:
        for node_id, force in _traction_to_nodes(mesh, load.ref, load.traction):
            _add_nodal(target, node_id, factor * force, np.zeros(3))

    if case.gravity is not None:
        # Gravity is an acceleration field, so combining cases sums the fields
        # rather than the resulting forces.
        current = (
            np.zeros(3) if target.gravity is None else np.asarray(target.gravity)
        )
        target.gravity = current + factor * np.asarray(case.gravity, dtype=float)


def _add_nodal(
    target: SolverLoadCase,
    node_id: int,
    force: np.ndarray,
    moment: np.ndarray,
) -> None:
    existing = target.nodal_loads.get(node_id)
    if existing is None:
        existing = np.zeros(6)
        target.nodal_loads[node_id] = existing
    existing[:3] += np.asarray(force, dtype=float)
    existing[3:] += np.asarray(moment, dtype=float)


def _traction_to_nodes(
    mesh: Mesh, ref: EntityRef, traction: np.ndarray
) -> List[tuple[int, np.ndarray]]:
    """Lump a force per unit area onto the nodes of a plate.

    The shares are the element's *consistent* load vector for a uniform
    traction, so the resultant is exact and a non-uniform mesh still
    distributes it correctly. For Q4 and T3 the shares are equal. For Q8 it is
    one third to each mid-side node and **minus** one twelfth to each corner;
    for T6 the corner integrals are zero and each mid-side node takes one
    third. These are the exact integrals of the corresponding shape functions.
    """

    element_ids = mesh.elements_of_face.get(ref.id)
    if not element_ids:
        raise ProjectError(
            f"surface traction references {ref}, which has no elements in the mesh"
        )

    accumulated: Dict[int, np.ndarray] = {}
    intensity = np.asarray(traction, dtype=float)
    for element_id in element_ids:
        if element_id in mesh.quads:
            nodes = mesh.quads[element_id]
            shares_by_count = {
                4: (0.25,) * 4,
                8: (-1.0 / 12.0,) * 4 + (1.0 / 3.0,) * 4,
            }
        elif element_id in mesh.tris:
            nodes = mesh.tris[element_id]
            shares_by_count = {
                3: (1.0 / 3.0,) * 3,
                6: (0.0,) * 3 + (1.0 / 3.0,) * 3,
            }
        else:
            raise ProjectError(
                f"surface traction references missing shell element {element_id}"
            )
        try:
            shares = shares_by_count[len(nodes)]
        except KeyError:
            raise ProjectError(
                f"surface traction does not support a {len(nodes)}-node shell"
            ) from None
        corners = np.array(
            [mesh.nodes[node] for node in mesh.corners_of(element_id)]
        )
        if len(corners) == 3:
            area = 0.5 * float(
                np.linalg.norm(
                    np.cross(corners[1] - corners[0], corners[2] - corners[0])
                )
            )
        else:
            area = 0.5 * (
                float(
                    np.linalg.norm(
                        np.cross(corners[1] - corners[0], corners[2] - corners[0])
                    )
                )
                + float(
                    np.linalg.norm(
                        np.cross(corners[2] - corners[0], corners[3] - corners[0])
                    )
                )
            )
        for node_id, weight in zip(nodes, shares):
            if node_id not in accumulated:
                accumulated[node_id] = np.zeros(3)
            accumulated[node_id] += weight * area * intensity
    return list(accumulated.items())


def _add_masses(project: Project, mesh: Mesh, fe_model: FEModel) -> None:
    """Attach lumped masses, sharing a total equally over an entity's nodes."""

    for mass in project.masses:
        node_ids = mesh.nodes_on(mass.ref)
        if not node_ids:
            raise ProjectError(
                f"mass {mass.name!r} references {mass.ref}, which has no nodes "
                "in the mesh"
            )
        share = float(mass.value) / len(node_ids)
        for node_id in node_ids:
            fe_model.add_point_mass(node_id, share)


def _apply_imperfections(
    project: Project, mesh: Mesh, fe_model: FEModel
) -> FEModel:
    """Move the stress-free geometry, through the solver's own builders."""

    from anysolver import (
        CompositeImperfection,
        apply_imperfection,
        standard_member_bow,
        standard_plate_mode,
    )

    fields = []
    for imperfection in project.imperfections:
        node_ids = mesh.nodes_on(imperfection.ref)
        if not node_ids:
            raise ProjectError(
                f"imperfection {imperfection.name!r} references "
                f"{imperfection.ref}, which has no nodes in the mesh"
            )
        if imperfection.resolved_kind == "plate_mode":
            fields.append(
                standard_plate_mode(
                    fe_model,
                    node_ids,
                    amplitude=imperfection.amplitude,
                    direction=imperfection.direction,
                    axes=imperfection.axes,
                    waves=imperfection.waves,
                    name=imperfection.name,
                )
            )
        else:
            fields.append(
                standard_member_bow(
                    fe_model,
                    node_ids,
                    amplitude=imperfection.amplitude,
                    direction=imperfection.direction,
                    name=imperfection.name,
                )
            )

    combined = fields[0] if len(fields) == 1 else CompositeImperfection(fields)
    return apply_imperfection(fe_model, combined, copy_model=False)


def _line_load_to_nodes(
    mesh: Mesh, ref: EntityRef, force_per_length: np.ndarray
) -> List[tuple[int, np.ndarray]]:
    """Lump a distributed line load onto the nodes along that line.

    The shares are the element's *consistent* load vector for a uniform load:
    half and half for a 2-node element, and one sixth, two thirds, one sixth
    for a 3-node one.  Splitting a quadratic element's load evenly between its
    stations instead would put a quarter at each end where a sixth belongs,
    and an inconsistent load vector drags the quadratic element back down to
    the convergence rate of a linear one -- so the extra nodes would cost
    something and buy nothing.
    """

    sequence = mesh.nodes_of_edge.get(ref.id)
    if not sequence:
        raise ProjectError(
            f"line load references {ref}, which has no nodes in the mesh"
        )

    step = 2 if mesh.is_quadratic else 1
    shares = (1.0 / 6.0, 2.0 / 3.0, 1.0 / 6.0) if step == 2 else (0.5, 0.5)

    accumulated: Dict[int, np.ndarray] = {}
    intensity = np.asarray(force_per_length, dtype=float)
    for start in range(0, len(sequence) - step, step):
        span = sequence[start : start + step + 1]
        positions = [mesh.nodes[node] for node in span]
        length = float(
            sum(
                np.linalg.norm(second - first)
                for first, second in zip(positions, positions[1:])
            )
        )
        for node_id, weight in zip(span, shares):
            if node_id not in accumulated:
                accumulated[node_id] = np.zeros(3)
            accumulated[node_id] += weight * length * intensity
    return [(node_id, force) for node_id, force in accumulated.items()]
