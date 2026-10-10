# Submodelling across ANYfem, ANYgeometry, ANYmesher and ANYsolver — investigation and plan

Date 2026-10-09, revision 3, **approved for S0 and C0** by GPT-6-Astra via Codex CLI in round 3;
see section 11. Status: **investigation only, no source change made.**
Written against local HEADs ANYfem `f74812d`, ANYgeometry `a534b0d`, ANYmesh `d7e7414`,
ANYsolver `da1ba2e9`. ANYfem and ANYsolver have dirty worktrees; any evidence run must record
the working-tree identity (diff hash) as well as the HEAD.

Read sections 1-4 for what exists, 5 for the design, 6 for the slices, 7 for the acceptance
plan, which is frozen before any assessment run.

## 1. The request and the answer

Requested: a user has a large, coarsely meshed model; they give the position and shape of a
volume; the structure inside is cut out automatically, meshed finely, and the solver transfers
the global displacements so the submodel can be run and inspected.

Answer: this is sequential, displacement-driven shell-to-shell submodelling (the Abaqus/ANSYS
cut-boundary method). Useful public building blocks exist in all four repositories, but several
are weaker than a first reading suggests (section 3), and four contracts are genuinely new:
a bounded volume cut with attached-member partitioning (ANYgeometry), a constraint-aware driven
boundary with field transfer and interface-force extraction (ANYsolver), a lineage-restricted
point correspondence (ANYmesher), and orchestration, attribute carry-over, staleness and GUI
(ANYfem). No new element physics is required. The scientific claim is the transfer.

## 2. Method choice and its admission conditions

| Route | Verdict | Why |
|---|---|---|
| **Sequential displacement-driven submodel** | **Chosen for v1** | Matches the request; one-way; standard; analytical oracles exist. |
| In-place local refinement (`Refinement` + face split, as `RefineForImpact` does) | Comparator, and the recommended route when the local detail changes global stiffness | Exists (`ANYfem/src/anyfem/commands.py:2576`, `ANYmesh/src/anymesher/refinement.py:79`). Re-solves the whole model. |
| Single-solve global/local with interpolation ties | v2 candidate | Two-way coupling; needs coarse-region deactivation and a shell-to-shell tie. |
| Force-driven submodel | Out of v1 | Well posed with compatible tractions plus treatment of rigid modes, but needs a separate traction-transfer contract and gives no advantage for stress-concentration work. |

Admission conditions, stated to the user and checked where possible, never inferred from a
successful interpolation:

1. The global solution is adequate at the cut (the cut lies where the coarse mesh resolves the
   field). The transfer/equilibrium diagnostic (section 5.6) reports evidence, it does not prove
   adequacy.
2. The local detail does not materially change the global response (Saint-Venant). If the
   submodel adds a hole or stiffener absent from the global model, that is a declared
   approximation, not a verified transfer.

## 3. Physics decisions for v1

**D1 Admitted formulations.** Source and target shells are the production defaults: qualified
E4-PL Q4 and qualified E4-PL S3 V2D, linear order, flat or cylindrical. Q8/T6 are legacy
formulations with their own shear treatment and are not admitted in v1; mixed order is
deferred. ANYsolver's interpolation contract refuses unadmitted formulations by capability check.

**D2 What is transferred.** The transfer is a *nodal-field interpolation* of the global nodal
DOFs with the source element's geometric interpolation functions. It is not a
formulation-native physical recovery, and the plan does not claim it is one.
Translations and the two bending rotations (about the local in-plane axes) are always driven.

**D3 Drilling rotation.** Drilling is not a physical Mindlin rotation in ANYsolver: the legacy
shell stabilises `θz − ½(v,x − u,y)` (`ANYsolver/src/anysolver/elements.py:1980`), E4-PL Q4 has
separate physical, PL and drilling-hourglass contributions, and the S3 V2C/V2D PL constraint
relates drilling to membrane spin (`e4_pl_s3_v2c_element.py:1461`). Two candidate policies:

- **P1 release**: the rotation about the local shell normal is left free at driven nodes. This
  needs directional constraint equations (drive the two in-plane rotation components in the
  local frame), not the omission of global `rz`. ANYfem already builds local-frame affine
  equations this way (`ANYfem/src/anyfem/solve/build.py:680-712`).
- **P2 drive**: interpolate the nodal drilling value like the other rotations.

The default is decided in S1 by tests stated now, run on each admitted formulation as it is:

- *Exactly representable fields* (A2-exact: tension, affine membrane with rotation, rigid
  motion). The candidate must reproduce all six analytical components within the A0/A1
  allowances. Bending is not in this set, because a quadratic `w` is not representable by the
  bilinear Q4 or linear S3 interpolation.
