"""Sparse nodal-force projection and the job-local batch load context.

The sparse projection is compared with a dense oracle built from the same
public triplet helpers.  The batch context is exercised for exact reuse
(results identical to the uncached path) and for its refusals: stale or
mutated meshes, untrusted providers, subclasses.  No solve runs.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest

from anymesher.serialize import mesh_to_dict
from anyloads.source_pressure_history import SourceAwarePressureHistory
from anyloads.spectral_pressure_history import SpectralMappedPressureHistory

from anyfem import Project, steel
from anyfem.application.irregular_wave_input import IrregularWaveInput
from anyfem.document import canonical_hash
from anyfem.model import WetPressure, WetSurface
from anyfem.model.project import ProjectError
from anyfem.solve import build as builder
from anyfem.solve import run


def _plate_project(cell=0.5):
    project = Project()
    project.add_material(steel())
    project.add_plate_section("plate", 0.01, "S355")
    g = project.geometry
    vertices = g.add_points([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
    face = g.add_face(g.add_polyline(vertices, close=True))
    project.assign_plate(face, "plate")
    project.add_wet_surface(WetSurface(ref=project.face(face), fluid_side="positive_normal"))
    mesh = project.generate_mesh(cell)
    return project, mesh, tuple(sorted(mesh.elements_of_face[face]))


def _history(mesh, ids, frames=4):
    config = IrregularWaveInput(component_count=2, duration=0.1 * (frames - 1), dt=0.1)
    realization = config.realization()
    n = len(ids)
    return SpectralMappedPressureHistory(
        config.times, ids, tuple(c.omega for c in realization.components),
        ((1.0,) * n, (2.0,) * n), ((0.1,) * n, (0.2,) * n), 0.0,
        canonical_hash(mesh_to_dict(mesh)), "b" * 64, json.dumps(realization.to_dict()),
    )


def _series_project(frames=4):
    project, mesh, ids = _plate_project()
    history = _history(mesh, ids, frames)
    owner = project.add_load_series("Sea", history.times)
    owner.wet_pressures.append(WetPressure(history=history, mapping_key="element"))
    return project, mesh, project.series_children("Sea"), history


def _context(project, mesh):
    return builder._BatchLoadContext(SimpleNamespace(project=project, mesh=mesh))


# ----------------------------------------------------------------------
# sparse projection
# ----------------------------------------------------------------------
def _dense_oracle(mesh, keys, pressure, nodal_keys, nodal):
    rows = {}
    triplets = []
    for column, element_id in enumerate(keys):
        _, normal = builder._element_centroid_normal(mesh, int(element_id))
        for node, force in builder._traction_elements_to_nodes(mesh, (int(element_id),), normal):
            for component in range(3):
                if force[component] != 0.0:
                    index = rows.setdefault((node, component), len(rows))
                    triplets.append((index, column, float(force[component])))
    targets = [rows.setdefault(key, len(rows)) for key in nodal_keys]
    proxy = np.zeros((len(rows), pressure.shape[1]))
    weights = np.zeros((len(rows), len(keys)))
    np.add.at(weights, ([t[0] for t in triplets], [t[1] for t in triplets]),
              [t[2] for t in triplets])
    proxy = weights @ pressure
    for position, target in enumerate(targets):
        proxy[target] += nodal[position]
    return proxy


def test_sparse_projection_matches_the_dense_oracle():
    _, mesh, ids = _plate_project(0.25)
    rng = np.random.default_rng(3)
    pressure = rng.normal(size=(len(ids), 7))
    node = sorted(mesh.nodes)[0]
    nodal_keys = [(node, 0), (node, 2)]
    nodal = rng.normal(size=(2, 7))
    got = run._nodal_force_proxy(mesh, list(ids), pressure, nodal_keys, nodal)
    want = _dense_oracle(mesh, ids, pressure, nodal_keys, nodal)
    assert isinstance(got, np.ndarray) and got.shape == want.shape
    np.testing.assert_allclose(got, want, rtol=1e-13, atol=1e-13 * np.abs(want).max())


def test_sparse_projection_without_pressure_triplets_is_the_nodal_part_only():
    _, mesh, _ = _plate_project()
    node = sorted(mesh.nodes)[0]
    nodal = np.arange(1.0, 4.0).reshape(1, 3)
    got = run._nodal_force_proxy(mesh, [], np.zeros((0, 3)), [(node, 1)], nodal)
    np.testing.assert_array_equal(got, nodal)


# ----------------------------------------------------------------------
# supported-history gate
# ----------------------------------------------------------------------
def _wrapper(inner):
    wrapper = object.__new__(SourceAwarePressureHistory)
    object.__setattr__(wrapper, "mapped_history", inner)
    return wrapper


def test_exact_mapped_histories_and_their_exact_wrapper_are_supported():
    _, mesh, ids = _plate_project()
    history = _history(mesh, ids)
    assert builder._supported_mapped_history(history)
    assert builder._supported_mapped_history(_wrapper(history))


def test_subclasses_and_foreign_providers_are_not_supported():
    _, mesh, ids = _plate_project()

    class Derived(SpectralMappedPressureHistory):
        pass

    derived = object.__new__(Derived)
    assert not builder._supported_mapped_history(derived)
    assert not builder._supported_mapped_history(_wrapper(derived))
    assert not builder._supported_mapped_history(object())
    assert not builder._supported_mapped_history(_wrapper(object()))

    class DerivedWrapper(SourceAwarePressureHistory):
        pass

    inner = _history(mesh, ids)
    subclass_wrapper = object.__new__(DerivedWrapper)
    object.__setattr__(subclass_wrapper, "mapped_history", inner)
    assert not builder._supported_mapped_history(subclass_wrapper)


# ----------------------------------------------------------------------
# context reuse is exact
# ----------------------------------------------------------------------
def test_context_results_are_identical_to_the_uncached_path_and_reused():
    project, mesh, children, _ = _series_project(5)
    context = _context(project, mesh)
    for case in children:
        plain = builder._build_load_case(project, mesh, case)
        reused = builder._build_load_case(project, mesh, case, _load_context=context)
        assert plain.pressure_loads == reused.pressure_loads  # exact, same arithmetic
    context.verify_unchanged()
    assert context.reuses["mesh_hash"] == len(children) - 1
    assert context.reuses["wet_elements"] == len(children) - 1


def test_the_mesh_hash_is_computed_once_per_batch(monkeypatch):
    project, mesh, children, _ = _series_project(6)
    context = _context(project, mesh)
    calls = []
    real = builder._BatchLoadContext._content_hash
    monkeypatch.setattr(
        builder._BatchLoadContext, "_content_hash",
        staticmethod(lambda value: calls.append(1) or real(value)),
    )
    for case in children:
        builder._build_load_case(project, mesh, case, _load_context=context)
    assert len(calls) == 1
    context.verify_unchanged()
    assert len(calls) == 2  # exactly one final re-verification


# ----------------------------------------------------------------------
# refusals
# ----------------------------------------------------------------------
def test_a_mesh_changed_during_the_batch_is_refused_at_the_end():
    project, mesh, children, _ = _series_project()
    context = _context(project, mesh)
    builder._build_load_case(project, mesh, children[0], _load_context=context)
    node = sorted(mesh.nodes)[0]
    mesh.nodes[node] = np.asarray(mesh.nodes[node]) + np.array([0.0, 0.0, 1e-3])
    with pytest.raises(ProjectError, match="mesh changed"):
        context.verify_unchanged()


def test_a_stale_mesh_before_the_batch_is_refused_by_the_load_binding():
    project, mesh, children, _ = _series_project()
    node = sorted(mesh.nodes)[0]
    mesh.nodes[node] = np.asarray(mesh.nodes[node]) + np.array([0.0, 0.0, 1e-3])
    context = _context(project, mesh)
    with pytest.raises(ProjectError):
        builder._build_load_case(project, mesh, children[0], _load_context=context)


def test_a_geometry_change_is_refused():
    project, mesh, children, _ = _series_project()
    context = _context(project, mesh)
    context._token = (0, "other-model", -1)  # as if the geometry owner moved on
    with pytest.raises(ProjectError, match="geometry changed"):
        context.verify_unchanged()


def test_untrusted_providers_never_use_or_keep_cached_values():
    project, mesh, children, history = _series_project()
    context = _context(project, mesh)
    builder._build_load_case(project, mesh, children[0], _load_context=context)
    assert context._hash is not None and context._wet and context._trusted
    expression = WetPressure(expression="1000.0")
    context.note_load(expression)
    assert not context._trusted
    assert context._hash is None and not context._wet and not context._effective
    # while untrusted the mesh hash is recomputed, not served from a cache
    first = context.mesh_content_hash(mesh)
    assert context._hash is None
    assert first == canonical_hash(mesh_to_dict(mesh))
    context.note_load(WetPressure(history=history, mapping_key="element"))
    assert context._trusted


def test_a_subclass_history_load_is_untrusted():
    _, mesh, ids = _plate_project()

    class Derived(SpectralMappedPressureHistory):
        pass

    derived = object.__new__(Derived)
    context = _context(*_plate_project()[:2])
    fake = SimpleNamespace(history=derived)
    # note_load requires an exact WetPressure: a lookalike is untrusted too
    context.note_load(fake)
    assert not context._trusted


@pytest.mark.parametrize("target", ["producing_mesh", "remapped_mesh"])
def test_real_source_aware_history_through_the_context_matches_the_uncached_path(target):
    """Actual SourceAwarePressureHistory (retained source bank), not a stub."""
    from test_hydro_pressure_glyphs import project_and_meshes, synthetic_history

    project, old, new = project_and_meshes()
    history, _bank = synthetic_history(project, old)
    assert type(history) is SourceAwarePressureHistory
    assert builder._supported_mapped_history(history)
    owner = project.add_load_series("wave", history.times)
    owner.wet_pressures.append(WetPressure(history=history, mapping_key="element", name="wave"))
    mesh = old if target == "producing_mesh" else new
    children = project.series_children("wave")
    context = _context(project, mesh)
    for case in children:
        plain = builder._build_load_case(project, mesh, case)
        reused = builder._build_load_case(project, mesh, case, _load_context=context)
        assert plain.pressure_loads and plain.pressure_loads == reused.pressure_loads
    context.verify_unchanged()
    assert context.reuses["mesh_hash"] == len(children) - 1
    assert context.reuses["remapped_history"] == len(children) - 1
    assert context.reuses["wet_elements"] == len(children) - 1


def test_repeated_contexts_do_not_accumulate_case_state():
    project, mesh, children, _ = _series_project()
    sizes = []
    for _ in range(3):
        context = _context(project, mesh)
        for case in children:
            builder._build_load_case(project, mesh, case, _load_context=context)
        sizes.append((len(context._wet), len(context._effective)))
    assert sizes[0] == sizes[1] == sizes[2]
