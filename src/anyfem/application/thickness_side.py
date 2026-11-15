"""Which side of a plate's modelled reference surface carries its solid thickness.

The drawing normal ``n`` of a plate is defined once: for a planar Sheet it is
the supporting-plane normal of :func:`plate_profiles._plane` (largest absolute
world component positive); for a cylindrical face it is the outward radial
direction from the cylinder axis.  A side selects the material band along
``n`` measured from the reference surface (metres):

* ``centre``: ``(-t/2, +t/2)`` (the historical drawing convention)
* ``front``:  ``(0, t)``, the full thickness on the ``+n`` side
* ``back``:   ``(-t, 0)``

The ``midsurface_2d`` representation draws no thickness at all; its band is
``(0, 0)``.  Plate profiles, Z schedules and takeoff mass are unaffected by the
representation.
"""
from __future__ import annotations

import numpy as np

from .drawing_cache import memo, scoped

SIDES = ("centre", "front", "back")
SIDE_CHOICES = ("auto", *SIDES)
REPRESENTATIONS = ("solid", "midsurface_2d")


def check_representation(representation: str) -> str:
    if representation not in REPRESENTATIONS:
        raise ValueError("Drawing representation must be solid or midsurface_2d")
    return representation


def thickness_band(thickness: float, side: str = "centre", representation: str = "solid") -> tuple[float, float]:
    """Return ``(s_lo, s_hi)``, the material band along the plate drawing normal."""
    if side not in SIDES:
        raise ValueError(f"Thickness side must be one of {', '.join(SIDES)}, not {side!r}")
    if check_representation(representation) == "midsurface_2d":
        return 0., 0.
    if side == "front":
        return 0., thickness
    if side == "back":
        return -thickness, 0.
    return -thickness/2, thickness/2


def band_centre_half(band: tuple[float, float]) -> tuple[float, float]:
    """Reference-surface shift to the band middle and the band half depth.

    The centred band gives exactly ``(0, t/2)``, so centred callers keep their
    historical arithmetic bit for bit.
    """
    low, high = band
    return (low + high)/2, (high - low)/2


def normal_label(normal) -> str:
    """Short world description of ``+n``, for example ``+Z`` or ``(+0.707, 0, -0.707)``."""
    values = [float(x) for x in normal]
    for axis, value in zip("XYZ", values):
        if abs(abs(value) - 1.) <= 1e-9:
            return ("+" if value > 0 else "-") + axis
    return "(" + ", ".join("0" if abs(v) < 5e-4 else f"{v:+.3f}" for v in values) + ")"


def side_label(side: str, normal=None) -> str:
    """Human-readable side, such as ``front (+n = +Z)``."""
    return side if normal is None else f"{side} (+n = {normal_label(normal)})"


def _sheet_key(geometry, key):
    """Resolve a call-time override key (Sheet ID or unique Sheet name) to a Sheet ID, or None."""
    if isinstance(key, bool):
        return None
    if isinstance(key, int):
        return key if key in geometry.sheets else None
    text = str(key)
    named = [sheet.id for sheet in geometry.sheets.values() if sheet.name == text]
    if len(named) == 1:
        return named[0]
    if len(named) > 1:
        raise ValueError(f"Thickness side override {text!r} names more than one Sheet")
    if text.isdigit() and int(text) in geometry.sheets:
        return int(text)
    return None


def _check_choice(choice):
    if choice not in SIDE_CHOICES:
        raise ValueError(f"Thickness side must be one of {', '.join(SIDE_CHOICES)}, not {choice!r}")


def _stored_id(key):
    """A project override key: the Sheet's ID (its stable identity), never its name."""
    if isinstance(key, bool) or not isinstance(key, int):
        raise ValueError(f"Project thickness side overrides are keyed by Sheet ID, not {key!r} "
                         "(use set_override, or a call-time override for Sheet names)")
    return key