- *Bending and in-plane bending* (A2-bend): error against the analytical field and its
  convergence rate, compared between P1 and P2.
- *Numerical-energy split*: physical versus PL/hourglass/drilling energy in the submodel under
  P1 and P2. For Q4 the permitted diagnostic variations are `pl_stabilization` and
  `hourglass_stabilization` (`e4_pl_element.py:5580`), run in a diagnostic route only and never
  as a production change. S3 V2D has no tuning parameter and forbids legacy stabilisations
  (`e4_pl_s3_v2d_element.py:599-600`), so it is compared at its fixed formulation only.

Outcomes are P1 preferred, P2 preferred, or inconclusive. Exact fields can give zero numerical
energy under both, so a tie is expected to be possible. On an inconclusive result the owner
decides with the recorded evidence; no default is picked automatically. The default is never
chosen by which option passes A3 or A5. P1 at junctions is constrained further by D6.

**D4 Position convention.** A fine boundary node `x_f` corresponds to a point `x_p` on a
source element. Driven values are `u(x_f) = u(x_p) + θ(x_p) × (x_f − x_p)` and `θ(x_f) = θ(x_p)`.
This reproduces rigid motion exactly. For planar faces `x_f = x_p`. The facet-position error on
cylinders is quantified separately from solution error (A6).

**D5 Constraint-reduced driven trace.** Eccentric stiffener nodes are slaves of
`InterpolatedBeamShellMPCElement` (`mesh_gen.py:131`), and connection slaves are slaves of their
equations, so the submodel retains those couplings and never fixes a slave directly. C0
measured the current behaviour: prescribing an element-provided coupling slave refuses
(`CONSTRAINT002`, multiple dependent definitions), even with consistent values, while a slave
of a model-level `ConstraintEquation` is re-pivoted by the audit and accepted. The defensive
check at `ANYsolver/src/anysolver/assembly.py:177-181` runs after that reduction.

The trace is defined after eliminating the retained internal constraints. Write them as
`u = T_i q + c_i`. The intended trace on the cut is a set of equations `B u = g` covering every
cut DOF the policy drives, slaves included. They are imposed on the independent coordinates as
`B T_i q = g − B c_i`. Admission requires this system to have full row rank and be consistent.
Otherwise the case is refused, because the intended slave trace is not determined (for example
`u_s = ½u_m1 + ½u_m2` with only `u_m1` on the cut leaves `u_s` partly undriven unless the
equation on `u_m2` is admitted by the rank test).

The work-conjugate interface force is recovered in the same reduced space: after removing the
retained-support reactions, the multipliers `λ` of the boundary equations satisfy
`(B T_i)ᵀ λ = T_iᵀ (K u − f)`, and `Bᵀ λ` is the interface force on the cut DOFs. For `u_s = u_m` the generalized reaction is `r_m + r_s`, not `r_m` alone; the existing
master-equivalent force bucket (`assembly.py:1482-1492`) is the precedent. C0 includes a small
analytical MPC example that checks both the displacement and the reaction.

**D6 Correspondence.** A fine node's candidate source elements are restricted by lineage:
submodel face, then source face through the closure maps and replacement history, then the
global mesh's `elements_of_face` for that source face, filtered by element activity.
`MeshElementBVH.locate_all` already accepts an element filter. A node on an edge shared by
several source faces is admitted only where the source mesh is conforming there and the
candidate values agree within the A0 allowance. Coincident or nearby disconnected sheets are
never resolved by nearest residual; unresolved ambiguity is a typed refusal.

A shared node keeps the set of all incident source faces, not one arbitrary face, because it has
no unique shell normal. Under P1 each incident face contributes its two in-plane rotation
directions. If the combined directions have rank 3 (a non-coplanar junction), all three
rotations are driven at that node, so P1 reduces to P2 there. If they have rank 2 (coplanar
faces), the shared normal rotation is released. Inconsistent normals within tolerance are a
refusal. C0 fixes this rule and its rank tolerance.

**D7 Attributes.** Carried over attribute by attribute (section 5.4), never by generic clipping.

**D8 Sources.** v1 drives from a successful linear static result only. Modal and buckling
transfer need their own contracts for normalisation, prestress and eigenvalue meaning, and are
not "the same solve". One source case drives one submodel case; each case has its own
prescribed offset `u0`.

## 4. Evidence: what exists and what it does not guarantee

### ANYsolver

