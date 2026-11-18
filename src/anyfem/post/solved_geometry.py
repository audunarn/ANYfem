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
* for nonlinear static and arc length the solver keeps its copy to itself, so
  the copy is rebuilt deterministically from an owned snapshot of the same
  imperfection field taken before the solve, through the solver's own
  :func:`apply_imperfection` (bit-identical coordinates).  The snapshot is
  checked against the solver's provenance record after the solve, and the base
  coordinates are checked again when the copy is finally rebuilt, so a
  disagreement or a later geometry edit is a typed refusal, not a plausible
  number.

The rebuild is lazy: it costs one model copy at the first recovery, nothing on
the solve path, and nothing at all for runs that never recover stresses.
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
]


class UnboundSolvedGeometryError(ProjectError):
    """A result's solved geometry is unknown or disagrees with its solver record."""


def _node_coordinates(model: Any) -> np.ndarray:
    nodes = model.mesh.nodes
    return np.array(
        [[nodes[key].x, nodes[key].y, nodes[key].z] for key in sorted(nodes)],
        dtype=np.float64,
    ).reshape(-1, 3)


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
    """Lazily resolved FE model whose coordinates a result is measured from."""

    __slots__ = ("_base", "_field", "_base_coordinates", "_model")

    def __init__(self, *, model: Any = None, base: Any = None, field: Any = None,
                 base_coordinates: Optional[np.ndarray] = None) -> None:
        if model is None and (base is None or field is None or base_coordinates is None):
            raise ValueError("SolvedGeometry needs a solved model or a base model and field")
        self._model = model
        self._base = base
        self._field = field
        self._base_coordinates = base_coordinates

    # ------------------------------------------------------------------
    @classmethod
    def exact(cls, model: Any) -> "SolvedGeometry":
        """Bind to the model object the solver returned as solved."""

        return cls(model=model)

    @classmethod
    def rebuild(cls, base_model: Any, imperfection_field: Any, raw_result: Any,
                *, context: str) -> Optional["SolvedGeometry"]:
        """Bind to ``base_model`` plus a run-time imperfection the solver applied.

        ``imperfection_field`` should be the snapshot from
        :func:`owned_imperfection_field` taken before the solve; any other
        imperfection object is snapshotted here.  Returns ``None`` when every
        offset is exactly zero: the solved coordinates are then the base
        coordinates bit for bit, and the ordinary route stays untouched.
        """

        from anyfesolver.imperfections import ImperfectionField

        owned = (
            imperfection_field
            if isinstance(imperfection_field, ImperfectionField)
            else owned_imperfection_field(base_model, imperfection_field)
        )
        records = _solver_imperfection_records(raw_result)
        build_records = _records(getattr(base_model, "imperfection_metadata", None))
        expected = {"name": owned.name, "max_offset": owned.max_offset}
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
        if not _has_offset(owned):
            return None
        return cls(base=base_model, field=owned,
                   base_coordinates=_node_coordinates(base_model))

    # ------------------------------------------------------------------
    def model(self) -> Any:
        """The solved FE model, rebuilt once on first use when necessary."""

        if self._model is None:
            if not np.array_equal(_node_coordinates(self._base), self._base_coordinates):
                raise UnboundSolvedGeometryError(
                    "the built model's coordinates changed after the nonlinear solve; "
                    "its imperfect solved geometry can no longer be reproduced"
                )
            from anyfesolver import apply_imperfection

            self._model = apply_imperfection(self._base, self._field, copy_model=True)
        return self._model

    @property
    def rebuilt(self) -> bool:
        """True when the model is reproduced from the base rather than retained."""

        return self._base is not None

    def __getstate__(self):
        # A rebuildable binding travels as base + field; the copy is remade on
        # the receiving side instead of doubling the transported model.
        model = None if self._base is not None else self._model
        return (self._base, self._field, self._base_coordinates, model)

    def __setstate__(self, state) -> None:
        self._base, self._field, self._base_coordinates, self._model = state


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
    reason = _runtime_imperfection_unbound(view)
    if reason is not None:
        raise UnboundSolvedGeometryError(
            f"this result was solved on geometry moved by {reason}, but it carries "
            "no binding to that solved model; stresses recovered on the perfect "
            "built model would be wrong, so recovery is refused"
        )
    return view.built.fe_model
