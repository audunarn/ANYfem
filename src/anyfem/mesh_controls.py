"""Toolkit-neutral, serializable controls for the public mesher API."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from time import perf_counter
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class AutomaticSelection:
    """Outcome of one Automatic -> SmartMapped applicability decision.

    ``eligible`` is applicability, never a promise that meshing succeeds: the
    owner pipeline still raises its own typed errors for quality/geometry
    failures after selection.  ``selection_seconds`` is execution-only timing.
    """

    eligible: bool
    reason: str
    verdict: Mapping[str, Any]
    seconds: float

    def to_dict(self) -> dict:
        return {
            # What the user asked for versus what was actually chosen.
            "requested": "auto",
            "selected": "smart_mapped" if self.eligible else "legacy_auto",
            "reason": self.reason,
            "eligibility": dict(self.verdict),
            "provenance": (
                "anymesher.smart_mapped.smart_mapped_eligibility on the detached "
                "prepared geometry, evaluated once before meshing"
            ),
            "selection_seconds": self.seconds,
        }


def faces_are_untrimmed_mappable(geometry: Any, face_ids: Iterable[int]) -> bool:
    """Whether every face is hole-free *and* owner-mappable (metadata only).

    ``anymesher.check_mappable`` deliberately permits holes and reports
    ``ok=True`` for them, so ``ok`` alone does not identify an already
    structured (mapped fast path / legacy Automatic) component.  A face that
    carries holes is never treated as already structured.  No meshing and no
    new algorithm: only the face's own hole metadata and the owner check.
    """

    from anymesher import check_mappable

    faces = tuple(face_ids)
    if not faces:
        return False
    for face_id in faces:
        face = geometry.faces[face_id]
        if getattr(face, "holes", None):
            return False
        if not check_mappable(geometry, face_id).ok:
            return False
    return True


def _owner_eligibility():
    """The owner's public eligibility function (single lookup seam)."""
    from anymesher.smart_mapped import smart_mapped_eligibility

    return smart_mapped_eligibility


def select_smart_mapped_for_automatic(
    geometry: Any,
    *,
    face_ids: Iterable[int] | None,
    target_size: float | None,
    order: str,
    refinements: Iterable[Any] = (),
    overrides: Mapping[int, int] | None = None,
    beam_edges: Iterable[int] = (),
    member_ids: Iterable[int] | None = (),
    seeding: Any = None,
) -> AutomaticSelection:
    """Ask the ANYmesher owner exactly once whether Automatic may use SmartMapped.

    The single reusable application-side selection (project API and headless
    backend share it); the geometry decision lives entirely in the owner's
    ``smart_mapped_eligibility``.  An owner without the helper keeps the legacy
    Automatic route with a recorded reason; any other exception propagates.
    """

    started = perf_counter()
    try:
        smart_mapped_eligibility = _owner_eligibility()
    except ImportError:
        return AutomaticSelection(
            False,
            "installed ANYmesher has no smart_mapped_eligibility; legacy Automatic kept",
            {},
            perf_counter() - started,
        )
    verdict = smart_mapped_eligibility(
        geometry,
        None if face_ids is None else tuple(face_ids),
        target_size,
        order=order,
        refinements=tuple(refinements),
        overrides=dict(overrides or {}),
        beam_edges=tuple(beam_edges),
        member_ids=None if member_ids is None else tuple(member_ids),
        seeding=seeding,
    )
    payload = dict(verdict.to_dict())
    eligible = bool(verdict.eligible)
    reason = str(
        payload.get("reason")
        or payload.get("reasons")
        or ("eligible" if eligible else "not eligible")
    )
    return AutomaticSelection(eligible, reason, payload, perf_counter() - started)


def native_v2_options_type():
    """Older supported mesher generations keep legacy GUI controls usable."""
    try:
        from anymesher.native_v2 import NativeMeshingOptions
    except ImportError:
        return None
    return NativeMeshingOptions


