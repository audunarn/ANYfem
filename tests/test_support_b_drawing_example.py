from math import fsum

import pytest

from anyfem.application.drawing_views import load_support_b_example
from anyfem.application.plate_profiles import build_plate_profiles


def test_packaged_example_reproduces_issued_plate_inventory_and_net_stock():
    pytest.importorskip("shapely",minversion="2")
    project = load_support_b_example()
    part, = project.geometry.parts.values()
    assert part.name=="Support B"
    profiles = build_plate_profiles(project,part.id)
    assert len(profiles)==11 and sum(p.hole_count for p in profiles)==4
    by_mark = {p.mark:p for p in profiles}
    assert {"Pl4102","Pl4097"} <= by_mark.keys()
    assert not ({"Pl171","Pl174"} & by_mark.keys())
    for mark in ("Pl4102","Pl4097"):
        assert by_mark[mark].thickness_m==.015 and by_mark[mark].hole_count==1
    for gauge, mass in ((.01,12.560000),(.015,186.718777)):
        net = fsum(p.area_m2 * p.thickness_m * 7850 for p in profiles if p.thickness_m==gauge)
        assert net==pytest.approx(mass,abs=0.000001)
    assert project.materials["AH36"].density==pytest.approx(9535.7)


def test_picked_slotted_plate_cad_has_exact_contours_dimensions_and_opening_location(tmp_path):
    ezdxf = pytest.importorskip("ezdxf")
    pytest.importorskip("shapely",minversion="2")
    from anyfem.application.plate_view import build_plate_view
    from anyfem.application.drawing_views import write_view_dxf
    project = load_support_b_example()
    g = project.geometry
    sheet = next(sheet for sheet in g.sheets.values() if sheet.name=="Pl4102")
    face_id = g.face_uses[sheet.face_use_ids[0]].face_id
    view = build_plate_view(project,face_id)
    target = tmp_path/"plate.dxf"
    write_view_dxf(view,target)
    doc = ezdxf.readfile(target)
    model = doc.modelspace()
    assert doc.units==ezdxf.units.MM
    assert model.query('SPLINE[layer=="HOLES"]')
    assert sorted(d.get_measurement() for d in model.query("DIMENSION"))==pytest.approx([500,1200],abs=.001)
    assert any("400.00 x 200.00" in t.dxf.text for t in model.query('TEXT[layer=="ANNOTATION"]'))
