"""Editable teaching models built with public owner APIs, without UI imports."""
from dataclasses import dataclass

from anygeometry.entities import EntityRef, OrientedEdge
from anygeometry.operations import trim_face
from anygeometry.surfaces import Plane
from anygeometry.structural import AttachmentEvidence, AttachmentKind, ParameterRange
from ..presentation.points import USER_POINT_GROUP

from .. import commands
from ..model.project import Project
from ..native_meshing import NativeMeshSettings
from ..model.materials import steel
from ..model.sections import PlateSection, BeamSection
from ..model.regions import ManualRegion, Region, RegionDomain, RegionRef
from ..model.attributes import fixed
from ..model.records import AnalysisDefinition

BARGE_DESCRIPTION = (
    "5 m midship section of a nominal 30 m × 11 m × 2.5 m box barge. "
    "X is longitudinal, Y transverse and Z upward from the bottom. "
    "Continuous deck, bottom, sides and transverse bulkhead; the cut ends are open. "
    "Only the eight outer corner points are displayed. "
    "Longitudinal stiffeners at 700 mm; transverse girders at 4000 mm (X = 0 and 4 m). "
    "End bays are shorter where the dimensions do not divide by the spacing. "
    "S355 steel: 12 mm deck, 10 mm shell, 8 mm bulkhead; T200×8/100×10 stiffeners "
    "and T600×10/200×16 girders (illustrative sections, dimensions in mm). "
    "A nominal 1.5 m draft places the waterline at Z = 1.5 m. "
    "The X = 0 end is fixed; an axial force couple at X = 5 m applies bending "
    "about Y: approximately 41.46 MN·m, calibrated to peak von Mises stress "
    "near 200 MPa on the default 700 mm mesh. Local peaks can change with refinement. "
    "Choose Generate mesh, then Solve (linear static). Hydrostatic equilibrium is not included."
)
BARGE_SOURCE = "https://www.damen.com/vessels/pontoons-and-barges/stan-pontoons/stan-pontoon-b11"
# Recovered maximum from the nominal-load linear run on the default mesh,
# ANYgeometry 0.4.5 / ANYmesher 0.5.1 / ANYsolver 0.4.6. Retained run evidence
# is documented in QT_CONVERSION.md; no tolerances or physics are calibrated.
BARGE_LINEAR_STRESS_SCALE = 200.e6 / 364767294.03233093
# Recovered maximum (186.869 MPa, on a side girder at the X = 12 m station) of the 15 m model
# run with the 5 m scale on its default 250 mm mesh, same versions as above; no physics is calibrated.
BARGE_LARGE_STRESS_SCALE = BARGE_LINEAR_STRESS_SCALE * 200.e6 / 186869055.43141928


BARGE_END_CONNECTION = "End section X = 5 m"
BARGE_END_LOADINGS = ("line_loads", "distributing", "rigid")


@dataclass(frozen=True)
class BulkheadOpening:
    """An opening in a transverse bulkhead (centre, size and corner radius in metres)."""

    y: float
    z: float
    width: float
    height: float
    radius: float = 0.


