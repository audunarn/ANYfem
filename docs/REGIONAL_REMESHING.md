# Update a published mesh

For this source candidate, use ANYmesh `c1630dc81688bcc1a6d96bfa68277e25d76eb1d8` and ANYgeometry `0bcc1b397cfa07ffa8bba26d8c7c5b5a2939baf4` with the other dependencies pinned in ANYfem’s caller workflow. These revisions provide the numbering, ownership and boundary APIs used below.

In the Mesh panel, select **Prepare mapped mesh for regional updates** and choose **Generate mesh**. After an edit, choose **Update mesh**, review the affected region, and confirm. For two independent plates, change the edge divisions on one plate. The other plate keeps its node numbers, coordinates, element numbers and ordered connectivity through saving, reopening and solver assembly.

Eligible meshes produced by the Automatic method can also carry an update baseline when the mesher records that it admitted mapped plates. The panel enables **Update mesh** when the active mesh has a baseline. Its preview checks the current edit. **Generate mesh** always requests a full rebuild.

## Working from Python

Use a project mesh session for generation, publication, undo history and disk artifacts. Given an existing project with two disconnected plates and the edge IDs of the first plate, the following updates only the first plate.

```python
from anyfem.io.project_file import load_project
from anyfem.mesh_jobs import MeshSettings

session = project.mesh_session()
settings = MeshSettings.create(0.5, element_order="linear", strategy="mapped")
parent = session.generate_mesh(settings)
path = session.save("assembly.anyfem")

session = load_project(path).mesh_session()
updated = MeshSettings.create(
    0.5, element_order="linear", strategy="mapped",
    overrides={edge: 4 for edge in first_plate_edges},
)
print(session.preview_update(updated).description)
child = session.update_mesh(updated)
session.save()
```

Keep the project file with its artifact directory when moving it. **Save As** copies the recorded artifacts to the destination. The lower-level `project.generate_mesh(...)` API returns a candidate for existing generation-only callers.

A headless mesh session takes the project’s edit lock when it publishes a change to an opened project. Call `save()` to persist that revision and release the lock, or `close()` to discard unsaved work. The artifact directory remembers the highest published numbers even when the newer mesh is discarded. If another session has saved a newer revision, reopen the file before publishing another mesh.

## Numbers and references

Nodes and elements have separate positive signed 64-bit number spaces. Shells, beams and couplings share the element counter. A new element receives a fresh number. Deletion, undo and branching can leave gaps. A cancelled, superseded or rejected update leaves the active mesh and both counters intact.

Mesh selections, loads, regions and output requests carry the identity of their producing mesh. An update advances an attachment when its referenced entity survives, including the orientation of an element face. The Qt panel lists references that need a new target. For interior patches it also highlights the exact parent elements before publication. Cancelling that review restores the prior mesh and selection. Results continue to open against the mesh used in their analysis.

Changing geometry can make the active mesh stale for solving. Update it or choose an explicit full rebuild. An old project without a compatible baseline, a zero-numbered legacy mesh or a changed producer build requires a full rebuild before regional updates. CalculiX deck export checks number limits before writing and accepts an explicit 64-bit target through `integer_bits=64` for INTSIZE64 builds.

## Supported edits

| Geometry and mesh | Regional operation |
| --- | --- |
| Disjoint, straight-sided mapped plates | Change geometry or edge divisions, or add or remove a separately owned plate. Check disjointness in both revisions. |
| Mapped planar plates with shared sides | Hold ordered stations on retained edges, including quadratic midside nodes. A required change in shared seeding expands the region shown in the preview. |
| Supported planar beam and shell components | Regenerate the affected component and rebuild its attachments. Supported prepared crossings and three-sided plate junctions use source-to-working correspondence. |
| Single planar Q4 plate | Remesh complete cells for supported polygonal holes, point sizing zones and unconstrained interior sizing curves. An edit to a spline control point is detected even if its bounds and sizing stations stay identical. A diagnosed change to the outer boundary can expand once to the whole plate, as shown in the preview. |
| Single open cylindrical or conical sector | Refine interior blocks on an unchanged, untrimmed source and fixed rim. Changing only compatible rim edge divisions can expand once to the whole sector. The preview identifies the face, and the new mesh must match the requested ordered rim stations. |
| Single closed cylindrical ring of untrimmed sectors | A density change can regenerate the whole ring when the geometry owner certifies one complete period and the quad-first route passes its quality checks. The update verifies both ordered sides of every shared edge, including the periodic seam and quadratic midside nodes. |
| Independently owned linear shell faces with source-cell evidence | Update eligible Bezier extrusions, sampled ruled surfaces, explicit Coons patches and NURBS surfaces. The preview names the complete faces to replace. Supported edits include edge divisions, target size, changes to the authored surface with unchanged bounds, and an inner trim defined on its NURBS support. |

For eligible disconnected four-sided faces, **Update mesh** can reuse an unchanged face's cells after reopening the project. The mesher skips tessellation on that face, checks its source definition and every rim station, and validates its cells against the current geometry. Publication also checks the permanent numbers, coordinates, oriented connectivity, grids and ownership against the parent mesh. The update record lists the faces actually regenerated and reused. Support for the polynomial charts requires the exact directed boundary to agree with its stored tensor representation.

The full and regional commands allocate permanent numbers while the generation worker still holds its live source-cell evidence. The solver input uses those same numbers. Curved Q4 shells support the verified static solve. The S3 V2D formulation requires a facet-normal reference direction and can refuse curved triangles whose source-face normal differs.

General curved updates currently replace complete changed faces. Curved interior patches with a prescribed cut, arbitrary combinations of planar and curved owners, and shared-edge curved edits outside the listed routes require further owner support. The existing NURBS, reported spline and split-cylinder release holds apply. Full-model certification continues through the existing full audit.
