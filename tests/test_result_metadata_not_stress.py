"""Boolean stress-recovery flags are metadata, never persisted Pa fields."""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest

from anyfem.io.artifacts import ArtifactStore
from anyfem.io.result_artifact import _Builder, _add_static_stresses, _add_stress_history

h5py = pytest.importorskip("h5py")

_FLAGS = ("stress_physical_stress_available", "stress_numerical_fields_excluded")


def _element(von_mises: float) -> dict:
    return {
        "von_mises": von_mises,
        "physical_stress_available": True,
        "numerical_fields_excluded": False,
    }


def _write(tmp_path, builder: _Builder):
    store = ArtifactStore(tmp_path / "metadata.anyfem")
    store.write_result(
        job_id="metadata-not-stress",
        document_id="doc",
        mesh_id="mesh",
        model_hash="m",
        mesh_hash="h",
        analysis_hash="a",
        fields=dict(builder.fields),
        frames=builder.frames,
        frame_kind=builder.frame_kind,
        histories=dict(builder.histories),
        tables=dict(builder.tables),
    )
    return store.resolve("results/metadata-not-stress.anyres.h5")


def test_static_flags_saved_as_metadata_and_numeric_stress_reopens(tmp_path):
    builder = _Builder(frames=(0.0,), frame_kind="static")
    result = SimpleNamespace(element_stresses={2: _element(2.5e6), 1: _element(1.5e6)})
    _add_static_stresses(builder, result, prefix="stress")

    assert set(builder.fields) == {"stress_von_mises"}
    for key in _FLAGS:
        assert key not in builder.fields
    path = _write(tmp_path, builder)

    with h5py.File(path, "r") as handle:
        fields = set(handle["fields"])
        assert fields == {"stress_von_mises"}
        group = handle["fields/stress_von_mises"]
        assert group.attrs["unit"] == "Pa"
        values = np.asarray(group["values"])
        assert values.dtype.kind == "f"
        np.testing.assert_array_equal(values.reshape(-1), [1.5e6, 2.5e6])
        np.testing.assert_array_equal(
            np.asarray(handle["tables/stress_von_mises_element_ids"]), [1, 2]
        )
        raw = handle["tables/stress_recovery_metadata"][()]
        metadata = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    assert metadata == {
        "1": {"physical_stress_available": True, "numerical_fields_excluded": False},
        "2": {"physical_stress_available": True, "numerical_fields_excluded": False},
    }
    for value in metadata.values():
        assert all(isinstance(flag, bool) for flag in value.values())

    anypost = pytest.importorskip("anypost")
    reader = anypost.open_results(path)
    try:
        quantities = {item["quantity_id"] for item in reader.components()}
    finally:
        reader.close()
    assert not any("physical_stress_available" in q for q in quantities)
    assert not any("numerical_fields_excluded" in q for q in quantities)
    assert any("von_mises" in q for q in quantities)


def test_history_flags_excluded_from_stress_fields():
    builder = _Builder(frames=(0.0, 1.0), frame_kind="time")
    history = ({1: _element(1.0e6)}, {1: _element(2.0e6)})
    _add_stress_history(builder, history, (0.0, 1.0), prefix="stress_history",
                        recovery="recovered")

    assert set(builder.fields) == {"stress_history_von_mises"}
    _descriptor, values = builder.fields["stress_history_von_mises"]
    assert values.dtype.kind == "f"
    np.testing.assert_array_equal(values.reshape(-1), [1.0e6, 2.0e6])
    frames = builder.tables["stress_history_recovery_metadata"]["frames"]
    assert frames == [
        {"1": {"physical_stress_available": True, "numerical_fields_excluded": False}},
    ] * 2


def test_repeated_stress_sources_keep_distinct_linked_metadata():
    builder = _Builder(frames=(0.0,), frame_kind="static")
    first = _element(1.0e6)
    second = _element(2.0e6)
    second['physical_stress_available'] = False
    for values in (first, second):
        _add_static_stresses(builder, SimpleNamespace(element_stresses={1: values}), prefix="stress")
    links = [descriptor.provenance['recovery_metadata_table']
             for descriptor, _values in builder.fields.values()]
    assert len(set(links)) == 2
    assert [builder.tables[key]['1']['physical_stress_available'] for key in links] == [True, False]
    np.testing.assert_array_equal(
        [values.item() for _descriptor, values in builder.fields.values()], [1.0e6, 2.0e6])