def _manual(project, overrides=None) -> dict[int, str]:
    """Project overrides, then call-time ``overrides``, as ``{sheet_id: choice}``.

    Project overrides are bound to Sheet IDs, so renaming a Sheet keeps its
    choice and a new Sheet never inherits a removed one's.  An ID naming no
    current Sheet is kept in the project (an undo may restore that Sheet) but
    has no effect.  Call-time overrides take Sheet IDs or unique names; a key
    naming no Sheet is refused, and a name and an ID for one Sheet must agree.
    """
    geometry = project.geometry
    result = {}
    for key, choice in project.drawing_thickness_sides.items():
        _check_choice(choice)
        if _stored_id(key) in geometry.sheets:
            result[key] = choice
    chosen = {}
    for key, choice in (overrides or {}).items():
        _check_choice(choice)
        sheet = _sheet_key(geometry, key)
        if sheet is None:
            raise ValueError(f"Thickness side override {key!r} names no Sheet")
        if chosen.get(sheet, choice) != choice:
            raise ValueError(f"Sheet {sheet} has conflicting thickness side overrides")
        chosen[sheet] = choice
    result.update(chosen)
    return result


def override_choices(project) -> dict[int, str]:
    """The project's manual choices by Sheet ID (Sheets without one are ``auto``)."""
    return _manual(project)


def set_override(project, sheet_id: int, choice: str) -> None:
    """Record one Sheet's side choice under its Sheet ID; ``auto`` removes it."""
    _check_choice(choice)
    if sheet_id not in project.geometry.sheets:
        raise ValueError(f"No Sheet with ID {sheet_id}")
    project.drawing_thickness_sides.pop(sheet_id, None)
    if choice != "auto":
        project.drawing_thickness_sides[sheet_id] = choice


def live_overrides(project) -> dict:
    """The project's overrides that name a current Sheet (the ones that are saved)."""
    return {key: side for key, side in project.drawing_thickness_sides.items()
            if _stored_id(key) in project.geometry.sheets}


def bind_stored_overrides(geometry, records) -> dict[int, str]:
    """Bind saved ``(sheet, side)`` records to Sheet IDs; a name must be unique and current.

    Older files of this branch keyed records by Sheet name.  A record naming no
    current Sheet, or two records for one Sheet, raise ``ValueError``.
    """
    bound = {}
    for key, side in records:
        _check_choice(side)
        sheet = _sheet_key(geometry, key)
        if sheet is None:
            raise ValueError(f"Thickness side record {key!r} names no Sheet")
        if sheet in bound:
            raise ValueError(f"Thickness side records name Sheet {sheet} twice")
        bound[sheet] = side
    return bound


def _sheet_faces(geometry, sheet):
    return [geometry.faces[geometry.face_uses[use].face_id] for use in sheet.face_use_ids]


def _vertices(geometry, faces):
    ids = set()
    for face in faces:
        for loop in (face.loop, *face.holes):
            for use in loop:
                edge = geometry.edges[use.edge]
                ids.update((edge.start, edge.end))
    return ids


def _auto(project, sheet_id, part_sheets, vertices):
    """Automatic side of one Sheet from the sides its attached plates occupy."""
    from .plate_profiles import _planar_geometry
    geometry = project.geometry
    from anygeometry.surfaces import Cylinder
    from .curved_strips import curved_strip
    sheet = geometry.sheets[sheet_id]
    faces = _sheet_faces(geometry, sheet)
    own = np.array([geometry.vertex_position(v) for v in vertices[sheet_id]])
    extent = float(np.linalg.norm(np.ptp(own, axis=0)))
    tolerance = max(geometry.tolerance.effective_length(extent), geometry.tolerance.effective_surface_residual(extent))
    if faces and all(isinstance(face.surface, Cylinder) for face in faces):
        try:
            strip = curved_strip(geometry, faces, sheet)
        except (ValueError, ImportError) as error:
            return "centre", f"no drawing surface ({error})"
        if not strip.consistent:
            return "centre", "outward normals disagree across the curved Sheet"
        signed = strip.radial_distance
    else:
        try:
            shape = _planar_geometry(geometry, faces, sheet)
        except (ValueError, ImportError) as error:
            return "centre", f"no drawing plane ({error})"

        def signed(points):
            return (points - shape.origin) @ shape.normal
    counts = {+1: 0, -1: 0}
    neighbours = [other for other in part_sheets if other != sheet_id and vertices[other] & vertices[sheet_id]]
    for other in neighbours:
        points = np.array([geometry.vertex_position(v) for v in sorted(vertices[other])])
        try:
            distance = signed(points)
        except ValueError as error:  # an ambiguous curved-strip mapping
            return "centre", f"neighbour side undefined ({error})"
        for sign in {int(np.sign(d)) for d in distance if abs(d) > tolerance}:
            counts[sign] += 1
    return _decide(len(neighbours), counts)


