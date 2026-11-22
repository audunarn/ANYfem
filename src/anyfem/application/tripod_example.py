"""Floating tripod example: three columns joined by inclined legs and crossbeams.

A generic, parametric semi-submersible tripod of the class used for floating wind
foundations.  It is built here from its main dimensions only; no project or vendor
data is used.  The structure follows the tripod workflow the meshing work was
qualified on: each arm is a vertical column, a 45 degree inclined transition and a
horizontal crossbeam, joined by exact mitre cuts; the crossbeams meet at the centre
on three vertical planes at 60 degrees to the arms.  Each cylinder skin is two
half-turn faces split on the arm's plane of symmetry, stiffened inside by
longitudinal plate webs.  A free-edged heave plate (a rounded hexagon) sits under
each column.  Arms 2 and 3 are rotated copies of arm 1.
"""
from __future__ import annotations

import math
from fractions import Fraction
from dataclasses import dataclass

import numpy as np

from anygeometry.entities import EntityRef
from anygeometry.exact_curves import EllipticArc
from anygeometry.surfaces import Cylinder, Plane

from .. import commands
from ..model.materials import steel
from ..model.project import Project
from ..model.sections import PlateSection
from ..native_meshing import NativeMeshSettings


@dataclass(frozen=True)
class TripodDimensions:
    """Main dimensions in metres; thicknesses in metres."""

    column_radius: float = 6.0            # columns, legs and crossbeams (diameter 12 m)
    column_centre_radius: float = 46.0    # plan radius of the column axes
    draft: float = 16.0                   # base of the columns below the waterline z = 0
    crossbeam_axis_z: float = 14.0        # elevation of the horizontal crossbeam axes
    lower_mitre_inner_z: float = 1.0      # inner (lowest) point of the column/leg mitre
    webs_per_turn: int = 48               # longitudinal webs, 7.5 degree pitch
    web_depth: float = .30
    heave_plate_circumradius: float = 12.0
    heave_plate_corner_radius: float = 2.0
    column_thickness: float = .022
    transition_thickness: float = .020
    crossbeam_thickness: float = .018
    heave_plate_thickness: float = .025
    web_thickness: float = .014
    steel_grade: str = "S355"


TRIPOD_DIMENSIONS = TripodDimensions()
TRIPOD_NAME = "Floating tripod — 12 m columns, 16 m draft"
_SEGMENTS = ("column", "inclined transition", "horizontal crossbeam")
# Each support cylinder runs this far beyond its mitres; the face boundary carries the cuts.
_SUPPORT_OVERRUN = 7.

# Parameter-domain start of every half-skin support: half a web pitch before the half (-pi / webs_per_turn).  The shape
# does not depend on it; it only places the parameter seam of each skin's full-turn support.  WORKAROUND for the open
# ANYgeometry defect (an artificial mid-sweep vertex on plane/cylinder circles plus default seam splinters): with this
# start the heave plates mesh with no short edges.  Another model can still meet that defect; it is not fixed here.
# Per-model seam policy and mesh settings of the example (set on the project, never through the environment).
_SEAM_POLICY = "ring_split"  # an attempt per face: the owner may decline one; the acceptance test checks the result
_MESH_SIZE = .5


def tripod_description(d: TripodDimensions = TRIPOD_DIMENSIONS) -> str:
    pitch = 360. / d.webs_per_turn
    return (
        "Generic floating tripod (not a specific design): three columns of "
        f"{2 * d.column_radius:g} m diameter on a {d.column_centre_radius:g} m radius, "
        f"draft {d.draft:g} m, joined at the centre by horizontal crossbeams "
        f"(axis z = {d.crossbeam_axis_z:g} m) through 45° inclined transitions. "
        f"Inside every cylinder, {d.webs_per_turn} longitudinal plate webs "
        f"{d.web_depth * 1000:g} × {d.web_thickness * 1000:g} mm at {pitch:g}°. "
        f"A rounded-hexagon heave plate ({2 * d.heave_plate_circumradius:g} m across corners) "
        "under each column. "
        f"{d.steel_grade} plating: columns {d.column_thickness * 1000:g} mm, transitions "
        f"{d.transition_thickness * 1000:g} mm, crossbeams {d.crossbeam_thickness * 1000:g} mm, "
        f"heave plates {d.heave_plate_thickness * 1000:g} mm. "
        "Column exteriors are marked wet (clipped at the waterline z = 0). "
        "Meshing is preset to quad-first, 0.5 m, linear elements, with the owner's ring-split seam placement requested for this model (attempted per face; the owner may decline one). "
        "No supports or loads are defined; this example is for geometry, sections and meshing.")


