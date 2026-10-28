"""One pressure arrow per original hydrodynamic source panel.

A ``SourceAwarePressureHistory`` retains the canonical ANYhydro pressure bank
and the actual wave realization it was produced from.  This module evaluates
that source at the first AVAILABLE time of the history (``history.times[0]``,
not necessarily ``t = 0``) and turns the single snapshot into plain arrow data.

Nothing here maps pressure to a structural mesh, evaluates the mapped
time history, or runs a BEM solve.  The work is: validate the wet domain,
decode the bank once, compile the response series (ANYloads) and take one
snapshot.  The decoded snapshot is kept in a small bounded cache keyed by the
immutable history content identity and the load binding.

Conventions (from the source bank): Pa, inward positive, outward panel normals,
``exp(-i omega t)`` phasors, hydrodynamic WORLD0 coordinates.  Display
coordinates come from ``hydro_to_project_points``; vectors are unchanged.
"""

from __future__ import annotations

from collections import OrderedDict
from copy import copy
from dataclasses import dataclass
import hashlib
import json
from typing import Optional, Sequence, Tuple

import numpy as np

__all__ = [
    "SOURCE_PANEL_LIMIT",
    "LEGEND_KIND",
    "SourcePressureSnapshot",
    "SourcePressureGlyphs",
    "is_source_load",
    "split_source_loads",
    "source_pressure_snapshot",
    "build_source_glyphs",
    "glyph_groups",
    "snapshot_cache_info",
    "clear_snapshot_cache",
    "NEUTRAL_COLOR",
    "pressure_color",
]

# The admitted physical source has 236 panels; this is the hard bound.
SOURCE_PANEL_LIMIT = 600
_CACHE_ENTRIES = 4
_FRAME = "hydro_to_project:+[0,0,1.5]"
# Marks this overlay legend so Scene.merge never replaces an existing legend.
LEGEND_KIND = "hydro_source_pressure"

# Diverging scale around zero: positive (inward, compression) is warm, negative
# (outward, suction) is cool, exactly zero is neutral grey.
NEUTRAL_COLOR = "#bdbdbd"
_NEGATIVE_END = "#2166ac"
_POSITIVE_END = "#b2182b"
_COLOR_STEPS = 10  # per side; at most 2 * 10 + 1 distinct colours per scene

_CACHE: "OrderedDict[tuple, SourcePressureSnapshot]" = OrderedDict()
_STATS = {"hits": 0, "misses": 0, "decodes": 0}


@dataclass(frozen=True)
class SourcePressureSnapshot:
    """Physical signed pressure (Pa, inward positive) at every source panel."""

    time: float
    panel_ids: tuple
    centers: np.ndarray  # project display coordinates
    normals: np.ndarray  # outward, unchanged from the source
    areas: np.ndarray
    pressures: np.ndarray
    bank_id: str
    geometry_key: str


@dataclass(frozen=True)
class SourcePressureGlyphs:
    starts: np.ndarray
    ends: np.ndarray
    colors: tuple
    pressures: np.ndarray
    panel_ids: tuple
    peak: float
    legend: dict


def snapshot_cache_info() -> dict:
    return dict(_STATS, entries=len(_CACHE), limit=_CACHE_ENTRIES)


def clear_snapshot_cache() -> None:
    _CACHE.clear()
    for key in _STATS:
        _STATS[key] = 0


def _source_history_type():
    from anyloads.source_pressure_history import SourceAwarePressureHistory

    return SourceAwarePressureHistory


def is_source_load(load) -> bool:
    history = getattr(load, "history", None)
    return history is not None and isinstance(history, _source_history_type())


def split_source_loads(case) -> Tuple[list, object]:
    """``(source loads, case without them)``; the case is never mutated."""

    loads = list(getattr(case, "wet_pressures", ()))
    sources = [load for load in loads if is_source_load(load)]
    if not sources:
        return [], case
    rest = copy(case)
    rest.wet_pressures = [load for load in loads if not is_source_load(load)]
    return sources, rest


