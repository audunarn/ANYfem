"""Visible static mode controls and saved option restoration."""
import json
import os
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1 for real Qt widgets", allow_module_level=True)
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QComboBox, QCheckBox
from anyfem.ui.qt.app import QtFemWindow
from anyfem.model.records import AnalysisDefinition


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(qapp, tmp_path, monkeypatch):
    from anyfem.io import recovery
    monkeypatch.setattr(recovery, "default_recovery_root", lambda: tmp_path / "recovery")
    window = QtFemWindow(viewer_backend="software")
    yield window.panels["Solve"]
    window.session.mark_saved()
    window.close()
    qapp.processEvents()


@pytest.mark.parametrize("name", ["Linear static", "Batch linear static"])
def test_static_combo_checkbox_and_saved_settings_round_trip(panel, name):
    panel.analysis.setCurrentText(name)
    mode = panel.controls.fields["constraint_mode"][0]
    balance = panel.controls.fields["allow_unbalanced_free_free"][0]
    assert isinstance(mode, QComboBox)
    assert isinstance(balance, QCheckBox)
    assert mode.currentData() == "transformation"
    assert not balance.isChecked()
    assert "not mass-based inertia relief" in balance.toolTip()
    mode.setCurrentIndex(mode.findData("nullspace"))
    balance.setChecked(True)
    settings = panel.settings()
    assert settings["constraint_mode"] == "nullspace"
    assert settings["allow_unbalanced_free_free"] is True
    # Exercise the actual persisted record representation and form restoration.
    stored = AnalysisDefinition("Floating", type=name, settings={
        key: settings[key] for key in ("constraint_mode", "allow_unbalanced_free_free")})
    restored = AnalysisDefinition.from_dict(json.loads(json.dumps(stored.to_dict())))
    panel.rebuild_controls()
    panel.controls.set_values(restored.settings)
    assert panel.settings()["constraint_mode"] == "nullspace"
    assert panel.settings()["allow_unbalanced_free_free"] is True
    with pytest.raises(ValueError, match="Invalid"):
        panel.controls.set_values({"constraint_mode": "inertia_relief"})


def test_static_controls_do_not_leak_into_nonlinear(panel):
    panel.controls.set_values({"constraint_mode": "nullspace", "allow_unbalanced_free_free": True})
    panel.analysis.setCurrentText("Nonlinear static")
    assert "constraint_mode" not in panel.controls.fields
    assert "allow_unbalanced_free_free" not in panel.controls.fields
    assert "constraint_mode" not in panel.settings()
    assert "allow_unbalanced_free_free" not in panel.settings()


def test_json_overrides_remain_supported(panel):
    panel.analysis.setCurrentText("Linear static")
    panel.options.setPlainText('{"constraint_mode":"nullspace","allow_unbalanced_free_free":true}')
    assert panel.settings()["constraint_mode"] == "nullspace"
    assert panel.settings()["allow_unbalanced_free_free"] is True


@pytest.mark.parametrize("name", ["linear_static", "batch_linear_static"])
def test_real_tree_restoration_and_user_control_edits_affect_solve(panel, name):
    record = panel.app.project.add_analysis(AnalysisDefinition("Saved floating", type=name,
        settings={"constraint_mode": "nullspace", "allow_unbalanced_free_free": True,
                  "load_tolerance": 1e-6} if name == "batch_linear_static" else
                 {"constraint_mode": "nullspace", "allow_unbalanced_free_free": True}))
    panel.app.tree.edit_record(("analyses", record.id))
    mode = panel.controls.fields["constraint_mode"][0]
    balance = panel.controls.fields["allow_unbalanced_free_free"][0]
    assert mode.currentData() == "nullspace"
    assert balance.isChecked()
    advanced = json.loads(panel.options.toPlainText())
    assert "constraint_mode" not in advanced and "allow_unbalanced_free_free" not in advanced
    mode.setCurrentIndex(mode.findData("transformation"))
    balance.setChecked(False)
    submitted = []
    panel.app.solve = lambda name, **settings: submitted.append((name, settings))
    panel.start()
    assert submitted[0][1]["constraint_mode"] == "transformation"
    assert submitted[0][1]["allow_unbalanced_free_free"] is False
