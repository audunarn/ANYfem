"""First-use recovery accounting keeps the current graph and legacy behavior."""
from collections.abc import Mapping
from types import MappingProxyType, SimpleNamespace
import sys

import numpy as np
import pytest

from anyfem.post.access import LiveResultAccess, ShapeProvider, retained_recovery_bytes
from anypost.errors import WorkingSetError


def original_bytes(shape, *, retain_projections=False):
    """Frozen oracle; optional keepalive prevents temporary dictionary id reuse."""
    seen = set()
    projections = []
    def size(value):
        if id(value) in seen:
            return 0
        seen.add(id(value))
        if isinstance(value, np.ndarray):
            base = value
            while isinstance(base.base, np.ndarray):
                base = base.base
            if base is not value:
                return sys.getsizeof(value) + size(base)
            return sys.getsizeof(value) + (0 if value.flags.owndata else value.nbytes)
        if isinstance(value, Mapping):
            return sys.getsizeof(value) + sum(size(k) + size(v) for k, v in value.items())
        if isinstance(value, (tuple, list)):
            return sys.getsizeof(value) + sum(size(v) for v in value)
        if hasattr(value, '__dict__'):
            own = sys.getsizeof(value)
            attributes = {k: v for k, v in vars(value).items()
                          if k not in {'model', 'mesh', 'built', 'fe_model'}}
            if retain_projections:
                projections.append(attributes)
            return own + size(attributes)
        return sys.getsizeof(value) if value is not None else 0
    return size(getattr(shape, '_stress', None))


@pytest.mark.parametrize('storage', ['owned', 'views', 'external'])
def test_current_recovery_graph_matches_original_accounting(storage):
    base = np.arange(40., dtype=np.float64)
    if storage == 'owned':
        values = base.copy()
    elif storage == 'views':
        values = base.reshape(5, 8)[:, ::2]
    else:
        values = np.frombuffer(base.tobytes(), dtype=np.float64)
    records = {101: {'stress': values, 'label': 'source', 'flag': True},
               102: {'stress': values, 'mixed': [1, 2., None, b'bytes', (3, 4.)]}}
    records['cycle'] = records
    recovery = SimpleNamespace(element_stresses=records, ignored=None,
                               model=np.ones(200), mesh=np.ones(300))
    shape = SimpleNamespace(_stress=recovery)
    assert retained_recovery_bytes(shape) == original_bytes(shape)
    old = retained_recovery_bytes(shape)
    records[103] = {'stress': np.ones(4096)}
    assert retained_recovery_bytes(shape) == original_bytes(shape)
    assert retained_recovery_bytes(shape) > old + 4096 * 8
    records[101]['stress'] = np.zeros(5000)
    assert retained_recovery_bytes(shape) == original_bytes(shape)


def test_custom_containers_and_scalar_attributes_keep_general_accounting():
    class IntWithData(int):
        pass
    class CustomDict(dict):
        def items(self):
            self.reads += 1
            return super().items()
    class CustomList(list):
        def __iter__(self):
            self.reads += 1
            return super().__iter__()
    scalar = IntWithData(2)
    scalar.samples = np.ones(100)
    mapping = CustomDict(value=scalar)
    mapping.reads = 0
    sequence = CustomList([mapping, MappingProxyType({'view': np.ones(50)})])
    sequence.reads = 0
    shape = SimpleNamespace(_stress=sequence)
    expected = original_bytes(shape)
    mapping.reads = sequence.reads = 0
    assert retained_recovery_bytes(shape) == expected
    assert mapping.reads == sequence.reads == 1


def test_first_component_accounts_for_current_growth_and_keeps_caller_recovery(monkeypatch):
    import anyfem.post.fields as fields
    import anyfem.post.solver_data as solver_data
    monkeypatch.setattr(fields, 'recover', lambda shape: shape._stress)
    canonical_calls = []
    def resolve(*args, **kwargs):
        canonical_calls.append(kwargs['recovered'])
        return SimpleNamespace(descriptor=SimpleNamespace(unit='Pa', basis='local'))
    monkeypatch.setattr(solver_data, 'resolve_solution_quantity', resolve)
    recovery = SimpleNamespace(element_stresses={1: {'membrane_xx': [1., 2.],
                                                    'bending_xx': [3., 4.]}})
    shape = SimpleNamespace(_stress=recovery, status='completed',
                            built=SimpleNamespace(mesh=SimpleNamespace(shells={1: ()}, beams={})))
    access = LiveResultAccess(cache_bytes=200_000)
    assert access.field(shape, 'membrane_xx').values == {1: 1.5}
    first = access._recovery_resident[id(shape)][2]
    recovery.additional = np.ones(30_000)
    with pytest.raises(WorkingSetError):
        access.field(shape, 'bending_xx')
    assert len(canonical_calls) == 2
    assert shape._stress is recovery  # Caller-owned source survives refusal.
    assert retained_recovery_bytes(shape) > first + 200_000
    assert (id(shape), 'bending_xx') not in access._readers
    access.close()


def test_empty_recovery_accounting():
    assert retained_recovery_bytes(SimpleNamespace()) == 0


