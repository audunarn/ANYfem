"""M30: the prepared load context keeps its wet-element sets across cases (fixed
owned geometry) with their bytes reserved, and clears everything after a failed
case. M31: it keeps per-wet-set geometry and fluid sides. M31b (review): the kept
entries are keyed by surface identity and checked against the surfaces' content,
the fluid sides follow a rebound fluid-side function, a reduced reserve drops them,
and public records own fresh writable arrays."""

import numpy as np
import pytest

from anyfem.model import ProjectError
from anyfem.solve import prepare_linear_static_evaluator
from anyfem.solve import prepared_static as adapter
from test_prepared_static_adapter import solver_spy  # noqa: F401  (fixture)
from test_static_batch_envelope_factors import _project


def _surface(surface_id, **changes):
    """A plain wet-surface record (M31d: other records are never cached)."""
    from anyfem.model import WetSurface
    from anygeometry.entities import EntityRef
    fields = dict(id=surface_id, name='wet surface', ref=EntityRef('face', 1), fluid_side='positive_normal')
    fields.update(changes)
    return WetSurface(**fields)


def _evaluator():
    from anysolver import ResourceConfig
    project, series, points, _ = _project()
    series.add_point_load(project.point(points[0]), force=(2, 0, 0))
    mesh = project.generate_mesh(1.0)
    name = project.series_children("series")[0].name
    evaluator = prepare_linear_static_evaluator(project, mesh=mesh,
                                                resource_config=ResourceConfig(memory_limit_bytes=10_000_000))
    return project, name, evaluator


def test_wet_elements_kept_across_cases_with_reserved_bytes(solver_spy, monkeypatch):
    calls = []

    def fake_wet(project, mesh, selected):
        calls.append(tuple(surface.id for surface in selected))
        return ({1: 'surface'}, {7: [1]})
    monkeypatch.setattr(adapter.builder, '_wet_elements', fake_wet)
    _project_, name, evaluator = _evaluator()
    with evaluator:
        context = evaluator._context
        assert context.cache_limit_bytes > 0
        selected = [_surface('s1')]
        first = context.wet_elements(selected)
        assert context._persistent_bytes > 0 and context._cache_bytes == context._persistent_bytes
        context.clear_case_caches()
        assert context.wet_elements(selected) is first and calls == [('s1',)]
        assert context._cache_bytes == context._persistent_bytes
        list(evaluator.iter_batches([name]))               # a successful case keeps the set
        assert context.wet_elements(selected) is first and calls == [('s1',)]
        assert context._cache_bytes == context._persistent_bytes
        assert not any((context._targets, context._nodes, context._elements, context._centroids))
        context.clear_all_caches()
        assert not context._wet and context._cache_bytes == 0 and context._persistent_bytes == 0


def test_failed_case_clears_the_kept_wet_elements(solver_spy, monkeypatch):
    monkeypatch.setattr(adapter.builder, '_wet_elements', lambda project, mesh, selected: ({}, {}))
    project, _name, evaluator = _evaluator()
    with evaluator:
        context = evaluator._context
        context.wet_elements([_surface('s1')])
        assert context._wet and context._persistent_bytes > 0
        external = project.load_case("invalid")
        external.add_wet_pressure(expression="undefined_symbol")
        with pytest.raises(ProjectError):
            list(evaluator.iter_batches([external]))
        assert not context._wet and context._cache_bytes == 0 and context._persistent_bytes == 0


# --------------------------------------------------------------------------- M31

def _wet_wall_cases(fluid_side="away_from_point", interior_point=(1.0, 5.0, 1.0)):
    from anyfem.model import WetSurface
    from test_wet_pressure import _wall
    project, face = _wall()
    project.add_wet_surface(WetSurface(ref=project.face(face), fluid_side=fluid_side,
                                       interior_point=interior_point))
    cases = []
    for index, draft in enumerate((0.0, 1.0, 1.75, 2.5)):
        case = project.load_case(f"draft{index}")
        case.set_parameter("rho", 1025.0)
        case.set_parameter("draft", draft)
        case.add_wet_pressure(expression="rho * g * max(0, draft - z)")
        case.add_wet_pressure(value=250.0, scale=-0.5)
        cases.append(case)
    return project, cases


def _same_pressures(left, right):
    assert [item.element_id for item in left] == [item.element_id for item in right]
    for a, b in zip(left, right):
        assert float(a.pressure).hex() == float(b.pressure).hex()
        assert np.array_equal(a.centroid, b.centroid) and np.array_equal(a.normal, b.normal)


def test_prepared_wet_pressures_equal_the_uncached_path(solver_spy):
    from anysolver import ResourceConfig
    from anyfem.solve import build as builder
    project, cases = _wet_wall_cases()
    mesh = project.generate_mesh(0.5)
    with prepare_linear_static_evaluator(project, mesh=mesh,
                                         resource_config=ResourceConfig(memory_limit_bytes=50_000_000)) as evaluator:
        context = evaluator._context
        owned_project, owned_mesh = context.built.project, context.built.mesh
        geometry_calls = []
        original = context.centroid_normal
        context.centroid_normal = lambda element_id: (geometry_calls.append(element_id), original(element_id))[1]
        for _round in range(2):
            for case in cases:
                expected = builder.wet_element_pressures(owned_project, owned_mesh, case)
                context.clear_case_caches()
                got = builder.wet_element_pressures(owned_project, owned_mesh, case, _load_context=context)
                _same_pressures(got, expected)
        assert len(context._wet_geometry) == 1 and len(geometry_calls) == len(set(geometry_calls))
        entry, = context._wet_geometry.values()
        signs = entry["signs"]
        assert signs and set(signs) <= set(int(e) for e in entry["element_ids"])
        assert context._persistent_bytes > 0 and context._cache_bytes == context._persistent_bytes
        context.clear_all_caches()
        assert not context._wet_geometry and context._persistent_bytes == 0