# The 15 m barge: three 5 m compartments split by two bulkheads, each bulkhead with
# three access openings whose edges carry a flat bar (FB 120x10 standing off the plate).
BARGE_LARGE_LENGTH = 15.
BARGE_LARGE_BULKHEADS = (5., 10.)
BARGE_LARGE_GIRDERS = (0., 4., 8., 12.)
# Openings sit on the 700 mm stiffener grid (Y = -3.4, 0.1, 3.6 m; edges on grid lines), so the
# bulkhead stiffeners stand on every grid line between them and meet the deck and bottom stiffeners.
BARGE_LARGE_OPENINGS = tuple(BulkheadOpening(y, 1.25, 1.4, 1.1, .25) for y in (-3.4, .1, 3.6))
BARGE_LARGE_OPENING_BAR = (.120, .010)  # FB height, thickness
BARGE_LARGE_BULKHEAD_STIFFENER = (.150, .006, .075, .008)  # T-bar hw, tw, b, tf
# The mesher quantises the divisions per stiffener bay: 700 mm gives 2 on this longer
# model but 3 (about 0.23 m elements) on the 5 m one, so 250 mm keeps the same element size.
BARGE_LARGE_TARGET_SIZE = .25
BARGE_LARGE_DESCRIPTION = (
    "15 m section of the same nominal 30 m × 11 m × 2.5 m box barge, three times the "
    "length of the basic example. X is longitudinal, Y transverse and Z upward from the "
    "bottom. Continuous deck, bottom and sides; the cut ends are open. Two transverse "
    "bulkheads at X = 5 m and 10 m divide it into three 5 m compartments. Each bulkhead "
    "has three 1.4 m × 1.1 m access openings with 250 mm corner radii (Y = -3.4, 0.1 and "
    "3.6 m, centred at Z = 1.25 m) and a flat bar FB 120×10 stands off the plate along every "
    "opening edge. Vertical T150×6/75×8 stiffeners at 700 mm stand on the bulkheads between "
    "the openings. "
    "Longitudinal stiffeners at 700 mm; transverse girders at 4000 mm (X = 0, 4, 8 and "
    "12 m). S355 steel: 12 mm deck, 10 mm shell, 8 mm bulkheads; T200×8/100×10 stiffeners "
    "and T600×10/200×16 girders (illustrative sections, dimensions in mm). "
    "The X = 0 end is fixed; an axial force couple at X = 15 m applies bending about Y, "
    "calibrated to peak von Mises stress near 200 MPa on the default 250 mm mesh "
    "(about 15 000 elements; meshing takes about a minute). Local peaks can change with "
    "refinement. Choose Generate mesh, then Solve (linear static). Hydrostatic "
    "equilibrium is not included."
)
BARGE_DRAWING_60M_DESCRIPTION = (
    "60 m × 5 m barge based on the supplied general arrangement, tank plan, structural "
    "drawing and matching GeniE model. X is longitudinal, Y transverse, Z upward from "
    "the baseline. The main hull is 2 m deep and simplified end structures reach 6 m. "
    "The hull has continuous deck, bottom and side plates, a centreline longitudinal "
    "bulkhead, and transverse bulkheads near frames 8, 17 and 26 (X = 5, 30 and 55 m). "
    "Frame stations are 625 mm apart at each end and approximately 2.78 m apart amidships. "
    "Main plating is 6 mm; T sections stand in for the drawn HP bulb stiffeners and web "
    "frames. S235 is an illustrative linear-elastic proxy for the drawing's grade A steel. "
    "An illustrative 1 MN·m end bending moment and a fixed opposite end make the example "
    "runnable; they are not a design load case. Tank boundaries, openings, end-structure "
    "details, local brackets and marine loading are simplified or omitted. The saved "
    "mesh uses 0.5 m quadratic adaptive quad-first settings; triangle angles below "
    "15° are reported as warnings and prevent strict certification. Generate mesh, then "
    "solve a linear static case. This model is for exercising the workflow, not structural "
    "approval or comparison with the vessel's assessed capacity."
)
BARGE_CONNECTION_DESCRIPTION = (
    "In this variant the bending moment is a single moment applied at the centre "
    "of the deck and bottom end lines (X = 5 m, Z = 1.25 m), carried by a "
    "{kind} connection instead of two line loads. A distributing connection "
    "spreads the moment over the lines exactly as the two line loads do; a rigid "
    "connection also holds the end section plane, which stiffens it and changes "
    "the local stresses, so the 200 MPa calibration above applies to the "
    "distributing and line-load versions."
)