def _decide(neighbours, counts):
    def many(n):
        return f"{n} neighbour" + ("" if n == 1 else "s")
    if counts[+1] and counts[-1]:
        return "centre", f"neighbours on both sides ({counts[+1]} on +n, {counts[-1]} on -n)"
    if counts[+1]:
        return "front", f"{many(counts[+1])} on +n"
    if counts[-1]:
        return "back", f"{many(counts[-1])} on -n"
    return "centre", "no neighbours" if not neighbours else f"{many(neighbours)} in plane only"


@scoped
def resolve_thickness_sides(project, part_id, overrides=None) -> dict[int, tuple[str, str, str]]:
    """Return ``{sheet_id: (side, 'auto'|'manual', reason)}`` for one Part.

    Manual values come from ``project.drawing_thickness_sides`` and then the
    call-time ``overrides`` (Sheet name or ID to centre/front/back/auto). Each
    other Sheet is interpreted: its neighbours are the other Sheets of the
    same Part sharing a topological vertex or edge with it. The signed
    distances of their vertices along the drawing normal (ignoring those
    within geometric tolerance of the plane) give the occupied sides: only
    ``+n`` is ``front``, only ``-n`` is ``back``, both or none is ``centre``.
    A plate whose attached structure is on one side keeps its free face on the
    modelled surface and its thickness toward that structure.
    """
    geometry = project.geometry
    part = geometry.parts.get(part_id)
    if part is None:
        raise ValueError(f"No structural Part with ID {part_id}")
    manual = _manual(project, overrides)
    return dict(memo("thickness_sides", (part_id, tuple(sorted(manual.items()))),
                     lambda: _resolve_part(project, part, manual), owners=(project, geometry), freeze=False))


def _resolve_part(project, part, manual):
    geometry = project.geometry
    sheets = tuple(part.sheet_ids)
    vertices = {sheet: _vertices(geometry, _sheet_faces(geometry, geometry.sheets[sheet])) for sheet in sheets}
    result = {}
    for sheet in sheets:
        choice = manual.get(sheet, "auto")
        if choice != "auto":
            result[sheet] = (choice, "manual", "override")
        else:
            result[sheet] = (*_auto(project, sheet, sheets, vertices), )
            result[sheet] = (result[sheet][0], "auto", result[sheet][1])
    return result


@scoped
def resolve_sides(project, thickness_sides="auto", sheet_ids=None) -> dict[int, str]:
    """Return resolved ``{sheet_id: side}`` for ``sheet_ids`` (default: every Sheet).

    ``"auto"`` uses the project overrides and the automatic interpretation
    (:func:`resolve_thickness_sides`); a mapping of Sheet ID or name to
    centre/front/back/auto adds call-time overrides to that. ``None`` or
    ``"centre"`` requests the historical all-centre drawing.
    """
    geometry = project.geometry
    ids = tuple(geometry.sheets) if sheet_ids is None else tuple(sheet_ids)
    if thickness_sides is None or (isinstance(thickness_sides, str) and thickness_sides == "centre"):
        return {sheet: "centre" for sheet in ids}
    if isinstance(thickness_sides, str):
        if thickness_sides != "auto":
            raise ValueError("Thickness sides must be 'auto', 'centre' or a mapping of Sheet to side")
        overrides = None
    elif hasattr(thickness_sides, "items"):
        overrides = thickness_sides
    else:
        raise ValueError("Thickness sides must be 'auto', 'centre' or a mapping of Sheet to side")
    wanted = set(ids)
    sides = {}
    for part_id in sorted({geometry.sheets[s].part_id for s in ids if geometry.sheets[s].part_id is not None}):
        for sheet, (side, _, _) in resolve_thickness_sides(project, part_id, overrides).items():
            if sheet in wanted:
                sides[sheet] = side
    manual = _manual(project, overrides)
    for sheet in ids:
        if sheet not in sides:  # A Sheet outside any Part has no neighbours.
            choice = manual.get(sheet, "auto")
            sides[sheet] = "centre" if choice == "auto" else choice
    return {sheet: sides[sheet] for sheet in ids}


def sides_for_part(project, part_id, thickness_sides="auto") -> dict[int, str]:
    """:func:`resolve_sides` for the Sheets of one Part."""
    part = project.geometry.parts.get(part_id)
    return resolve_sides(project, thickness_sides, () if part is None else part.sheet_ids)
