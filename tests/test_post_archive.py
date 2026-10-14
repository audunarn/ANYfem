"""Consumer ownership and refusal checks; native/GPU integration runs separately."""

from concurrent.futures import CancelledError, ThreadPoolExecutor
from threading import Event, get_ident
from queue import Empty, SimpleQueue
import time
import sys
from types import ModuleType, SimpleNamespace

import pytest
import numpy as np

from anyfem.post.archive import ArchiveSession


@pytest.fixture
def backend(monkeypatch):
    readers = []
    views = []
    package = ModuleType("anypost")
    presentation = ModuleType("anypost.presentation")

    def open_results(path, **options):
        reader = SimpleNamespace(
            options=options,
            closed=0,
            working_bytes=options["working_bytes"],
            metadata=lambda: {"model_identity": "model-A", "mesh_identity": "mesh-A"},
            cases=lambda: ({"case_id": "case-A"},),
            components=lambda quantity_id=None: ({"quantity_id": quantity_id or "u"},),
        )
        reader.close = lambda: setattr(reader, "closed", reader.closed + 1)
        readers.append(reader)
        return reader

    class View:
        def __init__(self, reader, viewer, dispatch, **options):
            self.reader = reader
            self.viewer = viewer
            self.dispatch = dispatch
            self.options = options
            self.handle = object()
            self.closed = 0
            self.requests = []
            self.selection_loaders = []
            self.latest_request_id = 0
            self.error = None
            views.append(self)

        def request_component(self, *request, **options):
            self.requests.append(request)
            self.selection_loaders.append(options.get("selection_loader"))
            self.latest_request_id = len(self.requests)
            return self.latest_request_id

        def close(self):
            self.closed += 1

    package.open_results = open_results
    package.WorkingSetError = type("WorkingSetError", (RuntimeError,), {})
    package.UnavailableFieldError = type("UnavailableFieldError", (RuntimeError,), {})
    presentation.RetainedResultView = View
    monkeypatch.setitem(sys.modules, "anypost", package)
    monkeypatch.setitem(sys.modules, "anypost.presentation", presentation)
    queued = []
    viewer = SimpleNamespace(add_mesh_arrays=lambda *_: None, submit_update=queued.append)
    return SimpleNamespace(readers=readers, views=views, viewer=viewer, queued=queued)


def test_viewport_queue_retirement_and_context_lifetime(backend):
    callback_results = []
    with ArchiveSession("archive.h5", SimpleNamespace(canvas=backend.viewer), expected_model_identity="model-A", expected_mesh_identity="mesh-A") as session:
        assert session.model_identity == "model-A"
        assert session.mesh_identity == "mesh-A"
        assert session.handle is session.view.handle
        assert session.cases()[0]["case_id"] == "case-A"
        assert session.components("stress")[0]["quantity_id"] == "stress"
        assert session.request_component("case-A", "u", "x", [2]) == 1
        assert session.view.requests == [("case-A", "u", "x", [2])]
        session.view.dispatch(lambda: callback_results.append("published"))
        backend.queued.pop()()
        session.view.dispatch(lambda: callback_results.append("retired"))
    backend.queued.pop()()
    session.close()
    assert callback_results == ["published"]
    assert session.view.closed == 1
    assert session.reader.closed == 1
    with pytest.raises(RuntimeError, match="closed"):
        session.request_component("case-A", "u", "x")


@pytest.mark.parametrize("identity", ["model", "mesh"])
def test_identity_mismatch_closes_reader_before_geometry(backend, identity):
    with pytest.raises(ValueError, match=f"{identity}_identity"):
        ArchiveSession("archive.h5", backend.viewer, **{f"expected_{identity}_identity": "different"})
    assert backend.readers[0].closed == 1
    assert backend.views == []


def test_host_identities_are_checked(backend):
    host = SimpleNamespace(canvas=backend.viewer, model_identity="different", mesh_identity="mesh-A")
    with pytest.raises(ValueError, match="model_identity"):
        ArchiveSession("archive.h5", host)
    assert backend.readers[0].options["expected_model_identity"] == "different"


