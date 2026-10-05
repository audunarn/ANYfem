"""Editable teaching models built through the public command path, without UI imports."""
from anygeometry.entities import EntityRef

from .. import commands
from ..model.project import Project
from ..model.materials import steel
from ..model.sections import PlateSection
from ..model.regions import ManualRegion, Region, RegionDomain

BARGE_DESCRIPTION = (
    "5 m midship section of a nominal 30 m × 11 m × 2.5 m box barge. "
    "X is longitudinal, Y transverse and Z upward from the bottom. "
    "Includes deck, bottom, sides and a transverse bulkhead; the cut ends are open. "
    "S355 steel: 12 mm deck, 10 mm shell and 8 mm bulkhead (illustrative). "
    "A nominal 1.5 m draft places the waterline at Z = 1.5 m. "
    "Geometry and plate properties are provided; hydrostatic loads and floating "
    "equilibrium are not applied."
)
BARGE_SOURCE = "https://www.damen.com/vessels/pontoons-and-barges/stan-pontoons/stan-pontoon-b11"


def floating_barge_section() -> Project:
    """Build a detached, unsolved midship bay with shared boundary vertices."""
    project = Project(name="Floating barge — midship section")
    stack = commands.CommandStack(project)
    stack.run(commands.AddMaterial(steel("S355", .010)))
    for name, thickness in (("Deck 12 mm", .012), ("Hull 10 mm", .010), ("Bulkhead 8 mm", .008)):
        stack.run(commands.AddPlateSection(PlateSection(name, thickness, "S355")))
    vertices = {}
    for x in (0., 2.5, 5.):
        for y in (-5.5, 5.5):
            for z in (0., 2.5):
                vertices[x, y, z] = stack.run(commands.AddPoint(x, y, z))
    groups = {name: [] for name in ("Deck", "Bottom", "Port side", "Starboard side", "Transverse bulkhead")}

    def plate(name, coordinates, section):
        identifier = stack.run(commands.AddPlate([vertices[coordinate] for coordinate in coordinates]))
        stack.run(commands.AssignPlate(identifier, section))
        groups[name].append(EntityRef("face", identifier))

    for a, b in ((0., 2.5), (2.5, 5.)):
        plate("Deck", ((a,-5.5,2.5),(b,-5.5,2.5),(b,5.5,2.5),(a,5.5,2.5)), "Deck 12 mm")
        plate("Bottom", ((a,-5.5,0.),(a,5.5,0.),(b,5.5,0.),(b,-5.5,0.)), "Hull 10 mm")
        plate("Port side", ((a,-5.5,0.),(b,-5.5,0.),(b,-5.5,2.5),(a,-5.5,2.5)), "Hull 10 mm")
        plate("Starboard side", ((a,5.5,0.),(a,5.5,2.5),(b,5.5,2.5),(b,5.5,0.)), "Hull 10 mm")
    plate("Transverse bulkhead", ((2.5,-5.5,0.),(2.5,5.5,0.),(2.5,5.5,2.5),(2.5,-5.5,2.5)), "Bulkhead 8 mm")
    for name, refs in groups.items():
        stack.run(commands.AddRegion(Region(name, RegionDomain.GEOMETRY, "face", ManualRegion(tuple(refs)))))
    return project
