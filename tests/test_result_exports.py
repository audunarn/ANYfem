"""Headless qualification of result CSV, bitmap and viewport forwarding."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from anyfem.io.artifacts import ArtifactStore
from anyfem.model.records import ResultQuantityDescriptor
from anyfem.ui.result_export import lazy_field_to_csv, save_gif, save_png
from anyfem.ui.panels import ResultsPanel
from anyfem.ui.viewport import Viewport


class StoredField:
    def __init__(self, values, *, location="node", components=("ux", "uy", "uz")):
        self.values = np.asarray(values)
        self.shape = self.values.shape
        self.reads = []
        self.descriptor = SimpleNamespace(
            label="Displacement",
            location=location,
            unit="m",
            components=components,
            frames=(0.0, 1.0),
        )

    def read(self, frame=None):
        self.reads.append(frame)
        return self.values if frame is None else self.values[frame]


class Dataset:
    def __init__(self, stored, *, frames=(0.0, 1.0), tables=None):
        self.stored = stored
        self.frames = np.asarray(frames, dtype=float)
        self.tables = dict(tables or {})
        self.table_keys = tuple(sorted(self.tables))
        self.field_keys = ("displacement",)

    def field(self, key):
        assert key == "displacement"
        return self.stored

    def table(self, key):
        return self.tables[key]


def test_lazy_csv_reads_only_selected_frame_and_uses_persisted_ids():
    values = np.array(
        [
            [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
            [[1.25, 2.5, 3.75], [4.0, 5.0, np.nan]],
        ]
    )
    stored = StoredField(values)
    dataset = Dataset(
        stored, tables={"displacement_node_ids": np.array([20, 10])}
    )

    exported = lazy_field_to_csv(dataset, "displacement", frame=1)

    assert stored.reads == [1]
    assert exported.splitlines() == [
        "frame_index,frame_value,node_id,ux [m],uy [m],uz [m]",
        "1,1,20,1.25,2.5,3.75",
        "1,1,10,4,5,nan",
    ]


def test_real_hdf5_sidecar_field_exports_without_eager_conversion(tmp_path: Path):
    descriptor = ResultQuantityDescriptor(
        key="displacement",
        label="Displacement",
        location="node",
        unit="m",
        components=("ux", "uy", "uz"),
        frames=(0.0, 2.0),
    )
    store = ArtifactStore(tmp_path / "model.anyfem")
    artifact = store.write_result(
        job_id="csv-job",
        document_id="document",
        mesh_id="mesh",
        model_hash="model-hash",
        mesh_hash="mesh-hash",
        analysis_hash="analysis-hash",
        fields={
            "displacement": (
                descriptor,
                np.array(
                    [
                        [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
                        [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]],
                    ]
                ),
            )
        },
        frames=(0.0, 2.0),
        tables={"displacement_node_ids": np.array([101, 202])},
    )
    dataset = store.open_result(artifact)

    exported = lazy_field_to_csv(dataset, "displacement", frame=1)

    assert exported.splitlines()[-2:] == [
        "1,2,101,1,2,3",
        "1,2,202,4,5,6",
    ]


def test_lazy_csv_marks_missing_associations_as_row_indexes():
    stored = StoredField(
        np.array([[2.0], [3.0]]),
        location="global",
        components=(),
    )
    stored.descriptor.frames = ()
    dataset = Dataset(stored, frames=())

    exported = lazy_field_to_csv(dataset, "displacement", frame=0)

    assert stored.reads == [None]
    assert exported.splitlines() == [
        "row_index,subindex_1,Displacement [m]",
        "0,0,2",
        "1,0,3",
    ]


def test_lazy_csv_exports_element_face_association_columns():
    stored = StoredField(
        np.array([10.0, 20.0]), location="element_face", components=()
    )
    stored.descriptor.frames = ()
    dataset = Dataset(
        stored,
        frames=(),
        tables={"displacement_element_ids": np.array([[8, 2], [9, 4]])},
    )

    exported = lazy_field_to_csv(dataset, "displacement")

    assert exported.splitlines() == [
        "element_id,face_id,Displacement [m]",
        "8,2,10",
        "9,4,20",
    ]


def test_lazy_csv_rejects_mismatched_association_table():
    stored = StoredField(np.zeros((2, 3)), components=("ux", "uy", "uz"))
    stored.descriptor.frames = ()
    dataset = Dataset(
        stored,
        frames=(),
        tables={"displacement_node_ids": np.array([1])},
    )
    with pytest.raises(ValueError, match="value rows"):
        lazy_field_to_csv(dataset, "displacement")


def test_results_panel_exports_the_selected_lazy_frame(monkeypatch, tmp_path: Path):
    stored = StoredField(
        np.array(
            [
                [[0.0, 0.0, 0.0]],
                [[1.0, 2.0, 3.0]],
            ]
        )
    )
    dataset = Dataset(
        stored, tables={"displacement_node_ids": np.array([42])}
    )
    destination = tmp_path / "field.csv"
    messages = []
    panel = ResultsPanel.__new__(ResultsPanel)
    panel._component = SimpleNamespace(get=lambda: "displacement")
    panel.app = SimpleNamespace(
        solution=None,
        active_job_id="job",
        result_datasets={"job": dataset},
        shape_index=1,
        project=SimpleNamespace(name="demo"),
        set_status=lambda message, **_kwargs: messages.append(message),
    )
    monkeypatch.setattr(
        "anyfem.ui.panels.filedialog.asksaveasfilename",
        lambda **_kwargs: str(destination),
    )

    panel._export_field()

    assert stored.reads == [1]
    assert destination.read_text(encoding="utf-8").splitlines()[-1] == (
        "1,1,42,1,2,3"
    )
    assert messages == [f"field written to {destination}"]


def test_results_panel_refuses_to_capture_a_nonspatial_lazy_table():
    stored = StoredField(np.array([1.0]), location="global", components=())
    stored.descriptor.frames = ()
    dataset = Dataset(stored, frames=())
    panel = ResultsPanel.__new__(ResultsPanel)
    panel._component = SimpleNamespace(get=lambda: "displacement")
    panel.app = SimpleNamespace(
        solution=None,
        active_job_id="job",
        result_datasets={"job": dataset},
    )

    with pytest.raises(ValueError, match="export it as CSV"):
        panel._require_capturable_result()


def test_png_and_gif_exports_are_readable(tmp_path: Path):
    Image = pytest.importorskip("PIL.Image")
    first = Image.new("RGB", (12, 8), "red")
    second = Image.new("RGB", (12, 8), "blue")

    png = save_png(first, tmp_path / "viewport.png")
    gif = save_gif((first, second), tmp_path / "shapes.gif", duration_ms=40)

    with Image.open(png) as loaded:
        assert loaded.size == (12, 8)
        assert loaded.format == "PNG"
    with Image.open(gif) as loaded:
        assert loaded.size == (12, 8)
        assert loaded.format == "GIF"
        assert loaded.n_frames == 2
        assert loaded.info["duration"] == 40


def test_viewport_uses_native_capture_and_forwards_section_plane(tmp_path: Path):
    Image = pytest.importorskip("PIL.Image")

    class Canvas:
        def __init__(self):
            self.plane = None
            self.redraws = 0

        def capture_image(self):
            return Image.new("RGB", (7, 5), "white")

        def set_section_plane(self, **kwargs):
            self.plane = kwargs

        def redraw(self):
            self.redraws += 1

    viewport = Viewport.__new__(Viewport)
    viewport.canvas = Canvas()

    assert viewport.capture_available
    assert viewport.supports_section_planes
    written = viewport.capture_png(tmp_path / "native.png")
    viewport.set_section_plane((2.0, 0.0, 0.0), 1.5)

    assert written.is_file()
    assert viewport.canvas.plane == {
        "normal": (1.0, 0.0, 0.0),
        "offset": 1.5,
        "enabled": True,
    }
    assert viewport.canvas.redraws == 1


def test_section_plane_rejects_zero_normal():
    viewport = Viewport.__new__(Viewport)
    viewport.canvas = SimpleNamespace(set_section_plane=lambda **_kwargs: None)
    with pytest.raises(ValueError, match="cannot be zero"):
        viewport.set_section_plane((0.0, 0.0, 0.0))
