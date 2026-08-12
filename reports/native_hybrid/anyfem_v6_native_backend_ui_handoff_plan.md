# ANYfem V6 native-backend UI/settings handoff

## Governing objective

Complete the UI/settings presentation required by the approved native hybrid
mesher plan and the format-6 headless migration. This handoff must be green
before ANYmesh changes the constrained-triangulation default from `python` to
`auto`.

Parent plans:

- `C:\Users\AudunArnesenNyhus\Downloads\ANYmesher_native_hybrid_mesher_plan.md`
- `C:\Github\ANYfem\reports\native_hybrid\anyfem_v6_native_backend_migration_plan.md`

## Owned paths

- `C:\Github\ANYfem\src\anyfem\ui\app.py`
- `C:\Github\ANYfem\src\anyfem\ui\panels.py`
- `C:\Github\ANYfem\tests\test_ui_smoke.py`
- `C:\Github\ANYfem\tests\test_background_meshing.py`

No other source, persistence, dependency, ANYmesh, ANYgeometry, or resolver
metadata path is in scope. `app.py` and `panels.py` contain unrelated dirty
work; both owned test files are clean. Use only disjoint source hunks and review
the post-edit diff against the frozen baselines below. Test-file changes belong
only to this handoff.

## Frozen pre-edit baselines

Original frozen schema-1 manifest identity:

- SHA-256 `9F8D8134F8E3BCC5BCEA169534B0631D85B8810E06F8241511F5C6197333D68A`
- This is the historical pre-edit capture identity; it is no longer the bytes at
  the live manifest path after evidence-artifact registration.

Current live schema-2 closeout manifest:

- `C:\Github\ANYfem\reports\native_hybrid\ui_baselines\manifest.json`
- SHA-256 `EA62D8F0227C669E542DC1A9F25AD92ABB9C2B7DAFA06E0F743779313AEA0124`

Dirty source baselines:

- `app.py` content SHA-256
  `DA4C122CCBF5AD0238B7337E648A83332DF927A92D4C3E12E25051FA52C6A1D5`;
  zero-context patch
  `C:\Github\ANYfem\reports\native_hybrid\ui_baselines\app.py.DA4C122CCBF5AD0238B7337E648A83332DF927A92D4C3E12E25051FA52C6A1D5.u0.patch`;
  patch SHA-256
  `8B4537139351C99B62B3B887B8E77D05DA7BA66815AEB5D648AD89073B2036A0`.
- `panels.py` content SHA-256
  `E6667FB2DB907BE52CD1C1F12F197C9C7FB2762389DB994310C814B489969E65`;
  zero-context patch
  `C:\Github\ANYfem\reports\native_hybrid\ui_baselines\panels.py.E6667FB2DB907BE52CD1C1F12F197C9C7FB2762389DB994310C814B489969E65.u0.patch`;
  patch SHA-256
  `67F86553D12810471BE62DE7E17BC036BC9F26738E471E8542F25F8EE2F287BB`.

Clean test baselines:

- `test_ui_smoke.py` content SHA-256
  `03CDD462F0E919B6308E577EB2D7DE8905E1512024707F3E992C362C269E7D13`.
- `test_background_meshing.py` content SHA-256
  `96C8AA614037FAAD8C76BBD3DB89EC178708227EB8E8B7F0EB71FDB9769FB526`.

## Registered post-edit evidence artifacts

These ignored report artifacts are part of the required durable delivery even
though they are not implementation source paths:

- `C:\Github\ANYfem\reports\native_hybrid\ui_baselines\app.py.handoff.semantic.u0.patch`
  - SHA-256 `BF3B4214FFA20EDA710172D9B2883EDD3FBC33A4261DB907C3E656D14417688C`
  - 9 handoff hunks against semantic baseline SHA-256
    `49B9AB7A6392BE45FE0E89ED1DE7F917864313C853B47FFD642A50C271B56A26`