| Item | Where | What it does / does not do |
|---|---|---|
| Prescribed values | `boundary.py:122` `BoundaryCondition`; `get_constrained_dofs` gives `(dof, value)` | One value per DOF name per node set. Per-node values need a new BC class with the full protocol. |
| Affine reduction | `analysis_session.py:176` `ConstraintPlan` (`u0`, separate structure and value keys) | Value-only changes can in principle reuse the reduced operator. Reuse across replaced BC objects must be verified, because revision bumps may invalidate caches. |
| Fixed vs slave conflict | `assembly.py:177-181`; `constraint_audit.py:347-400` | Element MPC slaves enter the audit unreduced, so prescribing one refuses (`CONSTRAINT002`). Model equations are re-pivoted against supports. Drives D5 (C0 evidence). |
| Affine equations | `fe_core.py:697` `add_constraint_equation` | Carrier for directional drilling release (P1). |
| Shell interpolation | `elements.py:1850` `compute_shape_functions`; factory `create_shell_element` at `:5758` | Geometric `N_i`: bilinear for Q4, linear for S3. Exact for affine fields and rigid motion, not for quadratic `w`. Q4/S3 production defaults route to E4-PL classes. |
| Drilling | `elements.py:1980`; `e4_pl_element.py:5580` (`pl_stabilization`, hourglass); `e4_pl_s3_v2c_element.py:1461`; `e4_pl_s3_v2d_element.py:599-600` | Stabilised or PL-constrained quantity, not a free physical rotation. S3 V2D forbids legacy stabilisations and has no tuning parameter. Drives D3. |
| Linear-solve certificate | `assembly.py:585` `_backward_error`; limit `assembly.py:70` | Normwise relative backward error `‖Kq − f‖∞ / (‖K‖∞‖q‖∞ + ‖f‖∞)` on the reduced system. It is not a forward-error bound on reconstructed displacements, interpolated values or interface forces. |
| Eccentric beam coupling | `mesh_gen.py:131` `InterpolatedBeamShellMPCElement` | Beam node is a slave with offset kinematics. |
| Reactions | `assembly.py:1578-1600` `compute_reactions` | Legacy sum of support reactions and MPC-slave forces. Not the interface force. The diagnostics API keeps support, slave and master-equivalent forces separate (`assembly.py:1478-1492`). |
| Many cases | `assembly.py:1015` `solve_linear_many`; `static_batch.py:164` | One model constraint state; per-case `u0` is not supported. |

### ANYmesher

| Item | Where | What it does / does not do |
|---|---|---|
| Element BVH and locate | `mesh_bvh.py:292`, `locate_all` `:508` with `element_ids` filter, `locate` `:555` | `locate` returns the lowest-residual hit. Nearest is not structural correspondence (D6). |
| Tolerance | `mesh_bvh.py:494-499`, `:210-226` | One number pads the AABB query, bounds the inverse-map residual and sets natural-coordinate slack. Quadratic element bounds use nodal extrema, which need not enclose a curved map. |
| Meshing a geometry | `hybrid.py:4557` `generate_hybrid_mesh_result` | Enough for the submodel, which is a separate geometry. |
| Entity lookup | `mesh.py:224` `nodes_on`, `:287` `elements_on` | Cut edges to boundary nodes; source face to candidate elements. |

### ANYgeometry

| Item | Where | What it does / does not do |
|---|---|---|
| Batch intersections | `batch_intersections.py:436` plan, `:1134` apply | Fragments faces into children, preserves hole loops, records replacement and remaps attachments (`:1036-1037`); splits member axis edges and updates member-use intervals. It does not create inside/outside member descendants. |
| Member split | `model.py:3977` `split_member` | Only clean members. Refuses members with attachments or junctions (`model.py:3998-4003`). |
| Closure | `closure.py:202` `extract_model_closure` | Expands to dependencies: members bring edge uses, attachments bring both parents, junctions bring members, sheets and attachments (`closure.py:377-413`). It can reintroduce outside structure, so it is not a bounded extraction. |
| Spatial query | `model.py:969` `spatial_candidates` | Candidate entities for a box. |
| Cut families | `docs/INTERSECTION_AND_OVERLAP_POLICY.md` | Plane/plane and qualified plane/cylinder analytical; general planar overlay needs the `[planar]` extra; unsupported pairs are typed refusals. |

### ANYfem