def test_worker_thread_publication_and_mutation_refuse(backend):
    with ArchiveSession("archive.h5", backend.viewer) as session:
        published = []
        session.view.dispatch(lambda: published.append(True))
        callback = backend.queued.pop()
        with ThreadPoolExecutor(max_workers=1) as pool:
            for operation in (callback, lambda: session.request_component("a", "u", "x"), session.close):
                with pytest.raises(RuntimeError, match="owner thread"):
                    pool.submit(operation).result()
        assert published == []
        assert session.view.requests == []
        assert not session._closed
        callback()
        assert published == [True]


def test_close_preserves_reader_until_view_retirement_and_allows_retry(backend):
    session = ArchiveSession("archive.h5", backend.viewer)

    def failure():
        raise RuntimeError("detach failure")

    session.view.close = failure
    with pytest.raises(RuntimeError, match="detach failure"):
        session.close()
    assert session.reader.closed == 0
    with pytest.raises(RuntimeError, match="closed"):
        session.request_component("case-A", "u", "x")
    session.view.close = lambda: None
    session.close()
    assert session.reader.closed == 1
    session.close()
    assert session.reader.closed == 1


def test_dispatcher_required_before_opening_archive(backend):
    with pytest.raises(TypeError, match="dispatcher"):
        ArchiveSession("archive.h5", SimpleNamespace(add_mesh_arrays=lambda *_: None))
    assert backend.readers == []


def test_empty_expected_identity_refuses_before_opening_archive(backend):
    with pytest.raises(ValueError, match="nonempty"):
        ArchiveSession("archive.h5", backend.viewer, expected_mesh_identity="")
    assert backend.readers == []


def test_worker_preparation_owner_attachment_uses_snapshots_without_archive_reads(backend, monkeypatch):
    owner = get_ident()
    reads = []
    class Shapes(dict):
        def values(self):
            reads.append(("shape-proof", get_ident()))
            return super().values()
    header = {"model_identity":"model-A","mesh_identity":"mesh-A",
              "cases":[{"case_id":"a"}],"descriptors":[{"quantity_id":"stress","component":"x","element_shapes":Shapes({"7":[7]})}]}
    geometry = {"positions":np.zeros((3,3)),"triangles":np.array([[0,1,2]])}
    package = sys.modules["anypost"]
    original = package.open_results
    def open_results(*args, **options):
        reads.append(("open",get_ident()))
        reader = original(*args, **options)
        reader.metadata = lambda: (reads.append(("metadata",get_ident())),header)[1]
        reader.geometry = lambda: (reads.append(("geometry",get_ident())),geometry)[1]
        return reader
    monkeypatch.setattr(package,"open_results",open_results)
    with ThreadPoolExecutor(max_workers=1) as pool:
        prepared = pool.submit(ArchiveSession.prepare,"archive.h5").result()
    assert [kind for kind,_ in reads] == ["open","metadata","geometry","shape-proof"]
    assert all(thread != owner for _,thread in reads)
    reader = backend.readers[-1]
    for name in ("metadata","geometry","cases","components"):
        setattr(reader,name,lambda *_: pytest.fail("owner attachment/UI metadata must use prepared snapshots"))
    with ArchiveSession(prepared,backend.viewer) as session:
        assert session.view.options["geometry"] is geometry
        assert session.view.options["descriptors"] == tuple(header["descriptors"])
        assert session.cases() == ({"case_id":"a"},)
        assert session.components("stress")[0]["component"] == "x"
        assert session.components("other") == ()
        assert session._native_scalar_proofs == {("stress","x"):True}
        prepared.close()  # ownership has moved; cannot close the session's reader
        assert reader.closed == 0
        with pytest.raises(RuntimeError,match="attached or discarded"):
            ArchiveSession(prepared,backend.viewer)
    assert reader.closed == 1


