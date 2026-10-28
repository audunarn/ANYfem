"""Colour the actual structural mesh by the pressure of one selected load step.

No solve, BEM or result artifact is involved.  The selected RESOLVED case step
is evaluated with the solver's own ``wet_element_pressures`` (which applies the
canonical envelope/load scale and the effective, source-aware mesh mapping of
``application/pressure_transfer.py``) on the active mesh.  Only that single
snapshot is evaluated; the whole history is never expanded.

Sign convention: ``WetElementPressure.pressure`` is the signed coefficient along
the ELEMENT NORMAL (the direction the solver's positive pressure acts along), in
Pa.  It is not an inward-positive fluid pressure; two elements wound in opposite
directions show opposite signs for the same inward fluid force.  The values are
shown unchanged (no ``abs`` and no inward conversion).

Coverage: ``wet_element_pressures`` returns only elements that received a
contribution, so an absent element may be unloaded, exactly zero or uncovered.
Such elements are drawn in the plain mesh colour and labelled
``NO_PRESSURE_LABEL``; they are never filled with 0.  An element that returned an
explicit 0 (e.g. by cancellation) is 0 on the signed scale (neutral grey).
"""

from __future__ import annotations

import numpy as np

from ..ui.scene import COLOR_MESH_FILL, Scene, build_mesh_scene
from .hydro_pressure_glyphs import NEUTRAL_COLOR, pressure_color

__all__ = [
    "LEGEND_KIND",
    "NO_PRESSURE_LABEL",
    "SIGN_NOTE",
    "MeshPressurePreviewError",
    "PreviewSelection",
    "resolve_preview_selection",
    "build_mesh_pressure_scene",
]

LEGEND_KIND = "mesh_pressure_preview"
NO_PRESSURE_LABEL = "No returned pressure (unloaded or zero)"
SIGN_NOTE = (
    "Sign is along each element's own normal, not inward fluid pressure; "
    "elements with opposite winding show opposite signs."
)
NO_PRESSURE_COLOR = COLOR_MESH_FILL


class MeshPressurePreviewError(ValueError):
    """An actionable refusal; the message says what the user must do."""


class PreviewSelection:
    """The resolved step a preview shows: truthful case, number and time."""

    def __init__(self, case, name, number, count, series):
        self.case, self.name, self.number, self.count, self.series = case, name, number, count, series

    @property
    def time(self):
        value = getattr(self.case, "time", None)
        return None if value is None else float(value)

    def label(self) -> str:
        if self.series is None:
            return f"case '{self.name}'"
        when = "" if self.time is None else f", t = {self.time:g} s"
        return f"series '{self.series}' step {self.number} of {self.count} ('{self.name}'){when}"


def _find_case(project, name):
    case = project.load_cases.get(name)
    if case is not None:
        return case
    for definition in project.load_cases.values():
        if getattr(definition, "times", None) is not None:
            for step in project.series_children(definition.name):
                if step.name == name:
                    return step
    raise MeshPressurePreviewError(f"load case {name!r} does not exist; choose a case in Load cases")


def resolve_preview_selection(project, name, number=None) -> PreviewSelection:
    """Pick the one step to show.  A series definition needs a step ``number`` (1-based)."""

    case = _find_case(project, name)
    series = case.name if getattr(case, "times", None) is not None else getattr(case, "parent", None)
    if series is None:
        return PreviewSelection(case, case.name, None, None, None)
    steps = list(project.series_children(series))
    if not steps:
        raise MeshPressurePreviewError(f"load series {series!r} has no time steps; set its times first")
    if getattr(case, "times", None) is None:
        index = next((i for i, step in enumerate(steps) if step.name == case.name), None)
        if index is None:
            raise MeshPressurePreviewError(f"step {name!r} is no longer part of series {series!r}")
        return PreviewSelection(steps[index], steps[index].name, index + 1, len(steps), series)
    chosen = 1 if number is None else int(number)
    if not 1 <= chosen <= len(steps):
        raise MeshPressurePreviewError(
            f"step number {chosen} is outside series {series!r}; choose 1 to {len(steps)}"
        )
    step = steps[chosen - 1]
    return PreviewSelection(step, step.name, chosen, len(steps), series)


