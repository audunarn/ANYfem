"""Reference stock footprints on a selected plate's shell and physical skins.

These paths are drawing annotations, never cutting contours or connection intent.
The finite stock/plane cuts are shared with the section drawing consumer.
"""
from __future__ import annotations

import numpy as np

from .drawing_sections import _Frame, _pieces, _sheet_cuts
from .drawing_views import DrawingPath
from .plate_profiles import _planar_geometry
from .thickness_side import thickness_band


def _bounds(geometry, faces, half):
    from .plate_view import _native_spans
    poles = [span[:, :3] / span[:, 3:] for face in faces
             for loop in (face.loop, *face.holes) for edge in loop
             for span in _native_spans(geometry, edge.edge)]
    points = np.vstack(poles)
    return points.min(axis=0)-half, points.max(axis=0)+half


def _reach(band):
    """Largest distance of a material band from its reference surface."""
    return max(-band[0], band[1])


def plate_intersections(project, sheet, faces, physical, thickness, *, cylindrical=False, develop_points=None,
                        sides=None):
    """Return clipped reference paths and honest limitations, without model edits.

    For planar targets the reference shell and both skins are cut. Consequently
    an offset T joint touching a skin is retained even if it misses the shell.
    This is a surface-reference drawing, not a solid interference assessment.
    ``sides`` maps Sheet IDs to thickness sides; skins follow each band.
    """
    from shapely import union_all

    g = project.geometry
    sides = sides or {}
    target_band = thickness_band(thickness, sides.get(sheet.id, "centre") if sheet is not None else "centre")
    selected = {face.id for face in faces}
    groups, owned = [], set(selected)
    for other in g.sheets.values():
        ids = {g.face_uses[uid].face_id for uid in other.face_use_ids}
        if ids & selected:
            continue
        groups.append((other.name or f"Sheet-{other.id}", other, [g.faces[i] for i in sorted(ids)]))
        owned.update(ids)
    groups.extend((f"Face {face.id}", None, [face]) for face in g.faces.values() if face.id not in owned)
    target_bounds = _bounds(g, faces, _reach(target_band))
    frame = None
    if not cylindrical:
        profile = _planar_geometry(g, faces, sheet)
        frame = _Frame(profile.origin, profile.normal, profile.axes)
    paths, notes = [], []
    for mark, other_sheet, others in groups:
        gauges = {project.plate_section_of(f.id).thickness if f.id in project.face_sections else 0.
                  for f in others}
        other_side = sides.get(other_sheet.id, "centre") if other_sheet is not None else "centre"
        bands = {thickness_band(t, other_side) for t in gauges}
        try:
            low, high = _bounds(g, others, max(map(_reach, bands)))
        except ValueError as error:
            notes.append(f"Plate intersections with {mark} omitted: cannot bound unsupported contour ({error}).")
            continue
        if np.any(high < target_bounds[0]-1e-8) or np.any(low > target_bounds[1]+1e-8):
            continue
        # Equivalent coplanar stock has no physical internal crease. Do not
        # turn its native face/Sheet seam into a fabricated reference edge.
        try:
            other_profile = _planar_geometry(g, others, other_sheet)
        except ValueError:
            other_profile = None
        if cylindrical:
            from anygeometry.surfaces import Cylinder
            if other_profile is None or any(isinstance(f.surface, Cylinder) for f in others):
                notes.append(f"Plate intersections with {mark} omitted: cylinder-to-cylinder or nonplanar reference is unsupported.")
                continue
            cosine = abs(other_profile.normal @ faces[0].surface.axis)
            if not (cosine < 1e-10 or abs(cosine-1.) < 1e-10):
                notes.append(f"Plate intersections with {mark} omitted: oblique cylindrical reference is unsupported.")
                continue
            # Reverse the shared finite stock cut, clip in the other plate's
            # net material, then develop its actual boundary on the shell.
            from .plate_view import _trace_spans
            from shapely import Polygon
            rings = [_trace_spans(loop) for loop in other_profile.loops]
            material = Polygon(rings[0], rings[1:])
            refs, omitted = [], []
            for offset in sorted({0., *(b[0] for b in bands), *(b[1] for b in bands)}):
                view = _Frame(other_profile.origin + offset*other_profile.normal,
                              other_profile.normal, other_profile.axes)
                for cut, _ in _sheet_cuts(project, faces, view, {}, {}, omitted, sides.get(sheet.id, "centre")
                                          if sheet is not None else "centre"):
                    for piece in _pieces(cut.intersection(material)):
                        lines = [piece.exterior, *piece.interiors] if piece.geom_type == "Polygon" else [piece]
                        for line in lines:
                            if line.geom_type not in ("LineString", "LinearRing") or line.length <= 1e-8:
                                continue
                            xyz = np.asarray(line.coords) @ view.axes.T + view.origin
                            refs.extend(part.intersection(physical) for part in develop_points(xyz))
            cuts = refs
        else:
            cuts, omitted = [], []
        materials = {project.plate_section_of(f.id).material if f.id in project.face_sections else None
                     for f in (*faces, *others)}
        if (not cylindrical and other_profile is not None and bands == {target_band} and len(materials) == 1
                and np.linalg.norm(np.cross(other_profile.normal, frame.normal)) < 1e-10
                and abs((other_profile.origin-frame.origin) @ frame.normal) < 1e-8):
            continue
        if not cylindrical:
            for offset in sorted({target_band[0], 0., target_band[1]}):
                view = _Frame(frame.origin + offset*frame.normal, frame.normal, frame.axes)
                cuts.extend(cut.intersection(physical) for cut, _ in
                            _sheet_cuts(project, others, view, {}, {}, omitted, other_side))
        notes.extend(f"Plate intersections with {mark}: {note}" for note in dict.fromkeys(omitted))
        gauge = ", ".join(f"{t*1000:g}" for t in sorted(gauges))
        label = f"{mark} | plate {gauge} mm" if gauges != {0.} else f"{mark} | thickness unassigned"
        shape = union_all(cuts, grid_size=1e-10)
        for piece in _pieces(shape):
            if piece.geom_type == "Polygon":
                for ring in (piece.exterior, *piece.interiors):
                    paths.append(DrawingPath("PLATE_INTERSECTIONS", tuple(ring.coords)[:-1], True, label))
            elif piece.geom_type == "LineString" and piece.length > 1e-8:
                paths.append(DrawingPath("PLATE_INTERSECTIONS", tuple(piece.coords), False, label))
    if paths:
        notes.append("Plate references: finite stock cuts at the shell reference surface and both skins; no connection or interference inference.")
    return paths, tuple(notes)
