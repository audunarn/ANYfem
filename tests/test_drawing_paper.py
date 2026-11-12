"""The A3 issue measures physical views and keeps references readable across pages."""
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("ANYFEM_RUN_QT_TESTS") != "1", reason="opt-in Qt printing")


def test_physical_dimensions_hole_register_and_paginated_references(tmp_path):
    pytest.importorskip("PySide6.QtPdf")
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtPdf import QPdfDocument
    from PySide6.QtWidgets import QApplication
    from anyfem.application.drawing_views import DrawingPath, DrawingView
    from anyfem.ui.qt.drawing_paper import write_sheets

    app = QApplication.instance() or QApplication([])
    if os.name == "nt":
        for font in ("segoeui.ttf", "segoeuib.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"])/"Fonts"/font))
    outline = ((0.,0.), (1.3,0.), (1.3,.4), (0.,.4))
    visible = DrawingPath("VISIBLE", outline, True)
    iso = DrawingView("Renamed presentation", (visible,), projection="isometric")
    front = DrawingView("Renamed elevation", (visible,), projection="front")
    cutout = ((.2,.1), (.4,.1), (.4,.2), (.2,.2))
    traces = tuple(DrawingPath("STIFFENERS", ((i*.04,.01),(i*.04,.39)), label=f"Member {i} T-bar")
                   for i in range(1,26))
    plate = DrawingView("Plate A", (DrawingPath("OUTLINE",outline,True),
                        DrawingPath("HOLES",cutout,True),*traces),
                        ("AH36 at 10 mm", *(f"Detail note {i}" for i in range(32))))
    dest = tmp_path / "set.pdf"
    write_sheets(dest,[iso,front,plate])
    doc = QPdfDocument()
    assert doc.load(str(dest)) == QPdfDocument.Error.None_
    pages = [" ".join(doc.getAllText(i).text().split()) for i in range(doc.pageCount())]
    assert "1,300" not in pages[0] and "1,300" in pages[1]
    assert "PDF scaled to fit A3" in pages[0]
    assert len(pages) >= 7
    assert "H1" in pages[2] and "200 × 100 mm" in pages[2] and "Centre X 300 Y 150" in pages[2]
    assert "Member 25 T-bar" in " ".join(pages)
    assert "Detail note 31" in pages[-1]
    assert app
