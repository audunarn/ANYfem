"""Presentation separates errors, warnings, information and incomplete coverage."""
import os
import time
from types import SimpleNamespace

import pytest

if os.environ.get('ANYFEM_RUN_QT_TESTS') != '1':
    pytest.skip('Qt opt-in', allow_module_level=True)

from anygeometry.cleaner import CleanerOptions
from anygeometry.cleaner_diagnostics import DiagnosisReport, DiagnosisFinding, DiagnosisCheck
from anygeometry import EntityRef
from anyfem.geometry_cleaner import diagnosis_summary
from test_qt_geometry_actions import qapp, window


def test_summary_groups_preserve_every_row_and_export(window, qapp, monkeypatch):
    findings = (
        DiagnosisFinding('error1', 'edge_crossing', (EntityRef('edge', 1),), 'Crossing', severity='error'),
        DiagnosisFinding('error2', 'edge_crossing', (EntityRef('edge', 2),), 'Crossing', severity='error'),
        DiagnosisFinding('warning', 'near_edges', (), 'Review separation', severity='warning'),
        DiagnosisFinding('info', 'intentional_coincidence', (), 'Declared intent', severity='info'),
        DiagnosisFinding('unsupported', 'unclassified_candidate', (), 'Not qualified', severity='blocker'),
        DiagnosisFinding('blocker', 'invalid_feature_history', (), 'Invalid feature', severity='blocker'),
    )
    report = DiagnosisReport('fixture', 1, CleanerOptions(), findings,
        (DiagnosisCheck('strict_audit', 'incomplete', 'Unsupported curved candidates'),), (), ())
    before = report.to_dict()
    dialog = window.open_geometry_cleaner()
    worker = SimpleNamespace(cancelled=False)
    dialog.runner = worker
    dialog._started = time.perf_counter()
    monkeypatch.setattr(dialog, '_binding_is_current', lambda: True)
    dialog._received(worker, report)
    dialog.runner = None
    assert '2 model errors' in dialog.summary.text()
    assert '1 model blockers' in dialog.summary.text()
    assert '1 review warnings' in dialog.summary.text()
    assert '1 information' in dialog.summary.text()
    assert '1 coverage limitations' in dialog.summary.text()
    assert 'incomplete' in dialog.summary.text()
    assert 'has not passed' in dialog.summary.text()
    assert 'not confirmed model defects' in dialog.coverage.text()
    assert dialog.grouped_findings.rowCount() == 5
    assert sum(int(dialog.grouped_findings.item(row, 2).text())
               for row in range(5)) == len(findings)
    assert dialog.findings.rowCount() == len(findings)
    assert report.to_dict() == before
    dialog.reject()


def test_empty_incomplete_report_is_never_presented_as_success():
    report = DiagnosisReport('fixture', 1, CleanerOptions(), (),
        (DiagnosisCheck('quality', 'failed', 'Failure'),), (), ())
    summary, groups = diagnosis_summary(report)
    assert 'incomplete' in summary and 'has not passed' in summary
    assert groups == ()