def test_preparation_failure_mismatch_and_unused_discard_close_readers(backend, monkeypatch):
    package = sys.modules["anypost"]
    original = package.open_results
    def open_results(*args, **options):
        reader = original(*args, **options)
        reader.geometry = lambda: {"positions":np.zeros((3,3)),"triangles":np.array([[0,1,2]])}
        return reader
    monkeypatch.setattr(package,"open_results",open_results)
    with pytest.raises(ValueError,match="model_identity"):
        ArchiveSession.prepare("archive.h5",expected_model_identity="wrong")
    assert backend.readers[-1].closed == 1
    prepared = ArchiveSession.prepare("archive.h5")
    with pytest.raises(ValueError,match="mesh_identity"):
        ArchiveSession(prepared,backend.viewer,expected_mesh_identity="wrong")
    assert backend.readers[-1].closed == 1
    prepared.close()
    assert backend.readers[-1].closed == 1
    unused = ArchiveSession.prepare("archive.h5")
    unused.close(); unused.close()
    assert backend.readers[-1].closed == 1
    invalid_view = ArchiveSession.prepare("archive.h5")
    with pytest.raises(TypeError,match="viewer"):
        ArchiveSession(invalid_view,object())
    assert backend.readers[-1].closed == 1
    def broken_open(*args, **options):
        reader = open_results(*args, **options)
        reader.geometry = lambda: (_ for _ in ()).throw(OSError("geometry read failure"))
        return reader
    monkeypatch.setattr(package,"open_results",broken_open)
    with pytest.raises(OSError,match="geometry read failure"):
        ArchiveSession.prepare("archive.h5")
    assert backend.readers[-1].closed == 1


def test_native_sample_ordinals_preserve_noncontiguous_entity_order_and_cache(backend):
    with ArchiveSession("archive.h5", backend.viewer) as session:
        calls = []
        ids = np.array([900, 7, 900, 42, 7, 900], dtype=np.int64)
        session.reader.associations = lambda *key: (calls.append(key), ids)[1]
        assert session.sample_count("stress", "x") == 3
        np.testing.assert_array_equal(session.sample_selection("stress", "x", 1), [0, 1, 3])
        np.testing.assert_array_equal(session.sample_selection("stress", "x", 2), [2, 4])
        np.testing.assert_array_equal(session.sample_selection("stress", "x", 3), [5])
        assert calls == [("stress", "x")]
        assert not session.sample_selection("stress", "x", 1).flags.writeable
        session.sample_selection("stress", "y", 2)
        assert calls == [("stress", "x"), ("stress", "y")]


def test_native_sample_ordinal_validation_and_budget_refusal(backend):
    with ArchiveSession("archive.h5", backend.viewer, working_bytes=512) as session:
        session.reader.associations = lambda *_: np.array([7, 900, 7, 900], dtype=np.int64)
        for invalid in (0, -1, 1.5, True):
            with pytest.raises(ValueError, match="positive integer"):
                session.sample_selection("stress", "x", invalid)
        with pytest.raises(RuntimeError, match="working bytes"):
            session.sample_selection("stress", "x", 1)
        assert session._sample_ordinals is None
        session.reader.working_bytes = 4096
        with pytest.raises(ValueError, match="exceeds maximum"):
            session.sample_selection("stress", "x", 3)


def test_native_sample_map_accounting_retires_previous_component_and_close(backend):
    session = ArchiveSession("archive.h5", backend.viewer)
    accounts = []
    session.reader.set_external_cache_bytes = lambda owner, size: accounts.append(size)
    session.reader.associations = lambda quantity, component: np.array([7, 7, 900]) if component == "x" else np.array([42])
    session.sample_selection("stress", "x", 2)
    session.sample_selection("stress", "y", 1)
    session.close()
    assert accounts == [48, 56, 16, 24, 0]
    assert session._sample_ordinals is None


def test_async_native_selection_reads_on_worker_and_publishes_range_on_owner(backend):
    owner = get_ident()
    reads, published = [], []
    with ArchiveSession("archive.h5", backend.viewer) as session:
        def associations(*_):
            reads.append(get_ident())
            return np.array([900, 7, 900, 42, 7, 900])
        session.reader.associations = associations
        serial = session.request_native_sample("case-A", "stress", "x", 2, on_samples_ready=lambda maximum: published.append((maximum, get_ident())))
        assert serial == 1 and reads == [] and published == []
        loader = session.view.selection_loaders[-1]
        with ThreadPoolExecutor(max_workers=1) as pool:
            selection = pool.submit(loader, Event()).result()
        np.testing.assert_array_equal(selection, [2, 4])
        assert reads and all(thread != owner for thread in reads)
        assert published == []
        backend.queued.pop(0)()
        assert published == [(3, owner)]
        assert session.native_sample_diagnostics["resident_bytes"] == 112


