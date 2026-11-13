import csv
import os
import re
import time
from pathlib import Path

import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS")!="1":
    pytest.skip("opt-in Qt workbench",allow_module_level=True)
pytest.importorskip("PySide6")


def test_datum_and_intersections_use_the_reviewed_ui_settings(tmp_path,monkeypatch):
    import ezdxf
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QFont,QFontDatabase
    from PySide6.QtPdf import QPdfDocument
    from PySide6.QtWidgets import QApplication,QFileDialog
    from anygeometry.entities import EntityRef
    from anyfem.application.controller import default_project
    from anyfem.io import recovery
    from anyfem.ui.qt import app as qt_app,drawing_workspace

    settings=lambda *_:QSettings(str(tmp_path/"settings.ini"),QSettings.IniFormat)
    monkeypatch.setattr(qt_app,"QSettings",settings)
    monkeypatch.setattr(drawing_workspace,"QSettings",settings)
    monkeypatch.setattr(recovery,"default_recovery_root",lambda:tmp_path/"recovery")
    p=default_project()
    g=p.geometry
    part=g.add_part(name="Joint")
    target=g.add_plate(g.add_points([(0,0,-4),(2,0,-4),(2,2,-4),(0,2,-4)]))
    other=g.add_plate(g.add_points([(1,0,-4),(1,2,-4),(1,2,-3.5),(1,0,-3.5)]))
    for face,name in [(target,"Base"),(other,"Web")]:
        g.add_sheet((face,),part_id=part,name=name)
        p.assign_plate(face,"plate")
    app=QApplication.instance() or QApplication([])
    for name in ("segoeui.ttf","segoeuib.ttf"):
        font=Path(os.environ.get("WINDIR","C:/Windows"))/"Fonts"/name
        if font.is_file():
            QFontDatabase.addApplicationFont(str(font))
    app.setFont(QFont("Segoe UI",10))
    window=qt_app.QtFemWindow(project=p,viewer_backend="software")
    window.show()
    pane=window.panels["Drawings"]
    def until(condition):
        end=time.monotonic()+30
        while not condition() and time.monotonic()<end:
            app.processEvents()
            time.sleep(.02)
        assert condition(),pane.feedback.text()
    try:
        window.workspace_actions["Drawings"].trigger()
        pane.elevation_datum.setCurrentIndex(pane.elevation_datum.findData("global"))
        pane.preview_btn.click()
        until(lambda:len(pane.views)==4)
        assert pane.views[2].extent()[0][1]==pytest.approx(-4.005)
        pane.keep.click()
        assert pane.saved_views
        pane.elevation_datum.setCurrentIndex(pane.elevation_datum.findData("support_base"))
        assert not pane.views and not pane.saved_views and not pane.export_btn.isEnabled()
        pane.preview_btn.click()
        until(lambda:len(pane.views)==4)
        assert pane.views[2].extent()[0][1]==pytest.approx(0.)
        pane.elevation_datum.setCurrentIndex(pane.elevation_datum.findData("global"))
        pane.preview_btn.click()
        until(lambda:len(pane.views)==4)
        folder=tmp_path/"global-issue"
        monkeypatch.setattr(QFileDialog,"getSaveFileName",lambda *a,**k:(str(folder),""))
        pane.export_btn.click()
        until(lambda:not pane.exporting)
        with (folder/"Plate-index.csv").open() as stream:
            rows=list(csv.DictReader(stream))
        assert min(float(row["Lower global Z (mm)"]) for row in rows)==-4005.
        drawing=ezdxf.readfile(folder/"profiles/Base.dxf")
        assert drawing.modelspace().query('LWPOLYLINE[layer=="PLATE_INTERSECTIONS"]')
        pdf=QPdfDocument()
        pdf.load(str(folder/"Drawings.pdf"))
        until(lambda:pdf.status()==QPdfDocument.Status.Ready)
        text=re.sub(r"\s+"," ",pdf.getAllText(pdf.pageCount()-1).text())
        assert "Global lower Z" in text and "Elevations use model global Z" in text
        pdf.close()
        pane.mode.setCurrentIndex(1)
        window.selection.select(EntityRef("face",target))
        until(lambda:bool(pane.views))
        assert any(path.layer=="PLATE_INTERSECTIONS" for path in pane.views[0].paths)
        pane.intersections.setChecked(False)
        assert not pane.views
        pane.preview_btn.click()
        until(lambda:bool(pane.views))
        assert not any(path.layer=="PLATE_INTERSECTIONS" for path in pane.views[0].paths)
    finally:
        window.session.mark_saved()
        window.close()
        app.processEvents()
