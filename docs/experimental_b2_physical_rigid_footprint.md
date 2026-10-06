# Experimental B2 physical rigid-footprint consumer

`ExperimentalB2PhysicalRigidFootprintPanel` stores
`anyfem.experimental-b2-physical-rigid-footprint-panel-v1` inputs and reconstructs
only the public `B2PhysicalRigidFootprintDefinition`. Its explicit public law is
`B2_PHYSICAL_RIGID_FOOTPRINT_DIRECTOR_REFERENCE_V1_EXPERIMENTAL`: five physical
position/director rows per selected node, with independent normal spin. The old
six-row nodal footprint, POINT, mass, modes and material formats retain their
schemas and meanings. Common Q4/member/affine/case syntax uses the existing B2
codec with empty POINT rows; this syntax never creates or solves a POINT owner.

The immutable envelope retains exact public footprint descriptors, frames and
typed recovery selections. Case files use `.anyfem-b2.json`; the existing
mesh-native B2 Project selector constructs, saves and reopens this schema.
Reopening stores inputs, not accepted state or results. Geometry/admission,
constraints, response, section traction, prestress and spectral mathematics
remain entirely in ANYsolver.

The example `examples/stiffened_shell_b2_physical_footprint_consumer.py` performs
standalone and Project persistence, a static solve, compatible 1/10/100-case
batches with selected coordinates/footprints/member stations and public
factorization/chunk counters, then typed physical-footprint reference buckling.
It supplies a small explicit synthetic square-section development fixture from
the public solver examples, in SI units, without an engineering qualification
claim. Batch failure/cancelled prefixes and public numerical reasons propagate.
Callback errors remain errors; they are not labelled numerical success.

`buckling` requires exactly `B2PhysicalFootprintBucklingRequest` and returns the
owner's `B2PhysicalFootprintBucklingResult`, including its actual status/policy.
The consumer does not persist a new prestress, mass or continuation definition.
Static-only storage rejects mass/material inputs via the existing strict codec;
modes/nonlinear/body/material routes are not admitted by this format. Historical
selectors and law substitutions fail closed.

Status remains `EXPERIMENTAL_NOT_QUALIFIED`. Scientific, different-family,
installed artifact acceptance, full programme, release and publication gates
are separate obligations; executed development evidence lives in the assigned
raw qualification directory and does not imply those gates passed.
