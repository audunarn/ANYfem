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
    """Resolve an override key (Sheet ID or unique Sheet name) to a Sheet ID, or None."""
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


def resolve_sides(project, thickness_sides=None, sheet_ids=None) -> dict[int, str]:
    """Return resolved ``{sheet_id: side}`` for ``sheet_ids`` (default: every Sheet).

    ``None`` or ``"centre"`` requests the historical all-centre drawing. A
    mapping of Sheet ID or name to a side gives explicit sides; unlisted Sheets
    are centred.
    """
    geometry = project.geometry
    ids = tuple(geometry.sheets) if sheet_ids is None else tuple(sheet_ids)
    sides = {sheet: "centre" for sheet in ids}
    if thickness_sides is None or thickness_sides == "centre":
        return sides
    if not hasattr(thickness_sides, "items"):
        raise ValueError("Thickness sides must be 'centre' or a mapping of Sheet to side")
    for key, side in thickness_sides.items():
        sheet = _sheet_key(geometry, key)
        if side not in SIDES:
            raise ValueError(f"Thickness side must be one of {', '.join(SIDES)}, not {side!r}")
        if sheet in sides:
            sides[sheet] = side
    return sides


def sides_for_part(project, part_id, thickness_sides=None) -> dict[int, str]:
    """:func:`resolve_sides` for the Sheets of one Part."""
    part = project.geometry.parts.get(part_id)
    return resolve_sides(project, thickness_sides, () if part is None else part.sheet_ids)
