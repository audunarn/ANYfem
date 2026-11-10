# Shell and solid submodelling review, 10–11 November 2026

The candidate implements regional shell remeshing followed by a new parent solve, shell-to-shell submodelling, and one-way shell-to-solid submodelling in the headless and Qt applications. Each row below applies to the cases, formulations and observables it names. Formal Level B acceptance of the entire shell workflow remains open.

## Candidate identity

| Owner | Source used in the assessment | Review |
|---|---|---|
| ANYfem application | `cc94be08251e5e9b9f872246bf54b2c23ff79af8`, source tree `9b99dfb68386f717dc0b769e0be5469b9f8b577e` | [ANYfem #17](https://github.com/audunarn/ANYfem/pull/17) |
| ANYgeometry | `33dfbaad89a76dd9e925fd6c628602213ce8c2b8` | [ANYgeometry #34](https://github.com/audunarn/ANYgeometry/pull/34) |
| ANYmesher (ANYmesh repository) | `770e900ad2d8f5cc422d8c56021b5793219a47f7` | [ANYmesh #15](https://github.com/audunarn/ANYmesh/pull/15) |
| ANYsolver mechanics | `45f11201a78cdb1f7d2f433ef859ae5928f5d589`, source tree `6236852eac9e6e7a89061231a9f493bcd9281815` | [ANYsolver #69](https://github.com/audunarn/ANYsolver/pull/69) |
| anysolver-results | `e78cccd3a76feb1793a38568f59d6d68f8224cb5`, distribution 2.1.1 | [Results distribution](https://github.com/audunarn/ANYsolver/tree/e78cccd3a76feb1793a38568f59d6d68f8224cb5/packages/results) |
| ANYsolid | `06f3276d9207376316502aca651c3455db984104` | [Backend source](https://github.com/audunarn/ANYsolid/tree/06f3276d9207376316502aca651c3455db984104) |

The installable review pins include the solver's subsequent test-only corrections at `adb0d6ac775d5c266a083e4ffa1bd86b6e7b6ea2`. The application and solver source trees identify the runtime independently of later changes to tests and documentation. For installation and operation see the [workflow contract](../../../SUBMODEL_WORKFLOW_CONTRACT.md) and [14 sibling pins](review-requirements.txt).

## Application and meshing

The two-plate case archives a successful parent job, publishes a child, changes the local element divisions and refuses to publish an already staged child. Save/reopen preserves the source and child artifacts. The updated parent must solve before a second child can run. The test compares actual exterior element numbers and ordered node tuples. A replaced plate's element numbers are retired.

The geometry checks cover clockwise rectangular imprinting, both orientations of the stiffened L-deck and detached volume construction. All 28 registered geometry source-byte pins matched. Use the geometry owner's `clone()` for a detached model. Python's shallow copy retains mutable unit/state aliases. On this candidate 159 geometry tests, 208 mesh tests, 49 repinned mesh checks and all 11 named mesher/application caller checks passed.

A solid child stores its generating oriented geometry and each exterior facet's face owner. A read verifies facet connectivity and ownership. The four through-thickness sides carry the driven coefficients. A uniform pressure is assigned to the specified physical skin. The application archives world displacements, the cut reactions and imposed coefficient values, force and moment about the declared datum, and tensors with physical quadrature coordinates. The native tetrahedral provider and two-way shell–solid mechanics retain their existing qualification gates.

The integrated shell/regional suite passed 153 cases. The solid persistence, jobs and refusal suite passed 119. The Qt source suite passed 18. The Windows packaged smoke built and installed 15 wheels, resolved the Qt entry point and exercised create, solve, display, save and reopen for both child types on the offscreen platform. This evidence concerns the one-way linear-static workflow and the named packages.

## Independent shell assessment

OpenAI Codex agent `/root/solver` assessed the candidate independently of the implementation and fixture authors. The registered shell run used 120 internal solves, with 174 passing numerical predicates and 110 descriptive observations across 15 cases. The measured A0/A1/A2/A7 processes used 443.8743123 of 1000 seconds. The closed-cylinder tranche used 36.9843969 of 400 seconds.

| Case | Result and interpretation |
|---|---|
| A0 | Both Q4 and S3 passed interpolation, rigid offset transport, typed refusals and the hand-eliminated multipoint constraint problems. |
| A1 | Both formulations passed the registered element-aligned restriction, including per-node and per-face interface forces/moments compared with an independent complementary outside solve. |
| A2 | All four Q4/S3 source–target pairs passed the exact tension, affine and rigid tests under both policies. The original physical bending fixture left rigid rotation about global z unrestrained. Its parent condition indicator exceeded the registered limit at all four resolutions. Its exact transfer of supplied displacements cannot certify the physical parent or choose a drilling policy. |
| A3 | Full-plate gross-section stress concentration at the two physical hole rims was 2.825902, 3.085807 and 3.158691 for 8, 16 and 32 divisions per quarter arc. Increasing the end distance at fixed finest ring discretization gave 3.158691. Observed order was 1.8343. The conditional finest Richardson estimate was 0.028404 in Kt. The Howland value 3.14 has no established precision interval here. |
| A4 | All nine registered rectangular placements passed the 3% inter-method screen at both rims against the finest full parent. The preselected rectangle (half-spans 2d and 1.75d) gave 3.152021, or −0.211% at each rim. The coarsest child on its refinement branch failed at 2.996225. The rectangular fixture splits clipped six-edge circular sectors through their existing spoke endpoints and reapplies their inherited supports and section. |
| A5 | **Inconclusive.** CalculiX 2.22 reproduced the isolated section/frame and positive pressure resultant. Neither bending axis of its shell-to-beam connection transmitted the required eccentric rotation. A second native-offset control also failed the physical law. The production comparison stopped at calibration. The historical 3.05–3.71% rotation discrepancies stand. A defensible 3% limit would have to contain the per-sensor difference and the independently measured reference error. Reference convergence for an equivalent stiffened plate has not been established. |
| A6 | The original closed-ended cylinder includes the axial pressure thrust. Both rotation policies and both mesh sizes met the registered 0.5% limits against +40.476190 µm radial displacement and +10 MPa hoop stress. Signed displacement errors were +0.127% to +0.251%. Hoop errors from the committed nodal stress field were −0.425% to −0.100%. |
| A7 | All 22 frozen solver refusal predicates and 22 external geometry, mesh and application tests passed. The original refusal inventory had partial mutation and chosen-case coverage. The subsequent regressions distinguish the selected clipped total load, changed source state, and artifact preservation explicitly. |
| A8 | The production regional update and the box-cut child were applied to the same loaded strip with the hole. Both coarse 4→8 arms failed the 3% screen against the finest full parent. Both 8→16 and 16→32 arms passed at both signed rims. |

The A8 square uses 2d half-spans. Its three pairs of (child, regional) Kt were (2.823428, 2.825762), (3.068489, 3.085705) and (3.151157, 3.158663). The finest child used 21,504 DOFs. The finest regional model used 34,848 DOFs and replaced cells on all six source faces. Preservation of unaffected faces is demonstrated separately on the two-plate problem. The hole measurements compare finite-model solutions at two named locations. Continuum uncertainty and general runtime savings remain unquantified.

The repaired bending fixture constrains the x displacement at the second support on the y-axis. In a separate, prospectively frozen check (`d3452906cedd3c565e12a358cea7b0bc9e6946776024dbda035d6efd80c3c318`), all eight physical parent support matrices had rigid-mode rank six. All 32 child-policy-resolution boundary checks passed with zero measured transfer error. Both policies tied. Measured core transverse-displacement orders were 1.994–2.012. This check used 40 solves and 175.5753033 of 600 seconds. The drilling-energy partition and translations-only comparison remain unmeasured.

## One-way solid assessment

Eleven rigid-motion, affine-field, signed bending-offset and pressure-control tests passed. Independent tilted S3 V2D checks used a nonconstant rotation field and an explicit polynomial transverse displacement. The solver also rejected a deliberately wrong tangential locator and underconstrained disconnected P1/P2 children. The application checks include a cut across parent-element interiors, stale parent data, tampered facet ownership and cancellation.

The original loaded detail is 0.2 × 0.2 × 0.01 m, with 10 kPa on its positive skin. Its archived parent contains 121 nodes and six shell coefficients per node. Five complete child fields were recovered when the original 1,000-second build reached its time limit. Their maximum relative mismatch against the archived Q4 cut displacements was 4.304 × 10⁻¹⁶. The original run stopped before saving its project registry and writing its independent reference decks. The retained HDF5 pairs have their own content and source identities.

The geometry and connectivity causing the cost were preserved. The review mesher adds a fast acceptance proof on verified perturbed Cartesian Kuhn grids at `3823b6912cd54f1f6a913a661a9f6739b3f55f2d`. It uses the unmodified coordinates, cell connectivity, Jacobian, exterior and face ownership checks. It retains the generic intersection search for unrecognised meshes. On the same stored 1,536-cell grid a single cell-validation call took 11.881 seconds on the generic path and 0.044 seconds on the new path, returning identical ordered exterior facets. On the stored 12,288-cell grid the new path took 0.325 seconds and reproduced the archived oriented exterior. These times measure cell validation only. All 164 focused validation, structured-box, volume-consumer and process tests passed, and the packaged Qt test passed with the updated mesher wheel.

The registered shell manifest SHA-256 is `dccf7158ac177e0a2146e2f039e2a49500d36a76906ba48309670435799e50b3`. The hole comparison manifest is `f66a559ea5cb0c3b525ddc9bbce629f218ba0bfcde19e6c47a6105531c123057`. The A5 and solid comparison manifest is `2fd248fc27a67fa9f475ed2f77dabbf76a34702cae703a91733902b0166673da`. The original prototype and failed error estimates remain available in [S0](../s0/RESULTS.md) and [S4b](../s4b/RESULTS.md).

## Loaded solid against CalculiX 2.22

The assessor registered the follow-up under SHA-256 `6455434134bd073f16e97ab70bf6fccbf0e6b7faaa41ec2567378c2d5716272b`. It reused the five saved children, solved the missing 16 × 16 × 8 P2 child and ran six independent brick references. The replacement source project had bitwise-identical Q4 displacement coefficients, coordinates and matching node IDs, connectivity and face lineage. Its document and job identities were recorded separately. The child took 55.68 seconds through solve, publication and reopening. The complete three-stage tranche used 61.3396643 of 785 process-wall seconds.

The frozen reader initially associated the sixth child with the original document and refused it. The assessor verified the saved project registry and both artifact hashes against the authorized replacement document and job. A separate extraction reproduced all six pairs, binding each to the exact source job in its displacement result provenance. It retained all twelve unresolved reference checks for moment and individually rounded force. The archive-reader regression suite passed 41 cases.

| Grid | Order | Center displacement, child / CCX (mm) | Core σxx, child (MPa) | Signed interface work, child / CCX (J) |
|---|---|---:|---:|---:|
| 4 × 4 × 2 | P1 | 2.098379 / 2.097882 | 11.398138 | 18.939755 / 6.998632 |
| 4 × 4 × 2 | P2 | 2.096658 / 2.102267 | 11.053938 | 2.129824 / 2.122834 |
| 8 × 8 × 4 | P1 | 2.096438 / 2.097364 | 11.148686 | 6.426708 / 2.939066 |
| 8 × 8 × 4 | P2 | 2.098571 / 2.098317 | 10.979195 | 1.236638 / 1.166450 |
| 16 × 16 × 8 | P1 | 2.096984 / 2.097403 | 11.024567 | 2.580217 / 1.455683 |
| 16 × 16 × 8 | P2 | 2.097882 / 2.097687 | 10.941029 | 0.886255 / 0.842587 |

C3D8 and C3D20 supply the same-order displacement and work comparisons. The prescribed common stress reference is C3D8, giving finest core σxx of 10.973057 MPa. The finest P2 center displacement differed by +0.195 µm. Finest signed work differed by +77.25% for P1 and +5.18% for P2. The final reference refinement changed the work by −1.483383 J for C3D8 and −0.323863 J for C3D20. Cross-method design-accuracy limits and a continuum work value remain open.

All six child trace and core-volume checks passed 10⁻⁹. Child force and moment residuals were at most 1.437 × 10⁻⁷ and 4.884 × 10⁻⁹ of the physical scales. All six CCX internally summed forces met 10⁻⁶. At the same datum the precision of the individually printed reference forces leaves all six reconstructed forces and moments inconclusive at 10⁻⁶. The independent assessment includes signed displacements, six stress components, force, moment, work, resolution bounds and refinement differences. [Machine-readable observations](observations.json)

The installed Windows Qt smoke passed again with the final pinned wheels. The four review branches retain their hosted check histories. The solver's separately owned contact, replay and qualification failures are tracked in its review alongside the matrix results. [Application checks](https://github.com/audunarn/ANYfem/pull/17/checks) · [Solver checks](https://github.com/audunarn/ANYsolver/pull/69/checks) · [Mesher checks](https://github.com/audunarn/ANYmesh/pull/15/checks) · [Geometry checks](https://github.com/audunarn/ANYgeometry/pull/34/checks)