def test_fluid_side_is_evaluated_only_for_loaded_elements(solver_spy):
    from anysolver import ResourceConfig
    from anyfem.solve import build as builder
    project, cases = _wet_wall_cases()
    mesh = project.generate_mesh(0.5)
    with prepare_linear_static_evaluator(project, mesh=mesh,
                                         resource_config=ResourceConfig(memory_limit_bytes=50_000_000)) as evaluator:
        context = evaluator._context
        owned_project, owned_mesh = context.built.project, context.built.mesh
        dry = project.load_case("dry")
        dry.set_parameter("rho", 1025.0)
        dry.set_parameter("draft", -1.0)
        dry.add_wet_pressure(expression="rho * g * max(0, draft - z)")
        assert builder.wet_element_pressures(owned_project, owned_mesh, dry, _load_context=context) == []
        entry, = context._wet_geometry.values()
        assert entry["signs"] == {}                                   # a dry case evaluates no fluid side
        wet = cases[1]
        loaded = [item.element_id for item in builder.wet_element_pressures(owned_project, owned_mesh, wet)]
        builder.wet_element_pressures(owned_project, owned_mesh, wet, _load_context=context)
        assert sorted(entry["signs"]) == sorted(loaded)               # only loaded elements
        _poison(entry)
        got = builder.wet_element_pressures(owned_project, owned_mesh, wet, _load_context=context)
        assert _signed(got) == _negated(builder.wet_element_pressures(owned_project, owned_mesh, wet))

# --------------------------------------------------------------------------- M31b

def test_kept_wet_sets_are_keyed_by_surface_identity_and_content(solver_spy, monkeypatch):
    calls = []

    def fake_wet(project, mesh, selected):
        calls.append(tuple(surface.fluid_side for surface in selected))
        return ({1: selected[0]}, {7: [1]})
    monkeypatch.setattr(adapter.builder, '_wet_elements', fake_wet)
    _project_, _name, evaluator = _evaluator()
    with evaluator:
        context = evaluator._context
        one, other = _surface('s1'), _surface('s1', fluid_side='negative_normal')
        first = context.wet_elements([one])
        assert context.wet_elements([other])[0][1] is other          # same id, another surface
        assert context.wet_elements([one]) is first and len(calls) == 2
        case_bytes = 1000
        context._cache_bytes += case_bytes                              # a case entry's bytes
        object.__setattr__(one, 'name', 'renamed')
        again = context.wet_elements([one])                              # content changed: rebuilt
        assert again is not first and len(calls) == 3
        assert context._cache_bytes == context._persistent_bytes + case_bytes
        context.clear_case_caches()
        assert context._cache_bytes == context._persistent_bytes


def _prepared(project, mesh):
    from anysolver import ResourceConfig
    return prepare_linear_static_evaluator(project, mesh=mesh,
                                           resource_config=ResourceConfig(memory_limit_bytes=50_000_000))


def _signed(items):
    return [(item.element_id, float(item.pressure).hex()) for item in items]


def test_changed_wet_surface_content_is_seen(solver_spy):
    from anyfem.solve import build as builder
    project, cases = _wet_wall_cases()
    mesh = project.generate_mesh(0.5)
    with _prepared(project, mesh) as evaluator:
        context = evaluator._context
        owned_project, owned_mesh = context.built.project, context.built.mesh
        before = builder.wet_element_pressures(owned_project, owned_mesh, cases[2], _load_context=context)
        surface, = owned_project.wet_surfaces
        surface.interior_point[1] = -5.0                                 # the fluid moves to the other side
        expected = builder.wet_element_pressures(owned_project, owned_mesh, cases[2])
        got = builder.wet_element_pressures(owned_project, owned_mesh, cases[2], _load_context=context)
        _same_pressures(got, expected)
        assert _signed(got) != _signed(before)
        object.__setattr__(surface, 'fluid_side', 'negative_normal')
        object.__setattr__(surface, 'interior_point', None)
        expected = builder.wet_element_pressures(owned_project, owned_mesh, cases[2])
        _same_pressures(builder.wet_element_pressures(owned_project, owned_mesh, cases[2], _load_context=context),
                        expected)


@pytest.mark.parametrize('where', ['build', 'wetfield'])
def test_rebound_fluid_side_function_is_seen(solver_spy, monkeypatch, where):
    from anyfem.model import wetfield
    from anyfem.solve import build as builder
    project, cases = _wet_wall_cases()
    mesh = project.generate_mesh(0.5)
    with _prepared(project, mesh) as evaluator:
        context = evaluator._context
        owned_project, owned_mesh = context.built.project, context.built.mesh
        before = builder.wet_element_pressures(owned_project, owned_mesh, cases[2], _load_context=context)
        if where == 'build':
            original = builder.fluid_sign
            monkeypatch.setattr(builder, 'fluid_sign', lambda *args: -original(*args))
        else:
            original = wetfield._fluid_sign
            monkeypatch.setattr(wetfield, '_fluid_sign', lambda *args, **kwargs: -original(*args, **kwargs))
        expected = builder.wet_element_pressures(owned_project, owned_mesh, cases[2])
        got = builder.wet_element_pressures(owned_project, owned_mesh, cases[2], _load_context=context)
        _same_pressures(got, expected)
        assert _signed(got) != _signed(before)


