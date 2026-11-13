# Structured L01 floating vessel candidate

This example generates a stationary structural vessel from a local L01 GDF hull.
The licensed source is not bundled. Internal arrangements and scantlings are
illustrative; the model is not a reproduction of the reference ship's structure.

```powershell
python examples/free_floating_l01.py --gdf "LOCAL/L01 Vessel mesh.gdf" --reference "LOCAL/reference_comparison.json" --output "NEW_OUTPUT_DIRECTORY"
```

The generator refuses an existing output directory. It builds geometry, meshes,
assembles dry mass, adds positive attached deadweight, then saves and reopens the
project. It does not launch hydrodynamic preparation or a structural solve.

The model contains eight closed, initially empty tank compartments, two transverse
bulkheads, hull/deck frames and longitudinals, and a two-level deckhouse. Upper
sides extend vertically from the imported waterline to the reference deck height.
Warped submerged facets use a reflection-consistent triangulation. Reference mass
and CG are targets for explicit attached masses; actual assembled FE inertia is
authoritative. Empty tanks imply no liquid pressure, liquid mass or sloshing.

`structured_l01.anyfem` is the base project; `mesh.json` is its frozen quadratic
mesh. `model_manifest.json` records source identity, groups, sections, assumptions
and mass provenance. All authored edges have one pinned quadratic division;
changing nominal target size alone does not refine those edges. Refinement needs
a coherent shared plate/beam seed change.

`wave_request.json` retains Hs = 1 m and Tp = 6 s, with illustrative defaults:
JONSWAP gamma 3.3, seed 0, 32 components, 0.1 s spacing over 120 s, 100 m water
depth, heading 0 (propagation in +X). It is a request, not an applied load history.
The explicit `faceted_quad_hull_v1` source uses an exact three-quad partition of
each planar source triangle. It selects the coherent direct family recipe and a
4000-panel ceiling; default source routes retain their existing limits.

Relative-waterline admission uses the saved phases and absolute time interval.
It bounds temporal extrema between samples at the retained waterline evaluation
points; the reported interpolation error is at most 1 mm. The amplitude-sum
envelope remains a diagnostic and cannot reject a realization. Existing geometric
clearance bounds and the five-degree rotation gate remain. This does not add
nonlinear wetting, stretching or spatial continuum certification.

## Current verification boundary (2026-10-30)

The local symmetric candidate has 4368 nodes, 488 Q8 shells, 1376 T6 shells and
1715 beams. Mass/CG, owner floating equilibrium, source closure and overlap,
and saved-project reopening pass. Twenty-seven of32 native frequency families
pass the unchanged damping gate. The family at omega2.7734372645 rad/s fails:
raw relative minimum eigenvalue-6.9112645324e-5 against-1e-9. Four remain unrun.
No frequencies are omitted or spliced into a different solution family.

Three bounded structural smoke attempts timed out. Stack samples locate the delay
in ANYsolver's projected Lanczos mechanism certification, before factorization;
they establish neither a mechanism nor a successful structural solve. No gate
was bypassed. Production floating Prepare/Apply, loaded-project reopening and
structural wave results remain unverified. No result artifact is supplied.

Evidence: `C:/ANYworkspaceWork/evidence/l01-structural-irregular-20261030/`.
The requested Opus 5.5/medium independent Read review failed on DNS before file
reads on its first attempt. The user-authorized retry completed a source review
of the waterline slice; parent repairs and focused checks are reconciled in the
evidence record. This is not independent acceptance of the full vessel example.
No release or full vessel campaign is implied.

A diagnostic at the exact failed frequency, with the identical hull and direct
method plus158 internal mathematical lid panels, passes the same damping gate
(+8.1468704692e-6). This supports a lid-capable owner milestone; it is not a
published profile or qualified pressure family. Full source geometry, masks and
potentials must be retained and all32 families recomputed coherently before use.

Waterline admission is bound into the immutable history. Apply, direct evaluation,
primary-acceleration and relief paths refuse times outside the admitted prepared
interval. UI selections are subsets of that interval; the label states the
prepared interval. Saved floating loads can revalidate from their embedded data
without BEM. This slice does not add GUI selection of a shorter interval before
Prepare. Long records can safely refuse when the extrema-resolution budget is
insufficient. Installed-wheel packaging/release checks were not run.
# Explicit mathematical internal-lid family

The optional `--direct-lid-profile PATH.json` accepts the complete neutral dictionary
returned by `anyhydro.direct_lid_profile.create_source_profile(...).to_dict()`.
This selects the separate versioned direct internal-lid recipe for every active
frequency and heading. Generate the profile explicitly for the exact mean-wet hull,
pose and geometry order; a changed physical hull refuses it. The profile contains
the lid and full source geometry, hull mask, generation parameters and ceilings.
It is mathematical BEM treatment, not physical plating or an additional load.

Neutral loading checks depth, dimensions, masks and duplicate panels. The first
executable request or offline reopen also regenerates the declared canonical lid
with Capytaine's supported geometry API and compares its full geometry and order.
This lazy geometry check requires the matching native geometry provider, runs no
BEM or Green-function fit, and memoizes only successful immutable profile hashes.
Prepared artifacts use `shared_v1` profile storage: one full profile with hash
references from settings and snapshots. The reader also accepts historical fully
expanded v3 artifacts; snapshot, request and standalone cache identities are unchanged.

ANYfem saves this explicit selection, checks it on offline reopen/remap and restores
it in the load dialog. Full retained potentials and Neumann data include the lid;
physical pressure transfer, added mass, damping and excitation use hull faces only.
Default examples, legacy recipes, capacities and cache identities are unchanged.
Use a new cache for a new treatment and recompute the complete 32-component family;
passing families from another recipe cannot be spliced into this source.

A public coarse box exercises retention, native force/moment normalization and
field recovery. That fixture is hydrodynamically unqualified and does not pass
the production damping gate. Neither it nor a single successful L01 diagnostic
establishes pressure accuracy, spatial convergence or engineering qualification.
All actual L01 families and the existing scientific/application gates must pass.
