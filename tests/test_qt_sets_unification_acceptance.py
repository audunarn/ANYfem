"""Independent native tree/table checks for unified user Sets."""
import os
import pytest
if os.environ.get('ANYFEM_RUN_QT_TESTS')!='1':
    pytest.skip('enable Qt checks explicitly',allow_module_level=True)
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton,QLabel
from PySide6.QtTest import QTest
from test_qt_geometry_actions import qapp,window,choose_set
from test_sets_unification_acceptance import assigned_model
from anyfem.io.project_file import project_to_dict


def test_native_sets_hide_internal_scopes_and_show_boolean_members(window,qapp,tmp_path):
    p,regions,refs=assigned_model();window._set_project(p);qapp.processEvents()
    before=project_to_dict(p);hidden={region.id for region in p.regions if region.hidden}
    assert 'Regions' not in window.tree._groups
    assert set(window.tree._rows['Sets'])=={region.id for region in regions}
    assert not hidden & set(window.tree._rows['Sets'])
    choose_set(window,qapp,regions[-1].id)
    assert window.viewport._collection_refs==frozenset((refs[0],))
    table=window.tree.open_table('Sets');qapp.processEvents()
    assert table.model.rowCount()==3
    row=next(i for i,row in enumerate(table.model.rows) if row.key==regions[-1].id)
    table.view.selectRow(row);qapp.processEvents()
    assert window.viewport._collection_refs==frozenset((refs[0],))
    assert project_to_dict(p)==before
    table.close()
    definitions=window.panels['Definitions']
    texts={item.text() for item in definitions.findChildren(QPushButton)}
    assert 'Create set from selection' in texts and 'Create Boolean set' in texts
    assert not any('region' in text.lower() for text in texts)
    assert len(definitions._region_ids)==3 and not hidden & set(definitions._region_ids)
    path=tmp_path/'user-sets.anyfem';window.save_project(path=str(path));window.open_project(str(path));qapp.processEvents()
    assert set(window.tree._rows['Sets'])=={region.id for region in regions}
    assert project_to_dict(window.project)==before
