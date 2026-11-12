import os
from pathlib import Path
import time

import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS")!="1":
    pytest.skip("opt-in Qt workbench", allow_module_level=True)
pytest.importorskip("ezdxf")
pytest.importorskip("shapely")
pytest.importorskip("PySide6")
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog

from anyfem.ui.qt.app import QtFemWindow
from anyfem.ui.qt import drawing_workspace


def _until(app, ready, pane, seconds=35):
    end = time.monotonic()+seconds
    while not ready() and time.monotonic()<end:
        app.processEvents()
        time.sleep(.025)
    assert ready(),pane.feedback.text()


def test_example_toolbar_real_pick_plane_export_and_missing_studio(tmp_path,monkeypatch):
    from anyfem.io import recovery
    monkeypatch.setattr(recovery,"default_recovery_root",lambda:tmp_path/"recovery")
    monkeypatch.setattr(drawing_workspace,"find_studio",lambda configured="":None)
    app = QApplication.instance() or QApplication([])
    window = QtFemWindow(viewer_backend="software")
    window.show()
    pane = window.panels["Drawings"]
    try:
        names = list(window.workspace_actions)
        assert names[names.index("Submodelling")+1]=="Drawings"
        window._actions["Support B — plate drawings…"].trigger()
        _until(app,lambda:len(pane.views)==4,pane)
        assert pane.quantity.value()==4 and pane.density.value()==7850
        assert pane.studio_message.isVisible() and "was not found" in pane.studio_message.text()
        assert pane.locate_btn.isVisible() and not pane.open_btn.isEnabled()
        assert "199.28 kg" in pane.feedback.text()
        pane.mode.setCurrentIndex(1)
        window.viewport.set_view("side")
        window.viewport.fit()
        app.processEvents()
        # Use an interior point of an actual visible source plate in the
        # example. Hit-testing, the selection service and the pane all run.
        canvas = window.viewport.canvas
        surface = canvas.event_widget
        patches = window.viewport._scene.faces
        import numpy as np
        triangles = [poly for patch in patches for poly in patch.polygons]
        def projected_area(poly):
            screen = np.asarray([canvas.project_point(v)[:2] for v in poly])
            return abs(np.dot(screen[:,0],np.roll(screen[:,1],1))-np.dot(screen[:,1],np.roll(screen[:,0],1)))
        polygon = max(triangles,key=projected_area)
        x,y,_ = canvas.project_point(np.mean(polygon,axis=0))
        dpr = surface.devicePixelRatioF()
        QTest.mouseClick(surface, Qt.LeftButton,pos=QPoint(round(x/dpr),round(y/dpr)))
        _until(app,lambda:bool(pane.views),pane)
        assert pane.face is not None and pane.views[0].title.startswith("Pl")
        pane.mode.setCurrentIndex(2)
        pane.plane.setCurrentIndex(4)
        pane.limited.setChecked(True)
        for spin,v in zip(pane.bounds,(-2000,2000,-2000,2000)):
            spin.setValue(v)
        app.processEvents()
        assert pane.bounds[-1].height()>=pane.bounds[-1].minimumSizeHint().height()
        assert pane.preview_btn.y()>pane.forms[2].geometry().bottom()
        pane.preview_btn.click()
        _until(app,lambda:bool(pane.views),pane)
        assert any("View limits" in note for note in pane.views[0].notes)
        pane.detail_name.setText("Detail C")
        pane.keep.click()
        target = tmp_path/"sheets"
        monkeypatch.setattr(QFileDialog,"getSaveFileName",lambda *a,**kw:(str(target),""))
        pane.export_btn.click()
        _until(app,lambda:not pane.exporting,pane)
        assert (target/"Drawings.pdf").read_bytes().startswith(b"%PDF")
        import ezdxf
        assert any(e.dxf.text=="Detail C" for e in ezdxf.readfile(target/"views/View-01.dxf").modelspace().query("TEXT"))
        assert not (target/"views/View-02.dxf").exists()
        pane.offset.setValue(2)
        assert not pane.export_btn.isEnabled() and not pane.views
    finally:
        window.session.mark_saved()
        window.close()
        app.processEvents()


def test_studio_discovery_accepts_a_portable_executable(tmp_path):
    from anyfem.ui.qt.open_cad_studio import find_studio
    portable = tmp_path/("OpenCADStudio-v2026.37-portable.exe" if os.name=="nt" else "OpenCADStudio")
    portable.touch()
    portable.chmod(0o755)
    assert find_studio(str(portable))==portable