def test_native_vector_shape_refuses_before_io_and_cached_map_reuse(backend):
    from anypost import UnavailableFieldError
    owner = get_ident()
    metadata_reads, associations = [], []
    with ArchiveSession("archive.h5", backend.viewer) as session:
        def components(quantity):
            metadata_reads.append(get_ident())
            return ({"component": "scalar", "element_shapes": {"7": [7]}},
                    {"component": "vector", "metadata": {"element_shapes": {"7": [7, 3]}}})
        session.reader.components = components
        session.reader.associations = lambda *_: (associations.append(get_ident()), np.repeat(7, 7))[1]
        assert session.sample_count("stress", "scalar") == 7
        cached = session._sample_ordinals
        reads_before = len(associations)
        with pytest.raises(UnavailableFieldError, match="scalar rank component.*explicit flat indices"):
            session.sample_count("stress", "vector")
        session.request_native_sample("a", "stress", "vector", 1)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(UnavailableFieldError, match="extra component axes"):
                pool.submit(session.view.selection_loaders[-1], Event()).result()
        # The immutable quantity proof contains the vector decision too;
        # switching components must not copy its per-entity metadata again.
        assert metadata_reads == [owner]
        assert len(associations) == reads_before
        assert session._sample_ordinals is cached
        session.request_component("a", "stress", "vector", [2])
        assert session.view.requests[-1] == ("a", "stress", "vector", [2])


def test_latest_native_range_publication_and_exact_cross_component_reuse(backend):
    updates = []
    with ArchiveSession("archive.h5", backend.viewer) as session:
        session.reader.associations = lambda *_: np.array([900, 7, 900, 42, 7, 900])
        session.request_native_sample("a", "stress", "x", 2, on_samples_ready=lambda maximum: updates.append("old"))
        old = session.view.selection_loaders[-1]
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(old, Event()).result()
            session.request_native_sample("b", "stress", "y", 2, on_samples_ready=lambda maximum: updates.append("latest"))
            second = pool.submit(session.view.selection_loaders[-1], Event()).result()
        assert first is second
        for callback in backend.queued:
            callback()
        assert updates == ["latest"]
        assert session.native_sample_diagnostics == {"association_reads": 2, "sorts": 1, "selector_builds": 1, "resident_bytes": 112, "maximum": 3}
        # Same entities in another order must build a different native map.
        session.reader.associations = lambda *_: np.array([7, 900, 7, 900, 42, 900])
        np.testing.assert_array_equal(session.sample_selection("stress", "z", 2), [2, 3])
        assert session.native_sample_diagnostics["sorts"] == 2


def test_legacy_shape_proofs_copy_quantity_metadata_once_on_worker(backend):
    from anypost import UnavailableFieldError
    owner = get_ident()
    metadata_reads = []
    with ArchiveSession("archive.h5", backend.viewer) as session:
        session.reader.components = lambda quantity: (metadata_reads.append((quantity, get_ident())), (
            {"quantity_id":quantity,"component":"x","element_shapes":{"7":[7]}},
            {"quantity_id":quantity,"component":"y","element_shapes":{"7":[7]}},
            {"quantity_id":quantity,"component":"vector","element_shapes":{"7":[7,3]}},
        ))[1]
        session.reader.associations = lambda *_: np.repeat(7, 7)
        with ThreadPoolExecutor(max_workers=1) as pool:
            for component in ("x", "x", "y"):
                session.request_native_sample("a", "stress", component, 2)
                selection = pool.submit(session.view.selection_loaders[-1], Event()).result()
                np.testing.assert_array_equal(selection, [1])
            session.request_native_sample("a", "stress", "vector", 2)
            with pytest.raises(UnavailableFieldError):
                pool.submit(session.view.selection_loaders[-1], Event()).result()
        assert len(metadata_reads) == 1 and metadata_reads[0][1] != owner
        assert session.native_sample_diagnostics["sorts"] == 1
        session._clear_sample_map()
        assert session.sample_count("stress", "y") == 7
        assert len(metadata_reads) == 1
    assert session._native_scalar_proofs == {} and session._native_shape_quantities == set()


