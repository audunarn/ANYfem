"""Button help in real Qt windows; no engineering computation."""
import os
import pytest
if os.environ.get('ANYFEM_RUN_QT_TESTS') != '1':
    pytest.skip('set ANYFEM_RUN_QT_TESTS=1', allow_module_level=True)
pytest.importorskip('PySide6')
from PySide6.QtCore import QEvent, QPoint
from PySide6.QtGui import QHelpEvent
from PySide6.QtWidgets import (QApplication, QAbstractButton, QPushButton,
                              QToolButton, QToolTip)
from anyfem.ui.qt.app import QtFemWindow
from anyfem.ui.qt.geometry_actions import VectorDialog


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    from anyfem.io import recovery
    monkeypatch.setattr(recovery, 'default_recovery_root', lambda: tmp_path/'recovery')
    window = QtFemWindow(viewer_backend='software')
    window.show(); qapp.processEvents()
    yield window
    QToolTip.hideText()
    window.session.mark_saved(); window.close(); qapp.processEvents()


def button(panel, text):
    return next(item for item in panel.findChildren(QPushButton) if item.text()==text)


def help_event(qapp, item):
    point=QPoint(5,5)
    qapp.sendEvent(item, QHelpEvent(QEvent.ToolTip, point, item.mapToGlobal(point)))


def test_all_workbench_buttons_have_descriptions(window, qapp):
    buttons = window.findChildren(QAbstractButton)
    assert len(buttons)>100
    assert all(item.toolTip().strip() for item in buttons)
    assert 'current mesh' in button(window.panels['Solve'],'Solve').toolTip()
    assert 'acknowledge' in window.run_monitor.cancel.toolTip()
    assert 'text and elapsed time' in window.run_monitor.pause.toolTip()
    assert 'undoable edit' in button(window.panels['Scripts'],'Run script').toolTip()
    for item in window.findChildren(QToolButton):
        action=item.defaultAction()
        if action is not None and action.text()=='Geometry':
            assert 'Create and edit' in item.toolTip()
            break
    else:
        pytest.fail('Geometry workspace button not found')


def test_hover_help_shows_description_and_apply_follows_operation(window,qapp):
    panel=window.panels['Geometry']
    window.details.setCurrentIndex(0); qapp.processEvents()
    panel.choice.setCurrentText('Add point'); qapp.processEvents()
    help_event(qapp,panel.apply)
    assert 'Add point' in QToolTip.text() and 'undoable model edit' in QToolTip.text()
    panel.choice.setCurrentText('Add line'); qapp.processEvents()
    help_event(qapp,panel.apply)
    assert 'Add line' in QToolTip.text() and 'Add point' not in panel.apply.toolTip()


def test_option_and_action_icons_describe_existing_controls(window,qapp):
    panel=window.panels['Geometry']
    for item in panel.option_icons.buttons:
        assert item.toolTip().startswith('Select ') and 'workspace' in item.toolTip()
    scripts=window.panels['Scripts']
    for source,item in zip(scripts.option_icons.sources,scripts.option_icons.buttons):
        assert item.toolTip()==source.toolTip()
    panel.choice.addItem('Temporary option','temporary')
    qapp.processEvents()
    assert panel.option_icons.buttons[-1].toolTip()=='Select Temporary option in this workspace.'


def test_dynamic_dialog_buttons_receive_help_without_running_actions(window,qapp):
    dialog=VectorDialog(window.geometry_actions,'Copy',())
    dialog.show();qapp.processEvents()
    copy=button(dialog,'Copy');cancel=button(dialog,'Cancel')
    assert 'displacement vector' in copy.toolTip()
    help_event(qapp,copy)
    assert 'displacement vector' in QToolTip.text()
    assert 'Cancel this operation' in cancel.toolTip()
    dialog.reject();qapp.processEvents()


def test_help_preserves_custom_hints_and_does_not_touch_other_windows(window,qapp):
    custom=QPushButton('Custom action',window)
    custom.setToolTip('A carefully written custom description.');custom.show()
    qapp.processEvents();help_event(qapp,custom)
    assert custom.toolTip()=='A carefully written custom description.'
    unrelated=QPushButton('Outside ANYfem');unrelated.show();qapp.processEvents()
    assert not unrelated.toolTip()
    unrelated.close();custom.close()
