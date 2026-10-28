"""AUTHORED, NOT RUN: the REAL floating Apply click through the WaveLoadDialog (offscreen Qt, opt-in).

Contract covered: the actual dialog and buttons, a typed floating prepared record, a selected absolute interval (handle drag + typed end),
the shared plot navigation (drag handle, Fit selected, Reset/fit all, extrema), the production WaveCalculation worker that REOPENS and
validates the record (no BEM), the ApplyFloatingLoad command run by the app's command stack, undo/redo, and a stale-phase refusal.

The record is built from the self-consistent SYNTHETIC family of tests/test_floating_pipeline.py on a small connected box (native Prepare +
worker are proven separately by the gated native nodes); the dialog's own wave inputs produce the saved realization, so no stale/phase
guard is bypassed.  Command for the parent (harness, sequential, Qt opt-in is set by the harness):
  check.py ... ANYfem tests/test_qt_floating_apply.py
"""
import math
import os
from dataclasses import asdict
from types import SimpleNamespace

import numpy as np
import pytest

if os.environ.get('ANYFEM_RUN_QT_TESTS') != '1':
    pytest.skip('Qt opt-in', allow_module_level=True)
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QLineEdit, QWidget
from test_qt_geometry_actions import qapp  # noqa: F401  (fixture)
from test_qt_wave_load import wait
import test_floating_pipeline as pipe

from anyfem.application import floating_apply as fa
from anyfem.application import floating_prepare as fp
from anyfem.application import floating_source as fsrc
from anyfem.application.floating_job import _settings, reopen_floating_job
from anyfem.application.floating_mass import dry_mass_properties
from anyfem.commands import CommandStack
from anyfem.document import canonical_hash
from anyfem.io.project_file import project_to_dict
from anyfem.solve.build import build_fe_model
from anyfem.ui.qt.wave_load import WaveLoadDialog
from anyhydro import floating_radiation_fields as ff
from anymesher.serialize import mesh_to_dict

NAME = 'FloatApply'


class App:
    """The minimum application surface the dialog uses (real command stack, real project/mesh)."""

    def __init__(self, project, mesh, path):
        self.project, self.mesh, self.path = project, mesh, str(path)
        self.session = SimpleNamespace(read_only=False)
        self.commands = CommandStack(project)
        self.panels = {}
        self.statuses = []

    def run(self, command):
        return self.commands.run(command)

    def set_status(self, text, **kwargs):
        self.statuses.append(text)