def test_public_records_own_fresh_writable_arrays(solver_spy):
    from anyfem.solve import build as builder
    project, cases = _wet_wall_cases()
    mesh = project.generate_mesh(0.5)
    with _prepared(project, mesh) as evaluator:
        context = evaluator._context
        owned_project, owned_mesh = context.built.project, context.built.mesh
        first = builder.wet_element_pressures(owned_project, owned_mesh, cases[2], _load_context=context)
        entry, = context._wet_geometry.values()
        kept = [array for pair in entry["geometry"].values() for array in pair] + [entry["centroids"]]
        for item in first:
            for array in (item.centroid, item.normal):
                assert array.flags.writeable and not any(np.shares_memory(array, other) for other in kept)
            item.centroid[:] = 99.0
            item.normal[:] = 0.0
        expected = builder.wet_element_pressures(owned_project, owned_mesh, cases[2])
        _same_pressures(builder.wet_element_pressures(owned_project, owned_mesh, cases[2], _load_context=context),
                        expected)


def test_solver_loads_equal_the_uncached_path(solver_spy):
    from anysolver import LoadCase as SolverLoadCase
    from anyfem.solve import build as builder
    project, cases = _wet_wall_cases()
    mesh = project.generate_mesh(0.5)
    with _prepared(project, mesh) as evaluator:
        context = evaluator._context
        owned_project, owned_mesh = context.built.project, context.built.mesh
        for case in cases + cases:
            made, expected = SolverLoadCase('made'), SolverLoadCase('expected')
            expected.pressure_loads[0] = 1.0
            made.pressure_loads[0] = 1.0
            builder._accumulate_wet_pressures(owned_project, owned_mesh, case, 0.75, expected)
            builder._accumulate_wet_pressures(owned_project, owned_mesh, case, 0.75, made, _load_context=context)
            assert list(made.pressure_loads) == list(expected.pressure_loads)
            assert [float(v).hex() for v in made.pressure_loads.values()] == \
                [float(v).hex() for v in expected.pressure_loads.values()]


def test_reduced_reserve_drops_the_kept_entries(solver_spy):
    from anyfem.solve import build as builder
    project, cases = _wet_wall_cases()
    mesh = project.generate_mesh(0.5)
    with _prepared(project, mesh) as evaluator:
        context = evaluator._context
        owned_project, owned_mesh = context.built.project, context.built.mesh
        builder.wet_element_pressures(owned_project, owned_mesh, cases[2], _load_context=context)
        assert context._wet and context._wet_geometry and context._wet_kept
        context.cache_limit_bytes = context._persistent_bytes - 1
        context.clear_case_caches()
        assert not (context._wet or context._wet_geometry or context._wet_kept)
        assert context._persistent_bytes == 0 and context._cache_bytes == 0
        expected = builder.wet_element_pressures(owned_project, owned_mesh, cases[2])
        _same_pressures(builder.wet_element_pressures(owned_project, owned_mesh, cases[2], _load_context=context),
                        expected)
        assert context._cache_bytes <= context.cache_limit_bytes


def _poison(entry):
    """Negate every cached fluid side: a reuse then shows as negated pressures."""
    for key in list(entry["signs"]):
        entry["signs"][key] = -entry["signs"][key]


def _negated(items):
    return [(item.element_id, float(-item.pressure).hex()) for item in items]


def test_fluid_sides_are_reused_only_where_computing_them_cannot_warn(solver_spy, monkeypatch):
    from anyfem.solve import build as builder
    project, cases = _wet_wall_cases()
    mesh = project.generate_mesh(0.5)
    with _prepared(project, mesh) as evaluator:
        context = evaluator._context
        owned_project, owned_mesh = context.built.project, context.built.mesh
        case = cases[2]

        def prepared():
            return _signed(builder.wet_element_pressures(owned_project, owned_mesh, case, _load_context=context))

        def plain():
            return builder.wet_element_pressures(owned_project, owned_mesh, case)
        with np.errstate(over='raise'):                              # not NumPy's default error state
            assert prepared() == _signed(plain())
        entry, = context._wet_geometry.values()
        assert entry["signs"] == {}                                   # nothing kept under that state
        assert prepared() == _signed(plain()) and entry["signs"]      # default state: kept
        _poison(entry)
        assert prepared() == _negated(plain())                        # ... and reused
        with np.errstate(under='warn'):
            assert prepared() == _signed(plain())                     # not reused
        standard = builder._standard_float_environment
        monkeypatch.setattr(builder, '_standard_float_environment', lambda: False)
        assert prepared() == _signed(plain())                         # e.g. directed rounding: not reused
        monkeypatch.setattr(builder, '_standard_float_environment', standard)
        assert prepared() == _negated(plain())
        calls = []
        original = builder.fluid_sign
        monkeypatch.setattr(builder, 'fluid_sign', lambda *args: (calls.append(1), original(*args))[1])
        counts = []
        for run in (prepared, prepared, plain):                       # a replaced function: called every time
            calls.clear()
            run()
            counts.append(len(calls))
        assert counts[0] == counts[1] == counts[2] > 0
        assert prepared() == _signed(plain())
        monkeypatch.setattr(builder, 'fluid_sign', original)
        surface, = owned_project.wet_surfaces
        surface.interior_point[1] = 1e200                             # not bounded: computed every time
        assert prepared() == _signed(plain())
        _poison(entry)
        assert prepared() == _signed(plain())


