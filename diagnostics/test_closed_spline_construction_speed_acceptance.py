"""Measure the actual Finish sketch route without weakening its contracts."""
import hashlib
import json
import marshal
import os
from pathlib import Path
import time
import numpy as np
import pytest

if os.environ.get('ANYFEM_RUN_QT_TESTS') != '1':
    pytest.skip('set ANYFEM_RUN_QT_TESTS=1', allow_module_level=True)

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from anygeometry import backend
from anygeometry import model as geometry_module, nurbs as nurbs_module
from anygeometry.nurbs import NurbsCurve
from test_qt_geometry_actions import qapp, window
from test_qt_sketcher_workflow import enter_sketch
from test_closed_spline_extrusion_acceptance import assert_display_follows_closed_profile


def test_cold_finish_retains_exact_shape_and_display(window, qapp, monkeypatch):
    panel = enter_sketch(window, qapp, 'sketch-yz')
    panel.closed.setChecked(True)
    panel.choose_tool('spline')
    for text in ('0, 0', '2, 0', '1, 2'):
        panel.coordinates.setText(text)
        QTest.keyClick(panel.coordinates, Qt.Key_Return)
    panel.enter()
    assert window.sketcher.task.entities[0].closed
    panel.extrusion.setText('3 m')
    phases = []
    original = window.session.execute
    def execute(*args, **kwargs):
        start = time.perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            phases.append(time.perf_counter() - start)
    monkeypatch.setattr(window.session, 'execute', execute)
    start = time.perf_counter()
    QTest.mouseClick(panel.buttons['Apply'], Qt.LeftButton)
    qapp.processEvents()
    window.viewport.canvas.event_widget.repaint()
    ready = time.perf_counter() - start
    assert not window.viewport.construction_active
    project = window.project
    assert len(project.geometry.faces) == 4
    assert_display_follows_closed_profile(project)
    uv = np.array([(u, v) for u in np.linspace(0., 1., 17) for v in (0., .3, 1.)])
    samples = {}
    for face in project.geometry.faces:
        samples[str(face)] = project.geometry.evaluate_face_many(face, uv).tolist()
    polygons = [p.tolist() for patch in window.viewport._scene.faces for p in patch.polygons]
    evidence = {
        'finish_to_painted_seconds': ready, 'session_execute_seconds': phases,
        'backend': backend.backend_info(), 'samples': samples, 'display_polygons': polygons,
        'runtime_code': {
            name: hashlib.sha256(marshal.dumps(function.__code__)).hexdigest()
            for name, function in {'curve': NurbsCurve.derivatives, 'kernel_wrapper': backend.bspline_curve}.items()
        },
        'sources': {name: hashlib.sha256(Path(path).read_bytes()).hexdigest() for name, path in {
            'model': geometry_module.__file__,
            'nurbs': nurbs_module.__file__,
            'backend': backend.__file__,
        }.items()},
    }
    destination = os.environ.get('ANYFEM_SPEED_RESULT')
    if destination:
        Path(destination).write_text(json.dumps(evidence), encoding='utf-8')
    reference = os.environ.get('ANYFEM_SPEED_REFERENCE')
    if reference:
        old = json.loads(Path(reference).read_text(encoding='utf-8'))
        for face, points in samples.items():
            np.testing.assert_allclose(points, old['samples'][face], rtol=0., atol=2e-12)
        assert len(polygons) == len(old['display_polygons'])
        np.testing.assert_allclose(polygons, old['display_polygons'], rtol=0., atol=2e-12)