def build_mesh_pressure_scene(project, mesh, name, number=None):
    """``(scene, selection, info)`` for one selected step on the actual mesh."""

    from ..solve.build import wet_element_pressures

    if mesh is None:
        raise MeshPressurePreviewError(
            "No structural mesh is active. Generate (or select) a mesh in the Mesh task, then Visualize on mesh again."
        )
    selection = resolve_preview_selection(project, name, number)
    resolved = project.resolve_case(selection.case)
    if not getattr(resolved, "wet_pressures", None):
        raise MeshPressurePreviewError(
            f"{selection.label()} has no wet pressure load; add a wet surface pressure or wave load to see pressure on the mesh."
        )
    try:
        records = wet_element_pressures(project, mesh, resolved, limit=None)
    except Exception as error:  # remap/identity/mapping refusals come from the existing owners
        raise MeshPressurePreviewError(
            f"Cannot map pressure for {selection.label()} onto the active mesh: {error}"
        ) from error
    values = {int(record.element_id): float(record.pressure) for record in records}
    if not all(np.isfinite(list(values.values()))):
        raise MeshPressurePreviewError(f"{selection.label()} produced a non-finite pressure; regenerate the load.")
    # No returned record is NOT numeric zero: the owner omits zero, off-envelope and
    # unloaded elements alike.  The mesh stays plain and is labelled as such.
    peak = max((abs(value) for value in values.values()), default=0.0)

    wet_info = _surface_wet_info(project, mesh, resolved, selection.time)
    scene = build_mesh_scene(project, mesh)
    mapped = unmapped = 0
    for patch in scene.faces:
        if patch.polygon_owners is None:
            continue
        colors = []
        for owners in patch.polygon_owners:
            element = next((o.id for o in owners if getattr(o, "kind", None) == "element"), None)
            if element is not None and int(element) in values:
                colors.append(pressure_color(values[int(element)], peak))
                mapped += 1
            else:
                colors.append(NO_PRESSURE_COLOR)
                unmapped += 1
        patch.colors = colors
    levels = np.linspace(-peak, peak, 5) if peak > 0.0 else np.zeros(5)
    when = "" if selection.time is None else f", t = {selection.time:g} s"
    number_text = "" if selection.number is None else f" step {selection.number}/{selection.count}"
    scene.legend = {
        "kind": LEGEND_KIND,
        "levels": [float(v) for v in levels],
        "unit": "Pa",
        "title": (f"Applied pressure (+ element normal), Pa - preview, not a result ({selection.name}{number_text}{when}); "
                  + (wet_info["legend_text"] if wet_info is not None and "legend_text" in wet_info else
                     "wave-following: colour = FULL-AREA element-average pressure (partial elements are scaled by "
                     "their wet fraction; the load is the consistent wet-subregion integral); plain mesh colour: "
                     "dry (exactly zero) or unloaded"
                     if wet_info is not None else "plain mesh colour: unloaded or zero")),
        "colors": [pressure_color(float(v), peak) for v in levels],
        "unmapped_label": NO_PRESSURE_LABEL,
        "unmapped_color": NO_PRESSURE_COLOR,
        "note": SIGN_NOTE,
    }
    info = dict(mapped=mapped, unmapped=unmapped, peak=peak, zero_color=NEUTRAL_COLOR,
                minimum=min(values.values(), default=None), maximum=max(values.values(), default=None),
                returned=len(values), values=values)
    if wet_info is not None:
        info.update(wet_info)
    return scene, selection, info


def _surface_wet_info(project, mesh, resolved, time):
    """Wet fractions and dry/partial/full counts for a wave-following load (None for every other load)."""

    from ..application.wave_surface_pressure import inspection_pressures, is_surface_history
    from ..solve.build import _surface_effective

    for load in resolved.wet_pressures:
        from anyloads.floating_history import FloatingCoupledLoadHistory
        if type(load.history) is FloatingCoupledLoadHistory:
            # Floating compound: mean-wet-region averages from the validated floating pressure view (never nodal forces).
            from ..application.floating_pressure_view import floating_element_pressures
            view = floating_element_pressures(load.history, project, mesh, float(time))
            fraction = view["wet_fraction"]
            return dict(
                wet_fraction={int(e): float(f) for e, f in zip(view["element_ids"], fraction)},
                dry_elements=0, partial_elements=int(np.sum(fraction < 1.0 - 1e-9)), full_wet_elements=int(np.sum(fraction >= 1.0 - 1e-9)),
                pressure_kind="full-area element-average pressure (floating, mean wet regions)",
                legend_text=view["labels"]["legend"],
            )
        if load.history is not None and is_surface_history(load.history):
            made = _surface_effective(project, mesh, load, None)
            records = inspection_pressures(project, mesh, made.history, time)   # same integration as the assembled loads
            states = [record[4] for record in records.values()]
            return dict(
                wet_fraction={int(e): float(r[3]) for e, r in records.items()},
                dry_elements=states.count(0), partial_elements=states.count(1), full_wet_elements=states.count(2),
                pressure_kind="full-area element-average pressure",
            )
    return None
