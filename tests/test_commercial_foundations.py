"""Foundation contracts for the commercial-style document workflow."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile

import numpy as np
import pytest

from anyfem import Project
from anyfem import commands as cmd
from anyfem.document import DocumentSession
from anyfem.io import ArtifactStore, load_project, save_project
from anyfem.jobs import JobManager
from anyfem.model import (
    AnalysisDefinition,
    BooleanRegion,
    CoordinateSystem,
    ManualRegion,
    QueryClause,
    QueryGroup,
    QueryRegion,
    Region,
    RegionRegistry,
    ResultQuantityDescriptor,
    unit_profile,
)
from anygeometry.entities import EntityRef


def test_unit_profiles_parse_explicit_and_profile_units():
    profile = unit_profile("SI-mm-N-MPa")
    assert profile.parse("12", "length") == pytest.approx(0.012)
    assert profile.parse("2.5 m", "length") == pytest.approx(2.5)
    assert profile.parse("355 MPa", "pressure") == pytest.approx(355.0e6)
    assert profile.format(0.010, "length") == "10 mm"


def test_cartesian_and_cylindrical_coordinate_bases_are_right_handed():
    cartesian = CoordinateSystem(
        "rotated", axis=(0, 1, 0), reference=(1, 0, 0)
    )
    basis = cartesian.basis_at()
    assert basis.T @ basis == pytest.approx(np.eye(3))
    assert np.linalg.det(basis) == pytest.approx(1.0)

    cylindrical = CoordinateSystem("pipe", kind="cylindrical")
    assert cylindrical.to_global((1, 0, 0), position=(0, 2, 0)) == pytest.approx(
        (0, 1, 0)
    )


def test_manual_query_and_boolean_regions_resolve_without_eval():
    first = EntityRef("face", 1)
    second = EntityRef("face", 2)
    manual = Region("manual", "geometry", "face", ManualRegion((first,)))
    query = Region(
        "query",
        "geometry",
        "face",
        QueryRegion(QueryGroup("all", (QueryClause("id", "ge", 2),))),
    )
    combined = Region(
        "combined",
        "geometry",
        "face",
        BooleanRegion("union", (manual.id, query.id)),
    )
    registry = RegionRegistry((manual, query, combined))
    assert set(
        registry.resolve(
            combined.id,
            candidates=(first, second),
            properties=lambda ref: {"id": ref.id, "kind": ref.kind},
        )
    ) == {first, second}


def test_document_batches_are_one_undo_and_snapshots_are_immutable():
    project = Project("document")
    session = DocumentSession(project)
    results = session.execute_many(
        (cmd.AddPoint(0, 0), cmd.AddPoint(1, 0)), label="two points"
    )
    assert results == [1, 2]
    assert session.commands.history() == ["two points"]
    snapshot = session.snapshot()
    session.execute(cmd.AddPoint(2, 0))
    assert len(project.geometry.vertices) == 3
    assert len(snapshot.thaw().geometry.vertices) == 2
    assert session.undo()
    assert len(project.geometry.vertices) == 2


def test_display_units_and_labels_do_not_stale_the_solver_model_hash():
    project = Project("hash semantics")
    point = project.geometry.add_point(0, 0, 0)
    coordinates = project.add_coordinate_system(CoordinateSystem("Local"))
    region = project.regions.add(
        Region(
            "Named point",
            "geometry",
            "vertex",
            ManualRegion((project.point(point),)),
        )
    )
    session = DocumentSession(project)
    original_model_hash = session.revision.model_hash
    original_document_hash = session.revision.document_hash

    with session.transaction("display preferences"):
        project.units = unit_profile("SI-mm-N-MPa")
        project.coordinate_systems[coordinates.id] = replace(
            coordinates, name="Renamed local"
        )
        region.name = "Renamed point"

    assert session.revision.model_hash == original_model_hash
    assert session.revision.document_hash != original_document_hash


def test_failed_document_transaction_restores_project_exactly():
    project = Project("rollback")
    session = DocumentSession(project)
    before = session.snapshot().document
    with pytest.raises(RuntimeError):
        with session.transaction("bad edit"):
            project.geometry.add_point(1, 2, 3)
            raise RuntimeError("stop")
    assert session.snapshot().document == before


def test_failed_transaction_restores_selection_and_command_history_exactly():
    from anyfem.selection import Selection

    project = Project("transaction state")
    original = project.geometry.add_point(0.0, 0.0, 0.0)
    selection = Selection("vertex")
    selection.select(project.point(original))
    session = DocumentSession(project, selection=selection)
    session.execute(cmd.AddPoint(1.0, 0.0, 0.0))
    done_before = tuple(session.commands._done)
    selection_before = selection.ordered_items
    filter_before = selection.filter
    mode_before = selection.mode
    document_before = session.snapshot().document

    class BreakAfterMutation(cmd.Command):
        label = "break after mutation"

        def do(self, live):
            live.geometry.add_point(2.0, 0.0, 0.0)
            selection.set_mode("face")
            session.commands._done.clear()
            raise RuntimeError("injected failure")

        def undo(self, live):  # pragma: no cover - failure never commits
            raise AssertionError("must not be called")

    with pytest.raises(RuntimeError, match="injected failure"):
        session.execute(BreakAfterMutation())

    assert session.snapshot().document == document_before
    assert selection.ordered_items == selection_before
    assert selection.filter == filter_before
    assert selection.mode == mode_before
    assert tuple(session.commands._done) == done_before


def test_v4_project_round_trip_preserves_new_registries(tmp_path: Path):
    project = Project("records", units=unit_profile("SI-mm-N-MPa"))
    coordinates = project.add_coordinate_system(
        CoordinateSystem("Deck", origin=(1, 2, 3))
    )
    vertex = project.geometry.add_point(0, 0, 0)
    region = project.regions.add(
        Region("origin", "geometry", "vertex", ManualRegion((project.point(vertex),)))
    )
    analysis = project.add_analysis(
        AnalysisDefinition("Static", target_id="default", settings={"solver": "auto"})
    )
    reloaded = load_project(save_project(project, tmp_path / "records.anyfem"))
    assert reloaded.units.name == "SI-mm-N-MPa"
    assert reloaded.coordinate_systems[coordinates.id].origin == pytest.approx((1, 2, 3))
    assert reloaded.regions[region.id].name == "origin"
    assert reloaded.analyses[analysis.id].settings == {"solver": "auto"}


def test_result_artifact_is_lazy_chunked_and_verified(tmp_path: Path):
    store = ArtifactStore(tmp_path / "model.anyfem")
    descriptor = ResultQuantityDescriptor(
        key="displacement",
        label="Displacement",
        location="node",
        unit="m",
        components=("ux", "uy", "uz"),
        frames=(0.0, 1.0),
        deformation_required=True,
    )
    values = np.arange(24.0).reshape(2, 4, 3)
    artifact = store.write_result(
        job_id="job-1",
        document_id="document-1",
        mesh_id="mesh-1",
        model_hash="model",
        mesh_hash="mesh",
        analysis_hash="analysis",
        fields={"displacement": (descriptor, values)},
        frames=(0.0, 1.0),
        histories={"energy": ((0.0, 1.0), (0.0, 5.0))},
        tables={"displacement_node_ids": np.arange(4, dtype=np.int64)},
    )
    dataset = store.open_result(artifact)
    assert dataset.field_keys == ("displacement",)
    assert dataset.field("displacement").shape == (2, 4, 3)
    assert dataset.field("displacement").read(1) == pytest.approx(values[1])
    assert dataset.field("displacement").descriptor.components == ("ux", "uy", "uz")
    assert dataset.table_keys == ("displacement_node_ids",)
    assert dataset.table("displacement_node_ids") == pytest.approx(np.arange(4))
    assert store.verify(artifact)

    relocated = ArtifactStore(tmp_path / "copy.anyfem")
    copied = relocated.copy_from(store, artifact)
    assert copied.sha256 == artifact.sha256
    assert relocated.open_result(copied).field("displacement").read(0) == pytest.approx(
        values[0]
    )


def test_failed_save_as_copy_preserves_previous_sidecar(tmp_path: Path, monkeypatch):
    import anyfem.io.artifacts as artifact_module

    descriptor = ResultQuantityDescriptor(
        key="scalar", label="Scalar", location="global", unit="1"
    )
    source = ArtifactStore(tmp_path / "source.anyfem")
    artifact = source.write_result(
        job_id="same-job",
        document_id="source",
        mesh_id="mesh",
        model_hash="model",
        mesh_hash="mesh",
        analysis_hash="analysis",
        fields={"scalar": (descriptor, np.asarray([1.0]))},
    )
    destination = ArtifactStore(tmp_path / "destination.anyfem")
    previous = destination.write_result(
        job_id="same-job",
        document_id="destination",
        mesh_id="mesh",
        model_hash="old",
        mesh_hash="mesh",
        analysis_hash="analysis",
        fields={"scalar": (descriptor, np.asarray([2.0]))},
    )
    previous_path = destination.resolve(previous.uri)
    previous_bytes = previous_path.read_bytes()
    actual_copy = artifact_module.shutil.copyfile

    def corrupt_copy(source_path, target_path):
        result = actual_copy(source_path, target_path)
        with open(target_path, "r+b") as stream:
            stream.seek(-1, 2)
            byte = stream.read(1)
            stream.seek(-1, 2)
            stream.write(bytes((byte[0] ^ 0x01,)))
        return result

    monkeypatch.setattr(artifact_module.shutil, "copyfile", corrupt_copy)

    with pytest.raises(artifact_module.ArtifactError, match="checksum mismatch"):
        destination.copy_from(source, artifact)

    assert previous_path.read_bytes() == previous_bytes
    assert destination.open_result(previous).field("scalar").read() == pytest.approx(
        np.asarray([2.0])
    )


def test_job_manager_runs_from_snapshot_and_retains_history():
    project = Project("jobs")
    session = DocumentSession(project)
    analysis = AnalysisDefinition("Check")
    manager = JobManager(project)

    class Result:
        def __init__(self, count):
            self.count = count

        def summary(self):
            return f"{self.count} points"

    def solve(*, project, progress, cancellation_token=None):
        progress("working")
        return Result(len(project.geometry.vertices))

    record = manager.submit(analysis, session.snapshot(), solve)
    project.geometry.add_point(0, 0, 0)
    completed = manager.wait(record.id, timeout=5.0)
    assert completed.status.value == "completed"
    assert manager.result(record.id).count == 0
    assert project.jobs[record.id] is record
    entries = manager.log(record.id)
    assert [entry["kind"] for entry in entries] == [
        "queued", "started", "progress", "completed"
    ]


def test_structured_job_log_round_trips_as_a_verified_sidecar(tmp_path: Path):
    store = ArtifactStore(tmp_path / "model.anyfem")
    entries = (
        {
            "timestamp": "2026-08-10T10:00:00Z",
            "kind": "started",
            "message": "assembling",
        },
        {
            "timestamp": "2026-08-10T10:00:01Z",
            "kind": "completed",
            "message": "completed",
        },
    )

    artifact = store.write_log("job-1", entries)

    assert artifact.kind == "log"
    assert artifact.uri == "logs/job-1.log"
    assert store.verify(artifact)
    assert store.read_log(artifact) == entries
