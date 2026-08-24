"""Resolve a meshed project into an ``anysolver`` FEModel.

This is the only place ANYfem speaks the solver's vocabulary.  Everything
upstream talks about plates, lines and points; everything downstream talks
about nodes, elements and DOFs.  Attribute resolution -- geometry entity to
node set -- happens here through the mesh association map.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations
from typing import Any, Dict, Iterable, List, Sequence

import numpy as np
from anygeometry.entities import EntityRef
from anysolver import (
    BeamElement,
    BoundaryCondition,
    CoupledBeamShellElement,
    FEModel,
    LegacyShellElement,
    QuadraticBeamElement,
    create_shell_element,
)
from anysolver import LoadCase as SolverLoadCase
from anymesher import S3QualityError, assert_s3_admissible

from ..mesh.mapped import Mesh
from ..model.attributes import LoadCase
from ..model.project import Project, ProjectError
from ..model.regions import ElementFaceRef, MeshEntityRef, RegionRef

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

    # A load-free build is used for deck handoff, neutral-mesh inspection, and
    # displacement-controlled analysis.  In the latter case the applied
    # action lives in the affine constraint RHS rather than in a LoadCase.
    has_prescribed_motion = any(
        value != 0.0
        for support in project.supports
        for value in support.constraints.values()
    )
    if (
        (not require_loads or has_prescribed_motion)
        and load_case == "default"
        and "default" not in project.load_cases
    ):
        resolved = None
    else:
        resolved = _resolve_load_case(project, load_case)
    # The UI may retain an empty named/default case after its last load was
    # deleted.  With a nonzero prescribed motion that case is not the driving
    # action and must not disguise the affine displacement path as a load-case
    # workflow in buckling/capacity metadata.
    if (
        has_prescribed_motion
        and resolved is not None
        and resolved.is_empty()
    ):
        resolved = None
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
    qualified_ids: list[int] = []
    owner_normals: dict[int, np.ndarray] = {}
    if project.shell_formulation_policy.s3 == "e4-pl-s3":
        for face_id, element_ids in mesh.elements_of_face.items():
            for element_id in element_ids:
                connectivity = mesh.tris.get(element_id)
                if connectivity is None or len(connectivity) != 3:
                    continue
                coordinates = np.asarray(
                    [mesh.nodes[node] for node in connectivity], dtype=float
                )
                centroid = np.mean(coordinates, axis=0)
                try:
                    owner_id, _point, parameters, _distance = (
                        project.geometry.closest_face(
                            centroid, face_ids=(int(face_id),)
                        )
                    )
                    if int(owner_id) != int(face_id):
                        raise ValueError(
                            "closest face did not preserve the element owner"
                        )
                    normal = project.geometry.face_normal(
                        int(face_id), float(parameters[0]), float(parameters[1])
                    )
                except Exception as error:
                    raise ProjectError(
                        f"qualified S3 element {element_id} has no authoritative "
                        f"owner normal: {error}"
                    ) from error
                qualified_ids.append(int(element_id))
                owner_normals[int(element_id)] = np.asarray(normal, dtype=float)
        try:
            assert_s3_admissible(
                mesh,
                element_ids=qualified_ids,
                element_owner_normals=owner_normals,
            )
        except S3QualityError as error:
            raise ProjectError(f"qualified S3 mesh admission failed: {error}") from error

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
            formulation = project.shell_formulation_policy.for_node_count(
                len(node_ids)
            )
            extra: dict[str, Any] = {}
            if formulation == "e4-pl-s3":
                extra["reference_normal"] = owner_normals[int(element_id)]
            element = create_shell_element(
                element_id,
                list(node_ids),
                material_name=section.material,
                thickness=section.thickness,
                formulation=formulation,
                **extra,
            )
            expected_id = project.shell_formulation_policy.formulation_id_for_node_count(
                len(node_ids)
            )
            if formulation in {"e4-pl", "e4-pl-s3"}:
                if getattr(element, "formulation_id", None) != expected_id:
                    raise ProjectError(
                        f"shell element {element_id} formulation identity mismatch: "
                        f"expected {expected_id}"
                    )
            elif type(element) is not LegacyShellElement:
                raise ProjectError(
                    f"shell element {element_id} did not resolve to the persisted "
                    "legacy identity"
                )
            fe_model.add_element(element_id, element)


def _add_beams(project: Project, mesh: Mesh, fe_model: FEModel) -> None:
    """Beam elements, linear or quadratic according to the mesh's order.

    Shells need no such branch: ANYsolver's public element selector reads
    topology from the node count and selects the qualified Q4 default while
    preserving TRI3, TRI6, Q8, and Q8R. The beam classes are separate, so the
    choice has to be made here.
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
        targets = _attribute_targets(project, support.region, support.ref)
        # A restraint holds a physical location, so it reaches the offset
        # stiffener nodes as well as the plating.
        node_ids = sorted(
            {
                node_id
                for target in targets
                for node_id in _nodes_on_target(mesh, target, constrained=True)
            }
        )
        if not node_ids:
            raise ProjectError(
                f"support {support.name!r} has an empty or unresolved scope in "
                "the active mesh"
            )
        system = _coordinate_system(project, support.coordinate_system_id)
        if system.id == "global":
            fe_model.add_boundary_condition(
                BoundaryCondition(
                    name=support.name,
                    node_ids=list(node_ids),
                    dof_constraints=dict(support.constraints),
                )
            )
            continue

        # Local prescribed values are affine equations in the global nodal
        # degrees of freedom.  Pick distinct, well-conditioned pivots for all
        # local components in one translation/rotation block; a naive
        # "largest coefficient" pivot can choose the same global DOF twice for
        # a rotated basis and make an otherwise valid support look dependent.
        for node_id in node_ids:
            basis = system.basis_at(mesh.nodes[node_id])
            dofs = fe_model.mesh.dof_manager.get_node_dofs(node_id)
            for prefix, offset in (("u", 0), ("r", 3)):
                rows = [
                    ("xyz".index(name[1]), name, float(value))
                    for name, value in support.constraints.items()
                    if name.startswith(prefix)
                ]
                if not rows:
                    continue
                pivots = _constraint_pivots(basis, [axis for axis, _name, _value in rows])
                for (axis, name, value), pivot in zip(rows, pivots):
                    terms = tuple(
                        (dofs[offset + component], float(basis[component, axis]))
                        for component in range(3)
                        if abs(float(basis[component, axis])) > 1.0e-13
                    )
                    fe_model.add_constraint_equation(
                        terms=terms,
                        rhs=value,
                        source_id=f"{support.id}:{node_id}:{name}",
                        dependent_dof=dofs[offset + pivot],
                    )


