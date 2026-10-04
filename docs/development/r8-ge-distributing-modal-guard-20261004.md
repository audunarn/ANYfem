# Reject unsupported distributing attachments during modal conversion

The GE point-modal converter now rejects a nonempty solver
distributing_footprints option before constructing its reduced point definition.
It previously could omit the new law. Existing schemas and older solver support
are preserved through getattr's empty default. No new distributing workflow is
admitted by ANYfem.

One focused existing unsupported-scope test passed against installed ANYsolver
wheel f0267b0f5803ce69c1a181bfd7aa22bde06fa1aafdb65cbd56bba4d550745b4e
and actual source ANYfem (.13s; whole2.134s/sample178933760B,pools1/JIToff).
Canonical LF source additionally passed the same changed-byte predicate (.11s;
whole2.229s/sample175415296B). Original mixed-ending bytes/run preserved; byte
comparison confirms no code difference beyond line endings. Final source
SHA22788c4353ef24316ad692c9097852e52f737e0d522949d2a7ea4aebe446ab60.
The probe verified the new field so the conditional control executed. Supporting
independent OpenAI source review clear; full installed consumer/platform and
scientific qualification not claimed. Evidence belongs to the ANYsolver slice:
C:/Github/ANYsolver/.perf2-worktrees/stiffened-main-reconcile-20261001/.qualification/b3-ge-e4-pl/r8-distributing-ge-owner-dev-20261004-v1/consumer-runtime-v2.json.
Root handoff remains the ANYsolver B3_GE_E4_PL_ACTIVE_STATE.md; full consumer
construction/persistence is the next boundary, not this fail-closed guard.