def test_100k_entity_header_shapes_proved_once_without_request_rescans(backend, monkeypatch, tmp_path):
    class ShapeTable(dict):
        scans = 0
        def values(self):
            self.scans += 1
            return super().values()
    shapes = ShapeTable({str(entity):[7] for entity in range(100_000)})
    descriptors = [{"quantity_id":"stress","component":f"c{index}","element_shapes":shapes} for index in range(9)]
    package = sys.modules["anypost"]
    original = package.open_results
    metadata_calls = []
    def open_results(*args, **options):
        reader = original(*args, **options)
        def metadata():
            metadata_calls.append(True)
            return {"model_identity":"model-A","mesh_identity":"mesh-A","descriptors":descriptors}
        reader.metadata = metadata
        reader.components = lambda *_: pytest.fail("header proofs must avoid descriptor deep copies")
        reader.associations = lambda *_: np.repeat(np.arange(100_000),7)
        return reader
    monkeypatch.setattr(package,"open_results",open_results)
    with ArchiveSession("archive.h5", backend.viewer) as session:
        assert metadata_calls == [True] and shapes.scans == 9
        assert len(session._native_scalar_proofs) == 9
        assert all(type(proof) is bool for proof in session._native_scalar_proofs.values())
        session.sample_selection("stress", "c0", 2)
        started = time.perf_counter_ns()
        for _ in range(50):
            session._prepare_sample_map("stress", "c0")
        elapsed = (time.perf_counter_ns()-started)/1e6
        session.sample_selection("stress", "c1", 2)
        assert shapes.scans == 9 and metadata_calls == [True]
        assert session.native_sample_diagnostics["sorts"] == 1
        (tmp_path/"header-proof-measurement.json").write_text(__import__("json").dumps({"entities":100000,"descriptors":9,"repeated_preparations":50,"total_ms":elapsed,"header_metadata_calls":1,"shape_scans":9}))


def test_native_cancel_and_close_retire_worker_before_releasing_cached_tables(backend):
    session = ArchiveSession("archive.h5", backend.viewer)
    session.reader.associations = lambda *_: np.array([7, 7, 900])
    session.sample_selection("stress", "x", 2)
    session.request_native_sample("a", "stress", "x", 2, on_samples_ready=lambda maximum: pytest.fail("closed publication"))
    loader = session.view.selection_loaders[-1]
    cancel = Event()
    cancel.set()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(CancelledError):
            pool.submit(loader, cancel).result()
        pool.submit(loader, Event()).result()
    before = session._sample_associations
    observed = []

    def retire():
        observed.append((session._sample_associations is before, session.reader.closed))

    session.view.close = retire
    session.close()
    assert observed == [(True, 0)]
    assert session._sample_associations is session._sample_ordinals is session._sample_indices is None
    assert session.reader.closed == 1
    for callback in backend.queued:
        callback()


def test_real_archive_native_loader_cancels_stale_io_without_ui_reads(tmp_path):
    anypost = pytest.importorskip("anypost")
    from any3dview import MeshHandle

    owner = get_ident()
    queued = SimpleQueue()
    changes = []
    class Viewer:
        def submit_update(self, callback): queued.put(callback)
        def add_mesh_arrays(self, mesh):
            return MeshHandle(mesh, on_change=lambda handle, kind: changes.append((kind, get_ident())))

    descriptors = tuple(dict(key=key, quantity_id="stress", component=key, location="element", unit="Pa", basis="local", sampling="native", availability="stored") for key in ("x", "y"))
    capsule = dict(schema="anysolver.recovery_capsule", version=1, runtime="anysolver-results/1", model_identity="async-model", mesh_identity="async-mesh", solver_identity={}, geometry={"positions":np.array([[0.,0.,0.],[1.,0.,0.],[1.,1.,0.],[0.,1.,0.]]),"triangles":np.array([[0,1,2],[0,2,3]]),"triangle_to_element":np.array([0,1]),"element_ids":np.array([7,900])}, patterns=(), coefficients=np.zeros((1,0)), cases=({"case_id":"a","status":"converged","certified":True,"route":"stored"},), descriptors=descriptors, associations={"x":np.array([7,7,900]),"y":np.array([7,7,900])}, responses={}, derived={}, stored={"x":{"case_indices":np.array([0]),"values":np.array([[1.,2.,3.]])},"y":{"case_indices":np.array([0]),"values":np.array([[10.,20.,30.]])}})
    path = anypost.write_results(tmp_path / "native.h5", capsule)
    entered, release = Event(), Event()
    reads, options = [], []
    with ThreadPoolExecutor(max_workers=1) as pool:
        prepared = pool.submit(ArchiveSession.prepare,path).result()
    reader = prepared._reader
    for name in ("metadata","geometry","components","cases"):
        setattr(reader,name,lambda *_: pytest.fail("owner must not read prefetched archive snapshots"))
    with ArchiveSession(prepared, Viewer()) as session:
        original = session.reader.associations
        def associations(quantity, component, selection=None, **kwargs):
            reads.append((component, get_ident()))
            if component == "x":
                entered.set()
                assert release.wait(5), "test release deadline"
            return original(quantity, component, selection, **kwargs)
        session.reader.associations = associations
        session.request_native_sample("a", "stress", "x", 2, on_samples_ready=lambda maximum: options.append("stale"))
        try:
            assert entered.wait(5)
            latest = session.request_native_sample("a", "stress", "y", 2, on_samples_ready=lambda maximum: options.append((maximum, get_ident())))
        finally:
            release.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                queued.get_nowait()()
            except Empty:
                time.sleep(.002)
            if session.handle.mesh.element_scalars is not None:
                break
        np.testing.assert_array_equal(session.handle.mesh.element_scalars, [20., np.nan])
        assert session.view.latest_request_id == latest
        assert session.view.error is None
        assert options == [(2, owner)]
        assert reads and all(thread != owner for component, thread in reads)
        assert changes and all(thread == owner for kind, thread in changes)
        assert session.native_sample_diagnostics["sorts"] == 1
        handle = session.handle
    assert handle.removed
    assert session._sample_associations is session._sample_ordinals is session._sample_indices is None