def _constraint_pivots(basis: np.ndarray, local_axes: Sequence[int]) -> tuple[int, ...]:
    """Choose distinct global pivot components for independent local rows."""

    best: tuple[float, tuple[int, ...]] | None = None
    for candidates in permutations(range(3), len(local_axes)):
        magnitudes = [abs(float(basis[pivot, axis])) for pivot, axis in zip(candidates, local_axes)]
        if any(value <= 1.0e-12 for value in magnitudes):
            continue
        score = float(np.prod(magnitudes))
        if best is None or score > best[0]:
            best = (score, tuple(candidates))
    if best is None:
        raise ProjectError("local support directions are linearly dependent")
    return best[1]


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
        targets = _attribute_targets(
            project,
            load.region,
            load.ref,
            geometry_kinds=("vertex",),
            mesh_kinds=("node",),
        )
        node_ids = sorted(
            {
                node_id
                for scoped in targets
                for node_id in _nodes_on_target(mesh, scoped)
            }
        )
        if not node_ids:
            raise ProjectError(
                f"point load {load.id!r} has an empty or unresolved scope in "
                "the active mesh"
            )
        share = 1.0 / len(node_ids) if load.distribution_policy == "total_distributed" else 1.0
        system = _coordinate_system(project, load.coordinate_system_id)
        for node_id in node_ids:
            position = mesh.nodes[node_id]
            force = system.to_global(load.force, position)
            moment = system.to_global(load.moment, position)
            _add_nodal(
                target,
                node_id,
                factor * share * force,
                factor * share * moment,
            )

    for load in case.pressures:
        targets = _attribute_targets(
            project,
            load.region,
            load.ref,
            geometry_kinds=("face",),
            mesh_kinds=("element", "element_face"),
        )
        element_ids = sorted(
            {
                element_id
                for scoped in targets
                for element_id in _elements_on_target(mesh, scoped)
            }
        )
        if not element_ids:
            raise ProjectError(
                f"pressure {load.id!r} has an empty or unresolved scope in the "
                "active mesh"
            )
        for element_id in element_ids:
            target.pressure_loads[element_id] = (
                target.pressure_loads.get(element_id, 0.0) + factor * load.value
            )

    for load in case.line_loads:
        system = _coordinate_system(project, load.coordinate_system_id)
        targets = _attribute_targets(
            project,
            load.region,
            load.ref,
            geometry_kinds=("edge",),
            mesh_kinds=("element",),
        )
        contributions: Dict[int, np.ndarray] = {}
        for scoped in targets:
            for node_id, local_force in _line_target_to_nodes(
                mesh, scoped, load.force_per_length
            ):
                contributions.setdefault(node_id, np.zeros(3))
                contributions[node_id] += system.to_global(
                    local_force, mesh.nodes[node_id]
                )
        if not contributions:
            raise ProjectError(
                f"line load {load.id!r} has an empty or unresolved scope in "
                "the active mesh"
            )
        for node_id, force in contributions.items():
            _add_nodal(target, node_id, factor * force, np.zeros(3))

    for load in case.surface_tractions:
        system = _coordinate_system(project, load.coordinate_system_id)
        targets = _attribute_targets(
            project,
            load.region,
            load.ref,
            geometry_kinds=("face",),
            mesh_kinds=("element", "element_face"),
        )
        contributions: Dict[int, np.ndarray] = {}
        for scoped in targets:
            for node_id, local_force in _traction_target_to_nodes(
                mesh, scoped, load.traction
            ):
                contributions.setdefault(node_id, np.zeros(3))
                contributions[node_id] += system.to_global(
                    local_force, mesh.nodes[node_id]
                )
        if not contributions:
            raise ProjectError(
                f"surface traction {load.id!r} has an empty or unresolved "
                "scope in the active mesh"
            )
        for node_id, force in contributions.items():
            _add_nodal(target, node_id, factor * force, np.zeros(3))

    if case.gravity is not None:
        # Gravity is an acceleration field, so combining cases sums the fields
        # rather than the resulting forces.
        system = _coordinate_system(project, case.gravity_coordinate_system_id)
        if system.kind == "cylindrical":
            raise ProjectError(
                "a cylindrical gravity/acceleration field varies by position "
                "and cannot be represented by the solver's uniform body-load "
                "field; use scoped nodal or distributed loads"
            )
        current = (
            np.zeros(3) if target.gravity is None else np.asarray(target.gravity)
        )
        target.gravity = current + factor * system.to_global(case.gravity)


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


