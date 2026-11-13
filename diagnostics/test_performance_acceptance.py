"""Opt-in scalability gates for the commercial-style workspace.

These tests intentionally construct 50,000 selection owners and result arrays
for 250,000 nodes.  They are qualification workloads, not ordinary regression
tests, and therefore require ``ANYFEM_RUN_SCALE_GATES=1``.  Set
``ANYFEM_RUN_HARDWARE_GATES=1`` as well on a representative workstation to run
the response-time targets from the usability plan.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
import statistics
import time
import tracemalloc

import numpy as np
import pytest

from anyfem.io import artifacts as artifact_module
from anyfem.io.artifacts import ArtifactStore
from anyfem.model.records import ResultQuantityDescriptor
from anyfem.ui.tree import bounded_entity_ids
from anytk3d import PickBinding, SelectionDepth, SelectionFilter
from anytk3d import _selection as projected_selection
from anytk3d._selection import ProjectedPrimitive, ProjectedSelectionIndex


OWNER_COUNT = 50_000
INDEX_WIDTH = 1004
INDEX_HEIGHT = 804
MIB = 1024 * 1024

RUN_SCALE_GATES = os.environ.get("ANYFEM_RUN_SCALE_GATES", "").casefold() in {
    "1",
    "true",
    "yes",
}
pytestmark = pytest.mark.skipif(
    not RUN_SCALE_GATES,
    reason=(
        "large 50k-owner/250k-node qualification is opt-in; set "
        "ANYFEM_RUN_SCALE_GATES=1 on a representative workstation"
    ),
)


@pytest.fixture(scope="module")
def projected_owner_index() -> tuple[ProjectedSelectionIndex, int]:
    """Build the qualified 50k-owner index and retain its allocation peak."""

    already_tracing = tracemalloc.is_tracing()
    if not already_tracing:
        tracemalloc.start()
    baseline = tracemalloc.get_traced_memory()[0]
    tracemalloc.reset_peak()

    def primitives():
        for identifier in range(OWNER_COUNT):
            column = identifier % 250
            row = identifier // 250
            x = float(2 + 4 * column)
            y = float(2 + 4 * row)
            yield ProjectedPrimitive(
                index=identifier,
                shape="point",
                points=((x, y),),
                depths=(1.0,),
                binding=PickBinding.one(
                    f"geometry:{identifier}", "geometry.point"
                ),
                radius=0.5,
            )

    index = ProjectedSelectionIndex(
        primitives(),
        INDEX_WIDTH,
        INDEX_HEIGHT,
        tile_size=16,
    )
    _current, peak = tracemalloc.get_traced_memory()
    incremental_peak = max(0, peak - baseline)
    if not already_tracing:
        tracemalloc.stop()
    return index, incremental_peak


def test_projected_index_50k_memory_and_postings_are_linearly_bounded(
    projected_owner_index,
):
    index, incremental_peak = projected_owner_index
    tile_postings = sum(len(values) for values in index._tiles.values())
    owner_postings = sum(len(values) for values in index._owners.values())

    assert len(index.primitives) == OWNER_COUNT
    assert len(index._owner_values) == OWNER_COUNT
    assert owner_postings == OWNER_COUNT
    # Small owners may straddle at most four adjacent screen tiles.  This is a
    # structural bound, not a timing proxy, and catches accidental all-to-all
    # indexing immediately.
    assert tile_postings + len(index._global) <= 4 * OWNER_COUNT
    assert incremental_peak <= 128 * MIB


def test_local_50k_box_drag_runs_exact_tests_only_for_nearby_tiles(
    projected_owner_index, monkeypatch
):
    index, _peak = projected_owner_index
    area = (0.0, 0.0, 32.0, 32.0)
    candidates = index._indices_for_rect(area)
    calls = 0
    original = projected_selection._primitive_crosses_rect

    def counted(primitive, rect):
        nonlocal calls
        calls += 1
        return original(primitive, rect)

    monkeypatch.setattr(projected_selection, "_primitive_crosses_rect", counted)
    hits = index.rectangle_hits(
        area,
        SelectionFilter(kinds=frozenset({"geometry.point"})),
        crossing=True,
        depth=SelectionDepth.THROUGH,
    )

    assert calls == len(candidates)
    assert calls <= 256
    assert len(hits) == 64
    assert calls < OWNER_COUNT // 100


def test_local_50k_lasso_uses_the_same_bounded_tile_candidates(
    projected_owner_index, monkeypatch
):
    index, _peak = projected_owner_index
    polygon = ((0.0, 0.0), (32.0, 0.0), (0.0, 32.0))
    candidates = index._indices_for_rect((0.0, 0.0, 32.0, 32.0))
    calls = 0
    original = projected_selection._primitive_crosses_polygon

    def counted(primitive, points):
        nonlocal calls
        calls += 1
        return original(primitive, points)

    monkeypatch.setattr(projected_selection, "_primitive_crosses_polygon", counted)
    hits = index.polygon_hits(
        polygon,
        SelectionFilter(kinds=frozenset({"geometry.point"})),
        depth=SelectionDepth.THROUGH,
    )

    assert calls == len(candidates)
    assert calls <= 256
    assert len(hits) == 36


def test_full_50k_crossing_drag_is_linear_and_returns_every_owner(
    projected_owner_index, monkeypatch
):
    index, _peak = projected_owner_index
    calls = 0
    original = projected_selection._primitive_crosses_rect

    def counted(primitive, rect):
        nonlocal calls
        calls += 1
        return original(primitive, rect)

    monkeypatch.setattr(projected_selection, "_primitive_crosses_rect", counted)
    hits = index.rectangle_hits(
        (0.0, 0.0, float(INDEX_WIDTH), float(INDEX_HEIGHT)),
        SelectionFilter(kinds=frozenset({"geometry.point"})),
        crossing=True,
        depth=SelectionDepth.THROUGH,
    )

    assert calls == OWNER_COUNT
    assert len(hits) == OWNER_COUNT
    assert hits[0].key == "geometry:0"
    assert hits[-1].key == f"geometry:{OWNER_COUNT - 1}"


@pytest.fixture(scope="module")
def large_result_dataset(tmp_path_factory):
    """Eight result frames at the qualified 250k-node scale."""

    directory = tmp_path_factory.mktemp("large-result")
    store = ArtifactStore(Path(directory) / "model.anyfem")
    frame_count = 8
    node_count = 250_000
    frames = tuple(float(index) for index in range(frame_count))
    descriptor = ResultQuantityDescriptor(
        key="displacement",
        label="Displacement",
        location="node",
        unit="m",
        components=("ux", "uy", "uz"),
        frames=frames,
        deformation_required=True,
    )
    values = np.zeros((frame_count, node_count, 3), dtype=np.float32)
    artifact = store.write_result(
        job_id="performance-result",
        document_id="performance-document",
        mesh_id="performance-mesh",
        model_hash="model",
        mesh_hash="mesh",
        analysis_hash="analysis",
        fields={"displacement": (descriptor, values)},
        frames=frames,
    )
    return store.open_result(artifact), values.shape, values.dtype.itemsize


def test_250k_node_results_are_frame_major_compressed_and_checksummed(
    large_result_dataset,
):
    h5py = pytest.importorskip("h5py")
    dataset, shape, _itemsize = large_result_dataset
    with h5py.File(dataset.path, "r") as handle:
        values = handle["fields/displacement/values"]
        assert values.shape == shape
        assert values.chunks == (1,) + shape[1:]
        assert values.compression == "gzip"
        assert values.shuffle
        assert values.fletcher32


def test_lazy_result_frame_read_indexes_one_frame_not_the_full_array(
    large_result_dataset, monkeypatch
):
    dataset, shape, itemsize = large_result_dataset
    field = dataset.field("displacement")
    real_h5py = artifact_module._h5py()
    selections: list[object] = []

    class TrackingValues:
        def __init__(self, values) -> None:
            self.values = values

        def __getitem__(self, selection):
            selections.append(selection)
            return self.values[selection]

    class TrackingFile:
        def __init__(self, *args, **kwargs) -> None:
            self.handle = real_h5py.File(*args, **kwargs)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            self.handle.close()

        def __getitem__(self, key):
            value = self.handle[key]
            if key == "fields/displacement/values":
                return TrackingValues(value)
            return value

    class TrackingH5py:
        File = TrackingFile

    monkeypatch.setattr(artifact_module, "_h5py", lambda: TrackingH5py)
    frame = field.read(3)

    assert selections == [3]
    assert frame.shape == shape[1:]
    assert frame.nbytes == math.prod(shape[1:]) * itemsize
    assert frame.nbytes * shape[0] == math.prod(shape) * itemsize


RUN_HARDWARE_GATES = os.environ.get("ANYFEM_RUN_HARDWARE_GATES", "").casefold() in {
    "1",
    "true",
    "yes",
}


@pytest.mark.skipif(
    not RUN_HARDWARE_GATES,
    reason="set ANYFEM_RUN_HARDWARE_GATES=1 on a representative workstation",
)
def test_optional_representative_workstation_response_times(
    projected_owner_index,
    large_result_dataset,
):
    index, _peak = projected_owner_index
    selection_filter = SelectionFilter(kinds=frozenset({"geometry.point"}))

    click_times = []
    for sample in range(40):
        x = float(2 + 4 * (sample % 10))
        y = float(2 + 4 * (sample // 10))
        started = time.perf_counter()
        assert index.point_hits(x, y, selection_filter)
        click_times.append(time.perf_counter() - started)
    click_p95 = sorted(click_times)[math.ceil(0.95 * len(click_times)) - 1]
    assert click_p95 <= 0.025

    drag_times = []
    for _sample in range(3):
        started = time.perf_counter()
        hits = index.rectangle_hits(
            (0.0, 0.0, float(INDEX_WIDTH), float(INDEX_HEIGHT)),
            selection_filter,
            crossing=True,
            depth=SelectionDepth.THROUGH,
        )
        drag_times.append(time.perf_counter() - started)
        assert len(hits) == OWNER_COUNT
    assert statistics.median(drag_times) <= 0.200

    ids = range(1, OWNER_COUNT + 1)
    started = time.perf_counter()
    assert bounded_entity_ids(ids, "Point 49,999") == [49_999]
    assert time.perf_counter() - started <= 0.250

    dataset, _shape, _itemsize = large_result_dataset
    field = dataset.field("displacement")
    started = time.perf_counter()
    field.read(0)
    assert time.perf_counter() - started <= 1.0
    started = time.perf_counter()
    field.read(1)
    assert time.perf_counter() - started <= 0.250