def floating_barge_section(
    end_loading: str = "line_loads",
    *,
    length: float = 5.,
    bulkheads: tuple[float, ...] = (2.5,),
    girders: tuple[float, ...] = (0., 4.),
    openings: tuple[BulkheadOpening, ...] = (),
    opening_bar: tuple[float, float] = BARGE_LARGE_OPENING_BAR,
    bulkhead_stiffener: tuple[float, float, float, float] | None = None,
    stress_scale: float = BARGE_LINEAR_STRESS_SCALE,
    target_size: float = .7,
    name: str = "Floating barge — midship section",
    breadth: float = 11.,
    depth: float = 2.5,
    stiffener_spacing: float = .7,
    plate_thicknesses: tuple[float, float, float] = (.012, .010, .008),
    steel_grade: str = "S355",
    stiffener_section: tuple[float, float, float, float] = (.200, .008, .100, .010),
    girder_section: tuple[float, float, float, float] = (.600, .010, .200, .016),
    centerline_bulkhead: bool = False,
    bending_moment: float | None = None,
    analysis_name: str = "Barge bending — approximately 200 MPa",
) -> Project:
    """Build continuous plates with persistent member-on-face attachments.

    ``end_loading`` chooses how the bending moment reaches the X = ``length`` end:
    ``line_loads`` applies it as two line loads (the calibrated default),
    ``distributing`` as one moment at the centre of the deck and bottom end
    lines, spread over them, and ``rigid`` as the same moment on a rigid
    connection that keeps the end section plane.

    ``bulkheads`` and ``girders`` are the X stations of the transverse plates and
    girders. ``openings`` cuts the same rectangular holes in every bulkhead and
    lays a flat bar ``opening_bar`` = (height, thickness) along each hole edge
    (straight sides and corner arcs). ``bulkhead_stiffener`` = (hw, tw, b, tf) puts a
    vertical T-bar on every 700 mm grid line of each bulkhead outside the openings.
    The defaults are the 5 m example.
    """

    if end_loading not in BARGE_END_LOADINGS:
        raise ValueError(f"end_loading must be one of {', '.join(BARGE_END_LOADINGS)}")
    if min(length, breadth, depth, stiffener_spacing, target_size, *plate_thicknesses) <= 0.:
        raise ValueError("Barge dimensions, spacing, plate thicknesses and mesh size must be positive")
    half_breadth = breadth / 2.
    deck_section, hull_section, bulkhead_section = (
        f"{kind} {thickness * 1000:g} mm"
        for kind, thickness in zip(("Deck", "Hull", "Bulkhead"), plate_thicknesses)
    )
    project = Project(name=name)
    stack = commands.CommandStack(project)
    stack.run(commands.AddMaterial(steel(steel_grade, max(plate_thicknesses))))
    for section, thickness in zip((deck_section, hull_section, bulkhead_section), plate_thicknesses):
        stack.run(commands.AddPlateSection(PlateSection(section, thickness, steel_grade)))
    # bulkhead and girder intersections are explicit stations
    xs = tuple(sorted({0., float(length), *map(float, bulkheads), *map(float, girders)}))
    end_x = float(length)
    bulkhead_names = {float(x): ("Transverse bulkhead" if len(bulkheads) == 1 else f"Bulkhead X = {x:g} m")
                      for x in bulkheads}
    ys = tuple(round(-half_breadth + i * stiffener_spacing, 6)
               for i in range(int(breadth / stiffener_spacing) + 1)
               if -half_breadth + i * stiffener_spacing < half_breadth) + (half_breadth,)
    zs = tuple(round(i * stiffener_spacing, 6)
               for i in range(int(depth / stiffener_spacing) + 1)
               if i * stiffener_spacing < depth) + (depth,)
    geometry = project.geometry
    vertices, edges, faces = {}, {}, {}

    def vertex(coordinate):
        if coordinate not in vertices:
            vertices[coordinate] = geometry.add_point(*coordinate)
        return vertices[coordinate]

    def edge(first, second):
        a, b = vertex(first), vertex(second)
        key = tuple(sorted((a, b)))
        if key not in edges:
            edges[key] = geometry.add_line(a, b)
        identifier = edges[key]
        return OrientedEdge(identifier, geometry.edges[identifier].start == a)

    def arc(first, via, last):
        a, v, b = vertex(first), vertex(via), vertex(last)
        return OrientedEdge(geometry.add_arc(a, v, b), True)

    def plate(name, coordinates):
        loop = tuple(edge(a, b) for a, b in zip(coordinates, coordinates[1:] + coordinates[:1]))
        positions = [geometry.vertex_position(vertex(point)) for point in coordinates]
        identifier = geometry.add_face_from_loop(loop, (0, 1, 2, 3), surface=Plane(
            positions[0], positions[1]-positions[0], positions[3]-positions[0]))
        faces.setdefault(name, []).append(EntityRef("face", identifier))

    # Plates are physical surfaces, not a grid of reinforcement bays. Beam
    # intersections retain their own topology and declared surface attachments.
    opening_edges = []  # (bulkhead name, edge) of every opening side, one flat bar each
    opening_vertices = set()
    with geometry.transaction():
        plate("Deck", ((0.,-half_breadth,depth),(end_x,-half_breadth,depth),
                       (end_x,half_breadth,depth),(0.,half_breadth,depth)))
        plate("Bottom", ((0.,-half_breadth,0.),(0.,half_breadth,0.),
                         (end_x,half_breadth,0.),(end_x,-half_breadth,0.)))
        plate("Port side", ((0.,-half_breadth,0.),(end_x,-half_breadth,0.),
                            (end_x,-half_breadth,depth),(0.,-half_breadth,depth)))
        plate("Starboard side", ((0.,half_breadth,0.),(0.,half_breadth,depth),
                                 (end_x,half_breadth,depth),(end_x,half_breadth,0.)))
        for x, bulkhead in bulkhead_names.items():
            plate(bulkhead, ((x,-half_breadth,0.),(x,half_breadth,0.),
                             (x,half_breadth,depth),(x,-half_breadth,depth)))
            holes = []
            for opening in openings:
                y0, y1 = opening.y - opening.width/2., opening.y + opening.width/2.
                z0, z1 = opening.z - opening.height/2., opening.z + opening.height/2.
                if not (-half_breadth < y0 < y1 < half_breadth and 0. < z0 < z1 < depth):
                    raise ValueError("A bulkhead opening must lie inside the bulkhead")
                r = opening.radius
                if not 0. <= r < min(y1 - y0, z1 - z0)/2.:
                    raise ValueError("A bulkhead opening corner radius must be less than half its size")
                k = r*(1. - .5**.5)  # inset of the 45 degree point of a corner arc
                # An inner trim loop runs against the outer one (clockwise seen from +X):
                # up the left side, along the top, down the right side, back along the bottom.
                if r > 0.:
                    steps = (((x,y0,z0+r), None, (x,y0,z1-r)), ((x,y0,z1-r), (x,y0+k,z1-k), (x,y0+r,z1)),
                             ((x,y0+r,z1), None, (x,y1-r,z1)), ((x,y1-r,z1), (x,y1-k,z1-k), (x,y1,z1-r)),
                             ((x,y1,z1-r), None, (x,y1,z0+r)), ((x,y1,z0+r), (x,y1-k,z0+k), (x,y1-r,z0)),
                             ((x,y1-r,z0), None, (x,y0+r,z0)), ((x,y0+r,z0), (x,y0+k,z0+k), (x,y0,z0+r)))
                else:
                    ring = ((x,y0,z0),(x,y0,z1),(x,y1,z1),(x,y1,z0))
                    steps = tuple((a, None, b) for a, b in zip(ring, ring[1:] + ring[:1]))
                loop = tuple(edge(a, b) if via is None else arc(a, via, b) for a, via, b in steps)
                holes.append(loop)
                opening_edges.extend((bulkhead, item) for item in loop)
                opening_vertices.update(vertices[point] for step in steps for point in step if point)
            if holes:
                trim_face(geometry, faces[bulkhead][0].id, holes)
        if centerline_bulkhead:
            plate("Centreline bulkhead", ((0.,0.,0.),(end_x,0.,0.),
                                          (end_x,0.,depth),(0.,0.,depth)))
        corners = tuple(EntityRef("vertex", vertex((x,y,z)))
                        for x in (0.,end_x) for y in (-half_breadth,half_breadth)
                        for z in (0.,depth))
        geometry.add_to_group(USER_POINT_GROUP, corners)
        part = geometry.add_part(name="Barge midship section")
        sheets = {name: geometry.add_sheet((refs[0].id,), part_id=part, name=name)
                  for name,refs in faces.items()}

    def region(name, refs, kind):
        made = Region(name, RegionDomain.GEOMETRY, kind, ManualRegion(tuple(refs)))
        stack.run(commands.AddRegion(made))
        return RegionRef(made.id)

    for face_name, refs in faces.items():
        section = (deck_section if face_name == "Deck" else bulkhead_section
                   if face_name in bulkhead_names.values() or face_name == "Centreline bulkhead"
                   else hull_section)
        project.assign_plate_region(region(face_name, refs, "face"), section)

    def attach(member, chain, surface):
        """Declare a member lying on a plate, so reinforcement endpoints follow it."""
        face_id = faces[surface][0].id
        first, last = geometry.edges[chain[0].edge], geometry.edges[chain[-1].edge]
        endpoints = (first.start if chain[0].forward else first.end,
                     last.end if chain[-1].forward else last.start)
        samples = [geometry.project_to_face(face_id, geometry.vertex_position(identifier))
                   for identifier in endpoints]
        if any(distance > 1.e-9 for _,_,distance in samples):
            raise ValueError("Barge reinforcement is outside its declared plate")
        uv = [sample[1] for sample in samples]
        ranges = tuple(ParameterRange(max(0.,min(1.,min(p[i] for p in uv))),
                                      max(0.,min(1.,max(p[i] for p in uv)))) for i in (0,1))
        geometry.add_attachment(member, AttachmentKind.MEMBER_ON_FACE, "face", face_id,
            ParameterRange(0.,1.), ranges, evidence=AttachmentEvidence.EXACT,
            tolerance_used=1.e-9, sheet_id=sheets[surface])

    surfaces = (
        ("Deck", ys, lambda x, s: (x,s,depth), (0.,0.,-1.)),
        ("Bottom", ys, lambda x, s: (x,s,0.), (0.,0.,1.)),
        ("Port side", zs, lambda x, s: (x,-half_breadth,s), (0.,1.,0.)),
        ("Starboard side", zs, lambda x, s: (x,half_breadth,s), (0.,-1.,0.)),
    )
    all_stiffeners, all_girders = [], []
    girder_stations = tuple(float(x) for x in girders)
    for surface, stations, point, inward in surfaces:
        stiffener_chains = [tuple(edge(point(a,s),point(b,s)) for a,b in zip(xs,xs[1:]))
                            for s in stations[1:-1]]
        girder_chains = [tuple(edge(point(x,a),point(x,b)) for a,b in zip(stations,stations[1:]))
                         for x in girder_stations]
        stiffeners = [EntityRef("edge", use.edge) for chain in stiffener_chains for use in chain]
        girders = [EntityRef("edge", use.edge) for chain in girder_chains for use in chain]
        for kind, refs, sizes in (("stiffeners", stiffeners, stiffener_section),
                                 ("girders", girders, girder_section)):
            name = f"{surface} {kind}"
            h, tw, b, tf = sizes
            stack.run(commands.AddBeamSection(BeamSection(name, "T-bar", steel_grade,
                web_height=h, web_thickness=tw, flange_width=b, flange_thickness=tf,
                web_direction=inward, offset_mode="automatic")))
            chains = stiffener_chains if kind == "stiffeners" else girder_chains
            members = [geometry.add_member(chain, part_id=part, name=f"{name} {index}")
                       for index,chain in enumerate(chains, 1)]
            project.assign_beam_region(region(name, refs, "edge"), name)
            for member, chain in zip(members, chains):
                attach(member, chain, surface)
        all_stiffeners.extend(stiffeners)
        all_girders.extend(girders)
    region("Longitudinal stiffeners — 700 mm", all_stiffeners, "edge")
    region("Transverse girders — 4000 mm", all_girders, "edge")
    region("User points — main corners", corners, "vertex")
    if opening_edges:
        # A flat bar FB (height x thickness) stands off the bulkhead along each opening edge.
        height, thickness = opening_bar
        bar = f"FB {height*1000:g}×{thickness*1000:g} opening edges"
        stack.run(commands.AddBeamSection(BeamSection(bar, "Flatbar", steel_grade,
            flange_width=thickness, flange_thickness=height,
            web_direction=(1.,0.,0.), offset_mode="automatic")))
        for index, (bulkhead, item) in enumerate(opening_edges, 1):
            member = geometry.add_member((item,), part_id=part, name=f"{bar} {index}")
            # This member follows that very trim edge, including its arc. A
            # FACE UV bounding box describes a different path at rounded corners.
            geometry.add_attachment(member, AttachmentKind.MEMBER_ON_FACE_BOUNDARY,
                "edge", item.edge, ParameterRange(0., 1.), (ParameterRange(0., 1.),),
                evidence=AttachmentEvidence.EXACT, tolerance_used=1.e-9,
                sheet_id=sheets[bulkhead])
        project.assign_beam_region(
            region(f"Bulkhead opening flat bars — FB {height*1000:g}×{thickness*1000:g}",
                   [EntityRef("edge", item.edge) for _, item in opening_edges], "edge"), bar)
    if bulkhead_stiffener:
        # Vertical T-bars on the lateral grid, between openings (and clear of their edges).
        hw, tw, b, tf = bulkhead_stiffener
        stiffener = "Bulkhead stiffeners"
        stack.run(commands.AddBeamSection(BeamSection(stiffener, "T-bar", steel_grade,
            web_height=hw, web_thickness=tw, flange_width=b, flange_thickness=tf,
            web_direction=(1.,0.,0.), offset_mode="automatic")))
        clear = [y for y in ys[1:-1] if not any(
            opening.y - opening.width/2. - 1.e-9 <= y <= opening.y + opening.width/2. + 1.e-9
            for opening in openings)]
        refs = []
        for x, bulkhead in bulkhead_names.items():
            for y in clear:
                chain = (edge((x,y,0.), (x,y,2.5)),)
                member = geometry.add_member(chain, part_id=part, name=f"{stiffener} X = {x:g} m Y = {y:g} m")
                attach(member, chain, bulkhead)
                refs.append(EntityRef("edge", chain[0].edge))
        project.assign_beam_region(region(stiffener, refs, "edge"), stiffener)
    # Continuous surfaces no longer share a subdivided bulkhead boundary.
    # Declare the existing beam intersection points and bulkhead corners as
    # owner attachments instead of relying on coordinate coincidence alone.
    for coordinate, identifier in vertices.items():
        x, y, z = coordinate
        if (x not in bulkhead_names and not (centerline_bulkhead and y == 0.)) or identifier in opening_vertices:
            continue  # opening corners lie on the trimmed bulkhead's own boundary
        targets = [bulkhead_names[x]] if x in bulkhead_names else []
        if centerline_bulkhead and y == 0. and (x not in (0., end_x) or z not in (0., depth)):
            targets.append("Centreline bulkhead")
        if abs(y) == half_breadth and z in (0.,depth):
            targets = ["Bottom" if z == 0. else "Deck",
                       "Port side" if y < 0. else "Starboard side"]
        for target in targets:
            face_id = faces[target][0].id
            _, uv, distance = geometry.project_to_face(face_id, geometry.vertex_position(identifier))
            if distance > 1.e-9:
                raise ValueError("Barge joint point is outside its declared plate")
            geometry.add_attachment(None, AttachmentKind.VERTEX_ON_FACE, "face", face_id,
                ParameterRange.point(0.), tuple(ParameterRange.point(max(0.,min(1.,value))) for value in uv),
                source_kind="vertex", source_id=identifier, evidence=AttachmentEvidence.EXACT,
                tolerance_used=1.e-9, sheet_id=sheets[target])
    # Clamp the plate end perimeter; attached reinforcement endpoints follow through MPCs.
    for identifier, axis in geometry.edges.items():
        if (geometry.faces_using_edge(identifier)
                and geometry.vertex_position(axis.start)[0] == geometry.vertex_position(axis.end)[0] == 0.):
            stack.run(commands.AddSupport(fixed(EntityRef("edge", identifier), name="Fixed X = 0")))
    moment = bending_moment if bending_moment is not None else barge_bending_moment(project, stress_scale)
    end_edges = {}
    for surface, sign in (("Deck", 1.), ("Bottom", -1.)):
        face = geometry.faces[faces[surface][0].id]
        loaded = [item.edge for item in face.loop
                  if geometry.vertex_position(geometry.edges[item.edge].start)[0]
                  == geometry.vertex_position(geometry.edges[item.edge].end)[0] == end_x]
        if len(loaded) != 1:
            raise ValueError("Barge end loading requires one continuous deck/bottom edge")
        end_edges[surface] = EntityRef("edge", loaded[0])
        if end_loading == "line_loads":
            stack.run(commands.AddLineLoad(end_edges[surface], (sign*moment/(depth*breadth), 0., 0.)))
    if end_loading != "line_loads":
        connection = f"End section X = {end_x:g} m"
        stack.run(commands.AddConnection(refs=tuple(end_edges.values()), name=connection,
                                         kind=end_loading, dofs="full"))
        stack.run(commands.AddConnectionLoad(connection, moment=(0., moment, 0.),
                                             name="Bending moment My"))
    project.target_size = target_size
    project.add_analysis(AnalysisDefinition(analysis_name))
    return project


