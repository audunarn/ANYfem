# Experimental native BL5 mixed shell J2 consumer

The explicit `anyfem.experimental-ge-bl5-panel-native-mixed-j2-v6` input selects
the public solver policy `BL5_NATIVE_FIBRE_MIXED_SHELL_MATERIAL_V1_DEV` through
the existing `ExperimentalGEBL5Panel` and mesh-native BL5 Project selector.
It requires all-S3 or mixed Q4/S3 shell topology and its isotropic J2 curve.
Physical axial/biaxial fibres and admitted native generalized members remain
owned and validated by ANYsolver; beam definitions retain their exact bytes.
Mixed Q4/S3 uses the selected objective Q4 pullback. Schemas v1–v5 retain their
existing topology, material-policy and format guards.

`examples/stiffened_shell_native_fibre_bl5_mixed_j2_consumer.py` constructs an
input, saves/reopens the standalone case and a mesh-only Project, preflights
the public owner, seeds ordinary nodal dead-translation loading, loads/unloads
under fixed physical translation targets, and restores a checkpoint into a
fresh owner before continuing. Its `workflow.json` preserves named public
`shell_s3_material`, `beam_fibre_material`, `beam_generalized_material` and
`shell_layer_alpha` outputs. `physical_fibre_members` binds accumulated history
to actual recovered fibre element IDs; generalized-member station history is
excluded from the physical-fibre observable. Inputs are separate from solver
checkpoints and results; reopening inputs does not restore accepted state.

The v6 consumer admits ordinary nodal dead actions only. Native adaptive/event,
chart/rebase, arc, follower/body/distributed, spectral and qualification routes
are outside this policy. Public solver typed validation/refusal and cancellation
propagate; a refused restore or cancelled unaccepted increment preserves accepted
state, while callback cancellation retains the accepted prefix for fresh restore.

This is a development consumer, `EXPERIMENTAL_NOT_QUALIFIED`. Scientific
acceptance, independent review, installed artifact acceptance, release readiness,
the full programme and Revision8 completion remain separate OPEN obligations.
