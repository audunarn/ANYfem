"""The reference geometry a nonlinear solve actually ran on.

Project imperfections are applied while the model is built, so they already
live in ``built.fe_model``.  A run-time ``imperfection`` passed to the
nonlinear-static or arc-length solve, and the capacity workflow's eigenmode
imperfection, are applied by the solver to a *copy* of that model.  The
displacements are measured from the copy's coordinates, so every recovery
that combines them with geometry -- element frames, strain-displacement
operators, transverse shear -- has to use the copy as well.  Recovering on the
perfect model mixes two geometries and silently returns wrong stresses.

:class:`SolvedGeometry` binds a result to that solved model.  It is exact or
it refuses:

* the capacity workflow returns the very model it solved, which is kept as is;
* for nonlinear static and arc length the solver keeps its copy to itself.
  ANYfem converts the imperfection once into an owned snapshot, hands that
  snapshot to the solver, checks it against the solver's provenance record
  after the solve, and immediately makes the same copy through the solver's
  own :func:`apply_imperfection` (bit-identical coordinates, and the model
  state as it was when solved).  A disagreement is a typed refusal, not a
  plausible number.

The copy costs one model deep copy after an imperfect solve; runs without an
imperfection pay nothing.
"""

from __future__ import annotations

from typing import Any, List, Mapping, Optional

import numpy as np

from ..model.project import ProjectError

__all__ = [
    "SolvedGeometry",
    "UnboundSolvedGeometryError",
    "owned_imperfection_field",
    "solved_model_of",
    "unbound_refusal",
]


class UnboundSolvedGeometryError(ProjectError):
    """A result's solved geometry is unknown or disagrees with its solver record."""


def owned_imperfection_field(base_model: Any, imperfection: Any):
    """An immutable snapshot of ``imperfection`` as the solver will see it.

    The conversion mirrors the solver's own owned-input snapshot: offsets as
    float64 copies keyed by integer node id, the field's name and a plain copy
    of its metadata.  Converting against ``base_model`` before the solve is the
    same model state the solver converts against.
    """

    from anyfesolver.imperfections import ImperfectionField, to_imperfection_field

    converted = to_imperfection_field(base_model, imperfection)
    offsets = {
        int(node_id): np.asarray(offset, dtype=np.float64).reshape(-1).copy()
        for node_id, offset in converted.offsets.items()
    }
    return ImperfectionField(
        offsets, name=str(converted.name), metadata=dict(converted.metadata)
    )


def _has_offset(imperfection_field: Any) -> bool:
    # ``!= 0`` is also true for NaN, so a non-finite field is never treated as
    # the perfect geometry.
    return any(np.any(offset != 0.0) for offset in imperfection_field.offsets.values())


def _records(entries: Any) -> List[Mapping[str, Any]]:
    if entries is None:
        return []
    return [entry for entry in entries if isinstance(entry, Mapping)]


