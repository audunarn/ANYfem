import csv
import json
import math

import numpy as np
import pytest

from anyfem.application.controller import default_project
from anyfem.application.drawing_assembly import assembly_views
from anyfem.application.plate_drawings import export_plate_drawings
from anyfem.document import DocumentSession, canonical_hash

pytest.importorskip("shapely", minversion="2")
pytest.importorskip("ezdxf")


@pytest.fixture
def elevated_plate():
    project = default_project()
    geometry = project.geometry
    face = geometry.add_plate(geometry.add_points([(-1,2,-4),(1,2,-4),(1,3,-4),(-1,3,-4)]))
    part = geometry.add_part(name="Elevated bracket")
    geometry.add_sheet((face,),part_id=part,name="P1")
    project.assign_plate(face,"plate")
    return project, part


def test_selected_datum_translates_assembly_elevations_without_model_edits(elevated_plate):
    project, part = elevated_plate
    before = canonical_hash(DocumentSession(project).snapshot().document)
    global_views = assembly_views(project,part,elevation_datum="global")
    zero_views = assembly_views(project,part,elevation_datum="support_base")
    base_z = -4-project.plate_section_of(next(iter(project.geometry.faces))).thickness/2
    vertical = {"isometric":math.sqrt(2/3),"plan":0.,"front":1.,"right":1.}
    for original, shifted in zip(global_views,zero_views):
        assert original.notes[1] == shifted.notes[1]
        assert len(original.paths)==len(shifted.paths)
        for a,b in zip(original.paths,shifted.paths):
            assert np.asarray(b.points)==pytest.approx(np.asarray(a.points)-(0,base_z*vertical[original.projection]))
    assert global_views[2].extent()[0][1]==pytest.approx(base_z)
    assert zero_views[2].extent()[0][1]==pytest.approx(0.)
    assert canonical_hash(DocumentSession(project).snapshot().document)==before


def test_global_schedule_has_global_labels_and_identical_mass_and_profiles(elevated_plate,tmp_path):
    import ezdxf
    project, part = elevated_plate
    snapshot = DocumentSession(project).snapshot()
    outputs = {mode:export_plate_drawings(snapshot,part,tmp_path/mode,elevation_datum=mode,
                                         include_plate_intersections=False)
               for mode in ("support_base","global")}
    def rows(path):
        with path.open() as stream:
            return list(csv.DictReader(stream))
    local, = rows(outputs["support_base"]/"Plate-index.csv")
    world, = rows(outputs["global"]/"Plate-index.csv")
    assert float(local["Lower edge Z above support base (mm)"])==0.
    assert float(world["Lower global Z (mm)"])==pytest.approx(-4005.)
    assert float(world["Upper global Z (mm)"])==pytest.approx(-3995.)
    assert local["Net mass per plate (kg)"]==world["Net mass per plate (kg)"]
    assert rows(outputs["support_base"]/"Weight-takeoff.csv")==rows(outputs["global"]/"Weight-takeoff.csv")
    basis=json.loads((outputs["global"]/"Drawing-basis.json").read_text())
    assert basis["elevation_datum"]=="global" and basis["base_global_z_m"]==0.
    assert basis["support_base_global_z_m"]==pytest.approx(-4.005)
    def contour(folder):
        drawing=ezdxf.readfile(folder/"profiles/P1.dxf")
        return [(tuple(e.dxf.start),tuple(e.dxf.end)) for e in drawing.modelspace().query('LINE[layer=="OUTLINE"]')]
    assert contour(outputs["support_base"])==contour(outputs["global"])


def test_invalid_datum_does_not_publish(elevated_plate,tmp_path):
    project, part=elevated_plate
    with pytest.raises(ValueError,match="Elevation datum"):
        export_plate_drawings(DocumentSession(project).snapshot(),part,tmp_path/"invalid",elevation_datum="invalid")
    assert not (tmp_path/"invalid").exists()


def test_reference_export_preserves_exact_cut_curves(elevated_plate,tmp_path):
    import ezdxf
    from anygeometry.operations import punch_hole
    project, part=elevated_plate
    geometry=project.geometry
    target=next(iter(geometry.faces))
    punch_hole(geometry,target,(0,2.5,-4),.15)
    web=geometry.add_plate(geometry.add_points([(0,2.1,-4.2),(0,2.9,-4.2),(0,2.9,-3.8),(0,2.1,-3.8)]))
    geometry.add_sheet((web,),name="Web")
    project.assign_plate(web,"plate")
    snapshot=DocumentSession(project).snapshot()
    outputs={enabled:export_plate_drawings(snapshot,part,tmp_path/str(enabled),include_plate_intersections=enabled)
             for enabled in (False,True)}
    def curves(path):
        document=ezdxf.readfile(path/"profiles/P1.dxf")
        result=[]
        for entity in document.modelspace():
            if entity.dxf.layer not in {"OUTLINE","HOLES"}:
                continue
            if entity.dxftype()=="LINE":
                result.append(("LINE",entity.dxf.layer,tuple(entity.dxf.start),tuple(entity.dxf.end)))
            else:
                assert entity.dxftype()=="SPLINE"
                result.append(("SPLINE",entity.dxf.layer,entity.dxf.degree,tuple(map(tuple,entity.control_points)),
                               tuple(entity.knots),tuple(entity.weights)))
        return document,result
    _,plain=curves(outputs[False])
    drawing,annotated=curves(outputs[True])
    assert plain==annotated
    assert drawing.modelspace().query('LWPOLYLINE[layer=="PLATE_INTERSECTIONS"]')
    assert drawing.layers.get("PLATE_INTERSECTIONS").dxf.linetype=="PLATE_REFERENCE"
    assert (outputs[False]/"Weight-takeoff.csv").read_text()==(outputs[True]/"Weight-takeoff.csv").read_text()