def test_sibling_attribute_projections_never_share_recycled_temporary_identity():
    # Each carrier has a distinct dictionary and array. Only the source key is
    # shared; temporary filtered attribute dictionaries are not graph aliases.
    carriers = [SimpleNamespace(samples=np.ones(i + 100)) for i in range(100)]
    shape = SimpleNamespace(_stress=carriers)
    expected = (sys.getsizeof(carriers) + sys.getsizeof('samples')
                + sum(sys.getsizeof(value) + sys.getsizeof({'samples': value.samples})
                      + sys.getsizeof(value.samples) for value in carriers))
    assert original_bytes(shape, retain_projections=True) == expected
    assert [retained_recovery_bytes(shape) for _ in range(4)] == [expected] * 4
    carriers[-1].samples = np.zeros(10_000)
    assert retained_recovery_bytes(shape) == original_bytes(shape, retain_projections=True)


def test_custom_size_callback_runs_before_projecting_current_attributes():
    class MutatingSize:
        def __sizeof__(self):
            self.samples = list(range(20))
            return object.__sizeof__(self)
    value = MutatingSize()
    value.samples = []
    shape = SimpleNamespace(_stress=value)
    actual = retained_recovery_bytes(shape)
    value.samples = []
    assert actual == original_bytes(shape, retain_projections=True)
    assert value.samples == list(range(20))


def test_shared_current_recovery_leaves_keep_exact_charges_after_mutation():
    shared = np.ones(20)
    records = {i: {'samples': np.ones(i + 1), 'shared': shared,
                   'scalar': np.float64(i), 'label': 'source', 'optional': None}
               for i in range(100)}
    shape = SimpleNamespace(_stress=SimpleNamespace(element_stresses=records))
    measured = retained_recovery_bytes(shape)
    assert measured == original_bytes(shape, retain_projections=True)
    records[99]['samples'] = np.ones(30_000)
    records[50]['scalar'] = np.float64(17)
    assert retained_recovery_bytes(shape) == original_bytes(shape, retain_projections=True)
    records[75]['samples'] = shared[::2]  # Native view forces general traversal.
    records[80]['optional'] = shared
    assert retained_recovery_bytes(shape) == original_bytes(shape, retain_projections=True)


def test_recovery_record_keeps_custom_leaf_callback_order():
    calls = []
    class ChangingLeaf:
        def __sizeof__(self):
            calls.append('size')
            records[2]['later'] = np.ones(600)
            return object.__sizeof__(self)
    leaf = ChangingLeaf()
    records = {1: {'custom': None, 'later': np.ones(1)},
               2: {'custom': leaf, 'later': np.ones(1)}}
    shape = SimpleNamespace(_stress=records)
    actual = retained_recovery_bytes(shape)
    assert calls == ['size']
    calls.clear()
    records[2]['later'] = np.ones(1)
    expected = original_bytes(shape, retain_projections=True)
    assert calls == ['size'] and actual == expected


def test_native_sample_counts_preserve_order_bits_and_fresh_mutation(monkeypatch):
    import anyfem.post.solver_data as solver_data
    monkeypatch.setattr(solver_data, 'resolve_solution_quantity', lambda *a, **k:
                        SimpleNamespace(descriptor=SimpleNamespace(unit='Pa', basis='local')))
    values = {2: np.arange(6.).reshape(2, 3), 3: np.float64(1.25), 4: -0.,
              5: np.arange(8.)[::2], 6: np.array([]), 7: np.array(13.)}
    recovery = SimpleNamespace(element_stresses={eid: {'axial_stress': value}
                                                for eid, value in values.items()})
    shape = SimpleNamespace(built=SimpleNamespace(mesh=SimpleNamespace(
        shells={eid: () for eid in reversed(values)}, beams={})))
    calls = []
    original = np.prod
    monkeypatch.setattr(np, 'prod', lambda *a, **k: (calls.append(a), original(*a, **k))[1])
    provider = ShapeProvider(shape, 'axial_stress', stresses=recovery)
    assert not calls
    assert provider.ids.tolist() == sorted(values)
    for index, eid in enumerate(provider.ids):
        raw = np.asarray(values[eid], dtype=np.float64).reshape(-1)
        assert provider.counts[index] == len(raw)
        assert provider.values[index, :len(raw)].tobytes() == raw.tobytes()
        assert np.isnan(provider.values[index, len(raw):]).all()
    values[2][0, 0] = 99.
    fresh = ShapeProvider(shape, 'axial_stress', stresses=recovery)
    assert fresh.values[0, 0] == 99. and provider.values[0, 0] == 0.


def test_custom_array_sample_metadata_keeps_generic_shape_product(monkeypatch):
    import anyfem.post.solver_data as solver_data
    monkeypatch.setattr(solver_data, 'resolve_solution_quantity', lambda *a, **k:
                        SimpleNamespace(descriptor=SimpleNamespace(unit='Pa', basis='local')))
    reads = []
    class CustomArray(np.ndarray):
        @property
        def shape(self):
            reads.append('shape')
            return super().shape
        @property
        def size(self):
            raise AssertionError('custom size bypassed shape protocol')
    raw = np.arange(6.).reshape(2, 3).view(CustomArray)
    shape = SimpleNamespace(built=SimpleNamespace(mesh=SimpleNamespace(shells={1: ()}, beams={})))
    recovery = SimpleNamespace(element_stresses={1: {'axial_stress': raw}})
    calls = []
    original = np.prod
    monkeypatch.setattr(np, 'prod', lambda *a, **k: (calls.append(a), original(*a, **k))[1])
    provider = ShapeProvider(shape, 'axial_stress', stresses=recovery)
    assert reads == ['shape'] and len(calls) == 1
    assert provider.counts.tolist() == [6]
    assert provider.values.tobytes() == np.arange(6.).reshape(1, 6).tobytes()