def _arm_rotation(k: int):
    """Map arm-1 coordinates to arm k + 1 (0, +120 or -120 degrees about z).

    Equal-radius mitres and the crossbeam hub touch tangentially.  Arm 1 is
    axis-aligned, so its double roots are exact in the stored doubles.  Arms 2
    and 3 are an exact rational (Pythagorean) rotation of arm 1's doubles, from
    t = tan(60 deg) as a double, each coordinate rounded once; arm 3 is the exact
    mirror image of arm 2 in the x-z plane.  Their contacts can still be
    undecidable at stored precision; ANYgeometry (from 11dddb1) accepts those
    only where a stored edge passes through the contact point.
    """

    if k == 0:
        return lambda p: np.asarray(p, dtype=float)
    t = Fraction(math.sqrt(3.))
    c, s = (1 - t * t) / (1 + t * t), 2 * t / (1 + t * t)
    if k == 2:
        s = -s

    def rotate(p):
        x, y, z = (Fraction(float(value)) for value in p)
        return np.array((float(c * x - s * y), float(s * x + c * y), float(z)))

    return rotate


def floating_tripod(d: TripodDimensions = TRIPOD_DIMENSIONS) -> Project:
    """Build the tripod example project."""

    R, N, h = float(d.column_radius), int(d.webs_per_turn), float(d.web_depth)
    if N < 4 or N % 4:
        raise ValueError("webs_per_turn must be a positive multiple of 4")
    if not 0. < h < R:
        raise ValueError("web_depth must lie between 0 and the column radius")
    zl = d.lower_mitre_inner_z + R * math.tan(math.pi / 8.)   # lower mitre centre
    zc = float(d.crossbeam_axis_z)
    xc = float(d.column_centre_radius)
    xu = xc - (zc - zl)                                        # upper mitre centre (45 degree leg)
    if not (-d.draft < zl - R and zl + R < zc and R / math.sin(math.pi / 3.) < xu - R):
        raise ValueError("dimensions do not give a valid column, transition and crossbeam")
    if d.heave_plate_circumradius * math.cos(math.pi / 6.) - d.heave_plate_corner_radius <= R:
        raise ValueError("heave plate must extend beyond the column")

    project = Project(name=TRIPOD_NAME)
    stack = commands.CommandStack(project)
    thicknesses = {
        "Column": d.column_thickness, "Transition": d.transition_thickness,
        "Crossbeam": d.crossbeam_thickness, "Heave plate": d.heave_plate_thickness,
        "Longitudinal web": d.web_thickness}
    stack.run(commands.AddMaterial(steel(d.steel_grade, max(thicknesses.values()))))
    section = {kind: f"{kind} {t * 1000:g} mm" for kind, t in thicknesses.items()}
    for kind, t in thicknesses.items():
        stack.run(commands.AddPlateSection(PlateSection(section[kind], t, d.steel_grade)))
    g = project.geometry

    # ---- arm-1 frame: segment axes in the x-z plane, tangent +y
    centres = [np.array(p, float) for p in ((xc, 0., -d.draft), (xc, 0., zl), (xu, 0., zc))]
    ends = centres + [np.array((0., 0., zc))]
    directions = [(b - a) / np.linalg.norm(b - a) for a, b in zip(ends, ends[1:])]
    tangent = np.array((0., 1., 0.))
    radials = [np.cross(tangent, a) for a in directions]
    ring_normals = [directions[0], directions[0] + directions[1], directions[1] + directions[2]]

    def ring_vectors(i):
        a, r, n = directions[i], radials[i], ring_normals[i]
        return R * (r - (n @ r) / (n @ a) * a), R * tangent

    rings = [(centres[i],) + ring_vectors(i) for i in range(3)]
    # Symmetric angles: webs j and N-j are exact mirror images in the arm plane.
    trig = []
    for j in range(N):
        m = min(j, N - j)
        c, s = (1., 0.) if m == 0 else (-1., 0.) if 2 * m == N else (
            (0., 1.) if 4 * m == N else (math.cos(2 * math.pi * m / N), math.sin(2 * math.pi * m / N)))
        trig.append((c, s if j <= N // 2 else -s))

    # ---- hub: the three central planes (at +-60 degrees of each arm) and their seams
    hub_centre = np.array((0., 0., zc))
    top, bottom = g.add_points([hub_centre + (0., 0., R), hub_centre - (0., 0., R)])
    stretch = R / math.sin(math.pi / 3.)

    def hub_direction(p):  # p-th central plane at 60 + 120 p degrees
        if p == 0:
            return np.array((.5, math.sqrt(3.) / 2., 0.))
        if p == 1:
            return np.array((-1., 0., 0.))
        return np.array((.5, -math.sqrt(3.) / 2., 0.))

    hub_seams = [g.add_curve(top, bottom, EllipticArc(tuple(hub_centre), (0., 0., R),
                                                       tuple(stretch * hub_direction(p)), 0., math.pi))
                 for p in range(3)]
    hub_points = {("axis", 0, False): top, ("axis", N // 2, False): bottom}

    def hub_point(p, j, inner):
        """Web j's end on central plane p (j = 0 / N/2: on the vertical hub axis)."""
        c, s = trig[j]
        scale = (R - h) / R if inner else 1.
        key = ("axis", j, inner) if s == 0. else (p, min(j, N - j), inner)
        if key not in hub_points:
            position = hub_centre + scale * (c * np.array((0., 0., R)) + abs(s) * stretch * hub_direction(p))
            hub_points[key] = g.add_point(*position)
        return hub_points[key]

    hub_lines = {}

    def hub_line(a, b):
        key = (min(a, b), max(a, b))
        if key not in hub_lines:
            hub_lines[key] = g.add_line(a, b)
        return hub_lines[key]

    groups = {}

    def group(name, faces):
        groups.setdefault(name, []).extend(faces)

    wet_faces = []
    for k in range(3):
        at = _arm_rotation(k)
        arm = f"Arm {k + 1}"
        plus_plane, minus_plane = k, (k + 2) % 3     # central planes at +60 / -60 degrees of this arm


        # mitre ring seam vertices (theta = 0, pi) and half arcs
        ring_vertex, ring_arc = [], []
        for centre, u, v in rings:
            a, b = g.add_points([at(centre + u), at(centre - u)])
            ring_vertex.append((a, b))
            ring_arc.append((
                g.add_curve(a, b, EllipticArc(tuple(at(centre)), tuple(at(u)), tuple(at(v)), 0., math.pi)),
                g.add_curve(b, a, EllipticArc(tuple(at(centre)), tuple(at(u)), tuple(at(v)), math.pi, math.pi))))
        # web end points on each ring (outer on the skin, inner at depth h), shared across the mitre
        ring_web = []
        for i, (centre, u, v) in enumerate(rings):
            ends = []
            for j in range(N):
                c, s = trig[j]
                if s == 0.:
                    outer = ring_vertex[i][0 if c > 0 else 1]
                else:
                    outer = g.add_point(*at(centre + c * u + s * v))
                inner = g.add_point(*at(centre + (R - h) / R * (c * u + s * v)))
                ends.append((outer, inner, g.add_line(outer, inner)))
            ring_web.append(ends)
        hub_web = []
        for j in range(N):
            c, s = trig[j]
            p = plus_plane if s > 0. else minus_plane
            outer, inner = hub_point(p, j, False), hub_point(p, j, True)
            hub_web.append((outer, inner, hub_line(outer, inner)))
        ends_by_station = ring_web + [hub_web]

        for segment, name in enumerate(_SEGMENTS):
            a0, a1 = ends_by_station[segment], ends_by_station[segment + 1]
            # seams on the arm plane: shared by the two half skins and the webs at theta = 0, pi
            seams = [g.add_line(a0[0][0], a1[0][0]), g.add_line(a0[N // 2][0], a1[N // 2][0])]
            axis = at(directions[segment])
            radial = at(radials[segment])
            length = (float(np.linalg.norm(centres[segment + 1] - centres[segment])) if segment < 2
                      else float(centres[2][0]))
            support = dict(origin=tuple(at(centres[segment] - _SUPPORT_OVERRUN * directions[segment])), axis=tuple(axis),
                           radial_direction=tuple(radial), radius=R,
                           height=length + 2. * _SUPPORT_OVERRUN,
                           sweep_angle=2. * math.pi)
            if segment < 2:
                far = ring_arc[segment + 1]
            else:
                far = (hub_seams[plus_plane], hub_seams[minus_plane])
            skins = []
            for half in (0, 1):
                loop = (ring_arc[segment][half], seams[1 - half] if half == 0 else seams[0],
                        far[half], seams[0] if half == 0 else seams[1])
                # each half's support domain starts just before that half (theta = 0 or pi)
                surface = Cylinder(**{**support, "start_angle": half * math.pi - math.pi / N})
                skins.append(g.add_face(loop, corners=(0, 1, 2, 3), surface=surface))
            kind = ("Column", "Transition", "Crossbeam")[segment]
            group(f"{arm} - {name} skin", skins)
            group(f"_section {kind}", skins)
            if segment == 0:
                wet_faces.extend(skins)
            webs = []
            for j in range(N):
                o0, i0, e0 = a0[j]
                o1, i1, e1 = a1[j]
                root = seams[0] if j == 0 else seams[1] if j == N // 2 else g.add_line(o0, o1)
                origin = g.vertex_position(o0)
                plane = Plane(tuple(origin), tuple(g.vertex_position(i0) - origin),
                              tuple(g.vertex_position(o1) - origin))
                webs.append(g.add_face((e0, g.add_line(i0, i1), e1, root), corners=(0, 1, 2, 3), surface=plane))
            group(f"{arm} - {name} longitudinals", webs)
            group("_section Longitudinal web", webs)

        # heave plate under the column: rounded hexagon, a vertex pointing radially out
        Rh, rc = float(d.heave_plate_circumradius), float(d.heave_plate_corner_radius)
        base = np.array((xc, 0., -d.draft))
        corners = [base + Rh * np.array((math.cos(q * math.pi / 3.), math.sin(q * math.pi / 3.), 0.)) for q in range(6)]
        edges, first, previous = [], None, None
        for q in range(6):
            corner = corners[q]
            back = (corners[q - 1] - corner) / Rh
            ahead = (corners[(q + 1) % 6] - corner) / Rh
            run = rc / math.tan(math.pi / 3.)
            t_in, t_out = corner + run * back, corner + run * ahead
            bisector = back + ahead
            centre = corner + rc / math.sin(math.pi / 3.) * bisector / np.linalg.norm(bisector)
            a, b = g.add_points([at(t_in), at(t_out)])
            u = t_in - centre
            v = np.cross((0., 0., 1.), u)
            edges.append(g.add_curve(a, b, EllipticArc(tuple(at(centre)), tuple(at(u)), tuple(at(v)), 0., math.pi / 3.)))
            if previous is not None:
                edges.append(g.add_line(previous, a))
            first = a if first is None else first
            previous = b
        edges.append(g.add_line(previous, first))
        plate = g.add_face(edges, surface=Plane(tuple(at(base)), tuple(at((1., 0., 0.))), tuple(at((0., 1., 0.)))))
        group(f"{arm} - heave plate", [plate])
        group("_section Heave plate", [plate])

        # waterline reference curves on the column (half a web pitch off the web roots)
        offset = math.pi / N
        wl = np.array((xc, 0., 0.))
        wu = R * np.array((math.cos(offset), math.sin(offset), 0.))
        wv = R * np.array((-math.sin(offset), math.cos(offset), 0.))
        quarter = g.add_points([at(wl + math.cos(q * math.pi / 2.) * wu + math.sin(q * math.pi / 2.) * wv)
                                for q in range(4)])
        waterline = [g.add_curve(quarter[q], quarter[(q + 1) % 4],
                                 EllipticArc(tuple(at(wl)), tuple(at(wu)), tuple(at(wv)), q * math.pi / 2., math.pi / 2.))
                     for q in range(4)]
        g.add_to_group(f"Operating waterline z=0 m; draft {d.draft:g} m (reference curves)",
                       [EntityRef("edge", e) for e in waterline])

    g.add_to_group("Central crossbeam interfaces - shared seams", [EntityRef("edge", e) for e in hub_seams])
    for name, faces in groups.items():
        if not name.startswith("_section "):
            g.add_to_group(name, [EntityRef("face", f) for f in faces])
    for kind in thicknesses:
        project.assign_plates(groups[f"_section {kind}"], section[kind])
    # Meshing as qualified: quad-first, 0.5 m, linear; the owner's ring_split seam policy is selected for this model only.
    project.target_size = _MESH_SIZE
    project.set_element_order("linear")
    project.set_native_mesh_settings(NativeMeshSettings.create(
        _MESH_SIZE, element_order="linear", backend="quad_first", parameters={"seam_policy": _SEAM_POLICY}))
    for face in wet_faces:
        stack.run(commands.AddWetSurface(EntityRef("face", face), name="Column exterior; clipped at SWL z=0",
                                         fluid_side="positive_normal"))
    g.add_to_group("Hydro - column exteriors (clip at z=0)", [EntityRef("face", f) for f in wet_faces])
    return project