| Item | Where | What it does / does not do |
|---|---|---|
| Prescribed attribute | `model/attributes.py:270` `prescribed` | Uniform per entity. |
| Attribute resolution | `solve/build.py:1571` `_attribute_targets`, `:1644` `_nodes_on_target` | Resolves regions and replacement history in one project. |
| Load semantics | `model/attributes.py:285` `Mass` (`total_distributed` or `per_target`), `:330` `PointLoad` (same policies), `:354` `Pressure`, `:372` `LineLoad`, `:395` `SurfaceTraction`, `:425` `WetSurface` (fluid side, interior point) | Totals versus intensities differ. Generic clipping would change load density. |
| Mapping precedent | `structural_preparation.py:357` `source_work_mapping`, `:428` `remap_mesh_to_source` | Special handling for vertices, edges, faces only; other kinds get an existence check. A precedent, not the needed composed map. |
| Compression guard | `solve/run.py:292` `_compressible` | Reads `bc.dof_constraints` and equation `rhs`. A new BC class could evade it. |
| Staleness | `application/workflow.py:213` `_job_is_stale` | Ordinary model, mesh and analysis inputs. No cross-project source dependency. |
| Project format | `io/project_file.py:70` `FORMAT_VERSION = 8` | Submodel definitions need format 9 with backward read. |
| Environment | default `python` on PATH | Does not import the stack. Every run must name its interpreter and installs. |

No existing submodelling code was found in any repository.

## 5. Design

### 5.1 Gaps and owners

| # | Gap | Owner |
|---|---|---|
| G1a | Planar volume cut: imprint box planes on faces, classify descendants inside/outside, return cut edges | ANYgeometry |
| G1b | Attached-member partitioning: split members carrying attachments/junctions, remap them, record structural lineage | ANYgeometry |
| G1c | Bounded extraction: closure policy that cannot reintroduce outside structure; verify every retained face and member span lies inside or on the volume; straddling relations remapped or refused | ANYgeometry |
| G2a | `DrivenDisplacementBC`: per-node values with the full BC protocol (apply, constrained values, audit, persistence, canonical nonzero-offset detection) | ANYsolver |
| G2b | `interpolate_nodal_field`: formulation capability check, D4 transport rule, offset rule for any unslaved beam DOF | ANYsolver |
| G2c | Directional drilling release (P1) as audited constraint equations, with the junction rank rule of D6 | ANYsolver |
| G2d | Constraint-reduced trace and interface force (D5): impose `B T_i q = g − B c_i` with rank/consistency admission; recover `λ` from `(B T_i)ᵀ λ = T_iᵀ(K u − f)` with retained supports removed; signs, frame, reference point, offset moments; numerical (PL/hourglass/drilling) versus physical contributions reported separately | ANYsolver |
| G2e | Error estimation for transfer checks: forward-error estimator, scaling, propagation to interface forces, inconclusive threshold (section 7) | ANYsolver |
| G3a | Lineage-restricted locate; separate physical-distance and natural-coordinate tolerances; conservative bounds for curved quadratic elements; `locate_many` | ANYmesher |
| G3b | Curved correspondence investigation (cylinders) | ANYmesher + ANYgeometry |
| G4 | Composed, persisted identity maps (faces, members, sheets, attachments, junctions, regions, feature-output refs); attribute carry-over; source admission; staleness binding; diagnostic report; format 9; GUI | ANYfem |
| G5 | Per-case `u0` with verified operator reuse; compression guard sees the new BC | ANYsolver + ANYfem |
| G6 | Mesh-only global models | out of v1 |

Ownership follows the ecosystem table: geometry tolerances, clipping and structural identity in
ANYgeometry; discrete location in ANYmesher; interpolation, directional constraints, offset
mechanics and interface forces in ANYsolver; workflow, attribute policy and source admission in
ANYfem.

### 5.2 Shared contracts, agreed before parallel work

```text
TransferHit      fine_node, source_element_id, source_formulation, natural (xi, eta),
                 x_p, distance, inverse-map residual, conforming flag,
                 incident_source_faces (all faces meeting the node, not one) + their sheets
IdentityMap      source handle -> work handle -> descendants -> submodel handle, per kind,
                 with an explicit "unresolved" set (never silently dropped)
Frame            global Cartesian, SI units; per incident face the local shell frame of the
                 source element; junction rotation rule and rank tolerance of D6
DrivenTrace      boundary equations B u = g on cut DOFs (slaves included), the retained
                 internal constraints T_i, c_i, and the rank/consistency admission of D5
InterfaceForce   per cut face: force and moment resultants about a stated point, from the
                 multipliers of D5; sign "force exerted by removed structure on the submodel";
                 physical and numerical parts
ErrorAllowance   estimator, norms and scaling, absolute floors, inconclusive threshold (section 7)
```

C0 also contains the small analytical MPC example of D5 (one slave averaged from two masters,
one on the cut and one interior), checking both the driven displacement and the generalized
reaction.

C0 closure obligations from review round 3, to settle before any downstream slice relies on
the contracts:

