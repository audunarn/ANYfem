"""Editable S355 barge with two shell knee brackets and a signed bending history."""

from dataclasses import replace

import numpy as np

from anygeometry.entities import EntityRef, OrientedEdge
from anygeometry.surfaces import CoonsSurface, Plane
from anymesher.refinement import size_override

from .. import commands
from ..model.records import AnalysisDefinition, OutputRequest
from ..model.regions import ManualRegion, Region, RegionDomain
from ..native_meshing import NativeMeshSettings
from .examples import floating_barge_section


SERIES = "Hogging and sagging"
ANALYSIS = "Bracket weld fatigue — signed bending record"
BRACKET = "Fatigue bracket 10 mm"
TIMES = (0., 2., 4., 6., 8., 10., 12., 14., 16.)
MOMENT_FACTORS = (0., .55, -.38, .85, -.65, .34, -.22, .68, 0.)
_BRACKETS = ((-4.8, "Port"), (5.0, "Starboard"))
DECK_WEB = "Deck stiffener web 8 mm"
DECK_FLANGE = "Deck stiffener flange 10 mm"
BULKHEAD_WEB = "Bulkhead stiffener web 6 mm"
BULKHEAD_FLANGE = "Bulkhead stiffener flange 8 mm"
FACE_FLAT = "Knee face flat 8 mm"
_FLAT_HALF = .0375
_LOCAL_SIZE = .020
# Reference couple of the original 5 m barge, retained when the beam model changes.
_REFERENCE_MOMENT_NM = 41_456_941.052910045


def barge_fatigue_detail(project, side="Port", leg="deck"):
    """Return receiver face, transverse terminal weld edge, and inward readout."""
    if side not in ("Port", "Starboard") or leg not in ("deck", "bulkhead"):
        raise ValueError("Select Port or Starboard and deck or bulkhead")
    regions = {region.name: region for region in project.regions}
    face, = regions[f"{side} {leg} toe receiver"].definition.anchors
    weld, = regions[f"{side} {leg} terminal weld toe"].definition.anchors
    return face.id, weld.id, (1., 0., 0.) if leg == "deck" else (0., 0., -1.)


