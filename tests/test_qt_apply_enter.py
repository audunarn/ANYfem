"""Enter follows the guarded Apply route for every command operation."""
import os
import pytest
if os.environ.get('ANYFEM_RUN_QT_TESTS') != '1':
    pytest.skip('set ANYFEM_RUN_QT_TESTS=1', allow_module_level=True)
from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit
from anyfem.ui.qt.editors import CommandEditor, CodeEdit
from test_qt_geometry_actions import qapp, window


@pytest.mark.parametrize('key', [Qt.Key_Return, Qt.Key_Enter])
def test_enter_applies_point_once_with_current_values_and_undo(window, qapp, key):
    editor = window.panels['Geometry']
    editor.choice.setCurrentIndex(editor.choice.findData('AddPoint'))
    editor.fields['x'][0].setText('2 m')
    editor.fields['y'][0].setText('3 m')
    field = editor.fields['z'][0]
    field.setText('4 m');field.setFocus()
    QTest.keyClick(field, key)
    assert len(window.project.geometry.vertices) == 1
    identifier = next(iter(window.project.geometry.vertices))
    assert tuple(window.project.geometry.vertex_position(identifier)) == (2, 3, 4)
    assert window.session.undo()
    assert len(window.project.geometry.vertices) == 0


def test_every_operation_rebuilt_fields_and_nested_records_apply_once(window):
    count = 0
    for editor in window.panels.values():
        if not isinstance(editor, CommandEditor):
            continue
        editor.apply.clicked.disconnect()
        clicks = []
        editor.apply.clicked.connect(lambda checked=False, clicks=clicks: clicks.append(1))
        for index in range(editor.choice.count()):
            editor.choice.setCurrentIndex(index)
            fields = editor.scroll.widget().findChildren(QLineEdit)
            for field in fields:
                before = len(clicks)
                QTest.keyClick(field, Qt.Key_Return)
                assert len(clicks) == before + 1, editor.choice.currentText()
                count += 1
            before = len(clicks)
            QTest.keyClick(editor.apply, Qt.Key_Enter)
            assert len(clicks) == before + 1
    assert count > 50


def test_modifiers_repeat_disabled_and_multiline_do_not_apply(window):
    editor = window.panels['Geometry']
    editor.apply.clicked.disconnect()
    clicks = []
    editor.apply.clicked.connect(lambda: clicks.append(1))
    field = editor.scroll.widget().findChildren(QLineEdit)[0]
    QTest.keyClick(field, Qt.Key_Return, Qt.ControlModifier)
    repeat = QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier, '\r', True, 2)
    QApplication.sendEvent(field, repeat)
    editor.apply.setEnabled(False)
    QTest.keyClick(field, Qt.Key_Return)
    editor.apply.setEnabled(True)
    code = CodeEdit();code.setParent(editor);code.installEventFilter(editor)
    QTest.keyClick(code, Qt.Key_Return)
    assert code.toPlainText() == '\n'
    assert clicks == []


def test_open_operation_popup_accepts_selection_without_applying(window, qapp):
    editor = window.panels['Geometry']
    editor.apply.clicked.disconnect()
    clicks = []
    editor.apply.clicked.connect(lambda: clicks.append(1))
    editor.choice.showPopup();qapp.processEvents()
    assert QApplication.activePopupWidget() is not None
    QTest.keyClick(editor.choice.view(), Qt.Key_Return)
    assert clicks == []
    editor.choice.hidePopup()


def test_invalid_enter_uses_existing_refusal_without_mutation(window, monkeypatch):
    editor = window.panels['Geometry']
    editor.choice.setCurrentIndex(editor.choice.findData('AddPoint'))
    editor.fields['x'][0].setText('invalid coordinate')
    statuses = []
    monkeypatch.setattr(window, 'set_status', lambda message, **kwargs: statuses.append((message, kwargs)))
    revision = window.session.revision
    QTest.keyClick(editor.fields['x'][0], Qt.Key_Return)
    assert not window.project.geometry.vertices
    assert window.session.revision is revision
    assert statuses and statuses[-1][1]['error']
