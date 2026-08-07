"""Results, addressed the way the model was built.

The solver returns flat DOF vectors.  Postprocessing here re-attaches them to
the things the user actually placed -- points, lines and plates -- so a query
never has to know a node number.

Every analysis produces one or more *shapes*: a static deflection, a vibration
mode, a buckling mode, a step of a nonlinear path, an instant of a transient.
They all share :class:`ShapeView`, so anything that can display a static result
-- the scene builder, the results panel, the animation -- displays a mode or a
time step without knowing the difference.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ..geometry.entities import EntityRef
from ..solve.build import BuiltModel

__all__ = [
    "BucklingSolution",
    "CapacitySolution",
    "ImpactSolution",
    "ImportedSolution",
    "LinearSolution",
    "ModalSolution",
    "MultiShapeSolution",
    "NonlinearSolution",
    "ShapeView",
    "TransientSolution",
]

_DOF_INDEX = {"ux": 0, "uy": 1, "uz": 2, "rx": 3, "ry": 4, "rz": 5}


@dataclass
class ShapeView:
    """One displacement field over the model.

    ``value`` carries whatever number the shape is indexed by -- a frequency, a
    buckling load factor, a load factor, a time -- so a caller can label it
    without knowing which analysis produced it.
    """

    displacements: np.ndarray
    built: BuiltModel
    label: str = "result"
    value: float = 0.0

    # ------------------------------------------------------------------
    # displacement access
    # ------------------------------------------------------------------
    def node_displacement(self, node_id: int) -> np.ndarray:
        """The six displacement components at one node."""

        dofs = self.built.fe_model.mesh.dof_manager.get_node_dofs(node_id)
        if not dofs:
            raise KeyError(f"node {node_id} is not in the solved model")
        return self.displacements[dofs]

    def displacement_at(self, ref: EntityRef) -> Dict[int, np.ndarray]:
        """Displacements of every node on one geometry entity."""

        return {
            node_id: self.node_displacement(node_id)
            for node_id in self.built.mesh.nodes_on(ref)
        }

    def point_displacement(self, ref: EntityRef) -> np.ndarray:
        """The six components at a modelled point."""

        if ref.kind != "vertex":
            raise ValueError("point_displacement expects a point reference")
        node_id = self.built.mesh.node_of_vertex.get(ref.id)
        if node_id is None:
            raise KeyError(f"{ref} has no node in the mesh")
        return self.node_displacement(node_id)

    def component(self, name: str) -> np.ndarray:
        """One displacement component for every node, ordered by node ID."""

        try:
            offset = _DOF_INDEX[name]
        except KeyError:
            raise ValueError(
                f"unknown component {name!r}; expected one of "
                f"{', '.join(_DOF_INDEX)}"
            ) from None
        manager = self.built.fe_model.mesh.dof_manager
        return np.array(
            [
                self.displacements[manager.get_node_dofs(node_id)[offset]]
                for node_id in sorted(self.built.mesh.nodes)
            ]
        )

    def translations(self) -> np.ndarray:
        """Translation vectors for every node, ordered by node ID."""

        manager = self.built.fe_model.mesh.dof_manager
        return np.array(
            [
                self.displacements[manager.get_node_dofs(node_id)[:3]]
                for node_id in sorted(self.built.mesh.nodes)
            ]
        )

    def max_translation(self) -> Tuple[int, float]:
        """The node with the largest translation magnitude, and that magnitude."""

        node_ids = sorted(self.built.mesh.nodes)
        magnitudes = np.linalg.norm(self.translations(), axis=1)
        index = int(np.argmax(magnitudes))
        return node_ids[index], float(magnitudes[index])

    def deformed_positions(self, scale: float = 1.0) -> np.ndarray:
        """Node positions displaced by ``scale`` times the translation."""

        base = self.built.mesh.node_positions()
        return base + scale * self.translations()


@dataclass
class ImportedSolution(ShapeView):
    """A result another solver produced, displayed through the same interface.

    Two things make it different from a solved one, and both are enforced
    rather than smoothed over.

    It knows **which components it has**.  A CalculiX FRD carries three
    translations and no rotations, and a rotation reported as zero is a
    plausible number and a wrong one, so asking for an absent component raises.
    The underlying array holds NaN there, so code that indexes it directly
    still cannot mistake the gap for an answer.

    Its **stresses came from the file**, not from a recovery here.  They are
    kept as ready-made fields; nothing recomputes them, and a node-valued
    stress stays node-valued so it is never confused with something this layer
    derived.
    """

    results: Any = None
    components: frozenset = field(default_factory=frozenset)
    fields: Dict[str, Any] = field(default_factory=dict)
    covered: int = 0
    label: str = "imported"

    def _require_translations(self) -> None:
        if not {"ux", "uy", "uz"}.issubset(self.components):
            raise KeyError(
                f"{self.label} carries no displacement field; this imported "
                "result contains stresses only"
            )

    def node_displacement(self, node_id: int) -> np.ndarray:
        self._require_translations()
        return super().node_displacement(node_id)

    def translations(self) -> np.ndarray:
        self._require_translations()
        return super().translations()

    def component(self, name: str) -> np.ndarray:
        if name in _DOF_INDEX and name not in self.components:
            available = ", ".join(sorted(self.components)) or "none"
            raise KeyError(
                f"{self.label} carries no {name!r}: this file format does not "
                f"store it. Available components are {available}. It is not "
                "zero -- it is absent."
            )
        return super().component(name)

    def available_fields(self) -> List[str]:
        """Displacement components plus whatever stresses the file named."""

        order = [name for name in _DOF_INDEX if name in self.components]
        displacement = ["magnitude"] + order if order else []
        return displacement + list(self.fields)

    def summary(self) -> str:
        pieces = [self.label, f"{self.covered} nodes matched"]
        if self.results is not None:
            pieces.append(self.results.summary())
        if {"ux", "uy", "uz"}.issubset(self.components):
            node_id, magnitude = self.max_translation()
            pieces.append(
                f"max translation {magnitude:.6g} m at node {node_id}"
            )
        return "; ".join(pieces)


@dataclass
class LinearSolution(ShapeView):
    """A linear static result."""

    info: Dict[str, Any] = field(default_factory=dict)
    label: str = "static"
    _stress: Any = field(default=None, repr=False)

    # ------------------------------------------------------------------
    def stresses(self, **kwargs: Any):
        """Recover element stresses, caching the result.

        Delegates to the solver's unified recovery so provenance and fallback
        labelling are the solver's, not a reimplementation here.
        """

        if self._stress is None or kwargs:
            from anysolver import recover_stress_result

            result = recover_stress_result(
                self.built.fe_model, self.displacements, **kwargs
            )
            if not kwargs:
                self._stress = result
            return result
        return self._stress

    def face_von_mises(self, ref: EntityRef) -> Dict[int, float]:
        """Peak von Mises stress per element on one plate."""

        if ref.kind != "face":
            raise ValueError("face_von_mises expects a plate reference")
        recovered = self.stresses()
        values: Dict[int, float] = {}
        for element_id in self.built.mesh.elements_on(ref):
            stress = recovered.element_stresses.get(element_id)
            if stress is None:
                continue
            von_mises = stress.get("von_mises")
            if von_mises is None:
                continue
            values[element_id] = float(np.max(np.abs(von_mises)))
        return values

    def summary(self) -> str:
        node_id, magnitude = self.max_translation()
        mesh = self.built.mesh
        return (
            f"{self.built.project.name}: {mesh.num_nodes} nodes, "
            f"{mesh.num_elements} elements, "
            f"max translation {magnitude:.6g} m at node {node_id}"
        )

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"LinearSolution({self.summary()})"


# ----------------------------------------------------------------------
# multi-shape results
# ----------------------------------------------------------------------
@dataclass
class MultiShapeSolution:
    """A result made of several shapes, browsed by index."""

    built: BuiltModel
    shapes: List[ShapeView] = field(default_factory=list)
    status: str = "ok"
    info: Dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.shapes)

    def __getitem__(self, index: int) -> ShapeView:
        return self.shapes[index]

    def __iter__(self):
        return iter(self.shapes)

    @property
    def values(self) -> List[float]:
        return [shape.value for shape in self.shapes]

    @property
    def labels(self) -> List[str]:
        return [shape.label for shape in self.shapes]

    def shape(self, index: int = 0) -> ShapeView:
        if not self.shapes:
            raise IndexError(f"{type(self).__name__} has no shapes")
        return self.shapes[index]

    # A multi-shape result stands in for its first shape wherever a single
    # displacement field is expected, so existing displays keep working.
    @property
    def displacements(self) -> np.ndarray:
        return self.shape(0).displacements

    def max_translation(self) -> Tuple[int, float]:
        return self.shape(0).max_translation()


@dataclass
class ModalSolution(MultiShapeSolution):
    """Natural frequencies and their mode shapes."""

    rigid_body_modes: int = 0

    @property
    def frequencies(self) -> List[float]:
        """Natural frequencies in Hz, one per mode."""

        return [shape.value for shape in self.shapes]

    def periods(self) -> List[float]:
        return [
            float("inf") if frequency <= 0.0 else 1.0 / frequency
            for frequency in self.frequencies
        ]

    def summary(self) -> str:
        if not self.shapes:
            return f"{self.built.project.name}: no modes found"
        listed = ", ".join(f"{value:.4g}" for value in self.frequencies[:5])
        extra = "" if len(self.shapes) <= 5 else ", ..."
        note = (
            ""
            if not self.rigid_body_modes
            else f" ({self.rigid_body_modes} rigid-body)"
        )
        return (
            f"{self.built.project.name}: {len(self.shapes)} modes{note}, "
            f"f = {listed}{extra} Hz"
        )


@dataclass
class BucklingSolution(MultiShapeSolution):
    """Elastic buckling load factors and their mode shapes.

    A load factor multiplies the *reference load case* that produced the
    prestress, so it is only meaningful together with that case.
    """

    reference_case: str = "default"

    @property
    def load_factors(self) -> List[float]:
        return [shape.value for shape in self.shapes]

    @property
    def critical_factor(self) -> float:
        if not self.shapes:
            raise IndexError("no buckling modes were found")
        return float(min(self.load_factors))

    def summary(self) -> str:
        if not self.shapes:
            return f"{self.built.project.name}: no buckling modes found"
        listed = ", ".join(f"{value:.4g}" for value in self.load_factors[:5])
        return (
            f"{self.built.project.name}: critical factor "
            f"{self.critical_factor:.5g} on case {self.reference_case!r} "
            f"(factors {listed})"
        )


@dataclass
class NonlinearSolution(ShapeView):
    """The converged end state of an incremental solve, plus its path."""

    steps: List[Any] = field(default_factory=list)
    status: str = "ok"
    info: Dict[str, Any] = field(default_factory=dict)
    label: str = "nonlinear"
    peak_load_factor: Optional[float] = None
    deleted_elements: Tuple[int, ...] = ()

    @property
    def load_factor(self) -> float:
        """How much of the load case was actually carried."""

        return float(self.value)

    def history(self) -> Dict[str, np.ndarray]:
        """Load factor and displacement norm at each converged step.

        This is the load-displacement path: the thing worth plotting.
        """

        return {
            "step": np.array([step.step_index for step in self.steps]),
            "load_factor": np.array([step.load_factor for step in self.steps]),
            "displacement_norm": np.array(
                [step.displacement_norm for step in self.steps]
            ),
            "iterations": np.array([step.iterations for step in self.steps]),
        }

    def summary(self) -> str:
        node_id, magnitude = self.max_translation()
        peak = (
            ""
            if self.peak_load_factor is None
            else f", peak factor {self.peak_load_factor:.5g}"
        )
        eroded = (
            ""
            if not self.deleted_elements
            else f", {len(self.deleted_elements)} element(s) eroded"
        )
        return (
            f"{self.built.project.name}: {self.status}, "
            f"load factor {self.load_factor:.5g}{peak}, "
            f"{len(self.steps)} steps, "
            f"max translation {magnitude:.6g} m at node {node_id}{eroded}"
        )


@dataclass
class CapacitySolution(NonlinearSolution):
    """A whole capacity assessment: static, buckling, imperfection, collapse.

    A ``NonlinearSolution`` at heart -- the shape it carries is the imperfect
    nonlinear end state, so everything that displays a nonlinear result displays
    this unchanged -- with the earlier stages kept alongside rather than thrown
    away.  The elastic critical factor and the actual capacity are the two
    numbers the workflow exists to compare, and seeing them apart is the point:
    the ratio is how much the imperfection and the plasticity cost.
    """

    label: str = "capacity"
    critical_factor: Optional[float] = None
    buckling: Optional["BucklingSolution"] = None
    mesh_adequacy: Dict[str, Any] = field(default_factory=dict)

    @property
    def capacity_factor(self) -> float:
        """The load factor the structure actually reached."""

        return float(self.value)

    @property
    def capacity_ratio(self) -> Optional[float]:
        """Capacity over elastic critical load, when both are known.

        Either side of one is a real result, and which one says what kind of
        structure this is.  Below one is imperfection sensitivity: a shell or a
        column that yields, or that its imperfection knocks down, never reaches
        the elastic critical load.  Above one is post-buckling reserve: a plate
        in compression keeps taking load after it buckles, by shedding it into
        membrane action near the supported edges.  Neither is a warning sign on
        its own -- reading it as one would be the easy mistake.
        """

        if not self.critical_factor:
            return None
        return self.capacity_factor / float(self.critical_factor)

    def summary(self) -> str:
        pieces = [f"capacity {self.capacity_factor:.4g} x the reference load"]
        if self.critical_factor:
            pieces.append(f"elastic critical {self.critical_factor:.4g}")
            ratio = self.capacity_ratio
            if ratio is not None:
                pieces.append(f"capacity/critical {ratio:.3f}")
        status = (self.mesh_adequacy or {}).get("status")
        if status and status != "ok":
            pieces.append(f"mesh adequacy: {status}")
        return "; ".join(pieces)


@dataclass
class TransientSolution(MultiShapeSolution):
    """A response history, one shape per stored time step."""

    times: np.ndarray = field(default_factory=lambda: np.zeros(0))
    peak_displacement: float = 0.0
    peak_node: Optional[int] = None

    def at_time(self, time: float) -> ShapeView:
        """The stored step nearest a given time."""

        if not self.shapes:
            raise IndexError("the transient result has no stored steps")
        index = int(np.argmin(np.abs(self.times - float(time))))
        return self.shapes[index]

    def node_history(self, node_id: int, component: str = "uz") -> np.ndarray:
        """One component at one node, over every stored step."""

        try:
            offset = _DOF_INDEX[component]
        except KeyError:
            raise ValueError(f"unknown component {component!r}") from None
        dofs = self.built.fe_model.mesh.dof_manager.get_node_dofs(node_id)
        if not dofs:
            raise KeyError(f"node {node_id} is not in the solved model")
        return np.array(
            [shape.displacements[dofs[offset]] for shape in self.shapes]
        )

    def summary(self) -> str:
        span = 0.0 if self.times.size == 0 else float(self.times[-1])
        node = "" if self.peak_node is None else f" at node {self.peak_node}"
        return (
            f"{self.built.project.name}: {self.status}, {len(self.shapes)} steps "
            f"to t = {span:g} s, peak displacement "
            f"{self.peak_displacement:.6g} m{node}"
        )


@dataclass
class ImpactSolution(MultiShapeSolution):
    """A rigid-sphere impact: the response, and what the contact did.

    Browsable by time like a transient, plus the things only an impact has --
    where the sphere was, how hard it pushed, how long it touched, and whether
    the model gave up any elements.
    """

    times: np.ndarray = field(default_factory=lambda: np.zeros(0))
    sphere_positions: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    sphere_velocities: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    # One force vector per saved step, not a scalar: the direction matters
    # for an oblique impact, so the magnitude is derived rather than stored.
    contact_force_history: np.ndarray = field(
        default_factory=lambda: np.zeros((0, 3))
    )
    peak_contact_force: float = 0.0
    contact_duration: float = 0.0
    max_penetration: float = 0.0
    max_penetration_ratio: float = 0.0
    momentum_balance_error: float = 0.0
    peak_displacement: float = 0.0
    peak_node: Optional[int] = None
    deleted_elements: Tuple[int, ...] = ()
    collision: Any = None
    timing: Any = None

    # ------------------------------------------------------------------
    def at_time(self, time: float) -> ShapeView:
        """The stored step nearest a given time."""

        if not self.shapes:
            raise IndexError("the impact result has no stored steps")
        index = int(np.argmin(np.abs(self.times - float(time))))
        return self.shapes[index]

    def sphere_speed(self) -> np.ndarray:
        """Sphere speed at each stored step."""

        if self.sphere_velocities.size == 0:
            return np.zeros(0)
        return np.linalg.norm(self.sphere_velocities, axis=1)

    def energy(self) -> Dict[str, float]:
        """Kinetic energy before and after, and what the structure took.

        The difference is energy the sphere no longer has: it went into
        deformation, plastic work and damage.  It is not a strain-energy
        measurement, and it is not split into those parts here.
        """

        if self.collision is None or self.sphere_velocities.size == 0:
            return {"initial": 0.0, "final": 0.0, "absorbed": 0.0}
        mass = float(self.collision.mass)
        speeds = self.sphere_speed()
        initial = 0.5 * mass * float(speeds[0]) ** 2
        final = 0.5 * mass * float(speeds[-1]) ** 2
        return {"initial": initial, "final": final, "absorbed": initial - final}

    def contact_force_magnitude(self) -> np.ndarray:
        """Contact force magnitude at each stored step."""

        history = np.asarray(self.contact_force_history, dtype=float)
        if history.size == 0:
            return np.zeros(0)
        return np.linalg.norm(history, axis=1) if history.ndim == 2 else np.abs(history)

    def contact_history(self) -> Dict[str, np.ndarray]:
        """Time against contact force and sphere speed, for plotting."""

        return {
            "time": np.asarray(self.times, dtype=float),
            "contact_force": self.contact_force_magnitude(),
            "contact_force_vector": np.asarray(
                self.contact_force_history, dtype=float
            ),
            "sphere_speed": self.sphere_speed(),
        }

    def time_of_peak_force(self) -> float:
        """When the contact force peaked."""

        magnitude = self.contact_force_magnitude()
        if magnitude.size == 0:
            return 0.0
        return float(self.times[int(np.argmax(magnitude))])

    def touched(self) -> bool:
        """Whether the sphere actually made contact."""

        return self.peak_contact_force > 0.0

    def summary(self) -> str:
        if not self.touched():
            return (
                f"{self.built.project.name}: {self.status}, the sphere did not "
                f"make contact over {len(self.shapes)} steps"
            )
        energy = self.energy()
        deleted = (
            ""
            if not self.deleted_elements
            else f", {len(self.deleted_elements)} element(s) deleted"
        )
        return (
            f"{self.built.project.name}: {self.status}, peak contact force "
            f"{self.peak_contact_force:.6g} N over {self.contact_duration:.4g} s, "
            f"{energy['absorbed'] / 1000.0:.4g} kJ absorbed, peak displacement "
            f"{self.peak_displacement:.6g} m{deleted}"
        )