def test_real_archive_vector_native_refusal_preserves_scalar_and_explicit_indices(tmp_path):
    anypost = pytest.importorskip("anypost")
    from any3dview import MeshHandle
    queued = SimpleQueue()
    class Viewer:
        def submit_update(self, callback): queued.put(callback)
        def add_mesh_arrays(self, mesh): return MeshHandle(mesh)
    descriptors = tuple(dict(key=key, quantity_id="stress", component=key, location="element", unit="Pa", basis="principal", sampling="native", availability="stored", element_shapes={"7":shape}) for key,shape in (("scalar",[7]),("vector",[7,3])))
    capsule = dict(schema="anysolver.recovery_capsule", version=1, runtime="anysolver-results/1", model_identity="shape-model", mesh_identity="shape-mesh", solver_identity={}, geometry={"positions":np.array([[0.,0.,0.],[1.,0.,0.],[0.,1.,0.]]),"triangles":np.array([[0,1,2]]),"triangle_to_element":np.array([0]),"element_ids":np.array([7])}, patterns=(), coefficients=np.zeros((1,0)), cases=({"case_id":"a","status":"converged","certified":True,"route":"stored"},), descriptors=descriptors, associations={"scalar":np.repeat(7,7),"vector":np.repeat(7,21)}, responses={}, derived={}, stored={key:{"case_indices":np.array([0]),"values":np.arange(count,dtype=float).reshape(1,-1)} for key,count in (("scalar",7),("vector",21))})
    path = anypost.write_results(tmp_path / "vector.h5", capsule)
    def drain_until(predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            try: queued.get_nowait()()
            except Empty: time.sleep(.002)
        assert predicate(), "publication deadline"
    with ArchiveSession(path, Viewer()) as session:
        session.request_native_sample("a", "stress", "scalar", 7)
        drain_until(lambda: session.handle.mesh.element_scalars is not None)
        np.testing.assert_array_equal(session.handle.mesh.element_scalars, [6.])
        associations = []
        original = session.reader.associations
        session.reader.associations = lambda *args,**kwargs: (associations.append(args), original(*args,**kwargs))[1]
        session.request_native_sample("a", "stress", "vector", 1)
        drain_until(lambda: session.view.error is not None)
        assert isinstance(session.view.error, anypost.UnavailableFieldError)
        assert associations == []
        np.testing.assert_array_equal(session.handle.mesh.element_scalars, [6.])
        with pytest.raises(anypost.UnavailableFieldError): session.sample_selection("stress", "vector", 1)
        session.request_component("a", "stress", "vector", [2])
        drain_until(lambda: session.view.error is None and np.array_equal(session.handle.mesh.element_scalars, [2.]))