@dataclass(frozen=True)
class MeshControls:
    recombine: bool = True
    certification_mode: str = "interactive"
    point_placement: str = "legacy_lattice"
    metric_mode: str = "legacy"
    max_insertions: int = 10_000
    max_topology_operations: int = 1_000_000
    cancellation_interval: int = 256

    def __post_init__(self):
        from anymesher.hybrid import CertificationMode

        if type(self.recombine) is not bool:
            raise ValueError("quad recombination must be Boolean")
        if self.certification_mode not in {"interactive", "strict"}:
            raise ValueError("certification must be interactive or strict")
        CertificationMode(self.certification_mode)
        self.native_options()  # Owner validation is authoritative.

    def native_options(self):
        values = {
            key: value for key, value in asdict(self).items()
            if key not in {"recombine", "certification_mode"}
        }
        options_type = native_v2_options_type()
        if options_type is None:
            defaults = {key: type(self).__dataclass_fields__[key].default for key in values}
            if values != defaults:
                raise ValueError("Installed ANYmesher does not provide native-v2 controls; use legacy defaults")
            return None
        return options_type(**values)

    def parameters(self):
        return {
            ("recombine" if key == "recombine" else "native_" + key): value
            for key, value in asdict(self).items() if key != "certification_mode"
        }

    def effective_dict(self, strategy):
        # Mapped meshing never invokes a native triangulator/filler.
        return ({"certification_mode": self.certification_mode}
                if strategy == "mapped" else asdict(self))

    @classmethod
    def display_values(cls, settings):
        """Read saved intent for display even if execution capability is absent."""
        values = {key: field.default for key, field in cls.__dataclass_fields__.items()}
        if settings is None:
            return values
        parameters = dict(settings.parameters)
        values.update({
            key: parameters["native_" + key]
            for key in cls.__dataclass_fields__
            if "native_" + key in parameters
        })
        values["recombine"] = parameters.get("recombine", True)
        values["certification_mode"] = str(settings.certification_mode.value)
        return values

    @classmethod
    def from_settings(cls, settings):
        return cls(**cls.display_values(settings))


@dataclass(frozen=True)
class StructuredMeshControls:
    """Serializable GUI controls for ANYmesher's global structure planner.

    Preference and element-quality limits remain first-class Mesh-panel
    fields.  This object carries the remaining public
    ``StructuredMeshingOptions`` values so background jobs, previews and saved
    projects all use the same explicit policy.
    """

    allow_detached_partition: bool = True
    max_element_growth: float = 1.5
    minimum_size_ratio: float = 0.67
    maximum_size_ratio: float = 1.5
    maximum_radial_sides: int = 8
    maximum_candidates_per_component: int = 256
    maximum_face_records: int = 100_000
    maximum_blocks: int = 100_000
    maximum_edge_records: int = 200_000
    maximum_estimated_elements: int = 2_000_000
    maximum_divisions_per_edge: int = 100_000

    def __post_init__(self):
        # The owning package remains authoritative for range and combination
        # validation.  Constructing its public options object also prevents a
        # future ANYmesher field change from being silently accepted here.
        self.owner_options()

    def owner_options(self, *, preference="balanced", quality_policy=None):
        from anymesher.structured import StructuredMeshingOptions

        return StructuredMeshingOptions(
            preference=preference,
            quality_policy={} if quality_policy is None else quality_policy,
            **asdict(self),
        )

    def parameters(self):
        return {
            "structured_" + key: value for key, value in asdict(self).items()
        }

    def effective_dict(self):
        return asdict(self)

    @classmethod
    def display_values(cls, settings):
        values = {
            key: field.default for key, field in cls.__dataclass_fields__.items()
        }
        if settings is not None:
            parameters = dict(settings.parameters)
            values.update(
                {
                    key: parameters["structured_" + key]
                    for key in cls.__dataclass_fields__
                    if "structured_" + key in parameters
                }
            )
        return values

    @classmethod
    def from_settings(cls, settings):
        return cls(**cls.display_values(settings))