def floating_barge_large(end_loading: str = "line_loads") -> Project:
    """The barge at three times the length, with openings in two bulkheads.

    A 15 m section: two transverse bulkheads (X = 5 and 10 m), three access openings
    with rounded corners in each, a flat bar FB 120x10 along every opening edge and
    vertical T-bar stiffeners on the bulkheads between the openings.
    The end moment is calibrated for the same peak stress as the basic example.
    """

    return floating_barge_section(
        end_loading, length=BARGE_LARGE_LENGTH, bulkheads=BARGE_LARGE_BULKHEADS,
        girders=BARGE_LARGE_GIRDERS, openings=BARGE_LARGE_OPENINGS,
        opening_bar=BARGE_LARGE_OPENING_BAR, bulkhead_stiffener=BARGE_LARGE_BULKHEAD_STIFFENER,
        stress_scale=BARGE_LARGE_STRESS_SCALE,
        target_size=BARGE_LARGE_TARGET_SIZE,
        name="Floating barge — 15 m section with bulkhead openings")


def drawing_barge_60m() -> Project:
    """Runnable schematic of the supplied 60 m barge, with explicit simplifications.

    The source drawings are read in place and are not packaged with this example.
    Framing stations between the 5 m end modules are distributed uniformly; the
    drawing varies them by about 100 mm. This is an application example, not a
    traced fabrication or classification model.
    """
    end_frames = tuple(i * .625 for i in range(9))
    port_half = end_frames + tuple(round(5. + i * 25. / 9., 6) for i in range(1, 10))
    stations = tuple(sorted({*port_half, *(round(60. - x, 6) for x in port_half)}))
    project = floating_barge_section(
        length=60., breadth=5., depth=2.,
        bulkheads=(5., 30., 55.), girders=stations,
        stiffener_spacing=.625, centerline_bulkhead=True,
        plate_thicknesses=(.006, .006, .006), steel_grade="S235",
        stiffener_section=(.120, .007, .025, .007),
        girder_section=(.300, .012, .100, .012),
        bending_moment=1.e6, target_size=.5,
        name="Drawing barge — 60 m structural example",
        analysis_name="60 m drawing barge — illustrative end bending",
    )
    _add_drawing_barge_end_structures(project)
    project.set_native_mesh_settings(NativeMeshSettings.create(
        .5, element_order="quadratic", backend="quad_first",
        parameters={"layout_policy": "adaptive",
                    "quad_t3_angle_action": "warning"},
    ))
    return project