def test_changes_made_by_the_pressure_source_are_seen_in_the_same_request(solver_spy, monkeypatch):
    # M31c (review round 2): the pressure source may run caller code; whatever it
    # changes is seen before any fluid side is reused in that request.
    from anyfem.solve import build as builder
    project, cases = _wet_wall_cases()
    mesh = project.generate_mesh(0.5)
    original_field = builder.field_pressure
    original_sign = builder.fluid_sign
    with _prepared(project, mesh) as evaluator:
        context = evaluator._context
        owned_project, owned_mesh = context.built.project, context.built.mesh
        surface, = owned_project.wet_surfaces
        case = cases[2]
        warm = builder.wet_element_pressures(owned_project, owned_mesh, case, _load_context=context)

        def moving_source(case, load, centroids):
            surface.interior_point[1] = -5.0                          # the fluid moves to the other side
            return original_field(case, load, centroids)
        monkeypatch.setattr(builder, 'field_pressure', moving_source)
        got = builder.wet_element_pressures(owned_project, owned_mesh, case, _load_context=context)
        _same_pressures(got, builder.wet_element_pressures(owned_project, owned_mesh, case))
        assert _signed(got) != _signed(warm)

        def rebinding_source(case, load, centroids):
            monkeypatch.setattr(builder, 'fluid_sign', lambda *args: -original_sign(*args))
            return original_field(case, load, centroids)
        monkeypatch.setattr(builder, 'field_pressure', original_field)
        reference = builder.wet_element_pressures(owned_project, owned_mesh, case)
        builder.wet_element_pressures(owned_project, owned_mesh, case, _load_context=context)   # sides kept
        monkeypatch.setattr(builder, 'field_pressure', rebinding_source)
        got = builder.wet_element_pressures(owned_project, owned_mesh, case, _load_context=context)
        assert _signed(got) == _negated(reference)
        monkeypatch.setattr(builder, 'fluid_sign', original_sign)
        assert _signed(got) == _signed(builder.wet_element_pressures(owned_project, owned_mesh, case))


def test_kept_metadata_is_charged_to_the_reserve(solver_spy):
    # M31c (review round 2): each kept entry is charged with the kept selection and
    # its content fingerprint (and the geometry record with its own fields).
    from anyfem.solve.prepared_static import _input_storage_bytes
    project, cases = _wet_wall_cases()
    mesh = project.generate_mesh(0.5)
    with _prepared(project, mesh) as evaluator:
        context = evaluator._context
        selected = list(context.built.project.wet_surfaces)
        context.clear_all_caches()
        assert context._persistent_bytes == 0
        value = context.wet_elements(selected)
        (key, (kept_selection, state)), = context._wet_kept.items()
        kept = (128 + 64 + 8 * len(kept_selection) + _input_storage_bytes(state)
                + _input_storage_bytes(tuple(kept_selection)))          # M31i: the surfaces themselves
        assert context._persistent_bytes == 128 + _input_storage_bytes((key, value)) + kept
        before = context._persistent_bytes
        entry = context.wet_geometry(selected, value[0])
        ids = entry["element_ids"]
        assert context._persistent_bytes - before == (
            128 + _input_storage_bytes((key, ids, entry["centroids"], entry["geometry"])) + 160 * len(ids)
            + _input_storage_bytes(entry) + _input_storage_bytes(state) + kept)
        assert context._cache_bytes == context._persistent_bytes <= context.cache_limit_bytes


def test_fluid_sides_of_unusual_surface_records_are_computed_every_time(solver_spy):
    # M31c: a str subclass as the name runs code each time a side is computed (the
    # label is built per call), so its sides are not reused.
    from anyfem.solve import build as builder
    project, cases = _wet_wall_cases()
    mesh = project.generate_mesh(0.5)
    calls = []

    class CountingName(str):
        def __repr__(self):
            calls.append(1)
            return str.__repr__(self)
    with _prepared(project, mesh) as evaluator:
        context = evaluator._context
        owned_project, owned_mesh = context.built.project, context.built.mesh
        surface, = owned_project.wet_surfaces
        object.__setattr__(surface, 'name', CountingName('hull'))
        counts = []
        for load_context in (context, context, None):
            calls.clear()
            builder.wet_element_pressures(owned_project, owned_mesh, cases[2], _load_context=load_context)
            counts.append(len(calls))
        assert counts[0] == counts[1] == counts[2] > 0


