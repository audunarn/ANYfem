"""Form changes must not read QWidget wrappers from a deleted form."""
import os
import sys
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("Qt opt-in", allow_module_level=True)

from PySide6.QtCore import QCoreApplication, QEvent
from shiboken6 import isValid
from test_qt_geometry_actions import qapp, window, geometry


def flush_deleted(qapp):
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.processEvents()


def test_support_advanced_and_operation_rebuild_after_deferred_delete(window, qapp, monkeypatch):
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *args: errors.append(args))
    _, refs = geometry(window)
    window.selection.set_mode("face")
    window.selection.apply([refs[2]])
    window.notebook.select("Loads & BC")
    panel = window.panels["Loads & BC"]
    panel.choice.setCurrentIndex(panel.choice.findData("AddSupport"))
    history = window.commands.history()
    old_target = panel.explicit_ref
    panel.advanced_form.setChecked(True)
    flush_deleted(qapp)
    assert not isValid(old_target)
    panel.advanced_form.setChecked(False)
    qapp.processEvents()
    assert not errors, [str(error[1]) for error in errors]
    assert isValid(panel.explicit_ref)
    for operation in ("AddPressure", "AddPointLoad", "AddSupport", "AddMass", "AddSupport"):
        panel.choice.setCurrentIndex(panel.choice.findData(operation))
        flush_deleted(qapp)
        if operation == "AddSupport":
            for preset in ("pinned", "symmetry", "fixed"):
                panel.preset.setCurrentText(preset)
            assert panel.apply.isEnabled()
    assert not errors, [str(error[1]) for error in errors]
    assert window.commands.history() == history
    panel.apply.click()
    qapp.processEvents()
    assert len(window.project.supports) == 1
    assert window.project.supports[0].ref == refs[2]
    assert window.project.supports[0].constraints == {key: 0. for key in ("ux", "uy", "uz", "rx", "ry", "rz")}
    assert not errors, [str(error[1]) for error in errors]
