# Automatic shell meshing: development scope

Interactive mesh jobs treat the selected method as a preference. ANYfem sends
the prepared geometry, requested target size and selected element order to
ANYmesher's `ANYMESHER_AUTOMATIC_RECOVERY_V1` controller. The controller tries
at most three supported routes and records the attempted and selected routes.
The Advanced **Use only the selected method** switch retains strict behavior.
Headless callers that omit automation retain their previous behavior.

The supplied 12-sector cylinder with a mid-height plate meshes at 0.25 m in
linear order from a single Quad-first Generate action. Quad-first's residual
triangles miss the existing S3 admission limit; the existing Automatic route
produces the solver-admitted mesh. No ANYsolver S3 limit or formulation was
changed.

A geometrically valid candidate that remains outside solver admission is
published for inspection with problem cells highlighted. It is saved with its
route and admission record, but is rejected before solver assembly and does
not replace a prior admitted mesh. Cancellation, timeout and stale completion
do not publish an accepted result. The same owner controller is used by the
small shell-only ANYstructure route.

This is a development implementation, not full AM1 qualification. The broader
fixture matrix, installed-package compatibility of the new route and
independent acceptance review remain open. A separate legacy Automatic test
for an undeclared three-Sheet junction currently fails at the source topology
policy and is not evidence of solver admission for that case. Historical SG1
and RA1 scientific decisions and evidence remain unchanged.
