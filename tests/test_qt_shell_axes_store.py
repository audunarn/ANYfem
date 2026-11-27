"""D2-1: the Results task's picker hides a declared shell result-axes store and
keeps ordinary shell_axes_* named tables of other artifacts visible (Codex PR-1
rounds 5, 7, 8)."""

import os

import numpy as np
import pytest

pytestmark = pytest.mark.skipif(os.environ.get("ANYFEM_RUN_QT_TESTS") != "1",
                                reason="set ANYFEM_RUN_QT_TESTS=1 for Qt result tests")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from anyfem.io.artifacts import ArtifactStore  # noqa: E402
from anyfem.ui.qt.app import QtFemWindow  # noqa: E402
from test_qt_workbench import settle_result_requests  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    from anyfem.io import recovery

    monkeypatch.setattr(recovery, "default_recovery_root", lambda: tmp_path / "recovery")
    app = QtFemWindow(viewer_backend="software")
    app.show()
    qapp.processEvents()
    yield app
    app.session.mark_saved()
    app.close()
    qapp.processEvents()


def _artifact(window, tmp_path, name, tables):
    store = ArtifactStore(tmp_path / f"{name}.anyfem")
    artifact = store.write_result(job_id=name, document_id=window.project.document_id, mesh_id="m",
                                  model_hash="model", mesh_hash="mesh", analysis_hash="a",
                                  fields={}, frames=(0.0,), tables=tables, histories={})
    return store.open_result(artifact)


def _choices(window, qapp, dataset, job):
    window.active_job_id = job
    window.result_datasets[job] = dataset
    results = window.panels["Results"]
    results.refresh_quantities()
    settle_result_requests(window, qapp)
    return [results.quantities.itemData(index) for index in range(results.quantities.count())]


def test_picker_hides_a_declared_store_and_shows_legacy_names(window, qapp, tmp_path):
    from anyfem.post.shell_axes_record import encode_meta

    declared = _artifact(window, tmp_path, "declared", {
        "shell_axes_meta__bytes": encode_meta({"schema": "x"}),
        "shell_axes_thickness": np.zeros(2),
        "ordinary_table": np.arange(2.0),
    })
    choices = _choices(window, qapp, declared, "declared")
    keys = [tuple(choice)[1] for choice in choices if choice is not None and tuple(choice)[0] == "table"]
    assert "ordinary_table" in keys
    assert not any(str(key).startswith("shell_axes_") for key in keys)
    legacy = _artifact(window, tmp_path, "legacy", {"shell_axes_orientation": np.arange(3.0)})
    choices = _choices(window, qapp, legacy, "legacy")
    keys = [tuple(choice)[1] for choice in choices if choice is not None and tuple(choice)[0] == "table"]
    assert "shell_axes_orientation" in keys
