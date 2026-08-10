"""Reproducible, bounded-memory reports from immutable result sidecars."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from anyfem.io.artifacts import ArtifactStore, LazyResultDataset, ResultField
from anyfem.model.records import AnalysisDefinition, JobRecord, JobStatus
from anyfem.post.report import (
    ResultReportError,
    build_result_report,
    result_report_context,
    result_report_html,
    result_report_markdown,
    write_result_report,
)
from anyfem.model.records import ResultQuantityDescriptor
from anyfem.ui.panels import ResultsPanel


def _retained_result(tmp_path: Path, *, stress_only: bool = False):
    fields = {}
    if not stress_only:
        fields["displacement"] = (
            ResultQuantityDescriptor(
                key="displacement",
                label="Displacement",
                location="node",
                unit="m",
                components=("ux", "uy", "uz"),
                basis="global",
                frames=(0.0, 0.5, 1.0),
                recovery="native",
                reduction="none",
                deformation_required=True,
                provenance={"source": "equation solution"},
            ),
            np.array(
                [
                    [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
                    [[1.0, -2.0, 3.0], [4.0, 5.0, 6.0]],
                    [[-7.0, 8.0, 9.0], [10.0, np.nan, -12.0]],
                ]
            ),
        )
    fields["stress_sxx"] = (
        ResultQuantityDescriptor(
            key="stress_sxx",
            label="Normal stress XX",
            location="integration_point",
            unit="Pa",
            components=("sxx",),
            basis="element_local",
            frames=(0.0, 0.5, 1.0),
            recovery="committed_state",
            reduction="unaveraged",
            provenance={"state": "plastic", "source": "integration points"},
        ),
        np.array(
            [
                [[1.0], [2.0]],
                [[-4.0], [3.0]],
                [[7.0], [-8.0]],
            ]
        ),
    )
    project_path = tmp_path / "model.anyfem"
    store = ArtifactStore(project_path)
    artifact = store.write_result(
        job_id="job-123",
        document_id="document-123",
        mesh_id="mesh-123",
        model_hash="sha256:model",
        mesh_hash="sha256:mesh",
        analysis_hash="sha256:analysis",
        fields=fields,
        frames=(0.0, 0.5, 1.0),
        frame_kind="load_factor",
        histories={
            "load_displacement": (
                np.array([0.0, 0.5, 1.0]),
                np.array([0.0, 12.0, 9.0]),
            )
        },
        tables={
            "stress_sxx_element_ids": np.array([40, 20]),
            "convergence": {"iterations": [2, 3, 4], "criterion": "residual"},
        },
        provenance={
            "submission": {
                "project_name": "Panel A",
                "project_hash": "sha256:project",
                "document_hash": "sha256:document",
                "job_hash": "sha256:job",
            },
            "producer_versions": {
                "ANYfem": "0.0.1",
                "ANYsolver": "0.2.0",
            },
            "solver": {"factorization": "sparse"},
        },
        summary={"status": "completed", "solution_type": "NonlinearSolution"},
        diagnostics=({"severity": "warning", "message": "test diagnostic"},),
        partial=False,
    )
    return store.open_result(artifact), artifact


def test_report_is_deterministic_and_reads_fields_one_frame_at_a_time(
    tmp_path: Path, monkeypatch
):
    dataset, _artifact = _retained_result(tmp_path)
    reads = []
    original = ResultField.read

    def tracked(self, frame=None):
        reads.append((self.key, frame))
        return original(self, frame)

    monkeypatch.setattr(ResultField, "read", tracked)
    analysis = AnalysisDefinition(
        id="analysis-123", name="Collapse", type="nonlinear_static"
    )
    job = JobRecord(
        id="job-123",
        analysis_id=analysis.id,
        name="Collapse run",
        model_hash="sha256:model",
        mesh_hash="sha256:mesh",
        analysis_hash="sha256:analysis",
        input_hash="sha256:job",
        status=JobStatus.COMPLETED,
        producer_versions={"ANYsolver": "0.2.0"},
    )
    project = SimpleNamespace(
        name="Panel A",
        document_id="document-123",
        analyses={analysis.id: analysis},
    )
    context = result_report_context(
        dataset,
        project=project,
        job=job,
        current_document_hash="sha256:edited-document",
        stale=True,
        captured_images=(Path("captures/deformed view.png"),),
    )

    first = result_report_markdown(dataset, context=context)
    second = result_report_markdown(dataset, context=context)

    assert first == second
    assert all(frame is not None for _key, frame in reads)
    assert set(reads) == {
        ("displacement", 0),
        ("displacement", 1),
        ("displacement", 2),
        ("stress_sxx", 0),
        ("stress_sxx", 1),
        ("stress_sxx", 2),
    }
    assert "`sha256:project`" in first
    assert "`sha256:document`" in first
    assert "`sha256:edited-document`" in first
    assert "`sha256:model`" in first
    assert "`sha256:mesh`" in first
    assert "`sha256:analysis`" in first
    assert "`sha256:job`" in first
    assert "committed_state" in first
    assert "unaveraged" in first
    assert "element_local" in first
    assert "test diagnostic" in first
    assert "captures/deformed%20view.png" in first
    assert "| displacement | uz | m | -12 | 9 | 12 | 2 | 6 | 0 |" in first
    assert "| stress_sxx | sxx | Pa | -8 | 7 | 8 | 2 | 6 | 0 |" in first

    html = result_report_html(dataset, context=context)
    assert html == result_report_html(dataset, context=context)
    assert html.startswith("<!doctype html>\n")
    assert "Result quantities" in html
    assert "captures/deformed view.png" in html


def test_stress_only_report_never_fabricates_displacement(tmp_path: Path):
    dataset, _artifact = _retained_result(tmp_path, stress_only=True)

    report = build_result_report(dataset)
    markdown = report.markdown()

    assert report.deformation_available is False
    assert "Deformation: `unavailable`" in markdown
    assert tuple(item.descriptor.key for item in report.quantities) == ("stress_sxx",)
    with pytest.raises(ResultReportError, match="no requested quantity: displacement"):
        build_result_report(dataset, quantities=("displacement",))


def test_report_fails_closed_for_corrupt_quantity_descriptor(tmp_path: Path):
    pytest.importorskip("h5py")
    import h5py

    dataset, artifact = _retained_result(tmp_path)
    with h5py.File(dataset.path, "r+") as handle:
        handle["fields/stress_sxx"].attrs["components"] = '["sxx","syy"]'
    unverified = LazyResultDataset(dataset.path)

    with pytest.raises(ResultReportError, match="component metadata"):
        build_result_report(unverified, quantities=("stress_sxx",))

    # Opening through ArtifactStore also rejects the edited bytes before a
    # quantity can be reported under the original artifact reference.
    store = ArtifactStore(tmp_path / "model.anyfem")
    with pytest.raises(ValueError, match="(?:size|checksum) mismatch"):
        store.open_result(artifact)


def test_markdown_and_html_writers_and_results_panel_use_retained_artifact(
    tmp_path: Path, monkeypatch
):
    dataset, _artifact = _retained_result(tmp_path)
    markdown_path = write_result_report(dataset, tmp_path / "report.md")
    html_path = write_result_report(dataset, tmp_path / "report.html")
    assert markdown_path.read_text(encoding="utf-8").startswith("# Panel A")
    assert html_path.read_text(encoding="utf-8").startswith("<!doctype html>")
    with pytest.raises(ValueError, match="must end"):
        write_result_report(dataset, tmp_path / "report.txt")

    destination = tmp_path / "panel-report.html"
    messages = []
    analysis = AnalysisDefinition(id="analysis", name="Static")
    job = JobRecord(
        id="job-123",
        analysis_id=analysis.id,
        input_hash="sha256:job",
        model_hash="sha256:model",
        mesh_hash="sha256:mesh",
        analysis_hash="sha256:analysis",
        status=JobStatus.COMPLETED,
    )
    project = SimpleNamespace(
        name="Panel A",
        document_id="document-123",
        analyses={analysis.id: analysis},
        jobs={job.id: job},
    )
    panel = ResultsPanel.__new__(ResultsPanel)
    panel.app = SimpleNamespace(
        solution=None,
        active_job_id=job.id,
        result_datasets={job.id: dataset},
        project=project,
        session=SimpleNamespace(
            revision=SimpleNamespace(document_hash="sha256:current")
        ),
        _job_is_stale=lambda _record: False,
        set_status=lambda message, **_kwargs: messages.append(message),
    )
    monkeypatch.setattr(
        "anyfem.ui.panels.filedialog.asksaveasfilename",
        lambda **_kwargs: str(destination),
    )

    panel._export_report()

    assert destination.read_text(encoding="utf-8").startswith("<!doctype html>")
    assert messages == [f"report written to {destination}"]