def test_changed_code_of_the_fluid_side_function_is_seen(solver_spy, monkeypatch):
    from anyfem.model import wetfield
    from anyfem.solve import build as builder
    project, cases = _wet_wall_cases()
    mesh = project.generate_mesh(0.5)
    with _prepared(project, mesh) as evaluator:
        context = evaluator._context
        owned_project, owned_mesh = context.built.project, context.built.mesh
        before = builder.wet_element_pressures(owned_project, owned_mesh, cases[2], _load_context=context)

        def always_along(fluid_side, interior_point, centroid, normal, *, label="surface"):
            return 1.0
        monkeypatch.setattr(wetfield._fluid_sign, '__code__', always_along.__code__)
        expected = builder.wet_element_pressures(owned_project, owned_mesh, cases[2])
        got = builder.wet_element_pressures(owned_project, owned_mesh, cases[2], _load_context=context)
        _same_pressures(got, expected)
        assert _signed(got) != _signed(before)


# --------------------------------------------------------------------------- M31d

def _outcome(function):
    import warnings
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        try:
            made = ('value', _signed(function()))
        except Exception as error:  # noqa: BLE001 - compared, not handled
            made = ('raise', type(error), str(error))
    return made + ([(item.category, str(item.message)) for item in caught],)


def test_unusual_surface_records_run_no_new_code_and_are_not_cached(solver_spy):
    # M31d (review round 3): a str subclass name whose __str__ raises; the original
    # path formats it only with !r. The prepared path must not call str() on it.
    from anyfem.solve import build as builder
    project, cases = _wet_wall_cases()
    mesh = project.generate_mesh(0.5)

    class RaisingStr(str):
        def __str__(self):
            raise RuntimeError('str() of the name')
    with _prepared(project, mesh) as evaluator:
        context = evaluator._context
        owned_project, owned_mesh = context.built.project, context.built.mesh
        surface, = owned_project.wet_surfaces
        object.__setattr__(surface, 'name', RaisingStr('hull'))
        for case in cases:
            got = _outcome(lambda: builder.wet_element_pressures(owned_project, owned_mesh, case,
                                                                 _load_context=context))
            assert got == _outcome(lambda: builder.wet_element_pressures(owned_project, owned_mesh, case))
            assert got[0] == 'value'
        assert not context._wet and not context._wet_geometry and not context._wet_kept


@pytest.mark.parametrize('fluid_side, point', [('positive_normal', None), ('away_from_point', (1.0, 5.0, 1.0))])
def test_a_record_changed_by_the_pressure_source_is_neither_reused_nor_retained(solver_spy, monkeypatch,
                                                                                fluid_side, point):
    # M31d (review round 3): a large interior point set during the pressure source.
    from anyfem.solve import build as builder
    project, cases = _wet_wall_cases(fluid_side, point)
    mesh = project.generate_mesh(0.5)
    original_field = builder.field_pressure
    with _prepared(project, mesh) as evaluator:
        context = evaluator._context
        owned_project, owned_mesh = context.built.project, context.built.mesh
        surface, = owned_project.wet_surfaces
        case = cases[2]
        builder.wet_element_pressures(owned_project, owned_mesh, case, _load_context=context)
        entry, = context._wet_geometry.values()
        kept_state, kept_bytes = entry["state"], context._persistent_bytes
        _poison(entry)

        def enlarging_source(case, load, centroids):
            object.__setattr__(surface, 'interior_point', np.full(100_000, 2.0))
            return original_field(case, load, centroids)
        monkeypatch.setattr(builder, 'field_pressure', enlarging_source)
        got = _outcome(lambda: builder.wet_element_pressures(owned_project, owned_mesh, case, _load_context=context))
        assert got == _outcome(lambda: builder.wet_element_pressures(owned_project, owned_mesh, case))
        assert entry["state"] is kept_state and context._persistent_bytes == kept_bytes
        context.clear_case_caches()                                   # M31f: the request boundary
        assert not (context._wet or context._wet_geometry or context._wet_kept)
        assert context._persistent_bytes == 0 == context._cache_bytes


def test_replaced_guard_functions_turn_reuse_off(solver_spy, monkeypatch):
    from anyfem.solve import build as builder
    project, cases = _wet_wall_cases()
    mesh = project.generate_mesh(0.5)
    with _prepared(project, mesh) as evaluator:
        context = evaluator._context
        owned_project, owned_mesh = context.built.project, context.built.mesh
        case = cases[2]
        builder.wet_element_pressures(owned_project, owned_mesh, case, _load_context=context)
        entry, = context._wet_geometry.values()
        _poison(entry)
        plain = builder.wet_element_pressures(owned_project, owned_mesh, case)
        prepared = lambda: _signed(builder.wet_element_pressures(owned_project, owned_mesh, case,
                                                                 _load_context=context))
        assert prepared() == _negated(plain)                          # reused
        original = np.geterr
        monkeypatch.setattr(np, 'geterr', lambda: original())
        assert prepared() == _signed(plain)                           # not reused
        monkeypatch.setattr(np, 'geterr', original)
        assert prepared() == _negated(plain)


def test_a_chain_installed_before_import_is_not_trusted(monkeypatch):
    # M31d (review round 3): only the libraries' own functions form the trusted chain.
    from anyfem.model import wetfield
    from anyfem.solve import build as builder
    assert builder._capture_sign_chain() is not None
    original = wetfield._fluid_sign
    flag = [False]

    def wrapper(*args, **kwargs):
        return -original(*args, **kwargs) if flag[0] else original(*args, **kwargs)
    monkeypatch.setattr(wetfield, '_fluid_sign', wrapper)                 # closure
    assert builder._capture_sign_chain() is None

    def lookalike(fluid_side, interior_point, centroid, normal, *, label="surface"):
        return 1.0
    monkeypatch.setattr(wetfield, '_fluid_sign', lookalike)               # defined elsewhere
    assert builder._capture_sign_chain() is None
    monkeypatch.setattr(wetfield, '_fluid_sign', original)
    monkeypatch.setattr(builder, 'fluid_sign', lambda *args: original(*args))
    assert builder._capture_sign_chain() is None