def _add_drawing_barge_end_structures(project: Project) -> None:
    """Add coarse end houses matching the GeniE model's 0–5 and 55–60 m extents."""
    geometry = project.geometry
    deck = next(face_id for face_id, section in project.face_sections.items()
                if section == "Deck 6 mm")
    coordinates = {tuple(geometry.vertex_position(identifier)): identifier
                   for identifier in geometry.vertices}
    lines = {tuple(sorted((item.start, item.end))): identifier
             for identifier, item in geometry.edges.items()}

    def point(position):
        position = tuple(float(value) for value in position)
        if position not in coordinates:
            coordinates[position] = geometry.add_point(*position)
        return coordinates[position]

    def line(start, end):
        first, last = point(start), point(end)
        key = tuple(sorted((first, last)))
        if key not in lines:
            lines[key] = geometry.add_line(first, last)
        return OrientedEdge(lines[key], geometry.edges[lines[key]].start == first)

    with geometry.transaction():
        part = geometry.add_part(name="Simplified end structures")
        for index, (start, stop) in enumerate(((0., 5.), (55., 60.)), 1):
            bottom = ((start, -2.5, 2.), (stop, -2.5, 2.),
                      (stop, 2.5, 2.), (start, 2.5, 2.))
            top = tuple((x, y, 6.) for x, y, _ in bottom)
            rings = [
                (bottom[0], bottom[1], top[1], top[0]),
                (bottom[1], bottom[2], top[2], top[1]),
                (bottom[2], bottom[3], top[3], top[2]),
                (bottom[3], bottom[0], top[0], top[3]),
                top,
            ]
            for number, ring in enumerate(rings, 1):
                loop = tuple(line(a, b) for a, b in zip(ring, ring[1:] + ring[:1]))
                positions = [geometry.vertex_position(point(coordinate)) for coordinate in ring]
                face_id = geometry.add_face_from_loop(loop, (0, 1, 2, 3), surface=Plane(
                    positions[0], positions[1] - positions[0], positions[3] - positions[0]))
                geometry.add_sheet((face_id,), part_id=part,
                                   name=f"End structure {index} surface {number}")
                project.assign_plate(face_id, "Deck 6 mm")
            for position in bottom:
                identifier = point(position)
                _, uv, distance = geometry.project_to_face(
                    deck, geometry.vertex_position(identifier))
                if distance > 1.e-9:
                    raise ValueError("End structure base is outside the main deck")
                geometry.add_attachment(
                    None, AttachmentKind.VERTEX_ON_FACE, "face", deck,
                    ParameterRange.point(0.),
                    tuple(ParameterRange.point(max(0., min(1., value))) for value in uv),
                    source_kind="vertex", source_id=identifier,
                    evidence=AttachmentEvidence.EXACT, tolerance_used=1.e-9,
                )