def test_real_apply_click_selected_interval_worker_reopen_undo_redo_and_shared_plot_navigation(qapp, tmp_path):
    project, faces = pipe.box_project()
    mesh = pipe.meshed(project, pipe.BASE)
    app = App(project, mesh, tmp_path / 'p.anyfem')
    panel = QWidget()
    panel.app, panel.case_name, panel.case = app, (lambda: 'default'), QLineEdit()
    dialog = WaveLoadDialog(panel)
    dialog.show()
    omega, heading, phase = 0.8, 0.3, 0.2
    for key, value in dict(name=NAME, height='2.0', period=repr(math.tau / omega), heading=repr(math.degrees(heading)),
                           depth='100.0', phase=repr(math.degrees(phase)), duration='4.0', dt='1.0').items():
        dialog.inputs[key].setText(value)
    dialog.body_mode.setCurrentIndex(dialog.body_mode.findData('floating'))
    dialog.floating_fields['panel_size'].setText(str(pipe.PANEL))
    qapp.processEvents()
    assert dialog.floating_selected() and dialog.apply_button.text() == '2. Apply selected interval'
    kwargs = dialog.floating_kwargs(dialog.settings())
    st = _settings(kwargs['settings'])                      # the SAME conversion the worker uses: authoritative realization
    assert st.times == (0.0, 1.0, 2.0, 3.0, 4.0) and len(st.components) == 1

    # typed floating prepared record on the same project/mesh (synthetic self-consistent family, no BEM)
    built = build_fe_model(project, mesh, require_loads=False, require_supports=False)
    props = dry_mass_properties(built.fe_model)
    rec = pipe.pose_record(props)
    pose = fp._pose(rec)
    target = fsrc.prepare_target_cells(project, mesh, pose)
    source = fsrc.prepare_source(project, mesh, pose, target, panel_size=st.panel_size, max_panels=st.max_panels)
    ff.save_family_snapshot(tmp_path / 'cache', pipe.synthetic_snapshot(source, rec, st.components[0]))
    prepared = fp.prepare_from_pose(project, mesh, st, rec, cache_dir=tmp_path / 'cache', built=built, props=props,
                                    evaluator=pipe.eta_evaluator)
    fa.save_prepared(prepared, kwargs['record_path'])

    # the Prepare-stage result the dialog would receive (same summary the production worker returns)
    summary = reopen_floating_job(document=project_to_dict(project), mesh_data=mesh_to_dict(mesh),
                                  settings=kwargs['settings'], record_path=kwargs['record_path'])
    assert summary['kind'] == 'floating_linear_response_v1' and summary['preview_times'] == [0.0, 1.0, 2.0, 3.0, 4.0]
    config = dialog.settings()
    dialog.input_session, dialog.input_project = app.session, project
    dialog.input_document_hash = canonical_hash(project_to_dict(project))
    dialog.input_mesh_hash = canonical_hash(mesh_to_dict(mesh))
    dialog.input_settings, dialog.input_settings_hash = asdict(config), canonical_hash(asdict(config))
    dialog.runner = SimpleNamespace(cancelled=False, isRunning=lambda: False)           # the finished Prepare worker
    dialog.floating_result(summary)
    assert dialog.prepared['kind'] == 'floating_linear_response_v1' and dialog.apply_button.isEnabled()

    # graph: the existing shared widget carries the floating (applied pressure) resultants with a truthful caption
    plot = dialog.plot
    assert plot.times is not None and plot.values.shape == (5, 6) and 'APPLIED pressure resultant' in plot.moment_origin_caption()
    assert 'mean centre of gravity' in plot.moment_origin_caption() and 'mean body axes' in plot.moment_origin_caption()
    plot.resize(900, 700)
    qapp.processEvents()
    rect = plot.plot_rects()[0]
    y = int(rect.center().y())
    x0, x1 = int(plot._x(plot.interval[0], rect)), int(plot._x(1.0, rect))
    QTest.mousePress(plot, Qt.LeftButton, Qt.NoModifier, QPoint(x0, y))                 # drag the left blue handle to t = 1 s
    QTest.mouseMove(plot, QPoint(x1, y))
    QTest.mouseRelease(plot, Qt.LeftButton, Qt.NoModifier, QPoint(x1, y))
    assert plot.interval[0] == 1.0 and dialog.window_start.text() == repr(1.0)           # synchronised with the dialog fields
    dialog.window_end.setText('3.0')
    dialog.numeric_window()
    assert plot.interval == (1.0, 3.0) and dialog.apply_button.isEnabled()
    QTest.mouseClick(plot.fit_selected_button, Qt.LeftButton)
    assert plot.view_interval == (1.0, 3.0)
    extrema = plot.visible_extrema()
    assert len(extrema) == 6 and all(1.0 <= e[1] <= 3.0 and 1.0 <= e[3] <= 3.0 for e in extrema)
    QTest.mouseClick(plot.fit_all_button, Qt.LeftButton)
    assert plot.view_interval == (0.0, 4.0) and plot.interval == (1.0, 3.0)             # reset keeps the selection

    # stale/phase guards are real: another phase cannot reopen this record
    other = {**kwargs['settings'], 'wave': {**kwargs['settings']['wave'], 'phase': 50.0}}
    with pytest.raises(Exception, match='stale|realization'):
        reopen_floating_job(document=project_to_dict(project), mesh_data=mesh_to_dict(mesh), settings=other,
                            record_path=kwargs['record_path'])

    # optional evidence instrumentation (parent-review-17): one screenshot of the prepared dialog before Apply, only when the evidence
    # directory variable is provided by the harness
    evidence = os.environ.get('ANYFEM_WAVE_SURFACE_EVIDENCE')
    if evidence:
        from pathlib import Path
        Path(evidence).mkdir(parents=True, exist_ok=True)
        dialog.grab().save(str(Path(evidence) / 'floating-dialog.png'))

    # the REAL Apply click: production worker reopens/validates (no BEM), then the command runs in the app
    QTest.mouseClick(dialog.apply_button, Qt.LeftButton)
    wait(qapp, lambda: NAME in project.load_cases or dialog.result() == QDialog.Accepted, seconds=90)
    assert NAME in project.load_cases, dialog.status.text()
    history = fa.floating_history_of(project.load_cases[NAME])
    assert history.times == (1.0, 2.0, 3.0) and history.selected_window == (1.0, 3.0)
    assert history.array_sha256 == prepared.history.array_sha256 and len(project.series_children(NAME)) == 3
    analysis = next(a for a in project.analyses.values() if a.target_id == NAME)
    assert analysis.settings['inertia_relief'] is True and analysis.settings['allow_unbalanced_free_free'] is False
    restored = fa.restore_dialog_settings(history)                                        # original inputs, not guesses
    assert restored['wave']['period'] == config.period and restored['wave']['phase'] == config.phase
    assert dialog.result() == QDialog.Accepted and panel.case.text() == NAME

    assert app.commands.undo() and NAME not in project.load_cases
    assert app.commands.redo() and NAME in project.load_cases
    assert fa.floating_history_of(project.load_cases[NAME]).content_sha256 == history.content_sha256
