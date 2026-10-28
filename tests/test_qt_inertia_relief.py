"""The same persisted Solve controls that expose nullspace expose mass relief."""
import json
import os
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1 for real Qt widgets", allow_module_level=True)
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QCheckBox
from anyfem.model.records import AnalysisDefinition
from test_qt_free_floating import qapp, panel


@pytest.mark.parametrize("name", ["Linear static", "Batch linear static"])
def test_visible_relief_selection_and_real_saved_tree_restoration(panel, name):
    panel.analysis.setCurrentText(name)
    relief=panel.controls.fields["inertia_relief"][0]
    mode=panel.controls.fields["constraint_mode"][0]
    balance=panel.controls.fields["allow_unbalanced_free_free"][0]
    reference=panel.controls.fields["reference_point"][0]
    assert isinstance(relief,QCheckBox)
    assert not relief.isChecked() and not reference.isEnabled()
    balance.setChecked(True)
    relief.setChecked(True)
    assert mode.currentData()=="nullspace" and not mode.isEnabled()
    assert not balance.isChecked() and not balance.isEnabled()
    assert reference.isEnabled()
    reference.setText("[1, 2, 3]")
    settings=panel.settings()
    assert settings["inertia_relief"] is True
    assert settings["allow_unbalanced_free_free"] is False
    assert list(settings["reference_point"])==[1.,2.,3.]
    record=panel.app.project.add_analysis(AnalysisDefinition("Saved relief",type=name,settings={
        key:settings[key] for key in ("inertia_relief","constraint_mode","reference_point","allow_unbalanced_free_free")}))
    panel.rebuild_controls()
    panel.app.tree.edit_record(("analyses",record.id))
    assert panel.controls.fields["inertia_relief"][0].isChecked()
    advanced=json.loads(panel.options.toPlainText())
    assert "inertia_relief" not in advanced and "reference_point" not in advanced
    submitted=[]
    panel.app.solve=lambda analysis,**options:submitted.append((analysis,options))
    panel.start()
    assert submitted[0][1]["inertia_relief"] is True
    assert list(submitted[0][1]["reference_point"])==[1.,2.,3.]


def test_unchecking_relief_restores_ordinary_controls_and_no_reference_leak(panel):
    panel.controls.fields["inertia_relief"][0].setChecked(True)
    panel.controls.fields["reference_point"][0].setText("[4,5,6]")
    panel.controls.fields["inertia_relief"][0].setChecked(False)
    assert panel.controls.fields["constraint_mode"][0].isEnabled()
    assert panel.controls.fields["constraint_mode"][0].currentData()=="transformation"
    assert panel.controls.fields["allow_unbalanced_free_free"][0].isEnabled()
    assert panel.settings()["inertia_relief"] is False
    assert panel.settings()["reference_point"] is None


def test_legacy_settings_and_json_conflicts_are_explicit(panel):
    panel.load_settings({"constraint_mode":"nullspace","allow_unbalanced_free_free":True})
    assert panel.settings()["inertia_relief"] is False
    assert panel.settings()["allow_unbalanced_free_free"] is True
    with pytest.raises(ValueError,match="Inertia relief"):
        panel.load_settings({"inertia_relief":True,"constraint_mode":"transformation"})
    with pytest.raises(ValueError,match="boolean"):
        panel.load_settings({"inertia_relief":"false"})
    panel.options.setPlainText('{"inertia_relief":true,"allow_unbalanced_free_free":true}')
    with pytest.raises(ValueError,match="generalized"):
        panel.settings()


def test_failed_tree_restore_clears_previous_hidden_options(panel):
    panel.options.setPlainText('{"solver_type":"iterative"}')
    record=panel.app.project.add_analysis(AnalysisDefinition(
        "Invalid relief",type="Linear static",
        settings={"inertia_relief":True,"constraint_mode":"transformation"}))
    with pytest.raises(ValueError,match="Inertia relief"):
        panel.app.tree.edit_record(("analyses",record.id))
    assert json.loads(panel.options.toPlainText())=={}


@pytest.mark.parametrize("name", ["Nonlinear static", "Modal"])
def test_relief_controls_do_not_leak_to_other_analyses(panel,name):
    panel.controls.fields["inertia_relief"][0].setChecked(True)
    panel.analysis.setCurrentText(name)
    assert "inertia_relief" not in panel.controls.fields
    assert "reference_point" not in panel.controls.fields
    assert "inertia_relief" not in panel.settings()


@pytest.mark.parametrize("name",["Linear static","Batch linear static"])
def test_real_solve_button_controller_worker_and_display(panel,qapp,name):
    from test_free_floating import free_beam
    from anyfem.model.records import JobStatus
    project,mesh,_=free_beam(balanced=False,count=2)
    window=panel.app
    window._set_project(project)
    window.mesh=mesh
    window.job_manager.executor="process"
    panel.refresh()
    panel.analysis.setCurrentText(name)
    panel.controls.fields["inertia_relief"][0].setChecked(True)
    panel.controls.fields["reference_point"][0].setText("[1,2,3]")
    panel.start()
    job_id=window.active_job_id
    assert job_id
    window.job_manager.wait(job_id,timeout=30)
    window._poll_jobs();qapp.processEvents()
    record=window.project.jobs[job_id]
    assert record.status is JobStatus.COMPLETED
    result=window.job_manager.result(job_id)
    shapes=result.shapes if hasattr(result,"shapes") else [result]
    assert all(shape.inertia_relief["reference_point"]==[1.,2.,3.] for shape in shapes)
    assert all(shape.info["reactions"]=={} for shape in shapes)
    assert "inertia relief" in panel.transcript.toPlainText()
    request=window.job_manager._requests[job_id]
    assert request.executor=="process" and not request.process.is_alive()


def test_actual_static_controls_capture(panel,qapp,tmp_path):
    from pathlib import Path
    from PySide6.QtGui import QFont,QFontDatabase
    from PySide6.QtWidgets import QWidget
    panel.controls.fields["inertia_relief"][0].setChecked(True)
    panel.controls.fields["reference_point"][0].setText("[1,2,3]")
    controls=panel.controls
    # The offscreen platform does not discover Windows system fonts. Register
    # a local UI font for this verification image only; product theme is intact.
    font_path=Path("C:/Windows/Fonts/segoeui.ttf")
    if font_path.is_file():
        identifier=QFontDatabase.addApplicationFont(str(font_path))
        assert identifier>=0
        family=QFontDatabase.applicationFontFamilies(identifier)[0]
        for widget in [controls,*controls.findChildren(QWidget)]:widget.setFont(QFont(family,10))
    controls.ensurePolished();controls.layout().activate()
    controls.resize(620,controls.sizeHint().height())
    qapp.processEvents()
    image=controls.grab()
    assert not image.isNull()
    assert image.save(str(Path(os.environ.get("ANYFEM_RELIEF_CAPTURE",tmp_path/"solve-controls.png"))))
