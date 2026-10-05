"""Editable teaching models built with public owner APIs, without UI imports."""
from anygeometry.entities import EntityRef, OrientedEdge
from anygeometry.surfaces import Plane
from anygeometry.structural import AttachmentEvidence, AttachmentKind, ParameterRange
from ..presentation.points import USER_POINT_GROUP

from .. import commands
from ..model.project import Project
from ..model.materials import steel
from ..model.sections import PlateSection, BeamSection
from ..model.regions import ManualRegion, Region, RegionDomain, RegionRef

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
    "Geometry and section properties are provided; hydrostatic loads and floating "
    "equilibrium are not applied."
)
BARGE_SOURCE = "https://www.damen.com/vessels/pontoons-and-barges/stan-pontoons/stan-pontoon-b11"


def floating_barge_section() -> Project:
    """Build continuous plates with persistent member-on-face attachments."""
    project = Project(name="Floating barge — midship section")
    stack = commands.CommandStack(project)
    stack.run(commands.AddMaterial(steel("S355", .010)))
    for name, thickness in (("Deck 12 mm", .012), ("Hull 10 mm", .010), ("Bulkhead 8 mm", .008)):
        stack.run(commands.AddPlateSection(PlateSection(name, thickness, "S355")))
    xs = (0., 2.5, 4., 5.)  # bulkhead and girder intersections are explicit stations
    ys = tuple(mm / 1000 for mm in range(-5500, 5501, 700)) + (5.5,)
    zs = (0., .7, 1.4, 2.1, 2.5)
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

    def plate(name, coordinates):
        loop = tuple(edge(a, b) for a, b in zip(coordinates, coordinates[1:] + coordinates[:1]))
        positions = [geometry.vertex_position(vertex(point)) for point in coordinates]
        identifier = geometry.add_face_from_loop(loop, (0, 1, 2, 3), surface=Plane(
            positions[0], positions[1]-positions[0], positions[3]-positions[0]))
        faces.setdefault(name, []).append(EntityRef("face", identifier))

    # Plates are physical surfaces, not a grid of reinforcement bays. Beam
    # intersections retain their own topology and declared surface attachments.
    with geometry.transaction():
        plate("Deck", ((0.,-5.5,2.5),(5.,-5.5,2.5),(5.,5.5,2.5),(0.,5.5,2.5)))
        plate("Bottom", ((0.,-5.5,0.),(0.,5.5,0.),(5.,5.5,0.),(5.,-5.5,0.)))
        plate("Port side", ((0.,-5.5,0.),(5.,-5.5,0.),(5.,-5.5,2.5),(0.,-5.5,2.5)))
        plate("Starboard side", ((0.,5.5,0.),(0.,5.5,2.5),(5.,5.5,2.5),(5.,5.5,0.)))
        plate("Transverse bulkhead", ((2.5,-5.5,0.),(2.5,5.5,0.),(2.5,5.5,2.5),(2.5,-5.5,2.5)))
        corners = tuple(EntityRef("vertex", vertex((x,y,z)))
                        for x in (0.,5.) for y in (-5.5,5.5) for z in (0.,2.5))
        geometry.add_to_group(USER_POINT_GROUP, corners)
        part = geometry.add_part(name="Barge midship section")
        sheets = {name: geometry.add_sheet((refs[0].id,), part_id=part, name=name)
                  for name,refs in faces.items()}

    def region(name, refs, kind):
        made = Region(name, RegionDomain.GEOMETRY, kind, ManualRegion(tuple(refs)))
        stack.run(commands.AddRegion(made))
        return RegionRef(made.id)

    for name, refs in faces.items():
        section = "Deck 12 mm" if name == "Deck" else "Bulkhead 8 mm" if name == "Transverse bulkhead" else "Hull 10 mm"
        project.assign_plate_region(region(name, refs, "face"), section)

    surfaces = (
        ("Deck", ys, lambda x, s: (x,s,2.5), (0.,0.,-1.)),
        ("Bottom", ys, lambda x, s: (x,s,0.), (0.,0.,1.)),
        ("Port side", zs, lambda x, s: (x,-5.5,s), (0.,1.,0.)),
        ("Starboard side", zs, lambda x, s: (x,5.5,s), (0.,-1.,0.)),
    )
    all_stiffeners, all_girders = [], []
    for surface, stations, point, inward in surfaces:
        stiffeners = [EntityRef("edge", edge(point(a,s),point(b,s)).edge)
                      for s in stations[1:-1] for a,b in zip(xs,xs[1:])]
        girders = [EntityRef("edge", edge(point(x,a),point(x,b)).edge)
                   for x in (0.,4.) for a,b in zip(stations,stations[1:])]
        for kind, refs, sizes in (("stiffeners", stiffeners, (.200,.008,.100,.010)),
                                 ("girders", girders, (.600,.010,.200,.016))):
            name = f"{surface} {kind}"
            h, tw, b, tf = sizes
            stack.run(commands.AddBeamSection(BeamSection(name, "T-bar", "S355",
                web_height=h, web_thickness=tw, flange_width=b, flange_thickness=tf,
                web_direction=inward, offset_mode="automatic")))
            project.assign_beam_region(region(name, refs, "edge"), name)
            face_id = faces[surface][0].id
            for ref in refs:
                members = geometry.members_using_edge(ref.id)
                if len(members) != 1:
                    raise ValueError("Barge reinforcement must resolve to one assigned member")
                member = members[0]
                axis = geometry.edges[ref.id]
                samples = [geometry.project_to_face(face_id, geometry.vertex_position(identifier))
                           for identifier in (axis.start, axis.end)]
                if any(distance > 1.e-9 for _,_,distance in samples):
                    raise ValueError("Barge reinforcement is outside its declared plate")
                uv = [sample[1] for sample in samples]
                ranges = tuple(ParameterRange(max(0.,min(1.,min(p[i] for p in uv))),
                                              max(0.,min(1.,max(p[i] for p in uv)))) for i in (0,1))
                geometry.add_attachment(member, AttachmentKind.MEMBER_ON_FACE, "face", face_id,
                    ParameterRange(0.,1.), ranges, evidence=AttachmentEvidence.EXACT,
                    tolerance_used=1.e-9, sheet_id=sheets[surface])
        all_stiffeners.extend(stiffeners)
        all_girders.extend(girders)
    region("Longitudinal stiffeners — 700 mm", all_stiffeners, "edge")
    region("Transverse girders — 4000 mm", all_girders, "edge")
    region("User points — main corners", corners, "vertex")
    # Continuous surfaces no longer share a subdivided bulkhead boundary.
    # Declare the existing beam intersection points and bulkhead corners as
    # owner attachments instead of relying on coordinate coincidence alone.
    for coordinate, identifier in vertices.items():
        x, y, z = coordinate
        if x != 2.5:
            continue
        targets = ["Transverse bulkhead"]
        if abs(y) == 5.5 and z in (0.,2.5):
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
    return project
