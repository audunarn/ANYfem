import csv
import json
import os

import pytest

from anyfem.application.plate_drawings import export_plate_drawings, main
from anyfem.document import DocumentSession
from anyfem.io import save_project
from anyfem.model.materials import Material
from anyfem.model.project import Project


@pytest.fixture
def plate():
    pytest.importorskip("ezdxf")
    pytest.importorskip("shapely")
    project = Project("shop export")
    project.add_material(Material("Test steel", 210e9, .3, density=8100))
    project.add_plate_section("10 mm", thickness=.01, material="Test steel")
    g = project.geometry
    points = g.add_points([(0, 0, 3), (1, 0, 3), (1, .5, 3), (0, .5, 3)])
    face = g.add_face(g.add_polyline(points, close=True))
    part = g.add_part(name="Pedestal")
    g.add_sheet([face], part_id=part, name="P1")
    project.assign_plate(face, "10 mm")
    return project, part


def test_file_to_profiles_and_unrounded_quantity_takeoff(plate, tmp_path):
    import ezdxf
    project, part = plate
    source = save_project(project, tmp_path / "model.anyfem")
    out = tmp_path / "order"
    main([str(source), str(out), "--part", str(part), "--quantity", "4", "--density", "7850"])
    drawing = ezdxf.readfile(out / "profiles" / "P1.dxf")
    assert drawing.units == ezdxf.units.MM
    assert len(drawing.modelspace().query('LINE[layer=="OUTLINE"]')) == 4
    with (out / "Plate-index.csv").open() as stream:
        item, = csv.DictReader(stream)
    assert item["Material"] == "Test steel"
    assert float(item["Net mass per plate (kg)"]) == 39.25
    assert float(item["Lower edge Z above support base (mm)"]) == 0
    assert float(item["Upper edge Z above support base (mm)"]) == 10
    with (out / "Weight-takeoff.csv").open() as stream:
        stock, total = list(csv.DictReader(stream))
    assert stock["Thickness (mm)"] == "10" and total["Thickness (mm)"] == "All"
    assert float(total["Extended net mass (kg)"]) == 157.0
    basis = json.loads((out / "Drawing-basis.json").read_text())
    assert basis["base_global_z_m"] == pytest.approx(2.995)
    assert basis["override_kg_m3"] == 7850
    snap = DocumentSession(project).snapshot()
    second = export_plate_drawings(snap, part, tmp_path / "assigned")
    with (second / "Plate-index.csv").open() as stream:
        native, = csv.DictReader(stream)
    assert float(native["Net mass per plate (kg)"]) == 40.5


def test_failed_export_and_retry_preserve_previous_files(plate, tmp_path, monkeypatch):
    from anyfem.application.plate_profiles import PlateProfile
    project, part = plate
    snap = DocumentSession(project).snapshot()
    out = tmp_path / "order"
    def fail_write(self, path):
        path.write_bytes(b"incomplete")
        raise OSError("failed DXF write")
    with monkeypatch.context() as patch:
        patch.setattr(PlateProfile, "write_dxf", fail_write)
        with pytest.raises(OSError, match="failed DXF"):
            export_plate_drawings(snap, part, out)
    assert list(tmp_path.iterdir()) == []
    export_plate_drawings(snap, part, out)
    with pytest.raises(FileExistsError):
        export_plate_drawings(snap, part, out)
    assert (out / "profiles" / "P1.dxf").stat().st_size > 0


def test_unavailable_dwg_converter_preserves_atomic_output(plate, tmp_path):
    project, part = plate
    out = tmp_path / "order"
    with pytest.raises(FileNotFoundError):
        export_plate_drawings(DocumentSession(project).snapshot(), part, out,
                              dwg_converter=tmp_path / "unavailable-converter")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.environ.get("ANYFEM_RUN_QT_TESTS") != "1", reason="opt-in Qt workbench")
def test_file_menu_exports_the_active_model_and_respects_cancel(plate, tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QFileDialog, QSpinBox
    from anyfem.io import recovery
    from anyfem.ui.qt.app import QtFemWindow
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(recovery, "default_recovery_root", lambda: tmp_path / "recovery")
    window = QtFemWindow(viewer_backend="software")
    project, _ = plate
    destination = tmp_path / "output"
    try:
        window.open_project(str(save_project(project, tmp_path / "input.anyfem")))
        action = window._actions["Create plate drawings…"]
        monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.Rejected)
        action.trigger()
        assert not destination.exists()
        def accept(dialog):
            dialog.findChild(QSpinBox).setValue(4)
            next(box for box in dialog.findChildren(QCheckBox) if "density" in box.text()).setChecked(True)
            return QDialog.Accepted
        monkeypatch.setattr(QDialog, "exec", accept)
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, **kw: ("", ""))
        action.trigger()
        assert not destination.exists()
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, **kw: (str(destination), ""))
        action.trigger()
        basis = json.loads((destination / "Drawing-basis.json").read_text())
        assert basis["quantity"] == 4 and basis["override_kg_m3"] == 7850
        assert (destination / "profiles" / "P1.dxf").is_file()
    finally:
        window.session.mark_saved()
        window.close()
        app.processEvents()