1. **Redundant versus inconsistent traces (D5).** Prescribing both `u_s` and `u_m` for
   `u_s = u_m` to the same value gives dependent but consistent equations. The full-row-rank
   test applies to an independent representation of the trace, after removing redundant
   equations that are consistent, while every original compatibility check is kept. A
   redundant equation is reported, an inconsistent one refuses, following the existing
   constraint audit (`ANYsolver/src/anysolver/constraint_audit.py:303-324`: `CONSTRAINT007`
   redundant warning, `CONSTRAINT005` inconsistent error). The force aggregation onto the
   independent equations is defined, so ordinary attached-beam boundaries are not refused.
2. **Allowance scaling and zero-load semantics (section 7).** The condition estimate and the
   backward error are computed for the same scaled operator with compatible norms, or an
   explicit conversion is stated; the existing certificate uses the unscaled reduced system in
   the infinity norm (`assembly.py:585`). Force floors are nonzero when the applied load
   `P = 0` (rigid motion, purely displacement-driven cases), for example a roundoff allowance
   built from `‖K‖` times the displacement scale.

### 5.3 Pipeline (one submodel job)

```text
 1. SubmodelDefinition {volume, fine size, transfer policy, tolerances, source job+case}  ANYfem
 2. Admit source: successful linear static case, full 6-component field, artifact present ANYfem
 3. Detached clone; planar volume cut (G1a); attached-member partitioning (G1b);          ANYgeometry
    bounded extraction with inside verification (G1c) -> submodel geometry + maps
 4. Compose identity maps (G4); carry attributes per section 5.4; refuse unresolved        ANYfem
 5. Mesh the submodel geometry                                                            ANYmesher
 6. Driven set from the submodel's constraint structure (D5)                              ANYsolver
 7. Correspondence with lineage-restricted locate (D6, G3a)                               ANYmesher
 8. Interpolate with transport rule (D4, G2b); build DrivenDisplacementBC (+P1 equations)  ANYsolver
 9. Solve                                                                                 ANYsolver
10. Interface forces (G2d); transfer/equilibrium diagnostic; persist with lineage          ANYsolver+ANYfem
```

### 5.4 Attribute carry-over table (v1)

| Attribute | Rule |
|---|---|
| Materials, plate/beam sections, beam offsets and orientation | Carried to every inside descendant through the identity map; an unresolved assignment refuses. |
| Coordinate systems, units | Copied unchanged; geometry is not transformed, so coordinate-dependent loads keep their meaning. |
| Supports | Inside: carried. On an original model boundary inside the volume: carried. Touching the artificial cut: carried; the driven trace treats a consistent support row as redundant and refuses an inconsistent one (C0 change; was a blanket refusal). |
| Pressure, surface traction, line load (intensities) | Carried to inside descendants with the same intensity, side and frame. |
| Point load, mass with `per_target` | Carried when the target is inside; refused when on the cut. |
| Point load, mass with `total_distributed` | Carried only when the whole original scope is inside; otherwise refused in v1 (clipping would raise the density). |
| Gravity and accelerations | Re-applied; mass from sections inside. |
| Wet surface and wet pressure | Carried only when the whole wet scope is inside, with identical fluid side and interior point; per-element side checked equal to the global elements' at matching positions; otherwise refused. |
| Connections (rigid/distributing) | Carried only when every tied target is inside; otherwise refused. |
| Refinements | Kept when their zone overlaps the volume. |
| Imperfections, thermal or initial-strain loads, element activity changes | Refused in v1. |

Artificial cuts are distinguished from original free or loaded boundaries in the geometry
result (G1a returns cut edges separately).

### 5.5 Staleness binding

A submodel result is bound to: source job id and case, source result artifact hash, the source
node and DOF layout, source formulation and section identities, element activity state,
geometry revision, units and frame, and the submodel's own volume, tolerances and transfer
policy. Any change invalidates the submodel result. Editing the submodel invalidates only the
submodel. Submodel definitions are excluded from the parent model hash, so adding a submodel
does not stale the parent's jobs.

### 5.6 Reporting

Results use the ordinary ShapeView/Field path on the submodel. A **transfer/equilibrium
diagnostic** shows interface force and moment resultants per cut face and the transfer
residuals. It is labelled as a diagnostic: matching prescribed displacements is largely
tautological and does not prove the global model was adequate.

### 5.7 GUI (last)

Submodel task panel beside the refinement form (`ui/qt/tasks.py:61`): box by numbers or picks,
fine size, source job and case, read-only cut preview, create, run. Results browser gains a
global/submodel switch and the diagnostic. Tree shows the submodel under its source job.

## 6. Slices

Order is by dependency; no duration estimates are given.

