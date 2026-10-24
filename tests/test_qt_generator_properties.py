import os
import pytest
if os.environ.get('ANYFEM_RUN_QT_TESTS')!='1':pytest.skip('Qt tests enabled explicitly',allow_module_level=True)
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from test_qt_geometry_actions import qapp,window


def test_generator_engineering_form_units_and_enter(window,qapp):
    from anyfem.model.units import UnitProfile
    window.project.units=UnitProfile('mm',{**window.project.units.units,'length':'mm'})
    panel=window.panels['Generators'];window.notebook.select('Generators');panel.kind.setCurrentText('Stiffened panel');panel.refresh()
    for key,value in {'length':'4000','width':'3000','longitudinal_spacing':'1000'}.items():panel.form.fields[key][0].setText(value)
    panel.beam_section.setCurrentIndex(panel.beam_section.findData('stiffener'));panel.material.setCurrentIndex(panel.material.findData('S355'));panel.plate_thickness.setText('20');panel.mesh_size.setText('400')
    QTest.keyClick(panel.mesh_size,Qt.Key_Return);qapp.processEvents()
    assert len(window.project.edge_sections)==2 and window.project.refinements
    assert all(window.project.plate_sections[name].thickness==pytest.approx(.02) for name in window.project.face_sections.values())
    for kind in ('Plate','Bulkhead','Frame','Girder','Stiffener','Cylinder','Cone'):
        panel.kind.setCurrentText(kind);qapp.processEvents()
        assert ('beam_section' not in panel.form.fields) and ('mesh_size' not in panel.form.fields)
        assert panel.engineering_form.isRowVisible(panel.plate_thickness)==(kind not in {'Girder','Stiffener'})
