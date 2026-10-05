# Experimental elastic native-fibre BL5 consumer

`ExperimentalGEBL5Panel` accepts the explicitly selected solver policies
`BL5_ELASTIC_NATIVE_FIBRE_V1` (pure Q4) and
`BL5_ELASTIC_NATIVE_FIBRE_MIXED_SHELL_V1` (actual S3 with optional Q4).
Their input schema is `anyfem.experimental-ge-bl5-panel-native-elastic-v5`.
Older generalized/material schemas, default policies and point meanings retain
their existing domains. No dynamics or plastic native BL5 capability is added.

The existing case and native Project routes store exact `BeamDefinition` bytes,
declared connectivity, fixed coordinates, five-row BL5 lines and explicit
policies. They store inputs; accepted states remain public solver checkpoints.
Construction, preflight, finite stepping and physical recovery all use the
solver facade. Fresh-owner restore and continuation use its unchanged binary
checkpoint schema and model identity. Yielded histories retain the solver's
typed `BL5NativeElasticDomainError`; incompatible/corrupt checkpoints and
unsupported request domains retain their solver refusals.

Run `examples/stiffened_shell_native_fibre_bl5_consumer.py --output <directory>
--shell q4|s3|mixed`. The example saves a case and Project, reopens both, solves
an accepted prefix, restores its checkpoint into a fresh owner and compares
continued checkpoint bytes and physical recovery. Optional family, member and
BL5 basis selectors use declared public physical inputs.

Fresh development evidence is under
`.qualification/r8-native-bl5-consumer-20261005-v1`. This consumer remains
experimental and is not scientifically or production qualified. Independent
family review, scale qualification and root acceptance remain separate gates.