def _same_record(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return (
        str(left.get("name")) == str(right.get("name"))
        and float(left.get("max_offset", np.nan)) == float(right.get("max_offset", np.nan))
    )


def _solver_imperfection_records(raw_result: Any) -> Optional[List[Mapping[str, Any]]]:
    info = getattr(raw_result, "info", None)
    if not isinstance(info, Mapping) or "imperfection" not in info:
        return None
    return _records(info.get("imperfection"))


class SolvedGeometry:
    """The FE model a result's displacements are measured from, or a refusal."""

    __slots__ = ("_model", "_refusal")

    def __init__(self, *, model: Any = None, refusal: Optional[str] = None) -> None:
        if (model is None) == (refusal is None):
            raise ValueError("SolvedGeometry needs exactly one of a model or a refusal")
        self._model = model
        self._refusal = refusal

    # ------------------------------------------------------------------
    @classmethod
    def exact(cls, model: Any) -> "SolvedGeometry":
        """Bind to the model object the solver returned as solved."""

        return cls(model=model)

    @classmethod
    def refused(cls, reason: str) -> "SolvedGeometry":
        """A binding that refuses recovery, e.g. for views of an unbound result."""

        return cls(refusal=str(reason))

    @classmethod
    def rebuild(cls, base_model: Any, imperfection_field: Any, raw_result: Any,
                *, context: str) -> Optional["SolvedGeometry"]:
        """Bind to ``base_model`` plus the run-time imperfection the solver applied.

        Call this straight after the solve, with the owned snapshot from
        :func:`owned_imperfection_field` that was also the object handed to the
        solver -- so solve and recovery consume one conversion.  The copy is
        made here, eagerly, so later edits to the built model (coordinates,
        thickness, materials, connectivity) cannot leak into it: it is the same
        deep copy of the same model state the solver made, moved by the same
        offsets through the same :func:`apply_imperfection`.  Returns ``None``
        when every offset is exactly zero: the solved coordinates are then the
        base coordinates bit for bit, and the ordinary route stays untouched.
        """

        from anyfesolver.imperfections import ImperfectionField

        if not isinstance(imperfection_field, ImperfectionField):
            raise TypeError("rebuild needs the owned ImperfectionField given to the solver")
        records = _solver_imperfection_records(raw_result)
        build_records = _records(getattr(base_model, "imperfection_metadata", None))
        expected = {"name": imperfection_field.name, "max_offset": imperfection_field.max_offset}
        if (
            records is None
            or len(records) != len(build_records) + 1
            or not all(_same_record(a, b) for a, b in zip(records, build_records))
            or not _same_record(records[-1], expected)
        ):
            raise UnboundSolvedGeometryError(
                f"{context}: the solver's imperfection record {records!r} does not "
                f"match the build-time imperfections {build_records!r} plus the "
                f"run-time imperfection {expected!r}; the solved geometry cannot be "
                "reproduced, so no stresses are recovered on it"
            )
        if not _has_offset(imperfection_field):
            return None
        from anyfesolver import apply_imperfection

        return cls(model=apply_imperfection(base_model, imperfection_field, copy_model=True))

    # ------------------------------------------------------------------
    def model(self) -> Any:
        """The solved FE model; raises when this binding is a refusal."""

        if self._refusal is not None:
            raise UnboundSolvedGeometryError(self._refusal)
        return self._model

    def __getstate__(self):
        return (self._model, self._refusal)

    def __setstate__(self, state) -> None:
        self._model, self._refusal = state


def _runtime_imperfection_unbound(view: Any) -> Optional[str]:
    records = _solver_imperfection_records(getattr(view, "raw_result", None))
    if records is not None:
        build_records = _records(getattr(view.built.fe_model, "imperfection_metadata", None))
        runtime = records[len(build_records):]
        if any(float(entry.get("max_offset", np.nan)) != 0.0 for entry in runtime):
            return "a run-time imperfection"
    info = getattr(view, "info", None)
    workflow = info.get("raw") if isinstance(info, Mapping) else None
    imperfect = getattr(workflow, "imperfect_model", None)
    if imperfect is not None and imperfect is not view.built.fe_model:
        field = getattr(workflow, "imperfection", None)
        if field is None or _has_offset(field):
            return "the capacity workflow's imperfection"
    return None


def unbound_refusal(view: Any) -> Optional[str]:
    """The refusal message for an imperfect result without a binding, if any."""

    reason = _runtime_imperfection_unbound(view)
    if reason is None:
        return None
    return (
        f"this result was solved on geometry moved by {reason}, but it carries "
        "no binding to that solved model; stresses recovered on the perfect "
        "built model would be wrong, so recovery is refused"
    )


def solved_model_of(view: Any) -> Any:
    """The FE model to recover ``view``'s displacements on.

    Views without a binding use ``built.fe_model``, exactly as before -- unless
    their solver record says the solve ran on imperfect geometry that is not
    in ``built.fe_model``, which is refused rather than recovered on the
    perfect shape.
    """

    geometry = getattr(view, "solved_geometry", None)
    if geometry is not None:
        return geometry.model()
    refusal = unbound_refusal(view)
    if refusal is not None:
        raise UnboundSolvedGeometryError(refusal)
    return view.built.fe_model