| Slice | Owner | Deliverable | Exit evidence |
|---|---|---|---|
| **S0 spike** (done 2026-10-09, see s0/RESULTS.md) | scratch, no owner change | Named interpreter and installs. Element-aligned A1 and A2-exact on a planar plate first; A2-bend as an error and convergence measurement; the rotation sign convention confirmed. Then a plate with a hole where the global model *has* the hole, fine submodel around it. A no-hole global source is a separately labelled approximation experiment. Hand-built subgeometry; uses the solver element factory and authoritative normals for S3. Prototypes the condition estimate and the allowances of section 7. | Table of results, the measured allowances, and the list of real gaps. States explicitly that hand-built geometry proves nothing about cuts, bounded closure or member partitioning. |
| **C0 contracts** (written 2026-10-09, owner review pending, see c0/README.md) | all four | Section 5.2 types and semantics, including DrivenTrace, the junction rank rule and ErrorAllowance, agreed and written into each owner repo's docs; the D5 MPC example written as a test specification | Owner review of each contract |
| **S1** (done 2026-10-10, s1s3/README.md) | ANYsolver | G2a-G2e, G5 | A0 including the D5 MPC example; A2-exact and A2-bend; drilling decision tests (D3) with the outcome recorded, inconclusive included; interface-force tests with known resultants; distinct `u0` per case with operator reuse shown in session diagnostics; refusal of unadmitted formulations |
| **S2a** | ANYgeometry | G1a, G1c for planar shells without members | Plate, plate with hole, T-junction cuts; inside verification; strict audit passes; typed refusals |
| **S3a** (done 2026-10-10, s1s3/README.md) | ANYmesher | G3a for planar faces | Lineage-restricted locate; disconnected coincident sheets refused; conforming shared edges agree to roundoff |
| **S4a** | ANYfem | Planar shell-only headless end to end | A1-A4 runnable; format 9 round trip; staleness tests |
| **S2b** | ANYgeometry | G1b attached-member partitioning | Stiffened panel cut through two stiffeners; attachments and junctions remapped with lineage |
| **S4b** | ANYfem + ANYsolver | Attached stiffeners with constraint-aware driven set | A5 |
| **S3b/S4c** | ANYmesher, ANYgeometry, ANYfem | Cylinders: curved correspondence, conservative bounds, transport rule | A6 and rigid-motion tests on cylinders |
| **S5** | ANYfem | Qt GUI | Packaged-artifact smoke test |
| **S6** | all | Level B assessment | Independent assessor named before the run; frozen cases, normalisations, resource limits |

S2a and S1 can run in parallel after C0 because they touch different repositories.

## 7. Acceptance plan (frozen before the assessment run)

Domain: linear elastic, small displacement, isotropic steel, SI units, admitted formulations
of D1, planar faces (A1-A5) and cylinders (A6). Direct solver. Every case runs on one
workstation within the workspace resource policy.

Tolerance rules. The C0 definition in `ANYsolver/docs/SUBMODEL_TRANSFER_CONTRACT.md` section 6
supersedes the proposals below where they differ (same equilibrated operator for backward error
and condition number, units carried by the solver's scaling, matrix-vector roundoff floor for
forces). Algebraic cases (A0, A1, A2-exact) use an **estimated error allowance**, and
the plan does not call it a rigorous bound. The solver certifies only the normwise backward
error `η` on the reduced system (`assembly.py:585`, limit `1e-10` at `assembly.py:70`). C0
freezes the following, with the values below as proposals:

- *Scaling*: translations in metres; rotations multiplied by a characteristic length `L` (the
  volume diagonal), so one infinity norm applies to the mixed vector.
- *Solve allowance*: `‖δu‖ ≈ κ̂₁ · η · ‖u‖`, with `κ̂₁` a 1-norm condition estimate of the reduced
  operator computed from the existing factorization. The global and the submodel solves each
  contribute, and the allowance is the sum of both propagated estimates.
- *Interpolation allowance* (A0): a stated multiple of machine epsilon times the largest nodal
  value for evaluating `Σ N_i u_i`, plus `‖∇u‖` times the inverse-map residual for the position
  error.
- *Interface-force allowance*: the solve allowance propagated through the D5 force operator,
  plus absolute floors tied to the total applied load `P` (N) and `P · L` (N·m).
- *Inconclusive*: if `κ̂₁ · η` exceeds a stated threshold (proposed `1e-6`), the algebraic
  comparison is reported inconclusive, neither passed nor failed.

Discretisation cases use an explicit error budget.

