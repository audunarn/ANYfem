# Owner-held full-cut NURBS mesh qualification

Owner decision: 2026-11-05. The following two registered full-cut NURBS meshing
capabilities are held outside release acceptance:

- `tests/test_nurbs_cut_mesh_integration.py::test_principal_public_cut_and_default_project_mesh`
- `tests/test_nurbs_cut_mesh_integration.py::test_dome_closed_loop_split_default_project_mesh`

Both original positive bodies, fixtures, inputs and assertions are retained.
Their unconditional `pytest.mark.skip` is an explicit owner hold, not numerical
success, an expected-failure match, or qualification. There is no environment
flag to bypass it. A normal CI run must not consume these retained model cases.

## Evidence and current limits

The principal case failed in all eight headless lanes of
[run41964321706](https://github.com/audunarn/ANYfem/actions/runs/41964321706),
F843d580636274c09904befa126d7b5bf69f98048, with
`PlaneNurbsNotCertified: qualification budget exhausted`. Every lane stopped at
that first failure after1445passed/122skipped. The declared sampling policy has
200,000boxes, depth32 and16Newton iterations. The actual exhausted local versus
ambient counter and failing arithmetic interval were not retained; no specific
counter value or algorithmic cause is inferred. The caller's bound remains
0.0025 and no Work limit, tolerance or source admission rule is enlarged.

The split-dome case was **unmeasured** in that CI run because fail-fast stopped
at the principal case; it is **not failed or qualified** by those artifacts.
Earlier failed/partial evidence is preserved separately and is not converted
into a success by this hold. The eight lane logs and two representative complete
JUnit/artifact trees are retained in the integration evidence under
`r10-headless-final-taxonomy`; original test bytes and exact hold proofs are
retained under`r10-nurbs-cut-capability-hold`.

## Requirements that stay active

The principal preview/topology/material/sheet-use test remains required.
Ordinary curved-support, source/owner, mesh quality, cancellation, serialization
and other integration gates remain unchanged. This hold grants no broader
NURBS capability, no altered approximation error, and no release evidence for
these two full-mesh cases.

## Future unhold

Fresh owner-approved qualification is required for each held capability,
with an explicit source-controlled amendment removing its unconditional hold,
a defined source/runtime/input profile and newly authorized bounded execution.
Consumed execution authority is not reusable. Removing a hold is not itself a
positive result: the original positive body and all its scientific assertions
must pass under that fresh authority before acceptance can be claimed.
