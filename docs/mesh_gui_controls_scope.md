# Mesh GUI controls — bounded consumer scope

User request: expose available mesher capabilities after the CPython 3.14
startup repair. No ANYmesh changes, builds, release or performance claims.

Owned implementation paths:
- `src/anyfem/mesh_controls.py`: toolkit-neutral public-option validation.
- `src/anyfem/mesh_jobs.py`: immutable submission identity and propagation.
- `src/anyfem/model/project.py`: persisted controls to public mesher API.
- `src/anyfem/ui/app.py`: settings persistence and submission.
- `src/anyfem/ui/panels.py`: explicit controls, contextual help and refresh.
- `tests/test_mesh_gui_controls.py`: headless controls/round-trip/dispatch tests.
- `tests/test_mesh_method_selection.py`: existing focused headless regressions.

Defaults remain legacy. Frontal Delaunay/spatial sizing are explicit Alpha
opt-ins, planar-only, with qualification still open. No field-guided fronts.
Validation uses public ANYmesher contracts; no limits/guards are weakened.
Tests must not create Tk roots or generate meshes. GUIexpert advice will be
requested if that task is available; no advisory acceptance is inferred.

## User-facing controls

In **Mesh → Mesh generation**, the method dropdown and Automatic / Mapped /
Unstructured radio shortcuts share the same value. Existing Automatic priority,
quality, preview, seeding and local refinement controls remain available.

For Automatic residual faces and Unstructured, quad recombination is selectable.
**Show advanced native / Alpha controls** reveals point placement, sizing metric
and bounded insertion/topology/cancellation settings. Legacy lattice remains the
default. Frontal Delaunay is an explicit planar-only Alpha choice; spatial
isotropic sizing uses target size/local refinement. Unsupported curved native
faces are rejected by ANYmesher, not silently rerouted. Curved frontal and
field-guided quad fronts are not advertised as available.

Geometry validation offers Interactive (existing default) or Strict full audit.
Interactive only invokes a changed-region audit when a change set is supplied;
it is not a full geometry certification. Neither mode disables element admission.
Strict may be expensive on large geometry and is never automatically selected.

Mapped hides native controls and does not pass native-v2 options. Legacy mode
disables frontal-only budgets; inactive drafts do not block Generate, and the
previously saved budgets are retained. Stored Alpha settings are made visible on
reload. Ordinary refresh preserves unsaved controls; opening/newing a document
discards drafts (including re-opening the same document). Undo/redo with no draft
reloads persisted controls; an existing unsaved draft remains until submitted or
the document is reopened. Cancelled document switching leaves the draft intact.

Controls use immutable `MeshControls`, persist in existing native-settings
parameters, survive project/snapshot serialization, and affect job hashes.
Mapped hashes exclude unused native controls. Requested controls appear in mesh
record diagnostics. Missing native-v2 API disables that opt-in while preserving
legacy operation; an explicit unsupported opt-in refuses with an explanation.

## Focused verification

CPython 3.14, launcher source-path setup and ecosystem preflight, then pytest:

```
tests/test_mesh_gui_controls.py
tests/test_mesh_method_selection.py::test_panel_routes_mapped_selection_and_hides_irrelevant_triangulator
tests/test_mesh_method_selection.py::test_existing_native_settings_schema_persists_the_method_without_tk
tests/test_mesh_method_selection.py::test_mesh_settings_strategy_is_canonical_and_hash_affecting
```

Final result: **33 passed in 2.94 seconds**. The real panel builder executes with widget
and variable-trace doubles, not Tcl. Worker/public-API propagation tests stop at
the mesher boundary without generating a mesh. `git diff --check` passes.
The earlier 28-test pass predates the constructor-trace/API-absence tests; the
30-test correction snapshot passed in 3.38 seconds before the disclosure follow-up.
The disclosure snapshot passed 31 tests in 3.44 seconds. Recovery-specific checks
(missing native-v2 saved-project reopen, same-ID reopen, and constructor/control
updater doubles) separately passed 3 tests in 1.21 seconds before the final run.

GUIexpert's first source review identified four state/validation issues; these
were corrected and regression-tested. Source advisory acceptance was received
for those original blocking scenarios. A subsequent isolated correction and test
preserve the user's collapsed Alpha disclosure on ordinary clean refresh. That
follow-up is distinct from the snapshot accepted by the advisor.

Known boundaries: dormant invalid budget drafts are discarded on a successful
Legacy/Mapped submission, while previously saved budgets are retained.

The missing-API recovery check identified unguarded executable validation during
saved-Alpha hydration. The authorized minimal follow-up now reads saved intent
for display separately from validation: original Alpha fields remain visible,
the incompatibility diagnostic is displayed, and Generate is disabled/refused.
The project/settings remain byte-for-byte equivalent through project_to_dict;
restoring the public capability clears the block through revalidation, without
downgrade. The constructor test executes the real method-controls updater and
asserts the disabled Generate state with widget doubles. Same-ID explicit reopen
discards a draft and reloads the exact saved Alpha settings.
Real keyboard/focus, DPI/layout and accessibility were not exercised;
there is no GUI runtime, algorithm, platform-scale or release qualification claim.

## Local integration boundary

The ecosystem boss accepted the bounded source/headless milestone and authorized
scoped local preservation of these eight registered files. Version remains 0.4.0;
no dependency metadata or workflow pins were changed.

Published dependency requirements still include ANYmesher `>=0.4,<0.5`,
ANYgeometry[planar] `>=0.4.2,<0.5`, ANYsolver `>=0.4.2,<0.5`, ANYmaterial
`>=0.2,<0.3`, ANYfileio `>=0.3.1,<0.4`, and the GUI viewer pair `>=0.5.5,<0.6`.
Native-v2 controls were qualified only against the current local 0.5 mesher API;
the older supported generation retains legacy behavior/capability refusal.
Release dependency reconciliation is a separate owner-coordinated gate.

A push to main/master or a pull request triggers tests.yml: Linux/Windows across
Python 3.11–3.14, full pytest, verification/parity, and build/wheel-install work.
It currently pins ANYmesh to the legacy `27e4281` reference rather than the local
native-v2 implementation. Broad CI/push readiness is therefore not claimed.
The Alpha-specific tests require the public native-v2 API and matching reviewed
source dependencies; CI pins must be reconciled before triggering that workload.
Tags `v*` trigger publish.yml and can publish to PyPI; no tag/push is authorized
by this local integration milestone. Remaining qualification includes exact
dependency pins/metadata, complete headless CI and separately authorized visual
and numerical/platform checks. No such workloads were run during preservation.
