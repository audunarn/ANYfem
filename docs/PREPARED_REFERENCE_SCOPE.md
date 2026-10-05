# Prepared Project reference scope — candidate contract

Public API: `anyfem.prepared_reference_scope`. This optional coordinated API
requires ANYgeometry's prepared-model-scope capability. It imports neither Qt
nor Tk and performs no meshing, solving, persistence normalization or design
mutation. It is not connected to the production meshing route.

For an already prepared Project geometry:

```python
manifest = query_prepared_project_reference_scope(project, project.geometry)
validate_prepared_project_reference_scope(project, project.geometry, manifest)
```

For a detached working model, capture provenance during extraction **before any
preparation or working-model edits**. Declare structural ownership first:

```python
basis = extract_prepared_project_closure(project, handles)
working = basis.working_model
# Use the owner's qualified preparation operation on working.
manifest = query_prepared_project_reference_scope(project, working, closure=basis)
validate_prepared_project_reference_scope(project, working, manifest, closure=basis)
```

The extraction record binds exact source content, initial work content and both
owner ID maps. A plain ModelClosure, reconstructed record, changed source or
preparation of different work refuses. Records are transient and process-bound;
saved metadata and checksums do not reconstruct preparation authority.

The immutable manifest retains complete original/current owner documents,
including FaceUse and Sheet occurrence IDs and orientations. Mapping rows use
explicit source and initial prepared namespaces; authored root rows identify
their source namespace. Each external reference retains its complete typed
record, source targets, current prepared targets and root/child scope. Supported
inventories include all load cases and loads, gravity, supports, masses,
imperfections, refinements, all regions/sets, section assignments and compatibility
caches, raw attachment bindings, output requests, seeding and Sheet Join intents.
Empty supported containers have explicit counts. Other public Project inputs
are fingerprinted; typed references outside inventoried containers refuse.
Missing references, unknown record types, mesh-domain regions and references
outside a detached closure refuse rather than become evidence of absence.

Partial face scopes require the selected children's complete loop/hole edges.
Explicit edge and vertex references also remain required constraints. Canonical
section caches are distinguished from independent legacy scopes. No coordinate,
centroid or proximity remapping, occurrence aliasing or root expansion is allowed.

Call the full validator immediately before publication. It checks live owner
preparation, exact Project contents, extraction mapping and recomputed inventory;
changing a value, container, identity or definition invalidates the record.
`assert_authored_root_face_scope(manifest, root, current_faces)` rejects root-only
associations when a child-local reference exists, and rejects a partial child set
presented as a root. It checks input intent only. Even success establishes no
cell coverage, conforming boundaries, mesh quality, science or publication gate.
The mesher must independently retain boundaries and establish exact child/cell
membership, and may not pass authored-root keys into the current-child remapper.

Portable counterexample: `examples/prepared_reference_scope.py`. A split plate
gets pressure on only its left child after geometry preparation. The geometry
snapshot alone cannot witness that load; this manifest detects it and rejects a
root-wide association. Tests are in `tests/test_prepared_reference_scope.py`.

Exact dependency for this handoff: ANYgeometry main
`6fc64b794e81fa671a4472e2d506a92b595c40d9`, owner checked source
`48e8f7fd5e88e749292142694050ba011ae09ed6`, wheel SHA256
`2d7468767d0cbca26185dcab543912582048d828d435a15fd3c6c6736cb4b553`.
Use the recorded exact-git-source wheel, not another same-version artifact.