def _coordinate_system(project: Project, coordinate_system_id: str):
    try:
        return project.coordinate_systems[str(coordinate_system_id)]
    except KeyError:
        raise ProjectError(
            f"coordinate system {coordinate_system_id!r} does not exist"
        ) from None


def _attribute_targets(
    project: Project,
    region_ref: RegionRef | None,
    fallback: EntityRef,
    *,
    geometry_kinds: Sequence[str] | None = None,
    mesh_kinds: Sequence[str] | None = None,
) -> tuple[Any, ...]:
    """Resolve a canonical region, retaining the legacy direct-ref fallback.

    Feature-output anchors resolve only through history output keys.  An edit
    that removes an output therefore produces an empty scope and a blocking
    diagnostic here; it is never silently retargeted to nearby geometry.
    """

    if region_ref is None:
        targets: tuple[Any, ...] = (fallback,)
    else:
        try:
            geometry = None if project.mesh_only else project.geometry
            feature_resolver = None
            if geometry is not None:
                feature_resolver = lambda anchor: project.geometry.features.resolve(
                    anchor, project.geometry
                )
            targets = project.regions.resolve(
                region_ref.id,
                geometry=geometry,
                feature_resolver=feature_resolver,
            )
        except Exception as exc:
            raise ProjectError(
                f"region {region_ref.id!r} cannot be resolved: {exc}"
            ) from exc
    if not targets:
        raise ProjectError(f"region {getattr(region_ref, 'id', '')!r} resolves to nothing")

    geometry_allowed = None if geometry_kinds is None else set(geometry_kinds)
    mesh_allowed = None if mesh_kinds is None else set(mesh_kinds)
    for target in targets:
        if isinstance(target, EntityRef):
            if geometry_allowed is not None and target.kind not in geometry_allowed:
                raise ProjectError(
                    f"scope contains geometry {target.kind!r}; expected "
                    f"{', '.join(sorted(geometry_allowed))}"
                )
        elif isinstance(target, MeshEntityRef):
            if mesh_allowed is not None and target.kind not in mesh_allowed:
                raise ProjectError(
                    f"scope contains mesh {target.kind!r}; expected "
                    f"{', '.join(sorted(mesh_allowed))}"
                )
        elif isinstance(target, ElementFaceRef):
            if mesh_allowed is not None and "element_face" not in mesh_allowed:
                raise ProjectError("scope contains an element face, which is invalid here")
        else:
            raise ProjectError(f"scope resolved to unsupported target {target!r}")
    return tuple(targets)