def _add_detail(project, y, side):
    geo = project.geometry
    vertices = {tuple(geo.vertex_position(v)): v for v in geo.vertices}
    edges = {frozenset((edge.start, edge.end)): eid for eid, edge in geo.edges.items()}
    sheets = {}
    sections = {}
    part = next(iter(geo.parts))

    def edge(a, b):
        ids = []
        for pos in (a, b):
            pos = tuple(round(v, 9) for v in pos)
            if pos not in vertices:
                vertices[pos] = geo.add_point(*pos)
            ids.append(vertices[pos])
        key = frozenset(ids)
        if key not in edges:
            edges[key] = geo.add_line(*ids)
        eid = edges[key]
        return OrientedEdge(eid, geo.edges[eid].start == ids[0])

    def panel(sheet, section, ring, *, refine=False):
        loop = tuple(edge(a, b) for a, b in zip(ring, ring[1:] + ring[:1]))
        # Some charts flare between stations. A planar Coons patch fits the
        # true corners; the Plane establishes the same physical orientation.
        a, b, _, d = ring
        fid = geo.add_face_from_loop(loop, (0, 1, 2, 3),
                    surface=Plane(np.array(a), np.subtract(b, a), np.subtract(d, a)))
        if not np.allclose(np.add(a, ring[2]), np.add(b, d), atol=1.e-10, rtol=0.):
            geo.set_face_parameterization(fid, CoonsSurface())
        sections[fid] = section
        sheets.setdefault(sheet, []).append(fid)
        size = .060 if sheet.endswith(" web") else .022
        if sheet == "deck stiffener web" and min(pt[0] for pt in ring) >= xf and max(pt[0] for pt in ring) <= xtoe:
            size = .020
        if sheet == "bulkhead stiffener web" and min(pt[2] for pt in ring) >= ztoe:
            size = .020
        if sheet.startswith("deck stiffener"):
            xmin,xmax = min(pt[0] for pt in ring),max(pt[0] for pt in ring)
            if xmax <= 1.8 or xmin >= 4.:
                size = .30
            elif xmax <= 2.30 or xmin >= 3.65:
                size = .13
            elif xmax <= 2.5 or xmin >= xtoe + .20:
                size = .060
        elif sheet.startswith("bulkhead stiffener"):
            top = max(pt[2] for pt in ring)
            if top <= ztoe - .45:
                size = .15
            elif top <= ztoe - .25:
                size = .060
        if refine:
            size = _LOCAL_SIZE if sheet.endswith(" web") else .008
        project.add_refinement(size_override(EntityRef("face", fid), size,
                                             name=f"{side} {sheet} mesh {fid}"))
        return fid

    def region(name, kind, ids):
        refs = tuple(EntityRef(kind, i) for i in ids)
        project.regions.add(Region(f"{side} {name}", RegionDomain.GEOMETRY, kind, ManualRegion(refs)))

    # Connect shell reference planes at the web roots and flange midplanes.
    # Deck T 200x8 + 100x10 points down, bulkhead T 150x6 + 75x8 points aft (+X).
    xf, zf = 2.654, 2.295
    xtoe, ztoe = 3.154, 1.895
    x_grid = (0., 1.8, 2.30, 2.5, xf, xf + .04, xtoe - .04, xtoe, xtoe + .05, xtoe + .20, 3.65, 4., 5.)
    z_grid = (0., .15, ztoe - .45, ztoe - .25, ztoe - .05, ztoe, ztoe + .05, zf - .05, zf)
    def deck_half(x):
        # Clip the flange shoulders locally at the crossing of the two Ts.
        return _FLAT_HALF if x in (2.5, xf) else .05
    half_spans = ((-_FLAT_HALF, 0.), (0., _FLAT_HALF))

    with geo.transaction():
        for x0, x1 in zip(x_grid, x_grid[1:]):
            near = x0 in (xtoe - .04, xtoe)
            panel("deck stiffener web", DECK_WEB,
                  [(x0,y,zf), (x1,y,zf), (x1,y,2.5), (x0,y,2.5)], refine=near)
            for k in (-1, 1):
                left, right = k * deck_half(x0), k * deck_half(x1)
                a = (x0,y,zf); b = (x1,y,zf)
                c = (x1,y+right,zf); d = (x0,y+left,zf)
                face = panel("deck stiffener flange", DECK_FLANGE,
                             [a,b,c,d] if k < 0 else [d,c,b,a], refine=near)
                if x0 == xtoe and k == 1:
                    deck_gauge = face
        for z0, z1 in zip(z_grid, z_grid[1:]):
            near = z1 in (ztoe, ztoe + .05)
            panel("bulkhead stiffener web", BULKHEAD_WEB,
                  [(2.5,y,z0), (xf,y,z0), (xf,y,z1), (2.5,y,z1)], refine=near)
            if z0 == 0.:
                # Snipe the lowest 150 mm of flange clear of the bottom. The
                # full web remains welded to both bulkhead and bottom.
                continue
            for v0, v1 in half_spans:
                face = panel("bulkhead stiffener flange", BULKHEAD_FLANGE,
                    [(xf,y+v0,z0), (xf,y+v1,z0), (xf,y+v1,z1), (xf,y+v0,z1)], refine=near)
                if z1 == ztoe and v0 == 0.:
                    bulkhead_gauge = face

        # The knee has a short horizontal end at its lower toe and a sloping
        # free edge up to the deck toe. Its welded face flat follows both edges.
        def knee_row(z):
            end = xf + .12 + (z - ztoe) / (zf - ztoe) * (.50 - .12)
            return [(x,y,z) for x in (xf, xf + .04, end - .04, end)]

        rows = [knee_row(z) for z in z_grid if z >= ztoe]
        for j,(low, high) in enumerate(zip(rows, rows[1:])):
            for i,(a,b) in enumerate(zip(low, low[1:])):
                panel("knee bracket", BRACKET, [a,b,high[i+1],high[i]],
                      refine=(j == 0 and i < 2) or (j == len(rows)-2 and i > 0))
        flat_path = rows[0] + [row[-1] for row in rows[1:]]
        for i,(a,b) in enumerate(zip(flat_path, flat_path[1:])):
            # The free-edge flat fans to the 100 mm deck flange and meets
            # the 75 mm bulkhead flange at its other end.
            ha = _FLAT_HALF + (.05 - _FLAT_HALF) * (a[2]-ztoe)/(zf-ztoe)
            hb = _FLAT_HALF + (.05 - _FLAT_HALF) * (b[2]-ztoe)/(zf-ztoe)
            for k in (-1, 1):
                ring = [a,b,(b[0],y+k*hb,b[2]),(a[0],y+k*ha,a[2])]
                panel("knee face flat", FACE_FLAT, ring if k == 1 else ring[::-1],
                      refine=i in (0,len(flat_path)-2))

        for sheet, faces in sheets.items():
            geo.add_sheet(faces, part_id=part, name=f"{side} {sheet}")
        for leg, face, x, z in (("deck", deck_gauge, xtoe, zf),
                                ("bulkhead", bulkhead_gauge, xf, ztoe)):
            half_width = .05 if leg == "deck" else _FLAT_HALF
            weld = edge((x,y,z),(x,y+half_width,z)).edge
            region(f"{leg} terminal weld toe", "edge", (weld,))
            region(f"{leg} toe receiver", "face", (face,))
        region("knee detail", "face", sheets["knee bracket"] + sheets["knee face flat"])
        region("bracket with shell stiffeners", "face", [fid for group in sheets.values() for fid in group])


    for fid, section in sections.items():
        project.assign_plate(fid, section)


