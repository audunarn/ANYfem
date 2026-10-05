"""Mesh and engineering result shortcuts use distinct semantic artwork."""
import os
import pytest
if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1", allow_module_level=True)
pytest.importorskip("PySide6")
from test_qt_geometry_actions import qapp, window
from anyfem.post.fields import available_fields
from anyfem.ui.qt.navigation import navigation_icon
from anyfem.ui.qt.field_icons import field_drawing


def pixels(name):
    image=navigation_icon(name).pixmap(24,24).toImage()
    return bytes(image.constBits())


def test_mesh_and_result_icons_are_unique_at_toolbar_size(qapp):
    for names in (("quad_first","auto","mapped","native"), available_fields()):
        assert all(field_drawing(name) is not None for name in names)
        images=[pixels(name) for name in names]
        assert len(set(images)) == len(names)


def test_live_buttons_use_semantic_icons_and_keep_help(window, qapp):
    for workspace, attr in (("Mesh","strategy"),("Results","field")):
        panel=window.panels[workspace]
        combo=getattr(panel,attr)
        strip=panel.option_icons
        assert len(strip.buttons)==combo.count()
        for index,button in enumerate(strip.buttons):
            assert button.toolTip() and button.accessibleName()==combo.itemText(index)
            assert button.icon().pixmap(24,24).toImage() == navigation_icon(combo.itemText(index)).pixmap(24,24).toImage()
        strip.buttons[-1].click()
        assert combo.currentIndex()==combo.count()-1