# --------------------------------------------------------------------------- M31e

def test_a_replaced_fluid_side_callable_is_not_inspected(solver_spy, monkeypatch):
    # M31e (review round 4): a replacement is rejected by identity before any of its
    # attributes is read (reading __code__ here would raise).
    from anyfem.solve import build as builder
    project, cases = _wet_wall_cases()
    mesh = project.generate_mesh(0.5)

    class Sign:
        def __init__(self, function):
            self.function = function

        def __call__(self, *args):
            return self.function(*args)

        @property
        def __code__(self):
            raise RuntimeError('replacement inspected')
    with _prepared(project, mesh) as evaluator:
        context = evaluator._context
        owned_project, owned_mesh = context.built.project, context.built.mesh
        builder.wet_element_pressures(owned_project, owned_mesh, cases[2], _load_context=context)
        monkeypatch.setattr(builder, 'fluid_sign', Sign(builder.fluid_sign))
        for case in cases:
            got = _outcome(lambda: builder.wet_element_pressures(owned_project, owned_mesh, case,
                                                                 _load_context=context))
            assert got == _outcome(lambda: builder.wet_element_pressures(owned_project, owned_mesh, case))
            assert got[0] == 'value'


def test_the_pressure_source_gets_the_same_kind_of_centroid_array(solver_spy, monkeypatch):
    # M31e (review round 4): the pressure source may run caller code; it receives an
    # (n, 3) view of a new array on both paths. (field_pressure is wrapped only as a probe.)
    from anyfem.solve import build as builder
    project, cases = _wet_wall_cases()
    mesh = project.generate_mesh(0.5)
    original = builder.field_pressure
    seen = []

    def probe(case, load, centroids):
        base = centroids.base
        seen.append((type(centroids), centroids.dtype.str, centroids.shape, centroids.flags.c_contiguous,
                     centroids.flags.writeable, centroids.flags.owndata, type(base),
                     None if base is None else (base.shape, base.flags.owndata, base.flags.writeable)))
        return original(case, load, centroids)
    monkeypatch.setattr(builder, 'field_pressure', probe)
    with _prepared(project, mesh) as evaluator:
        context = evaluator._context
        owned_project, owned_mesh = context.built.project, context.built.mesh
        for case in cases + cases:
            seen.clear()
            builder.wet_element_pressures(owned_project, owned_mesh, case, _load_context=context)
            prepared = list(seen)
            seen.clear()
            builder.wet_element_pressures(owned_project, owned_mesh, case)
            assert prepared == seen and prepared
        assert context._wet_geometry                                   # the cached path was taken


# --------------------------------------------------------------------------- M31f

def test_a_changed_and_replaced_surface_is_not_kept_past_the_request_boundary(solver_spy):
    # M31f (review round 5): surface A is enlarged in place and replaced by B; at the
    # next request boundary nothing of A is kept (checked with a weak reference, not
    # with the charging formula).
    import gc
    import weakref
    from anyfem.model import WetSurface
    from anyfem.solve import build as builder
    project, cases = _wet_wall_cases()
    mesh = project.generate_mesh(0.5)
    with _prepared(project, mesh) as evaluator:
        context = evaluator._context
        owned_project, owned_mesh = context.built.project, context.built.mesh
        builder.wet_element_pressures(owned_project, owned_mesh, cases[2], _load_context=context)
        old, = owned_project.wet_surfaces
        gone = weakref.ref(old)
        object.__setattr__(old, 'name', 'x' * 2_000_000)                  # enlarged in place
        new = WetSurface(ref=old.ref, name='hull', region=old.region, fluid_side=old.fluid_side,
                         interior_point=old.interior_point.copy(), id=old.id)
        owned_project.wet_surfaces[0] = new
        del old
        context.clear_case_caches()
        gc.collect()
        assert gone() is None and not context._wet_kept and context._persistent_bytes == 0
        expected = builder.wet_element_pressures(owned_project, owned_mesh, cases[2])
        _same_pressures(builder.wet_element_pressures(owned_project, owned_mesh, cases[2], _load_context=context),
                        expected)
        (selected, _state), = context._wet_kept.values()
        assert selected[0] is new