- `C:\Github\ANYfem\reports\native_hybrid\ui_baselines\panels.py.handoff.semantic.u0.patch`
  - SHA-256 `C40E7E4194CB394E1269CD61DF5260779A0C153623736C344B3E6315FE774D62`
  - 6 handoff hunks against semantic baseline SHA-256
    `BFE6801C0DEFD4CFCE9DEBB6576FDB8E6F7922DC02F60C23A31E130AEA1126A1`

Both artifacts must remain byte-identical. Because the repository ignores this
report location, the final delivery commit must explicitly force-add these two
exact files (and no broader ignored path) with `git add -f -- <exact-paths>`.
The commit evidence must show that both blobs are tracked at the registered
hashes. They must not be omitted merely because the implementation files pass.

## Presentation contract

1. Add one read-only-choice control to the Mesh panel labelled
   `native triangulator`. Its displayed choices map exactly to project values:
   `Automatic` -> `auto`, `Python compatibility` -> `python`, and
   `Compiled native` -> `native`.
2. Place explanatory text beside the control stating that this chooses the
   triangulator used by the native meshing strategy; it is not the separate
   mapped/native strategy choice.
3. A new project displays `Automatic`. A migrated format-1-through-5 project
   displays `Python compatibility`. Refresh/open/undo must always reflect the
   authoritative `Project.native_triangulation_backend` value.
4. Applying the Mesh panel stores the selection through
   `Project.set_native_triangulation_backend` inside the existing undoable
   `mesh settings` transaction before synchronous or background generation.
   Do not write nested `NativeMeshSettings.parameters.native_backend`.
5. Explicit `Compiled native` is fail-hard when the compiled capability is
   absent or corrupt. `Automatic` may fall back only for an absent extension,
   according to the ANYmesh contract; the UI must not catch and relabel an ABI
   or execution failure as fallback.
6. Mesh input identity is changed wholly within `ui/app.py`; `MeshSettings` and
   its owner remain untouched. The synchronous identity dictionary adds
   `native_backend` beside target size, overrides, and element order. The
   background path replaces direct use of `settings.input_hash` with a canonical
   hash of `{mesh_settings: settings.input_hash, native_backend: <snapshotted
   project value>}`. Focused tests prove equal selectors retain identity and a
   selector-only change alters it. Running/completed MeshRecord summaries include
   the requested selector. Where the returned mesh exposes per-face backend
   provenance, present requested/selected/actual values truthfully rather than
   inferring them from the requested setting.
7. Synchronous and background UI paths consume the same project selector. A
   background task uses the immutable project snapshot captured at submission;
   later UI changes affect only later submissions.

## Focused tests

- Mesh-panel refresh shows `Automatic` for a new project and
  `Python compatibility` for a migrated project model.
- Selecting each UI choice stores exactly `auto`, `python`, or `native` through
  the project setter and never creates a nested selector.
- The synchronous path and immutable background snapshot receive the same
  selected value and include it in mesh input identity/record summaries; a
  selector-only change changes each `mesh_input_hash`, while an unchanged
  selector reproduces it.
- A selector change after background submission does not alter the in-flight
  snapshot.
- Explicit-native capability failure remains visible as a failed MeshRecord;
  corrupt ABI/execution errors are not presented as absence fallback.
- Existing element-order, cancellation, stale-result, quality, and save/restore
  UI behavior remains unchanged in focused regression scope.

## Acceptance and handoff

Run only focused UI/background nodes first. GUI skips are reported as
unqualified, not passing evidence. Broad GUI suites, builds, wheels, and
performance runs remain lease-gated. Report exact commands, pass/fail/skip
counts, changed paths, dirty-hunk preservation evidence, platform, and any
display/runtime limitation.

Only after this plan is registered and its implementation evidence is accepted
may governance authorize the separate ANYmesh public-default switch. This plan
does not itself authorize that switch.
