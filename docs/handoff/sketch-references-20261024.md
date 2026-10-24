# Sketch reference geometry — 2026-10-24

Status: implemented and verified against the bounded development criteria below. No commit, publication, scientific qualification or release performed. Existing dirty work and the preceding sketch-coordinate implementation were preserved.

## Adopted behavior

Existing geometry intersecting the sketch plane can be imported as fixed reference anchors: vertices in the plane, crossing edges, endpoints of in-plane edges, and straight sections/boundaries of flat, straight-trimmed plates. References appear separately from the sketch contour, are selectable for coincidence and dimensions, and cannot be dragged. Drawing at an anchor creates an ordinary coincident sketch point. Face sections preserve individual intervals rather than joining gaps or holes.

Select source geometry before starting a sketch, use **Use selected**, or use **Pick references** while sketching. The reference list distinguishes linked and detached fixed snapshots. Removal and explicit **Refresh from selection** are supported. Save, reopen and edit preserve snapshots and constraints.

The user explicitly chose immediate invalidation: moving, deleting or ambiguously replacing a source breaks its optional source link. Captured coordinates and sketch constraints remain fixed and usable. No automatic following, cascading regeneration, new feature input dependencies or dependency graph was added. Saved sketches detach after successful commands; unsaved working copies detach on document refresh, including source-history changes that do not change the rendered model hash. Failed commands do not detach saved references. Command undo/redo preserves saved-reference link state. A detached draft remains detached until explicitly refreshed.

## Future extension seam

ANYgeometry owns two separate data objects: immutable `SketchReference` geometry snapshots in the sketch definition, and optional `ReferenceLink` source identity/signature metadata in `reference_links`. `bind_references` solves only against stored fixed snapshots; it never reads the source model. Capture, source resolution/signature comparison and surviving-link policy are separate functions. This leaves a defined place for a future dependency policy without requiring one now or changing the current fixed-snapshot contract.

Source links use feature-output identity when available and exact materialized-ID-independent geometry signatures. They are metadata rather than feature input ports. The metadata update API is restricted to reference links on sketch features; it cannot silently update geometry-defining parameters. Old definitions omit the optional fields and retain their existing schema version. Existing face-support dependencies are unchanged.

## Scope and ownership

ANYgeometry: new `sketch_references.py`, integration in `sketch.py`, `sketch_entities.py`, `features.py`, exports, and owner tests.

ANYfem: new reference facade, command/undo hooks, sketch working-copy/controller integration, Construction panel and viewport picking/rendering, stale-link handling on project load, headless and Qt regression tests.

Mistral remained exhausted. The substantial connected implementation used the Claude CLI subscription route (Sonnet 5.5, verified `claude.ai` Max authentication), with API/proxy overrides removed only in child processes. API billing was not used. Worker scope and tools were bounded. Permission/tool failures and all partial runs are retained in the evidence directory. The parent independently inspected the implementation, corrected Qt test harness assumptions and the final document-refresh/contrast integration, and ran the final checks. No different-family scientific gate was required for this UI/geometry development slice.

Baseline copies preserve pre-existing dirty content. Task-only patches and a final SHA-256 manifest are saved outside the repositories. Protected `model/project.py` and `diagnostics.py` match their captured baselines byte for byte. No branches/history or unrelated configuration were changed.

## Final verification

Runtime: `C:/Python/Python314/python.exe`; source checkouts on PYTHONPATH; Qt tests explicitly enabled. Each check records its exact command, effective environment, exit/timeout and process-wall time in a matching `*-run.json`, with full output in `.log`. Each process had a 180-second process-tree timeout. All final checks exited 0 without timeout.

| Evidence stem | Result | Process wall |
| --- | --- | --- |
| `final-owner-integration` | 102 passed: reference capture/binding, sketch features/entities, feature editing/isolation and ANYfem headless reference integration | 29.00 s |
| `final-fem-regression` | 142 passed: coordinate overlays, overlay lifecycle, sketch controller/workflow, plate cuts, attachments, commands, combined point input, unknown-feature loading and document hashes | 84.63 s |
| `active-reference-document-refresh` | 12 Qt reference cases passed, offscreen software | 13.41 s |
| `native-reference-final` | Same 12 cases passed, native Windows/GPU | 23.75 s |
| `hidpi-reference-final` | Same 12 cases passed, offscreen at scale factor 2 | 16.66 s |
| `native-preview-final` | Native Windows/GPU section preview: source move detached the active draft immediately and fixed coordinates were unchanged | 6.82 s |

