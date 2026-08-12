# ANYfem v6 Native Triangulation Backend Migration Plan

## Authority

This bounded downstream plan implements the persistence supplement in the
approved ANYmesher compiled-triangulation addendum:

- governing plan: `C:\Users\AudunArnesenNyhus\Downloads\ANYmesher_native_hybrid_mesher_plan.md`
- approved addendum: `C:\Github\ANYmesh\reports\native_hybrid\compiled_triangulation_addendum.md`
- approved addendum SHA-256: `A64E3DC1DC7733A6ED065E85C7475B49A1071840BE7A075D976F13935CFFBD95`

The migration is required before ANYmesh changes the public triangulation
selector default from `python` to `auto`.

## Objective

Version ANYfem project persistence from format 5 to format 6 so old projects
retain Python triangulation semantics while new projects explicitly request
automatic compiled-or-absence-only-fallback selection.

The setting is distinct from `NativeMeshSettings.backend`, which selects the
top-level mapped/native/automatic meshing strategy. The new setting selects
only the native face triangulator.

## Public contract

1. Add a validated project-level native triangulation selector with exactly
   `auto`, `python`, and `native` values.
2. A newly constructed Project defaults this selector to `auto`.
3. Canonical format-6 output always writes the effective selector explicitly
   as `meshing.native_backend`, including when native settings are otherwise
   null.
4. Loading formats 1 through 5 with no explicit selector resolves to `python`.
5. Saving a migrated legacy project writes format 6 and preserves the resolved
   `python` selector; reopening it cannot silently switch to `auto`.
6. A legacy explicit `native_backend` inside native-settings parameters remains
   authoritative and is canonicalized into the format-6 top-level field.
7. Format-6 input must contain a valid explicit selector. Missing, boolean,
   non-string, unknown, or conflicting top-level/nested values fail closed with
   `ProjectFileError`.
8. Project meshing always forwards one explicit effective selector. Existing
   nested parameters are retained for format-5 compatibility but cannot create
   two contradictory runtime choices.
9. No geometry schema is parsed or rewritten outside ANYgeometry's public
   codec, and no meshing geometry/tolerance/topology behavior changes here.

## Owned files

- `C:\Github\ANYfem\src\anyfem\model\project.py`
- `C:\Github\ANYfem\src\anyfem\io\project_file.py`
- `C:\Github\ANYfem\tests\test_native_meshing_project.py`
- `C:\Github\ANYfem\docs\NATIVE_MESH_BACKEND_MIGRATION.md`

No ANYmesh, ANYgeometry, ANYsolver, ANYfileio, UI, or unrelated dirty ANYfem
path is owned by this slice.

## Implementation sequence

1. Add selector validation and the Project field/default.
2. Make Project mesh generation forward the explicit effective selector while
   rejecting contradictory nested configuration.
3. Raise `FORMAT_VERSION` to 6 and implement deterministic v1-v5 migration.
4. Require and validate the explicit field in v6 documents.
5. Write migration documentation distinguishing package compatibility from
   persisted behavior.
6. Add focused constructor, migration, round-trip, save-after-migration,
   malformed-input, conflict, and runtime-forwarding tests.

## Focused evidence

Run only after implementation:

```powershell
$env:PYTHONPATH='C:\Github\ANYfem\src;C:\Github\ANYmesh\src;C:\Github\ANYgeometry\src;C:\Github\ANYsolver\src;C:\Github\ANYmaterial\src;C:\Github\ANYfileIO\src'
python -m pytest tests\test_native_meshing_project.py -q
```

Add the smallest directly affected project-codec nodes if existing migration
fixtures live in another test module. A broad suite, wheel build, benchmark, or
performance run requires separate governance and performance approval.

## Acceptance gates

- New Project -> format 6 -> explicit `auto` -> identical reopen.
- Each v1-v5 omission fixture -> effective `python` -> format-6 save -> explicit
  `python` -> identical reopen.
- Legacy explicit selector remains unchanged and is canonicalized.
- Format-6 omission and invalid/conflicting values fail closed precisely.
- Runtime forwards exactly one explicit selector to ANYmesher.
- Existing native settings, model-bound controls, and geometry identity still
  round-trip.
- Dependency resolver conflict is not hidden: ANYfem currently requires
  `ANYmesher>=0.2,<0.3`, while ANYsolver and ANYfileio metadata require
  `ANYmesher>=0.1,<0.2`; sibling metadata alignment is a separate governed
  blocker.

## Out of scope

- Changing ANYmesh's default before this migration is green.
- Reopening mutable ANYgeometry stores or parsing schema-4 internals.
- Coordinate-inferred connectivity or geometry healing.
- Solver/reference import refactors.
- Resolver-range edits in sibling repositories.
- Performance or wheel qualification.

## Governance revision 1 (authoritative)

This revision supersedes any conflicting wording above.

### Canonical dependency source

Focused integration and migration evidence must use the canonical declared dependency source at `C:\Github\ANYfileIO\src`. `C:\Github\ANYio\src` is void and must not be used. Resolver metadata remains a separate governed slice and is not changed by this plan.

### Additional owned persistence regression

The implementation slice also owns `C:\Github\ANYfem\tests\test_section_assignments.py` solely to migrate its hard-coded format-5 assertion and preserve its section-assignment round trip under format 6. No unrelated behavior in that module is in scope.

### Versioned canonical-source rule

A nested `native_backend` compatibility value is recognized only while reading formats 1 through 5. For those legacy formats, an explicitly present nested value is migrated deterministically into the project-level selector; an omitted value migrates to `python`.

For format 6, `meshing.native_backend` is the sole canonical source. A format-6 document must contain one valid top-level value (`auto`, `python`, or `native`). Any nested `native_backend` occurrence is rejected as a non-canonical duplicate, even when it matches the top-level value. Missing, invalid, or conflicting format-6 representations fail closed.

### Dirty-hunk preservation

`Project._ensure_plate_ownership` is pre-existing integration work in the dirty tree. The migration implementation must use disjoint hunks and review the resulting diff specifically to prove this ownership logic is preserved byte-for-byte; it must not be reverted, reformatted, or folded into the migration change.

### Required UI/settings handoff

UI/settings presentation is a separate required downstream handoff. Before ANYmesh changes the public triangulation default from `python` to `auto`, a dedicated UI/settings plan must be created and registered with governance, with its own exact path, owned files, persistence behavior, presentation semantics, tests, and evidence. The core format-6 migration may land first, but it does not authorize the ANYmesh default change by itself.
## Governance revision 2 (incremental runtime ownership)

The approved headless runtime contract also requires the incremental component path, which currently forwards `native_backend` from `NativeMeshSettings.parameters`. The following paths are therefore added to the owned implementation slice:

- `C:\Github\ANYfem\src\anyfem\native_meshing_backend.py`
- `C:\Github\ANYfem\tests\test_native_meshing_backend.py`

The incremental runtime must carry the project-level selector as a distinct snapshotted request value, exclude `native_backend` from nested settings/control parameters, and pass exactly one explicit `native_backend` argument to `generate_hybrid_mesh_result`. Focused evidence must prove ordinary `Project.generate_mesh` and incremental component generation select the same persisted value. This addition does not authorize UI edits, ANYmesh default changes, resolver metadata changes, or unrelated runtime behavior changes.