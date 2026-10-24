"""Clipboard fallback, fresh Qt status context and script-source omission."""
import os
import json
import pytest
if os.environ.get('ANYFEM_RUN_QT_TESTS')!='1':pytest.skip('Qt opt-in',allow_module_level=True)
from test_qt_geometry_actions import qapp,window


def test_help_report_fallback_is_never_empty(window,qapp,monkeypatch):
    monkeypatch.setattr(window,'diagnostic_report',lambda **kwargs:None)
    qapp.clipboard().setText('old');window._actions['Copy diagnosis'].trigger();qapp.processEvents()
    payload=json.loads(qapp.clipboard().text())
    assert payload['current_state']['solver_output']['status']=='not_run'
    assert payload['application']['report_route_error'] and 'copied' in window._status.get().lower()


def test_scripts_copy_uses_bounded_document_blocks_not_source_editor(window,qapp,monkeypatch):
    panel=window.panels['Scripts'];panel.command_stream.setPlainText('commands.run(commands.AddPoint(1, 2, 3))\nRunScript(code="private script")')
    panel.source.setPlainText('never include this source')
    monkeypatch.setattr(panel.command_stream,'toPlainText',lambda:(_ for _ in ()).throw(AssertionError('full command dump forbidden')))
    panel.copy_diagnosis();payload=json.loads(qapp.clipboard().text())
    assert len(payload['recent_gui_commands'])==2 and payload['recent_gui_commands'][1]=='<script command omitted>'
    assert 'never include this source' not in qapp.clipboard().text() and 'private script' not in qapp.clipboard().text()
    assert isinstance(payload['application']['status_text'],str)
