"""The project: geometry plus everything attached to it.

This is the headless API.  A script can build a complete model -- geometry,
sections, supports, loads -- and solve it without a GUI ever being imported.
That property is deliberate: it is what makes the preprocessor testable, it
gives the application its scripting console, and it is the seam a parametric
front-end would later call into.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Sequence

from ..geometry.entities import EntityRef
from ..geometry.model import GeometryModel
from ..mesh.mapped import ELEMENT_ORDERS, Mesh, generate_mesh
from ..mesh.refinement import Refinement
from ..mesh.seeding import Seeding
from .attributes import Combination, LoadCase, Mass, Support
from .imperfections import Imperfection
from .materials import Material
from .sections import BeamSection, PlateSection

__all__ = ["Project", "ProjectError"]


class ProjectError(ValueError):
    """Raised when a model is incomplete or inconsistent."""


@dataclass
class Project:
    """A complete finite element model, before meshing."""

    name: str = "model"
    geometry: GeometryModel = field(default_factory=GeometryModel)
    materials: Dict[str, Material] = field(default_factory=dict)
    plate_sections: Dict[str, PlateSection] = field(default_factory=dict)
    beam_sections: Dict[str, BeamSection] = field(default_factory=dict)
    face_sections: Dict[int, str] = field(default_factory=dict)
    edge_sections: Dict[int, str] = field(default_factory=dict)
    supports: List[Support] = field(default_factory=list)
    masses: List[Mass] = field(default_factory=list)
    load_cases: Dict[str, LoadCase] = field(default_factory=dict)
    combinations: Dict[str, Combination] = field(default_factory=dict)
    imperfections: List[Imperfection] = field(default_factory=list)
    refinements: List[Refinement] = field(default_factory=list)
    element_order: str = "linear"

    # ------------------------------------------------------------------
    # materials and sections
    # ------------------------------------------------------------------
    def add_material(self, material: Material) -> Material:
        self.materials[material.name] = material
        return material

    def add_plate_section(
        self, name: str, thickness: float, material: str
    ) -> PlateSection:
        self._require_material(material)
        section = PlateSection(name=name, thickness=thickness, material=material)
        self.plate_sections[name] = section
        return section

    def add_beam_section(self, section: BeamSection) -> BeamSection:
        self._require_material(section.material)
        self.beam_sections[section.name] = section
        return section

    # ------------------------------------------------------------------
    # assignment
    # ------------------------------------------------------------------
    def assign_plate(self, face_id: int, section: str) -> None:
        """Give a plate its thickness and material."""

        self.geometry.entity_ref("face", face_id)
        if section not in self.plate_sections:
            raise ProjectError(f"no plate section named {section!r}")
        self.face_sections[int(face_id)] = section

    def assign_plates(self, face_ids: Iterable[int], section: str) -> None:
        for face_id in face_ids:
            self.assign_plate(face_id, section)

    def assign_beam(self, edge_id: int, section: str) -> None:
        """Turn a line into a beam member."""

        self.geometry.entity_ref("edge", edge_id)
        if section not in self.beam_sections:
            raise ProjectError(f"no beam section named {section!r}")
        self.edge_sections[int(edge_id)] = section

    def assign_beams(self, edge_ids: Iterable[int], section: str) -> None:
        for edge_id in edge_ids:
            self.assign_beam(edge_id, section)

    @property
    def beam_edges(self) -> List[int]:
        return sorted(self.edge_sections)

    @property
    def beam_offsets(self) -> Dict[int, float]:
        """Eccentricity per beam line, taken from its section."""

        return {
            edge_id: float(self.beam_sections[name].eccentricity)
            for edge_id, name in self.edge_sections.items()
            if name in self.beam_sections and self.beam_sections[name].eccentricity
        }

    # ------------------------------------------------------------------
    # supports and loads
    # ------------------------------------------------------------------
    def add_support(self, support: Support) -> Support:
        self._require_entity(support.ref)
        self.supports.append(support)
        return support

    def add_symmetry(
        self,
        ref: EntityRef,
        normal: str | Sequence[float],
        *,
        antisymmetric: bool = False,
        tolerance: float = 1.0e-6,
    ) -> Support:
        """Restrain a point, line or plate as a symmetry plane.

        Checks that the entity actually *lies* in the plane before adding the
        support.  A symmetry condition on an edge that runs across the plane
        rather than along it restrains the wrong degrees of freedom everywhere
        it touches, and the model still solves and still looks reasonable, so
        the mistake would otherwise surface as a stiffness that is merely a bit
        wrong.

        ``antisymmetric`` swaps to the complementary set, for a load that is
        antisymmetric about the plane.
        """

        from .attributes import _AXES, _symmetry_axis, antisymmetry, symmetry

        self._require_entity(ref)
        axis = _symmetry_axis(normal)
        self._require_in_plane(ref, _AXES[axis], axis, tolerance)
        build = antisymmetry if antisymmetric else symmetry
        return self.add_support(build(ref, axis))

    def _require_in_plane(
        self, ref: EntityRef, index: int, axis: str, tolerance: float
    ) -> None:
        points = self._entity_points(ref)
        spread = float(points[:, index].max() - points[:, index].min())
        if spread > tolerance:
            raise ProjectError(
                f"{ref} does not lie in a plane normal to {axis}: its "
                f"{axis} coordinate varies by {spread:.6g} m (from "
                f"{points[:, index].min():.6g} to {points[:, index].max():.6g}). "
                "A symmetry condition applies to the entities lying *in* the "
                "plane, not to those crossing it."
            )

    def _entity_points(self, ref: EntityRef) -> "np.ndarray":
        """Points along an entity, enough to tell whether it is planar.

        For a plate the boundary is enough: a Coons patch is an affine
        combination of points on its four sides, so a face whose whole boundary
        lies in a plane lies in that plane too.
        """

        import numpy as np

        geometry = self.geometry
        if ref.kind == "vertex":
            return np.asarray([geometry.vertex_position(ref.id)], dtype=float)
        if ref.kind == "edge":
            return np.asarray(
                geometry.sample_edge(ref.id, np.linspace(0.0, 1.0, 9)),
                dtype=float,
            )
        if ref.kind == "face":
            samples = [
                geometry.sample_edge(item.edge, np.linspace(0.0, 1.0, 5))
                for item in geometry.faces[ref.id].loop
            ]
            return np.concatenate(samples, axis=0)
        raise ProjectError(f"cannot take points of a {ref.kind}")

    def add_mass(self, mass: Mass) -> Mass:
        self._require_entity(mass.ref)
        self.masses.append(mass)
        return mass

    def load_case(self, name: str = "default") -> LoadCase:
        """Fetch a load case, creating it on first use."""

        if name not in self.load_cases:
            self.load_cases[name] = LoadCase(name=name)
        return self.load_cases[name]

    def add_combination(
        self, name: str, factors: Mapping[str, float]
    ) -> Combination:
        """Define a factored sum of load cases."""

        unknown = sorted(set(factors) - set(self.load_cases))
        if unknown:
            raise ProjectError(
                f"combination {name!r} references undefined load case(s) "
                f"{unknown}"
            )
        combination = Combination(name=name, factors=dict(factors))
        self.combinations[name] = combination
        return combination

    def add_imperfection(self, imperfection: Imperfection) -> Imperfection:
        self._require_entity(imperfection.ref)
        self.imperfections.append(imperfection)
        return imperfection

    # ------------------------------------------------------------------
    # meshing controls
    # ------------------------------------------------------------------
    def add_refinement(self, refinement: Refinement) -> Refinement:
        """Ask for smaller elements in one region."""

        if refinement.ref is not None:
            self._require_entity(refinement.ref)
        self.refinements.append(refinement)
        return refinement

    def set_element_order(self, order: str) -> str:
        """Choose Q4 and 2-node beams, or Q8 and 3-node beams."""

        if order not in ELEMENT_ORDERS:
            raise ProjectError(
                f"unknown element order {order!r}; expected one of "
                f"{', '.join(ELEMENT_ORDERS)}"
            )
        self.element_order = order
        return order

    # ------------------------------------------------------------------
    # meshing
    # ------------------------------------------------------------------
    def generate_mesh(
        self,
        target_size: float,
        *,
        overrides: Mapping[int, int] | None = None,
        seeding: Seeding | None = None,
        refinements: Iterable[Refinement] | None = None,
        order: str | None = None,
    ) -> Mesh:
        """Mesh every plate, plus every line carrying a beam.

        ``refinements`` and ``order`` default to the project's own, so a script
        that sets them once does not have to repeat them at every call, and a
        one-off comparison can still override them here.
        """

        if not self.geometry.faces and not self.edge_sections:
            raise ProjectError(
                "nothing to mesh: the model has no plates and no beams"
            )
        return generate_mesh(
            self.geometry,
            target_size=target_size,
            overrides=overrides,
            beam_edges=self.beam_edges,
            beam_offsets=self.beam_offsets,
            seeding=seeding,
            refinements=(
                self.refinements if refinements is None else list(refinements)
            ),
            order=self.element_order if order is None else order,
        )

    # ------------------------------------------------------------------
    # validation
    # ------------------------------------------------------------------
    def validate(
        self, *, require_loads: bool = True, require_supports: bool = True
    ) -> None:
        """Fail closed on an incomplete model, naming what is missing.

        Not every analysis needs the same things: a free-free modal analysis
        has no supports and no loads by design, so the caller says what this
        run actually requires.
        """

        problems: List[str] = []

        unsectioned = sorted(set(self.geometry.faces) - set(self.face_sections))
        if unsectioned:
            problems.append(
                f"plates without a section: {unsectioned}. Assign a plate "
                "section, or the solver has no thickness to use."
            )

        for name, section in self.plate_sections.items():
            if section.material not in self.materials:
                problems.append(
                    f"plate section {name!r} uses undefined material "
                    f"{section.material!r}"
                )
        for name, beam in self.beam_sections.items():
            if beam.material not in self.materials:
                problems.append(
                    f"beam section {name!r} uses undefined material "
                    f"{beam.material!r}"
                )

        if require_supports and not self.supports:
            problems.append(
                "the model has no supports; a linear static solve would be "
                "singular"
            )

        if require_loads and (
            not self.load_cases
            or all(case.is_empty() for case in self.load_cases.values())
        ):
            problems.append("the model has no loads")

        if problems:
            raise ProjectError(
                f"model {self.name!r} is not ready to solve:\n  - "
                + "\n  - ".join(problems)
            )

    # ------------------------------------------------------------------
    # lookup
    # ------------------------------------------------------------------
    def plate_section_of(self, face_id: int) -> PlateSection:
        try:
            return self.plate_sections[self.face_sections[int(face_id)]]
        except KeyError:
            raise ProjectError(f"face {face_id} has no plate section") from None

    def beam_section_of(self, edge_id: int) -> BeamSection:
        try:
            return self.beam_sections[self.edge_sections[int(edge_id)]]
        except KeyError:
            raise ProjectError(f"edge {edge_id} has no beam section") from None

    def _require_material(self, name: str) -> Material:
        try:
            return self.materials[name]
        except KeyError:
            raise ProjectError(f"no material named {name!r}") from None

    def _require_entity(self, ref: EntityRef) -> None:
        self.geometry.entity_ref(ref.kind, ref.id)

    # ------------------------------------------------------------------
    # convenience references
    # ------------------------------------------------------------------
    def face(self, face_id: int) -> EntityRef:
        return self.geometry.entity_ref("face", face_id)

    def edge(self, edge_id: int) -> EntityRef:
        return self.geometry.entity_ref("edge", edge_id)

    def point(self, vertex_id: int) -> EntityRef:
        return self.geometry.entity_ref("vertex", vertex_id)