@pytest.mark.parametrize('change', ['view', 'attribute'])
def test_records_the_fingerprint_cannot_bound_are_not_kept(solver_spy, change):
    # M31f: an interior point that is a view of a larger buffer, or an attribute beyond
    # the fields, could hold storage the fingerprint does not see: such a record is not
    # cached, and a kept one is dropped at the next request boundary.
    from anyfem.solve import build as builder
    project, cases = _wet_wall_cases()
    mesh = project.generate_mesh(0.5)
    with _prepared(project, mesh) as evaluator:
        context = evaluator._context
        owned_project, owned_mesh = context.built.project, context.built.mesh
        builder.wet_element_pressures(owned_project, owned_mesh, cases[2], _load_context=context)
        assert context._wet_kept
        surface, = owned_project.wet_surfaces
        if change == 'view':
            large = np.zeros(1_000_000)
            large[:3] = surface.interior_point
            object.__setattr__(surface, 'interior_point', large[:3])
        else:
            object.__setattr__(surface, 'notes', 'y' * 1_000_000)
        assert builder._wet_surface_state(surface) is None
        context.clear_case_caches()
        assert not context._wet_kept and context._persistent_bytes == 0
        got = _outcome(lambda: builder.wet_element_pressures(owned_project, owned_mesh, cases[2], _load_context=context))
        assert got == _outcome(lambda: builder.wet_element_pressures(owned_project, owned_mesh, cases[2]))
        assert not context._wet_kept and not context._wet and not context._wet_geometry


# --------------------------------------------------------------------------- M31g (review round 6)

def test_magnitudes_whose_iteration_runs_code_take_the_original_path(solver_spy):
    # M31g: a pressure record whose field returns an ndarray subclass that changes the
    # surface while it is iterated; sides are then computed as on the original path.
    from dataclasses import dataclass
    from anyfem.model import WetPressure
    from anyfem.solve import build as builder
    project, cases = _wet_wall_cases('positive_normal', None)
    mesh = project.generate_mesh(0.5)
    with _prepared(project, mesh) as evaluator:
        context = evaluator._context
        owned_project, owned_mesh = context.built.project, context.built.mesh
        surface, = owned_project.wet_surfaces

        class Turning(np.ndarray):
            def __iter__(self):
                object.__setattr__(surface, 'fluid_side', 'negative_normal')
                return super().__iter__()

        class Field:
            def evaluate(self, context, centroids):
                return np.full(len(centroids), 100.0).view(Turning)

        @dataclass(frozen=True)
        class CustomPressure(WetPressure):
            @property
            def field(self):
                return Field()
        builder.wet_element_pressures(owned_project, owned_mesh, cases[2], _load_context=context)   # sides kept
        custom = project.load_case("custom")
        custom.wet_pressures.append(CustomPressure(value=1.0))
        got = builder.wet_element_pressures(owned_project, owned_mesh, custom, _load_context=context)
        object.__setattr__(surface, 'fluid_side', 'positive_normal')
        expected = builder.wet_element_pressures(owned_project, owned_mesh, custom)
        _same_pressures(got, expected)
        assert all(item.pressure > 0.0 for item in got)                  # the side after the change


@pytest.mark.parametrize('ending', ['returns', 'raises'])
def test_the_end_of_the_request_iterator_is_a_request_boundary(solver_spy, ending):
    # M31g: a request iterator that changes and replaces a kept surface and then ends
    # (or fails) leaves nothing of it kept (weak reference).
    import gc
    import weakref
    from anyfem.model import WetSurface
    project, cases = _wet_wall_cases()
    mesh = project.generate_mesh(0.5)
    with _prepared(project, mesh) as evaluator:
        context = evaluator._context
        owned_project = context.built.project
        holder = {}

        def requests():
            yield cases[2]
            old, = owned_project.wet_surfaces
            holder['gone'] = weakref.ref(old)
            object.__setattr__(old, 'name', 'x' * 2_000_000)
            owned_project.wet_surfaces[0] = WetSurface(ref=old.ref, name='hull', region=old.region,
                                                       fluid_side=old.fluid_side,
                                                       interior_point=old.interior_point.copy(), id=old.id)
            del old
            if ending == 'raises':
                raise RuntimeError('request source failed')
        if ending == 'raises':
            with pytest.raises(RuntimeError, match='request source failed'):
                list(evaluator.iter_batches(requests()))
        else:
            list(evaluator.iter_batches(requests()))
        gc.collect()
        assert holder['gone']() is None and not context._wet_kept and context._persistent_bytes == 0


def test_a_record_dictionary_enlarged_and_emptied_again_is_not_kept(solver_spy):
    # M31g: the fingerprint includes the sizes of the record dictionaries.
    import sys
    from anyfem.solve import build as builder
    project, cases = _wet_wall_cases()
    mesh = project.generate_mesh(0.5)
    with _prepared(project, mesh) as evaluator:
        context = evaluator._context
        owned_project, owned_mesh = context.built.project, context.built.mesh
        builder.wet_element_pressures(owned_project, owned_mesh, cases[2], _load_context=context)
        surface, = owned_project.wet_surfaces
        before = sys.getsizeof(vars(surface))
        for k in range(500):
            object.__setattr__(surface, f'extra{k}', k)
        for k in range(500):
            object.__delattr__(surface, f'extra{k}')
        assert sys.getsizeof(vars(surface)) > before and builder._wet_surface_state(surface) is not None
        context.clear_case_caches()
        assert not context._wet_kept and context._persistent_bytes == 0