def barge_nominal_bending_moment(project, target_stress=200.e6):
    """Elastic My estimate using the gross hull plus longitudinal stiffeners.

    Uses owner beam section properties. Excludes transverse members and the
    bulkhead from longitudinal section area; local clamp/load peaks are not
    predicted by this nominal beam-section estimate.
    """
    import numpy as np
    deck = project.plate_sections["Deck 12 mm"].thickness
    hull = project.plate_sections["Hull 10 mm"].thickness
    components = [(11.*deck, 2.5, 11.*deck**3/12.),
                  (11.*hull, 0., 11.*hull**3/12.),
                  (2.5*hull, 1.25, hull*2.5**3/12.),
                  (2.5*hull, 1.25, hull*2.5**3/12.)]
    for identifier, name in project.edge_sections.items():
        edge = project.geometry.edges[identifier]
        a, b = (project.geometry.vertex_position(v) for v in (edge.start, edge.end))
        if not min(a[0],b[0]) < 1. < max(a[0],b[0]):
            continue
        section = project.beam_sections[name]
        properties = section.properties()
        _, local_y, local_z = project.beam_section_frame(identifier)
        z = a[2] + np.asarray(project.beam_offset_vector(identifier))[2]
        inertia = properties["Iy"]*local_z[2]**2 + properties["Iz"]*local_y[2]**2
        components.append((properties["area"], z, inertia))
    neutral = sum(area*z for area,z,_ in components)/sum(area for area,_,_ in components)
    inertia = sum(local + area*(z-neutral)**2 for area,z,local in components)
    extreme = max(2.5 + deck/2. - neutral, neutral + hull/2.)
    return target_stress*inertia/extreme