| Id | Case | Oracle | Criterion |
|---|---|---|---|
| A0 Transfer units | Interpolation and transport on single elements and patches, plus the D5 MPC example | Affine fields, rigid motions, hand-solved MPC case | Within the interpolation allowance for every admitted formulation; MPC displacement and generalized reaction within the force allowance; ambiguity cases refuse |
| A1 Restriction | **Element-aligned** box: the submodel mesh is the global elements inside, same formulation, geometry, loads partitioned to the retained elements, all MPCs to removed structure exposed at the driven interface | The global solution and the D5 interface force evaluated with the global `u` | Interior displacements within the solve allowance; per-cut-face interface resultants (with signs) within the force allowance. Closed-cut total resultants are not used, because they can vanish. Inconclusive if the conditioning threshold is exceeded. |
| A2-exact Patch fields | Flat plate in the xy-plane, patch-test-admissible meshes, consistent uniform edge tractions. (a) Uniaxial tension: `u = σx/E`, `v = −νσy/E`, `w = 0`, `θx = θy = θz = 0`. (b) Affine membrane with rotation: `u = εx·x + (γ/2 − ω)y`, `v = (γ/2 + ω)x + εy·y`, `w = 0`, `θx = θy = 0`, `θz = ω`, stresses from plane-stress Hooke. (c) Rigid motion: `u = a + Ω × x`, `θ = Ω` for arbitrary `a`, `Ω`, no load | Closed form, all six components | Interpolated boundary values within the interpolation allowance; solved interior field within the solve allowance; interface forces in (c) zero within the force allowance |
| A2-bend | Constant-curvature bending by consistent edge moments: `w = ½κx²`, `θy = −κx` (right-handed rotation vector; sign confirmed against ANYsolver in S0), `u = v = 0`, `θx = θz = 0` | Closed form | Not exactly representable by bilinear Q4 or linear S3 interpolation. Boundary values must equal the explicitly interpolated coarse field within the interpolation allowance. Error against the analytical field and its observed convergence order are reported and used in the D3 comparison; no algebraic exactness is claimed. |
| A3 Hole | Flat thin plate, d/W = 0.2, membrane tension, loaded ends far enough that the end effect is below a stated fraction of the budget; global model with the hole; fine submodel around it | Howland (1930), with the exact table and page or a reproducible evaluation recorded before the run; nominal stress on the **gross** section stated; Kirsch K_t = 3 as small d/W sanity | `σxx` at the hole edge at ±90° with a recovery method fixed beforehand. Error budget: reference discretisation by at least three refinement levels with the observed order checked, finite-length error, coarse-transfer error, submodel discretisation. The engineering limit (proposed 3 %) is justified in writing before the run. |
| A4 Cut placement | A3 with box half-widths across the strip of 1.25d, 1.75d, 2.25d (the strip half-width is 2.5d) and along the length 1.5d, 2d, 3d | Reference of A3 in a common interior region | Error reported per placement; no monotonicity required; the recommended placement meets the A3 budget |
| A5 Stiffened panel | Pressure, box cutting two attached stiffeners | Analytical bounds where available; full fine model as comparative evidence only | Error budget as A3; slave/master structure audited; free-standing beam across the cut refused |
| A6 Cylinder | Closed-ended thin cylinder under internal pressure, ends far from the box | Hoop stress `pR/t`; radial displacement `pR²(1 − ν/2)/(Et)` | Within the budget after separating facet-position error; rigid motions within the A0 allowance |
| A7 Fail closed | Empty volume; coincident cut plane; unsupported surface; straddling relation; ambiguous correspondence; driven trace not determined or inconsistent after constraint elimination (D5); inconsistent junction normals under P1 (D6); clipped total-distributed load; stale source; nonlinear source | Typed error | No partial submodel, no silent fallback |
| A8 Comparator | A3 by in-place refinement | A3 reference | Both within budget; written guidance on when to use which |

Independence: the analytical oracles are independent. A full-fine ANYsolver model shares element
and recovery mathematics and is comparative evidence only. Whether a CalculiX reference
(through ANYfileIO deck export) is used as an independent implementation is decided before S6.
A0-A3 inform development and are declared regression benchmarks, not blind validation.

## 8. v1 limits and v2 candidates

v1 refuses: Q8/T6 and mixed order; surfaces other than planes and cylinders; free-standing
beams across the cut; supports on the cut; clipped total-distributed loads and masses;
straddling connections and wet surfaces; imperfections, thermal loads and activity changes;
non-linear-static sources; mesh-only global projects; volumes other than boxes.
v2 candidates: Q8/T6 and mixed order; cylinder/sphere volumes; beam interpolation across cuts;
modal and buckling sources with their own contracts; single-solve tied global/local; mesh-only
globals by element selection.

## 9. Open decisions for the owner

1. Where the submodel lives: proposal is a child document in the same project file, with
   export to a standalone project.
2. Cutting-face insertion versus a native plane-imprint operand in ANYgeometry; S0 informs, S2a decides.
3. Whether to qualify a CalculiX reference as an independent implementation before S6.
4. Whether S1 is dispatched to a Mistral owner per ANYsolver's AGENTS.md allocation rule.

