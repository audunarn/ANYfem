# Static inertia relief

In **Solve**, select **Linear static** or **Batch linear static**, then enable
**Mass-based inertia relief** beside the constraint controls. This selects
**Free-floating (nullspace)** and disables generalized load balancing.

Leave **Reference point** empty to report acceleration at the centre of mass,
or enter a global point in metres. Turning relief off removes the reference
point from the submitted solve options. Saved analyses restore these controls
directly; advanced JSON is not needed.

The solver uses the selected structural mass to calculate rigid-body
accelerations and balancing inertial loads. Batch cases retain their individual
accelerations and diagnostics. The result summary and run log report inertia
relief, and saved result artifacts retain its acceleration and balance metadata.
Inertial loads are not reported as support reactions.

Use one connected free-free linear beam/shell assembly with valid mass and
compatible internal constraints. Unsupported analyses and incompatible settings
are refused. Static relief frames do not constitute a transient floating-body
response calculation.

## Delivery verification

This UI integration is verified with the matching development dependencies;
the combined application PR still has independent material-source-transfer and
dependency-delivery blockers. It does not establish a clean published ecosystem
installation or hydrodynamic qualification. Exact source identities, test
receipts and the Opus 5.5 medium review are recorded under
`C:/ANYworkspaceWork/floating-wind-wave-delivery-20261028` and
`C:/ANYworkspaceWork/anyfem-relief-ui-opus-review-20261028`.