The Qt cases exercise actual mouse selection, dimensions/coincidence, refused conflicting constraints without mutation, read-only anchors, drawing at references, cancellation, finish/edit/save/reopen, dedicated picking under another normal selection filter, planar sections and immediate draft invalidation for both move and delete. Independent local-oracle checks also cover far-origin plane membership and preservation of legacy ordinary point keys beginning `ref:`. Git diff whitespace checks passed in both repositories.

The native preview source edit plus event processing measured approximately 80 ms on this machine. This is a single smoke observation, not a broad performance claim. Reference contrast was checked visually on the dark native GPU viewport. The preceding coordinate/responsiveness sweep is documented separately in `sketch-coordinates-20261024.md`.

Six deprecation warnings came from existing Qt tests. Some Python 3.14 runs printed a `pyreadline3` console-deallocator invalid-handle message at shutdown after pytest passed; exit status remained 0. Full logs retain these messages.

## Limits and retained findings

Curved/non-flat face sections and curved face trims are explicitly refused. Curved edges are supported where the existing owner curve/root service supports them; in-plane curves expose their endpoints as fixed anchors and a sampled display curve. There is no general point-on-curve constraint introduced by this change.

Independent baseline reproduction found that the existing raw `MovePoint` command can leave an authored source feature with a materialization-checksum mismatch; raw `DeleteEntity` has related pre-existing feature-output limitations. This task preserves their public identifier behavior and does not weaken checksum validation or claim to repair those source-edit paths. Valid authored-source editing/deletion was checked through `EditFeature`, `MoveEntities` and `DeleteFeature`. Reference snapshots still detach and remain fixed under raw source mutation.

All earlier failures, superseded designs and partial handoffs remain available in `task-note-history.md` and individual logs outside the repository. Final behavior supersedes the initial live-associative proposal. The draft-delete defect was traced to a feature-history change with unchanged model hash, then repaired at document refresh; the speculative viewport refresh hooks were removed.

Evidence directory: `C:/ANYworkspaceWork/anyfem-sketch-references-20261024/`. Task deltas: `ANYfem-task-delta.patch`, `ANYgeometry-task-delta.patch`; source hashes: `final-manifest.json`; native preview: `preview-result.json`, `reference-linked.png`, `reference-after-source-move.png`.

## Follow-up: plate/stiffener edges and reference appearance

Plate boundary edges and stiffener member-axis edges are ordinary geometry edges and are included in the capture path. Added a generated stiffened-panel regression that captures both a plate boundary and an actual stiffener member edge, checking their fixed endpoint coordinates. Render-only beam cross-section silhouettes are not topology edges.

Reference curves now use separated stipple spans: green for a valid source link, yellow for a detached fixed snapshot. Markers and labels use the same status color, with contrast variants for light/dark backgrounds. The geometry remains fixed after detachment. Both viewer backends use the same span generation, with a continuous phase along sampled curves and a bounded span count. This small presentation follow-up was implemented directly by the parent; no substantial implementation milestone or worker was needed.

The focused software/headless suite passed 29 cases (`stipple-edge-check-fixed`, 17.92 s process wall). Its initial failed attempt is retained in `stipple-edge-check`: corrected a missing local NumPy import and the new test's member-edge accessor. The rendering regression checks actual gaps, green linked lines, yellow detached lines and unchanged snapshot coordinates after a source move. All 12 Qt cases also passed on native Windows/GPU (`stipple-native-gpu`, 22.40 s) and at 200% scale (`stipple-hidpi`, 16.12 s). The native preview passed and was visually inspected (`stipple-native-preview`, 6.29 s); linked/detached screenshots show the stipple/status colors. Commands, environments, full logs and timing records remain in the same evidence directory. Git diff whitespace checks passed. No geometry-owner code changed for this follow-up.

## Follow-up: persistent global origin and subtle axes

The sketch coordinate overlay now always identifies world `Global (0,0,0)`. It marks the actual camera projection while visible. Offscreen origins receive a clamped badge with a direction arrow; an unprojectable origin is explicitly marked behind the view. Offset planes explicitly mark the origin as off-plane, avoiding confusion with the sketch's local origin. Faint global X/Y/Z axes pass through the actual visible global origin. If it is offscreen, a compact orientation triad remains visible; an axis viewed end-on appears as a circle with its axis label.

This is presentation only, using the existing mouse-transparent, paint-driven coordinate widget. It adds a constant number of camera projections and painter primitives, no scene entities or timers. No solver, reference-link or model behavior changes. This small coupled overlay follow-up was implemented directly by the parent in `ui/qt/sketch_coordinates.py`, with focused regression coverage in `tests/test_qt_sketch_coordinates.py`.