## 10. Task record

```text
Outcome and intended use:        sequential displacement-driven shell submodelling; user gives
                                 a box, gets a finely meshed, driven submodel with lineage
Risk / acceptance level:         S0-S5 Level A; S6 Level B with a named independent assessor
Current implementation:          none in owner repos; S0 spike done 2026-10-09 (s0/RESULTS.md,
                                 scratch scripts on HEAD snapshots)
Next integrated test or result:  S4a (ANYfem planar headless workflow) once S2a (volume cut, done
                                 by the owner) lands
S1 + S3a (2026-10-10):           implemented on branches claude/submodel-s1 (ANYsolver) and
                                 claude/submodel-s3a (ANYmesh); approved by GPT-6-Astra after six
                                 review rounds; 49 + 14 tests; uncommitted (s1s3/README.md).
                                 Written directly by user instruction (no Mistral; Codex reviews)
C0 (2026-10-09):                 contracts written as new untracked docs in all four repos;
                                 both round-3 closure obligations closed; checks in
                                 c0/verify_c0.py; written directly, not dispatched to Mistral,
                                 because governing contracts are reserved lead decisions
Unchanged criteria / authority:  ecosystem ownership; patch-only releases; no new element
                                 physics; resource policy per C:/Github/AGENTS.md
Material findings:               fixed+slave conflict; split_member refuses attached members;
                                 closure is not bounded; compute_reactions is not an interface
                                 force; drilling is a stabilised quantity; locate is nearest-only
Decision and remaining limit:    revision 3 approved for S0 and C0 (review round 3); two C0
                                 closure obligations recorded in 5.2; S0 done; nothing built
                                 in owner repos
```

### S0 outcome (2026-10-09), inputs to C0 and later slices

Full evidence in [s0/RESULTS.md](s0/RESULTS.md). Developmental, not acceptance.

- A2-exact (24 runs, Q4/S3 source and target, P1/P2) and A1 (element-aligned, with a
  complementary outside model) pass within the prototype allowances.
- A2-bend converges at second order; translations-only transfer does not converge, so
  rotations must be driven. Driving `w` and `θ` from a coarse field leaves a shear boundary
  layer at the cut; report errors away from the cut separately.
- D3: both policies are exact on representable fields; P2 reproduces A1 exactly while P1
  perturbs a coupled field by 2.5e-4 relative in rotation.
- C0 ErrorAllowance: use the equilibrated operator (unscaled κ₁ is about 1000 times larger and
  nearly vacuous); include both solves; zero-load force floors are needed (3e-9 N measured).
- C0 InterfaceForce: define the attribution of corner nodes shared by two cut faces.
- A1: adopt the complementary outside model as an independent Newton check (held to 1e-13).
- Hole: coarse-transfer error dominates (−5.4 %, −3.5 %, −1.4 % for three global
  refinements; submodel discretisation about 0.2 %), and a smaller box is worse. Admission
  condition 1 is binding; drive from two global refinements as a diagnostic.
- Owner gaps found on HEAD, independent of submodelling: the butterfly hole fails structural
  preparation, and a neutral trimmed hole fails qualified-S3 repair admission at every size
  tried (`s0/repro_hole_routes.py`). Real cut geometry with holes will hit both in S2a/S4a.

## 11. Review history

- Round 1, 2026-10-09, GPT-6-Astra (high effort) via Codex CLI, read-only, session
  `01a121fa-3931-7232-b3b7-c171a704a79e`: REVISE, 18 findings (8 blocking). Blocking findings
  1-5 and 9 were checked against source and confirmed. Revision 2 addresses all 18.
- Round 2, same session: REVISE, 1 blocking and 4 important findings, all checked against
  source and confirmed. Revision 3: bending removed from the exact tests (quadratic `w` is not
  representable by bilinear Q4 or linear S3) and A2 split into A2-exact and A2-bend with full
  six-component fields; certified backward error distinguished from an estimated forward-error
  allowance, with scaling, propagation, floors and an inconclusive outcome; drilling decision
  tests made per formulation with an inconclusive outcome and no implicit coefficient change;
  constraint-reduced driven trace and work-conjugate interface force (D5, G2d) with a small MPC
  example in C0; junction rotation rank rule (D6); closure citation extended to `:413`; D7
  reference corrected.
- Round 3, same session: **APPROVED** for starting S0 and C0. New citations verified by the
  reviewer. Two important C0 closure items recorded in section 5.2 (redundant versus
  inconsistent traces; allowance scaling and zero-load force floors); the cited constraint
  audit behaviour was checked against source. S0 outcomes remain developmental evidence; C0
  must settle the contracts before downstream implementation relies on them; S6 retains
  scientific acceptance.