def bracketed_barge_fatigue():
    """Five-metre S355 barge with shell T-stiffeners and two face-plated knees.

    Each knee connects a deck longitudinal to a transverse bulkhead stiffener.
    The two transverse welds on the receiving flanges are the actual terminal
    edges of the knee face flat. Opposed end line loads follow an illustrative
    signed bending record with nine quasi-static steps over 16 seconds.
    """
    project = floating_barge_section(
        "line_loads", bending_moment=_REFERENCE_MOMENT_NM, shell_mount_charts=True, shell_deck_longitudinals=tuple(y for y, _ in _BRACKETS),
        name="5 m barge — bracket weld fatigue",
    )
    # Use each flat triangular facet's own normal for the solver reference
    # plane. Source-owner admission still establishes the physical weld side.
    project.shell_formulation_policy = replace(project.shell_formulation_policy, s3_reference_surface="faceted")
    for section, thickness in ((BRACKET,.010), (FACE_FLAT,.008), (DECK_WEB,.008),
                               (DECK_FLANGE,.010), (BULKHEAD_WEB,.006), (BULKHEAD_FLANGE,.008)):
        project.add_plate_section(section, thickness=thickness, material="S355")
    for y, side in _BRACKETS:
        _add_detail(project, y, side)


    for region in project.regions:
        if region.name not in ("Deck","Transverse bulkhead","Port side","Starboard side"):
            continue
        for ref in region.definition.anchors:
            bounds = project.geometry.entity_bounds_many((("face",ref.id),))[0]
            center = (np.asarray(bounds[:3])+np.asarray(bounds[3:])) / 2.
            if region.name.endswith(" side"):
                # Match the local deck and bulkhead chart boundaries on the
                # adjacent topside bay. The other side panels stay coarse.
                if bounds[2] >= 1.4 and 1.4 < center[0] < 4.3:
                    target = .125 if bounds[2] >= 2.1 and 2.15 < center[0] < 3.55 else .26
                    project.add_refinement(size_override(ref,target,name=f"{region.name} mount bay {ref.id}"))
                continue
            if bounds[4]-bounds[1] > 4.:
                continue
            lateral = abs(center[1]) > 4.15
            at_mount = (2.15 < center[0] < 3.55 if region.name=="Deck" else center[2]>1.55)
            target = .21 if region.name == "Transverse bulkhead" else .24
            if lateral and at_mount:
                target = .075 if region.name == "Transverse bulkhead" else .095
            project.add_refinement(size_override(ref,target,name=f"{region.name} mount bay {ref.id}"))
    project.set_native_mesh_settings(NativeMeshSettings.create(
        project.target_size, element_order=project.element_order, backend="smart_mapped"))

    base = project.load_case()
    end_loads = list(base.line_loads)
    commands.DeleteLoadCase("default").do(project)
    series = project.add_load_series(SERIES, TIMES)
    envelope = {"by": "time", "points": list(map(list, zip(TIMES, MOMENT_FACTORS)))}
    for load in end_loads:
        commands.AddLineLoad(load.ref, load.force_per_length, case=series.name, envelope=envelope).do(project)
    project.analyses.clear()
    shells = project.regions.add(Region(
        "Barge shell results", RegionDomain.GEOMETRY, "face",
        ManualRegion(EntityRef("face", fid) for fid in project.geometry.faces)))
    components = tuple(f"stress.global_{axis}_{side}" for side in ("top", "bot")
                       for axis in ("xx", "yy", "zz", "xy", "yz", "xz"))
    stress = OutputRequest(components, shells.id, "element", basis="global", reduction="mean",
                           label="Global shell stress — top and bottom surfaces")
    motion = OutputRequest(("displacement",), shells.id, "node", label="Shell displacement")
    for request in (stress, motion):
        project.add_output_request(request)
    project.add_analysis(AnalysisDefinition(ANALYSIS, type="batch_linear_static",
                                            target_kind="load_cases", target_id="batch",
                                            output_request_ids=(stress.id, motion.id)))
    return project
