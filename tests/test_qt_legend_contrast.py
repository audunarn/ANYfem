"""Real themed legend foreground and background must remain readable."""
import os
import numpy as np
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("enable Qt checks explicitly", allow_module_level=True)

from PySide6.QtGui import QPalette
from anyfem import commands
from anyfem.ui.qt import theme
from test_qt_geometry_actions import qapp, window


def luminance(rgb):
    values=np.array(rgb)/255.
    linear=np.where(values<=.04045, values/12.92, ((values+.055)/1.055)**2.4)
    return float(linear@np.array([.2126,.7152,.0722]))


@pytest.mark.parametrize("mode", ["thickness", "section", "density"])
def test_light_and_dark_category_legends_have_contrasting_text(window,qapp,mode):
    original=theme.is_dark()
    window.run(commands.AddStiffenedPanel(4,3,1,beam_section="stiffener",plate_section="plate"))
    window.set_model_colour_mode(mode)
    try:
        for dark in (False, True, False):
            theme.set_dark(window,dark,persist=False);qapp.processEvents()
            label=window.viewport._colour_legend
            assert label.isVisible()
            image=label.grab().toImage()
            background=image.pixelColor(image.width()-12,image.height()-6)
            foreground=label.palette().color(QPalette.WindowText)
            values=sorted((luminance(background.getRgb()[:3]),luminance(foreground.getRgb()[:3])))
            assert (values[1]+.05)/(values[0]+.05)>=7
            assert background.lightness()<80 if dark else background.lightness()>230
            assert foreground.lightness()>180 if dark else foreground.lightness()<90
            if os.environ.get("ANYFEM_LEGEND_REVIEW_IMAGES")=="1":
                image.save("C:/ANYworkspaceWork/legend-"+mode+("-dark" if dark else "-light")+"-20261022.png")
    finally:
        theme.set_dark(window,original,persist=False)
