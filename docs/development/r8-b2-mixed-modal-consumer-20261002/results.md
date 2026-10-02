# Experimental mixed B2 modal consumer — 2 October 2026

ANYfem now persists explicit virgin mixed Q4/native-S3 and all-S3 B2 reference
mode inputs through the existing experimental Project bridge. The separate
`anyfem.experimental-b2-mixed-reference-modal-panel-v1` schema owns canonical
bytes and reconstructs detached definitions. Reopen performs no assembly or
factorization. Historical static and footprint-modal schemas retain their meanings.

The consumer delegates modes through the public solver facade, validates declared
facet mass policies, preserves failed/cancelled numerical status and closes owners.
Programming exceptions propagate. It rejects static/finite controls, unsupported
mass policies, missing public exports, footprints, prestress, MPCs and nonzero
prescribed offsets for this input type. Persistence is a virgin definition, not
a material-state checkpoint or qualification.

Native S3 facets supply lumped translational inertia and omit **all** rotary
contributions. Q4 facets, when present, supply physical director inertia including
shared nodes. Beam inertia is explicitly declared. The only solver change is a
thin public alias of its unchanged Q4 mass-policy identity.

## Checks actually executed

Initial source checks: 223 passed / 1 failed. The new legacy fixture incorrectly
called a nonexistent `to_dict()` method; it was corrected to decode the existing
owned payload. The affected source check then passed 1/1. No legacy API was added.

Fresh paired installed wheels: **224/224 passed** across six focused consumer
modules, including 57 new cases. All 98 consumer / 391 solver Python modules match
actual source, wheel and installed bytes. All 275 observed imports use those sites.
Mixed/all-S3 roundtrip, free interior normal spin, public-owner parity, strict
negative input, ownership, pre-cancellation, failure propagation, cleanup and
historical consumers are covered. No unchanged solver campaign was repeated;
its mechanics are unchanged from the preceding installed 429-case record.

Both installed Project examples complete save/reopen/modes with one factorization
and 21 linear solves. Illustrative first frequencies are 0.6563227 Hz (mixed) and
0.4199549 Hz (all-S3). They use different facet inertia and are not engineering
references. A review wording correction was verified by rerunning only the two
affected examples; their previous outputs remain retained.

Python 3.14.2, NumPy 2.4.6, SciPy 1.17.1, Numba 0.65.1; effective numerical
pools and Numba one thread, JIT off. Installed supervision took 12.024 s with
203,513,856 bytes sampled process-tree RSS, inside 600 s / 1 GiB. Seven sampled
process threads include infrastructure; this is not seven numerical threads.
No speedup is claimed.

## Exact artifacts and boundary

Solver wheel SHA256:
`80ca5f5e882b6aba16a25e373b78b0928f1964724351ea4fd641a5ad55a578d5`.
Consumer wheel:
`93b21a205dd587740b60261f68de119355f33ce074e6929f9ea4b0d46eefbbd0`.

[Raw manifest](raw-v1.json), [raw archive](raw-v1.zip),
[review correction manifest](examples-v2.json), [example delta](examples-v2.zip),
[independent Read review](review.md). Initial failure, repair, invoked runners,
fixtures, actual import/settings records and both wheels are retained. Rebuild
and site duplicates are omitted; wheel/source identities permit reconstruction.

This closes this experimental input/consumer slice, not M3/M4, physical frequency
accuracy, complete F07/F12/G4B, external reference, scale/platform or production
acceptance. No different-model-family acceptance, formal F03 run, main merge,
default/version change or publication. Full programme remains active.