def test_a_damaged_discarded_record_neither_raises_nor_is_kept(solver_spy):
    # M31g: the boundary scan reads records without attribute lookup.
    from anyfem.model import WetSurface
    from anyfem.solve import build as builder
    project, cases = _wet_wall_cases()
    mesh = project.generate_mesh(0.5)
    with _prepared(project, mesh) as evaluator:
        context = evaluator._context
        owned_project, owned_mesh = context.built.project, context.built.mesh
        builder.wet_element_pressures(owned_project, owned_mesh, cases[2], _load_context=context)
        old, = owned_project.wet_surfaces
        owned_project.wet_surfaces[0] = WetSurface(ref=old.ref, name=old.name, region=old.region,
                                                   fluid_side=old.fluid_side,
                                                   interior_point=old.interior_point.copy(), id=old.id)
        del vars(old)['ref']
        assert builder._wet_surface_state(old) is None
        context.clear_case_caches()
        assert not context._wet_kept and context._persistent_bytes == 0
        expected = builder.wet_element_pressures(owned_project, owned_mesh, cases[2])
        _same_pressures(builder.wet_element_pressures(owned_project, owned_mesh, cases[2], _load_context=context),
                        expected)


def test_a_float64_dtype_carrying_metadata_is_not_kept(solver_spy):
    # M31h (review round 7): a dtype with metadata compares equal to float64 but can
    # hold any payload; such a record is not kept past the request boundary.
    from anyfem.solve import build as builder
    project, cases = _wet_wall_cases()
    mesh = project.generate_mesh(0.5)
    with _prepared(project, mesh) as evaluator:
        context = evaluator._context
        owned_project, owned_mesh = context.built.project, context.built.mesh
        builder.wet_element_pressures(owned_project, owned_mesh, cases[2], _load_context=context)
        surface, = owned_project.wet_surfaces
        surface.interior_point.dtype = np.dtype(np.float64, metadata={'payload': b'z' * 1_000_000})
        assert surface.interior_point.dtype == np.float64 and builder._wet_surface_state(surface) is None
        context.clear_case_caches()
        assert not context._wet_kept and context._persistent_bytes == 0
        got = _outcome(lambda: builder.wet_element_pressures(owned_project, owned_mesh, cases[2], _load_context=context))
        assert got == _outcome(lambda: builder.wet_element_pressures(owned_project, owned_mesh, cases[2]))
        assert not context._wet_kept


# --------------------------------------------------------------------------- M31i (review round 8)

def test_a_geometry_entry_charges_the_surfaces_it_keeps(solver_spy):
    # M31i: a surface whose dictionary was enlarged (and emptied) before admission;
    # with a reserve that holds the geometry entry but not the wet-element entry, the
    # surface is either not kept or charged in full; after replacement it is released.
    import gc
    import sys
    import weakref
    from anyfem.model import WetSurface
    from anyfem.solve import build as builder
    project, cases = _wet_wall_cases()
    mesh = project.generate_mesh(0.5)
    with _prepared(project, mesh) as evaluator:
        context = evaluator._context
        owned_project, owned_mesh = context.built.project, context.built.mesh
        builder.wet_element_pressures(owned_project, owned_mesh, cases[2], _load_context=context)
        wet_bytes = next(iter(context._wet.values())) and context._persistent_bytes
        context.clear_all_caches()
        old, = owned_project.wet_surfaces
        for k in range(20_000):
            object.__setattr__(old, f'extra{k}', k)
        for k in range(20_000):
            object.__delattr__(old, f'extra{k}')
        enlarged = sys.getsizeof(vars(old))
        assert enlarged > 4 * wet_bytes
        context.cache_limit_bytes = enlarged // 2              # too small for anything holding the surface
        builder.wet_element_pressures(owned_project, owned_mesh, cases[2], _load_context=context)
        assert not context._wet_kept or context._persistent_bytes >= enlarged
        assert context._cache_bytes <= context.cache_limit_bytes
        gone = weakref.ref(old)
        owned_project.wet_surfaces[0] = WetSurface(ref=old.ref, name=old.name, region=old.region,
                                                   fluid_side=old.fluid_side,
                                                   interior_point=old.interior_point.copy(), id=old.id)
        del old
        context.clear_case_caches()
        gc.collect()
        assert gone() is None


@pytest.mark.parametrize('cleanup', ['succeeds', 'fails'])
def test_a_request_source_close_that_changes_surfaces_is_a_boundary(solver_spy, cleanup):
    # M31i (review round 8 NEEDS): the source's close() changes and replaces a kept
    # surface (and may fail); nothing of the old surface is kept afterwards.
    import gc
    import weakref
    from anyfem.model import WetSurface
    project, cases = _wet_wall_cases()
    mesh = project.generate_mesh(0.5)
    with _prepared(project, mesh) as evaluator:
        context = evaluator._context
        owned_project = context.built.project
        holder = {}

        class Source:
            def __init__(self):
                self.items = iter([cases[2]])

            def __iter__(self):
                return self

            def __next__(self):
                return next(self.items)

            def close(self):
                old, = owned_project.wet_surfaces
                holder['gone'] = weakref.ref(old)
                object.__setattr__(old, 'name', 'x' * 2_000_000)
                owned_project.wet_surfaces[0] = WetSurface(ref=old.ref, name='hull', region=old.region,
                                                           fluid_side=old.fluid_side,
                                                           interior_point=old.interior_point.copy(), id=old.id)
                del old
                if cleanup == 'fails':
                    raise RuntimeError('source cleanup failed')
        if cleanup == 'fails':
            with pytest.raises(RuntimeError, match='source cleanup failed'):
                list(evaluator.iter_batches(Source()))
        else:
            list(evaluator.iter_batches(Source()))
        gc.collect()
        assert holder['gone']() is None and not context._wet_kept and context._persistent_bytes == 0