Checks: 22 combined coordinate/reference GUI cases passed (`origin-axes`, 24.67 s process wall). The final 10-case coordinate suite passed on native Windows/GPU (`origin-axes-gpu`, 21.26 s) and 200% scaling (`origin-axes-hidpi`, 16.42 s), including translated/oblique/off-plane coordinates, origin projection, offscreen/behind-view handling, camera changes, cancellation, renderer switching and idle repaint behavior. The final badge paint refinement passed its focused regression (`origin-badge-final`, 8.48 s). Native GPU preview was visually inspected (`origin-axes-preview`, 6.67 s). All exits 0 without timeout; the pre-existing Python console-deallocator shutdown message remains in logs. Git diff whitespace checks passed. Evidence remains in the directory above; preview PNGs now include the origin and axes.

## Completed follow-up: global-axis intersection anchors

User now requests usable fixed reference points where global axes meet the actual sketch plane. Adopt unique finite axis/plane intersections; parallel disjoint axes have none. An axis contained in the plane has no unique crossing, so expose world origin once, merging only exactly coincident axis-origin hits. Do not create falsely projected anchors for off-plane axes. Reuse fixed reference snapping, dimensioning and persistence; no dependencies or source links for intrinsic datums. Unused automatic datums should not alter old saved definitions; used ones persist as fixed snapshots.

The bounded Claude subscription assignment failed at the service layer before any source inspection or edits (repeated unknown transport/service errors). Its invocation was interrupted; no API-credit fallback or repeat inference was attempted. Baseline comparison confirmed all six existing owned files were unchanged. The parent completed the connected implementation locally. Exact scoped brief, settings, auth summary, failure events and captured dirty baselines remain in `axis-anchor-*` / `axis-anchors-*` evidence. No sibling edits, source-history changes or new dependencies were introduced.

Implementation: `FaceSketchTask` computes intrinsic fixed axis snapshots separately from user-imported references, while exposing their anchors through the same selection/drag/dimension paths. Only snapshots used by constraints enter the saved definition. Clicking near an axis marker while drawing reuses the existing ordinary-endpoint plus coincidence route. Used snapshots reopen fixed; unreferenced datums do not change legacy saved definitions. Built-in datum names are reserved, and removal/relinking refuses without changing them. Mixed removal selections containing a datum are rejected before removing imported references. Markers are green and labelled with their axis; they have no optional source links and are not classified as detached model references. Distinct nearby axis intercepts are never merged by distance.

Mathematical checks passed: translated XY planes produce one world-origin anchor; z=5 produces only the Z-axis anchor at (0,0,5); translated oblique planes produce three qualified intercepts satisfying the axis and plane equations; nearby distinct intercepts stay separate. Drawing, dimensions, drag refusal, local undo/redo, cancel, save and reopen preserve the fixed anchor contract. These are captured fixed snapshots, not a newly introduced associative dependency policy.

Verification (all exit 0 without timeout; exact runtime/environment/command/full output in matching evidence records):

| Evidence stem | Result | Process wall |
| --- | --- | --- |
| `axis-anchor-headless` | 29 passed: initial anchor tests, external reference integration and controller | 13.94 s |
| `axis-anchor-math-final` | 7 passed: final axis math/fixed/persistence suite | 6.91 s |
| `axis-anchor-workflow` | 53 passed: sketch workflow, cuts and attachments | 25.07 s |
| `axis-anchor-qt` | 56 passed: reference/coordinate/controller/workflow GUI suite | 49.52 s |
| `axis-anchor-offset-qt` | 4 passed: actual snapping/dimension/save/reopen on XY and z=5 planes | 9.12 s |
| `axis-anchor-gpu` | 16 reference GUI cases passed, native Windows/GPU | 28.29 s |
| `axis-anchor-hidpi` | Same 16 cases passed at 200% scale | 21.06 s |
| `axis-anchor-preview` | Native GPU oblique-plane preview passed; three green labelled intercepts visually checked | 5.95 s |

Six existing Qt deprecation warnings and the known Python console shutdown warning remain in logs where emitted. Git diff whitespace checks passed. Five implementation/test files changed in this follow-up; controller and previous headless external-reference test file match their captured baselines. Task-only patch: `axis-anchor-task-delta.patch`; hashes: `axis-anchor-final-manifest.json`; preview: `axis-anchor-oblique.png`. No installed artifact or release claim.
