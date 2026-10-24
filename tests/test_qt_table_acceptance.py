"""Independent real-event acceptance of the worktree table workflow."""
import os
import pytest
if os.environ.get('ANYFEM_RUN_QT_TESTS')!='1':
    pytest.skip('enable Qt checks explicitly',allow_module_level=True)

from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QMenu,QLineEdit
from anyfem import commands
from anyfem.tables import CATEGORIES
from test_qt_geometry_actions import qapp,window,geometry


def native_open(window,qapp,category):
    tree=window.tree;group=tree._groups[category]
    tree.scrollTo(group.index());qapp.processEvents()
    point=tree.visualRect(group.index()).center();errors=[]
    selected=tuple(window.selection.items)
    def choose():
        menu=QApplication.activePopupWidget()
        try:
            assert isinstance(menu,QMenu)
            action=next(action for action in menu.actions() if action.text()=='Open table')
            action.trigger()
        except BaseException as error:errors.append(error)
        finally:
            if menu is not None:menu.close()
    QTest.mousePress(tree.viewport(),Qt.RightButton,pos=point)
    assert tuple(window.selection.items)==selected
    QTest.mouseRelease(tree.viewport(),Qt.RightButton,pos=point)
    QTimer.singleShot(0,choose)
    QApplication.sendEvent(tree.viewport(),QContextMenuEvent(QContextMenuEvent.Mouse,point,tree.viewport().mapToGlobal(point)))
    qapp.processEvents();assert not errors,errors
    from anyfem.ui.qt.record_table import RecordTableDialog
    return next(dialog for dialog in window.findChildren(RecordTableDialog) if dialog.category==category and dialog.isVisible())


def cell(dialog,key,field):
    row=next(i for i,row in enumerate(dialog.model.rows) if row.key==key)
    column=next(i for i,column in enumerate(dialog.model.columns) if column.key==field)
    return dialog.proxy.mapFromSource(dialog.model.index(row,column))


def edit_native(dialog,qapp,index,text):
    dialog.view.setCurrentIndex(index);dialog.view.scrollTo(index)
    QTest.mouseClick(dialog.view.viewport(),Qt.LeftButton,pos=dialog.view.visualRect(index).center())
    QTest.keyClick(dialog.view,Qt.Key_F2);qapp.processEvents()
    editor=QApplication.focusWidget()
    assert isinstance(editor,QLineEdit)
    editor.selectAll();QTest.keyClicks(editor,text);QTest.keyClick(editor,Qt.Key_Return)
    qapp.processEvents()


def test_native_category_table_edit_add_delete_undo_and_tree(window,qapp,tmp_path):
    _,refs=geometry(window);window.selection.set_mode('face');window.selection.restore((refs[2],))
    table=native_open(window,qapp,'Plate sections')
    section=window.project.plate_sections['plate']
    edit_native(table,qapp,cell(table,section.id,'thickness'),'20 mm')
    assert window.project.plate_sections['plate'].thickness==pytest.approx(.02)
    assert window.project.plate_sections['plate'].id==section.id
    window.undo();qapp.processEvents()
    assert window.project.plate_sections['plate'].thickness==pytest.approx(.01)
    errors=[]
    def fill():
        modal=QApplication.activeModalWidget()
        try:
            assert modal is not None
            modal.fields['name'].setText('Heavy')
            modal.fields['thickness'].setText('30 mm')
            QTest.mouseClick(modal.apply,Qt.LeftButton)
        except BaseException as error:
            errors.append(error)
            if modal is not None:modal.reject()
    QTimer.singleShot(0,fill);QTest.mouseClick(table.add_button,Qt.LeftButton);qapp.processEvents()
    assert not errors,errors
    heavy=window.project.plate_sections['Heavy']
    assert heavy.thickness==pytest.approx(.03)
    assert heavy.id in window.tree._rows['Plate sections']
    table.search.setText('Heavy');qapp.processEvents();assert table.proxy.rowCount()==1
    table.view.selectRow(0);QTest.mouseClick(table.delete_button,Qt.LeftButton);qapp.processEvents()
    assert 'Heavy' not in window.project.plate_sections,table.feedback.text()
    assert heavy.id not in window.tree._rows['Plate sections']
    window.undo();qapp.processEvents();assert table.proxy.rowCount()==1
    assert window.project.plate_sections['Heavy'].id==heavy.id
    table.search.clear();qapp.processEvents()
    from anyfem.ui.qt import theme
    original_theme=theme.is_dark()
    try:
        for dark in (False,True):
            theme.set_dark(window,dark,persist=False);qapp.processEvents()
            table.grab().save('C:/ANYworkspaceWork/table-sections-'+('dark' if dark else 'light')+'-20261022.png')
    finally:theme.set_dark(window,original_theme,persist=False)
    path=tmp_path/'table.anyfem';window.save_project(path=str(path));table.close()