def _element_nodes(mesh: Mesh, element_id: int) -> tuple[int, ...]:
    for collection in (mesh.quads, mesh.tris, mesh.beams):
        if element_id in collection:
            return tuple(int(node) for node in collection[element_id])
    if element_id in mesh.couplings:
        coupling = mesh.couplings[element_id]
        if hasattr(coupling, "beam_node"):
            return (int(coupling.beam_node),) + tuple(
                int(node) for node in coupling.plate_nodes
            )
    raise ProjectError(f"mesh scope references missing element {element_id}")


def _nodes_on_target(
    mesh: Mesh, target: Any, *, constrained: bool = False
) -> list[int]:
    if isinstance(target, EntityRef):
        resolver = mesh.constrained_nodes_on if constrained else mesh.nodes_on
        return list(resolver(target))
    if isinstance(target, MeshEntityRef):
        if target.kind == "node":
            if target.id not in mesh.nodes:
                raise ProjectError(f"mesh scope references missing node {target.id}")
            return [int(target.id)]
        return list(_element_nodes(mesh, int(target.id)))
    if isinstance(target, ElementFaceRef):
        # The qualified structural element families are beams and shells.  A
        # selected shell face therefore maps to the shell's interpolation
        # nodes; no solid-face topology is invented.
        return list(_element_nodes(mesh, int(target.element_id)))
    raise ProjectError(f"unsupported scoped target {target!r}")


def _elements_on_target(mesh: Mesh, target: Any) -> list[int]:
    if isinstance(target, EntityRef):
        return list(mesh.elements_on(target))
    if isinstance(target, MeshEntityRef):
        if target.kind != "element":
            return []
        _element_nodes(mesh, int(target.id))
        return [int(target.id)]
    if isinstance(target, ElementFaceRef):
        _element_nodes(mesh, int(target.element_id))
        return [int(target.element_id)]
    raise ProjectError(f"unsupported scoped target {target!r}")


def _line_target_to_nodes(
    mesh: Mesh, target: Any, force_per_length: np.ndarray
) -> List[tuple[int, np.ndarray]]:
    if isinstance(target, EntityRef):
        return _line_load_to_nodes(mesh, target, force_per_length)
    if isinstance(target, MeshEntityRef) and target.kind == "element":
        if target.id not in mesh.beams:
            raise ProjectError(
                f"line load mesh scope element {target.id} is not a beam"
            )
        nodes = tuple(int(node) for node in mesh.beams[target.id])
        shares = (
            (0.5, 0.5)
            if len(nodes) == 2
            else (1.0 / 6.0, 2.0 / 3.0, 1.0 / 6.0)
        )
        positions = [mesh.nodes[node] for node in nodes]
        length = float(
            sum(
                np.linalg.norm(second - first)
                for first, second in zip(positions, positions[1:])
            )
        )
        intensity = np.asarray(force_per_length, dtype=float)
        return [
            (node, float(weight) * length * intensity)
            for node, weight in zip(nodes, shares)
        ]
    raise ProjectError(f"unsupported line-load scope target {target!r}")


def _traction_target_to_nodes(
    mesh: Mesh, target: Any, traction: np.ndarray
) -> List[tuple[int, np.ndarray]]:
    if isinstance(target, EntityRef):
        return _traction_to_nodes(mesh, target, traction)
    element_ids = _elements_on_target(mesh, target)
    accumulated: Dict[int, np.ndarray] = {}
    for element_id in element_ids:
        for node_id, force in _traction_elements_to_nodes(
            mesh, (element_id,), traction
        ):
            accumulated.setdefault(node_id, np.zeros(3))
            accumulated[node_id] += force
    return list(accumulated.items())


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

    return _traction_elements_to_nodes(mesh, element_ids, traction)


def _traction_elements_to_nodes(
    mesh: Mesh, element_ids: Iterable[int], traction: np.ndarray
) -> List[tuple[int, np.ndarray]]:
    """Consistently lump a uniform traction over explicit shell elements."""

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
        targets = _attribute_targets(project, mass.region, mass.ref)
        node_ids = sorted(
            {
                node_id
                for target in targets
                for node_id in _nodes_on_target(mesh, target)
            }
        )
        if not node_ids:
            raise ProjectError(
                f"mass {mass.name!r} has an empty or unresolved scope in the mesh"
            )
        share = (
            float(mass.value) / len(node_ids)
            if mass.distribution_policy == "total_distributed"
            else float(mass.value)
        )
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