def _frozen(value) -> np.ndarray:
    """Read-only float64 array on its own bytes storage (cannot poison a cache)."""

    a = np.asarray(value, dtype=np.float64)
    return np.frombuffer(a.tobytes(), dtype=np.float64).reshape(a.shape)


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def source_pressure_snapshot(
    project, load, envelope_factor: float = 1.0
) -> SourcePressureSnapshot:
    """The load's scaled source pressure at its first available time.

    Raises ``PressureRemapError``/``ValueError``/``LoadError``/``ImportError``
    when the wet geometry is not the admitted one, the source is stale or the
    optional bank reader is unavailable; the caller draws nothing then.
    """

    from ..application.hydro_wave_pressure import hydro_to_project_points
    from ..application.pressure_transfer import PressureRemapError, _domain

    history = load.history
    lineage = json.loads(history.domain_json)
    domain = _domain(project, load.surfaces)
    if lineage["domain"] != domain:
        raise PressureRemapError(
            "wave source wet geometry/selection changed; regenerate wave input"
        )
    t0 = float(history.times[0])
    scale = float(load.scale) * float(envelope_factor)
    domain_key = hashlib.sha256(_canonical(domain).encode()).hexdigest()
    key = (
        history.content_sha256, t0, float(load.scale), float(envelope_factor),
        str(getattr(load, "id", "")), tuple(load.surfaces), domain_key, _FRAME,
    )
    found = _CACHE.get(key)
    if found is not None:
        _CACHE.move_to_end(key)
        _STATS["hits"] += 1
        return found
    _STATS["misses"] += 1

    from anyhydro.pressure_bank import PressureResponseBank
    from anyloads.pressure_series import compile_pressure_series
    from anyloads.sea_state import WaveRealization
    from ..application.pressure_transfer import _validate_source

    _STATS["decodes"] += 1
    bank = PressureResponseBank.from_dict(json.loads(history.source_payload_json))
    _validate_source(bank)
    if bank.bank_id != lineage["source_bank_id"]:
        raise PressureRemapError("retained pressure source identity changed")
    if len(bank.panel_ids) > SOURCE_PANEL_LIMIT:
        raise PressureRemapError("source panel count exceeds the display bound")
    realization = WaveRealization.from_dict(json.loads(history.realization_json))
    series = compile_pressure_series(bank, realization)
    values = np.asarray(series.snapshot(t0).pressures, dtype=float) * scale
    if not np.isfinite(values).all():
        raise PressureRemapError("nonfinite source pressure snapshot")
    centers = _frozen(hydro_to_project_points(bank.centers))
    normals = _frozen(bank.normals)
    geometry_key = hashlib.sha256(
        centers.tobytes() + normals.tobytes() + str(bank.panel_ids).encode()
    ).hexdigest()
    made = SourcePressureSnapshot(
        t0, tuple(bank.panel_ids), centers, normals,
        _frozen(bank.areas), _frozen(values), bank.bank_id, geometry_key,
    )
    _CACHE[key] = made
    while len(_CACHE) > _CACHE_ENTRIES:
        _CACHE.popitem(last=False)
    return made


def _hex(color: str) -> np.ndarray:
    return np.array([int(color[i : i + 2], 16) for i in (1, 3, 5)], dtype=float)


def _mix(first: str, second: str, amount: float) -> str:
    mixed = _hex(first) + (_hex(second) - _hex(first)) * amount
    return "#{:02x}{:02x}{:02x}".format(*np.clip(np.rint(mixed), 0, 255).astype(int))


def pressure_color(pressure: float, peak: float) -> str:
    """Signed diverging colour; exactly zero (or a zero peak) is neutral."""

    if not (peak > 0.0) or not np.isfinite(pressure) or pressure == 0.0:
        return NEUTRAL_COLOR
    level = int(np.ceil(min(abs(pressure) / peak, 1.0) * _COLOR_STEPS))
    amount = max(level, 1) / _COLOR_STEPS
    return _mix(NEUTRAL_COLOR, _POSITIVE_END if pressure > 0.0 else _NEGATIVE_END, amount)