def test_every_category_opens_without_mutation_and_table_selection_is_lightweight(window,qapp,monkeypatch):
    ids,refs=geometry(window)
    assert set(window.tree._groups)==set(CATEGORIES)
    history=window.commands.history();geometry_identity=window.project.geometry
    for category in CATEGORIES:
        table=window.tree.open_table(category);qapp.processEvents()
        assert table.category==category and table.model.columnCount()>0
        for row in range(table.model.rowCount()):
            for column in range(table.model.columnCount()):
                table.model.data(table.model.index(row,column),Qt.DisplayRole)
        table.close();qapp.processEvents()
    table=window.tree.open_table('Points');qapp.processEvents()
    def unexpected(*args,**kwargs):raise AssertionError('selection rebuilt the whole tree')
    monkeypatch.setattr(window.tree,'refresh',unexpected)
    index=cell(table,ids[0],'x');table.view.setCurrentIndex(index)
    QTest.mouseClick(table.view.viewport(),Qt.LeftButton,pos=table.view.visualRect(index).center())
    qapp.processEvents()
    assert window.viewport._collection_refs==frozenset((refs[0],))
    assert window.commands.history()==history and window.project.geometry is geometry_identity
    table.close()


def test_sorted_filtered_edit_and_stale_project_refusal(window,qapp):
    from anyfem.model.sections import PlateSection
    window.run(commands.AddPlateSection(PlateSection('Alpha',.03,'S355')))
    window.run(commands.AddPlateSection(PlateSection('Zulu',.04,'S355')))
    table=window.tree.open_table('Plate sections');qapp.processEvents()
    target=window.project.plate_sections['Zulu']
    table.view.sortByColumn(0,Qt.DescendingOrder)
    table.search.setText('Zulu');qapp.processEvents()
    assert table.proxy.rowCount()==1
    edit_native(table,qapp,cell(table,target.id,'thickness'),'50 mm')
    assert window.project.plate_sections['Zulu'].thickness==pytest.approx(.05)
    assert window.project.plate_sections['Alpha'].thickness==pytest.approx(.03)
    table.search.clear();qapp.processEvents()
    index=table.proxy.mapToSource(cell(table,target.id,'thickness'))
    history=window.commands.history()
    assert not table.model.setData(index,'bad',Qt.EditRole)
    assert table.feedback.text() and window.commands.history()==history
    old_project=window.project
    window.new_project();qapp.processEvents()
    assert not table.model.setData(index,'60 mm',Qt.EditRole)
    assert old_project.plate_sections['Zulu'].thickness==pytest.approx(.05)
    assert 'Zulu' not in window.project.plate_sections
    table.close()


def test_large_table_is_complete_lazy_and_selection_preserves_scene(window,qapp,monkeypatch):
    import json
    from pathlib import Path
    from time import perf_counter
    from anyfem.application.controller import default_project
    p=default_project();p.geometry.add_points((float(i),0.,0.) for i in range(2051))
    window._set_project(p);window.show_geometry(reset_view=True);qapp.processEvents()
    scene=window.viewport._scene;revision=p.geometry.revision
    def forbidden(*args,**kwargs):raise AssertionError('table inspection rebuilt scene or tree')
    monkeypatch.setattr(window,'show_geometry',forbidden)
    monkeypatch.setattr(window.tree,'refresh',forbidden)
    started=perf_counter();table=window.tree.open_table('Points');qapp.processEvents()
    opened=(perf_counter()-started)*1000
    assert table.model.rowCount()==2051
    assert not table.view.indexWidget(table.proxy.index(0,0))
    samples=[]
    for row in (0,100,1000,2000,2050):
        index=table.proxy.index(row,0);table.view.scrollTo(index);qapp.processEvents()
        started=perf_counter()
        QTest.mouseClick(table.view.viewport(),Qt.LeftButton,pos=table.view.visualRect(index).center())
        qapp.processEvents();samples.append((perf_counter()-started)*1000)
    assert window.viewport._scene is scene and p.geometry.revision==revision
    assert len(window.viewport._collection_refs)==1
    Path('C:/ANYworkspaceWork/table-native-performance-20261022.json').write_text(json.dumps({
        'rows':2051,'backend':'software','open_ms':opened,'selection_samples_ms':samples,
        'full_scene_or_tree_rebuilds':0,'per_cell_widgets':False},indent=2))
    table.grab().save('C:/ANYworkspaceWork/table-points-light-20261022.png')
    table.close()
