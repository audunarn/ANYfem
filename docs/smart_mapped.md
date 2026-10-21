# SmartMapped

Choose **SmartMapped** in the mesh controls, or use
`project.generate_mesh(target_size=0.25, strategy="smart_mapped")`.
The persisted backend token is `smart_mapped`. Linear and quadratic shells,
beams and structural couplings use the normal project workflow.

SmartMapped starts with regular quadrilaterals and adapts local regions around
holes and angled boundaries. Quality repair keeps the original regular
rectangles fixed. A failing curved-boundary triangle can be repaired together
with an adjacent irregular quad by adding one interior node. Every replacement
must pass the existing angle, mapping and coverage checks; an unresolved case
is reported as a failure.

On supported planar curved boundary bars, quadratic shells retain their true
curve midsides. Straight-sided B3 beam segments use chord midpoints. Their
couplings retain source-edge ownership and include the chord-to-curve geometric
offset as well as the authored beam offset.

## Automatic selection

Automatic retains the existing mapped route for suitable untrimmed geometry.
For other models it asks the mesher once whether SmartMapped is applicable,
after structural preparation. This reads geometry metadata and planar charts;
it does not launch trial meshers or parallel applicability jobs. There is no
global eligibility cache to become stale after geometry changes.

Selection time and reasons are available in the execution diagnostics under
`automatic_selection`. If SmartMapped is selected, the mesh diagnostics also
record that decision. When recovery is enabled, its `automation` report records
the method that actually completed, including any rejected earlier attempt.
Applicability is not a guarantee that the final quality checks will pass.

The current explicit route requires supported planar charts, straight or arc
boundaries and a uniform target size. Local size refinements, explicit edge pins,
precomputed seeding and source-point cuts are not admitted. Automatic retains
its existing route when these controls make SmartMapped inapplicable. Other
curved-beam scopes retain their existing refusal.

SmartMapped is implemented in ANYmesh; ANYfem consumes its public strategy and
eligibility API. The older private prototype context is not used by production
dispatch.

Circular boundaries use a SmartMapped approximation profile limiting the analytic
radial error of each quadratic arc interpolation to 0.05 mm. Both element orders
use the same boundary stations. This may add local divisions independently of
the global target size; it does not change the geometry owner's tolerance.
Regular seed nodes are fixed during optimization and quality repair.