def barge_bending_moment(project, stress_scale=BARGE_LINEAR_STRESS_SCALE):
    """Default example moment scaled from its recovered linear elastic peak."""
    return barge_nominal_bending_moment(project)*stress_scale


BARGE_SEAWAY = "Seaway"
def barge_seaway_description(
    duration=60.0, step=0.1, amplitude=0.6, period=6.0, mean=0.3, swing=0.4,
    lag=0.8, window=(10.0, 50.0), ramp=4.0, wave_ramp=6.0,
) -> str:
    return (
        f"The same barge in a regular head sea, as a quasi-static time history of "
        f"{int(round(duration / step))} load cases ({duration:g} s at {step:g} s) defined "
        f"by ONE load series, '{BARGE_SEAWAY}', whose loads are stored once. A wave of "
        f"{amplitude:g} m amplitude and {period:g} s period travels along X and presses on "
        "the bottom and sides as rho g max(0, draft + a sin(omega t - k x) - z); it fades in "
        f"over the first {wave_ramp:g} s. The bending moment on the rigid end connection at "
        f"X = 5 m acts only between {window[0]:g} s and {window[1]:g} s (a window by time), "
        f"ramped in over {ramp:g} s, as {mean:g} + {swing:g} sin(omega t - {lag:g}) times the "
        f"calibration moment. The steps {BARGE_SEAWAY}_000 ... are derived cases; choose "
        "Batch linear static to solve them all with one factorization. Hydrostatic "
        "equilibrium, inertia and wave diffraction are not included."
    )


