"""Native shared table lifecycle, route and numeric sorting checks."""
import os
import pytest
if os.environ.get('ANYFEM_RUN_QT_TESTS')!='1':pytest.skip('Qt opt-in',allow_module_level=True)
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog
from test_qt_geometry_actions import qapp,window,geometry
from anyfem import commands
from anyfem.tables import CATEGORIES


def test_numeric_sort_and_close_releases_data(window,qapp):
    window.run_many([commands.AddPoint(x,0) for x in (100,9,20)])
    table=window.tree.open_table('Points');qapp.processEvents();table.proxy.sort(0,Qt.AscendingOrder)
    xs=[float(table.proxy.data(table.proxy.index(i,0),Qt.UserRole+1)) for i in range(table.proxy.rowCount())]
    assert xs==sorted(xs)
    table.close();qapp.processEvents();assert 'Points' not in window.tree._table_windows


def test_creation_routes_do_not_execute_or_refuse_in_silence(window,qapp,monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog,'getText',lambda *args,**kwargs:('',False))
    before=window.session.revision.id
    for category in CATEGORIES:
        table=window.tree.open_table(category);qapp.processEvents()
        if table.model.adapter.creation_fields or category=='Mesh sizes':table.close();continue
        table.add_record();qapp.processEvents()
        assert window.session.revision.id==before
        if category=='Beams':assert 'Select the line axes' in table.feedback.text()
        elif category=='Sets':assert table.isVisible()
        else:assert not table.isVisible(),(category,table.feedback.text())
        table.close()