def build_source_glyphs(
    snapshot: SourcePressureSnapshot, arrow_length: float,
    peak: Optional[float] = None, time_label: Optional[str] = None,
) -> SourcePressureGlyphs:
    """Arrow geometry and colours for a snapshot.

    Length is the same readable size everywhere, ``0.7 * sqrt(panel area)``
    capped at half the scene arrow length; magnitude is carried by colour.
    Positive (inward) pressure points into the hull and ends on the panel
    centre; negative (outward) pressure starts on the panel and points away.
    """

    pressures = snapshot.pressures
    if peak is None:
        peak = float(np.max(np.abs(pressures))) if len(pressures) else 0.0
    if time_label is None:
        time_label = f"t = {snapshot.time:g} s"
    cap = 0.5 * float(arrow_length) if arrow_length > 0.0 else np.inf
    length = np.minimum(0.7 * np.sqrt(snapshot.areas), cap)[:, None]
    inward = (pressures >= 0.0)[:, None]
    centers, outward = snapshot.centers, snapshot.normals
    starts = np.where(inward, centers + length * outward, centers)
    ends = np.where(inward, centers, centers + length * outward)
    colors = tuple(pressure_color(float(p), peak) for p in pressures)
    levels = np.linspace(-peak, peak, 5) if peak > 0.0 else np.zeros(5)
    legend = {
        "kind": LEGEND_KIND,
        "levels": [float(v) for v in levels],
        "unit": "Pa",
        "title": f"Source hydro pressure, inward + ({time_label})",
        "colors": [pressure_color(float(v), peak) for v in levels],
    }
    return SourcePressureGlyphs(
        starts, ends, colors, pressures, snapshot.panel_ids, peak, legend
    )


def _first_time_case(project, case, t0: float):
    """The case an envelope must see at ``t0``.

    Inside a load series this is the canonical resolved step whose time is
    ``t0`` (own step/number/name/parameters/window times), found through
    ``Project.series_children``/``resolve_case``; the envelope arithmetic itself
    stays with the case.  Outside a series the case is kept with only its time
    overridden.
    """

    # resolve_case clears parent/times on a derived step but keeps ``series``.
    definition = getattr(case, "parent", None)
    if definition is None and getattr(case, "times", None) is not None:
        definition = case.name
    if definition is None:
        named = getattr(case, "series", None)
        owner = project.load_cases.get(named) if named is not None else None
        if owner is not None and owner.times is not None:
            definition = named
    if definition is not None:
        steps = [s for s in project.series_children(definition) if s.time is not None]
        if steps:
            step = min(steps, key=lambda s: abs(float(s.time) - t0))
            if abs(float(step.time) - t0) <= 1e-9 * max(1.0, abs(t0)):
                return project.resolve_case(step)
    fallback = copy(case)
    fallback.time = t0
    return fallback


def glyph_groups(
    project, case, loads: Sequence, arrow_length: float
) -> list:
    """Glyphs for source loads; loads on the same panels at one time are summed.

    Refused loads (changed wet geometry, stale or unreadable source, envelope
    off at the first time) contribute nothing and are never mapped instead.
    """

    groups: "OrderedDict[tuple, SourcePressureSnapshot]" = OrderedDict()
    for load in loads:
        try:
            t0 = float(load.history.times[0])
            probe = _first_time_case(project, case, t0)
            factor = float(probe.envelope_factor(load.envelope))
            if factor == 0.0:
                continue
            snapshot = source_pressure_snapshot(project, load, factor)
        except Exception:  # refusal: no glyph, no fallback to mapped arrows
            continue
        key = (snapshot.geometry_key, snapshot.time)
        previous = groups.get(key)
        if previous is not None:
            snapshot = SourcePressureSnapshot(
                previous.time, previous.panel_ids, previous.centers, previous.normals,
                previous.areas, previous.pressures + snapshot.pressures,
                previous.bank_id, previous.geometry_key,
            )
        groups[key] = snapshot
    snapshots = list(groups.values())
    if not snapshots:
        return []
    # One symmetric scale and one truthful time label for every group, so colours
    # and the single legend agree across loads with different first times.
    peak = max(float(np.max(np.abs(s.pressures))) for s in snapshots)
    times = sorted({s.time for s in snapshots})
    label = (f"t = {times[0]:g} s" if len(times) == 1
             else "first times " + ", ".join(f"{t:g}" for t in times) + " s")
    return [build_source_glyphs(s, arrow_length, peak, label) for s in snapshots]