def floating_barge_seaway(
    duration: float = 60.0,
    step: float = 0.1,
    *,
    wave_amplitude: float = 0.6,
    wave_period: float = 6.0,
    wave_ramp: float = 6.0,
    draft: float = 1.5,
    mean_moment: float = 0.3,
    moment_swing: float = 0.4,
    moment_lag: float = 0.8,
    moment_window: tuple[float, float] = (10.0, 50.0),
    moment_ramp: float = 4.0,
) -> Project:
    """The barge with a time-varying wave pressure and end moment, as one load series.

    ``duration / step`` steps (600 by default) are derived from ONE load series:
    a travelling wave presses on the wetted bottom and sides (fading in over
    ``wave_ramp`` seconds), and the bending moment at the X = 5 m end acts at the
    centre of a *rigid* connection on the deck and bottom end lines only inside
    ``moment_window`` (seconds), ramped in over ``moment_ramp`` seconds, varying as
    ``mean + swing * sin(omega t - lag)`` of the calibrated moment.  Each load is
    stored once with its envelope; the steps solve together with one factorization.
    """

    import numpy as np

    project = floating_barge_section("rigid")
    stack = commands.CommandStack(project)
    # The static end moment of the base example is replaced by the series.
    nominal = barge_bending_moment(project)
    stack.run(commands.DeleteLoadCase("default"))

    geometry = project.geometry
    wet = {"Bottom": (2, 0.), "Port side": (1, -5.5), "Starboard side": (1, 5.5)}
    for name, (axis, coordinate) in wet.items():
        for identifier, face in geometry.faces.items():
            corners = [geometry.vertex_position(geometry.edges[item.edge].start) for item in face.loop]
            if all(corner[axis] == coordinate for corner in corners):
                stack.run(commands.AddWetSurface(
                    EntityRef("face", identifier), name=f"{name} (wet)",
                    fluid_side="away_from_point", interior_point=(2.5, 0., 1.25)))

    omega = 2. * np.pi / wave_period
    start, stop = moment_window
    stack.run(commands.AddLoadSeries(
        BARGE_SEAWAY, duration=duration, step=step,
        parameters={"rho": 1025., "draft": draft, "amp": wave_amplitude, "omega": omega,
                    "kw": omega**2 / 9.81, "mean": mean_moment, "swing": moment_swing,
                    "lag": moment_lag, "ramp": moment_ramp}))
    stack.run(commands.AddWetPressure(
        expression="rho * g * max(0, draft + amp * sin(omega * t - kw * x) - z)",
        name="Travelling wave pressure", case=BARGE_SEAWAY,
        envelope={"by": "time", "points": [[0.0, 0.0], [wave_ramp, 1.0]]} if wave_ramp > 0 else None))
    stack.run(commands.AddConnectionLoad(
        project.connections[0].name, moment=(0., nominal, 0.), case=BARGE_SEAWAY,
        name="Bending moment My(t)",
        envelope={"by": "time", "start": start, "stop": stop,
                  "expression": "min(1, tau / ramp) * (mean + swing * sin(omega * t - lag))"}))
    count = len(project.series_children(BARGE_SEAWAY))
    project.analyses.clear()
    project.add_analysis(AnalysisDefinition(
        f"Barge seaway — {count} load cases", type="batch_linear_static",
        target_kind="load_cases", target_id="batch"))
    project.name = "Floating barge — seaway time history"
    return project
