# Native ANYpost archive viewing

Install `ANYfem[post,tk]` for the archive reader and standalone Tk GPU viewer.
The `post` extra requests `ANYpost[results]`, including the lightweight
reconstruction runtime even when an existing full solver satisfies its version
requirement. Compact reconstructable fields therefore have their runtime
dependency installed; entirely stored archives remain supported too.
The native `anypost.results` archive is separate from ANYfem's existing
`anyfem.result` sidecars. This adapter does not modify project persistence or
legacy result loading.

```console
python -m anyfem.post.archive result.h5 --list
python -m anyfem.post.archive result.h5 --case service --quantity displacement --component UX
python -m anyfem.post.archive result.h5 --case service --quantity stress --component membrane_xx --sample 2
```

The first command discovers identities, cases (including failed cases), and
component descriptors without reading fields. The second opens a real
ANY3dView Tk/ModernGL viewport. Case and component selectors reuse one retained
mesh. Failed or uncertified cases cannot provide success fields. Unsupported
components and ambiguous multiple samples per entity are refused. `--indices`
accepts explicit association row indices for a declared sample selection; it
does not average samples or invent missing values.

The GUI visibly defaults to **Sample 1** and offers each native sample ordinal
per entity. Selecting Sample 2 means the second occurrence of each persistent
entity ID in that component's archive association order. Entities with fewer
samples remain NaN. These are native ordinals, not interpolated positions or
averaged fields. **All samples** preserves ambiguity refusal. `--sample` selects
the initial ordinal; `--indices` supplies an alternative explicit selection.

Applications can attach a session to an existing viewer or ANYfem viewport:

```python
from anyfem.post.archive import ArchiveSession
from concurrent.futures import ThreadPoolExecutor

# Prepare before creating an active UI. For an existing UI, schedule prepare
# on its I/O executor and dispatch attachment when the future completes.
with ThreadPoolExecutor(max_workers=1) as io:
    prepared = io.submit(
        ArchiveSession.prepare, "result.h5",
        expected_model_identity=current_model_identity,
        expected_mesh_identity=current_mesh_identity,
    ).result()
# Attach on the viewport's owner thread:
session = ArchiveSession(prepared, viewport)
serial = session.request_component("service", "displacement", "UX")
# GUI callers should let the worker prepare the native selector:
serial = session.request_native_sample("service", "stress", "membrane_xx", 2)
# On the same UI thread, before destroying the viewer:
session.close()
```

Supply current identities when attaching to a current engineering model. If
the host exposes `model_identity` / `mesh_identity`, they are used by default.
A standalone session validates and uses the archive identities. Mismatches
close the reader before geometry attachment. Persistent node/element IDs,
not row positions, govern association mapping in ANYpost's retained view.

`PreparedArchive` is single-use and owns the reader until attachment. Preparation
opens the archive and captures header, geometry, cases, descriptors and compact
axis proofs on the calling I/O worker. Attachment performs no archive reads;
prepared cases/components are served from memory. Call `prepared.close()` to
discard an unused preparation; failed preparation or attachment closes the
reader. After attachment, session.close owns retirement and reader release.
The standalone CLI finishes preparation on a worker before creating Tk.
`ArchiveSession(path, viewer)` remains an explicitly synchronous programmatic
constructor; active UI hosts should use the preparation path.

The default dispatcher is `viewer.submit_update`; a custom `dispatch(callback)`
must enqueue work on the creating UI thread. Background loading uses the
latest request, publishes immutable arrays on that thread, and drops stale or
closed requests. Session publication guards also retire queued callbacks when
closed. Archive engineering values remain float64; display conversion is owned
by the retained presentation layer. `session.reader` exposes reader cache/read
diagnostics, and `session.view` exposes background errors and presentation
state. Call `close()` or use a context manager to detach the handle and release
the file, even when no field has been requested.

`request_native_sample(..., ordinal, on_samples_ready=callback)` performs native
association reads and map/selector preparation on the sole loader worker.
`callback(maximum)` publishes the actual native sample range on the UI thread
only for the latest request. CLI component/case changes use this async route;
`sample_selection` remains an explicitly synchronous API for existing callers.
Identical ordered association tables reuse one immutable map and selector across
components; both tables and the retained selector are byte-accounted. No station
coordinates or field values are averaged. `native_sample_diagnostics` supplies
compact read/sort/selector counts; query it after a request completes.
Native ordinals refuse a descriptor with extra component axes, such as
`element_shapes={"51": [7, 3]}`. Those flattened entries include vector
components and cannot be labelled as 21 native stations. Choose a scalar
principal-rank component or use explicit flat indices; scalar shapes `[7]`
retain all seven native samples. Refusal occurs before association reads or
reuse of another component's cached map.
Axis admission is proved once from the source-bound immutable header already
read during session creation. Only compact per-component booleans are retained;
requests do not copy or rescan per-entity shape metadata. A legacy reader without
header descriptors loads each quantity's metadata once on the loader worker.
These proofs survive sample-map cache clearing and retire with the session.
Optional recovery-frame metadata is preserved as finite JSON; runtime admission
does not certify that an arbitrary frame is a physical coordinate transform.
If retiring a load times out, publication remains retired and the reader stays
open; retry `close()` after the I/O completes. A timeout is not evidence of
successful worker retirement.

The Tk GPU viewer exposes `presented_frame_sequence` and
`add_frame_presented_listener(callback)`. The callback receives a
`any3dview.contracts.PresentedFrame(sequence, presented_ns)` only after a
successful host swap on the owner thread. `presented_ns` uses
`time.perf_counter_ns()`; it measures swap completion, not display scan-out.
The returned unsubscribe callable must also run on the owner thread. This
facility supports actual request-to-swap measurements. No speed claim is
established by mock lifecycle checks; real GUI/GPU evidence is separate.

The solver exporter names displacement components UX, UY, UZ, RX, RY and RZ;
inspect archive descriptors before requesting other quantity/component names.
An existing Qt GPU viewport can attach through its viewer with the default
`on_presented=None`. The current public frame-listener facility and measured
request-to-swap callbacks apply to the Tk GPU viewer. Qt does not yet expose that
same public listener, so requesting `on_presented` there is explicitly refused;
Qt scalar integration does not imply universal frame timestamps.
