# N2c full-model identity gate (S3 T6)

Question it answers: does preparing the full tripod model (N2c, ring_split seam, 561 faces in, 1269 out) with the thread route ON
give a result **bit-identical** to the serial route, and identical to the frozen reference? Everything is compared after masking
UUIDs and 64-hex strings (they differ run to run).

## What runs where
| Part | Where | Proves |
|---|---|---|
| `tools/ci_n2c_gate.py` + `tests/test_ci_n2c_gate.py` | every nightly (`gate-selftest`, GitHub-hosted) and in the normal test suite | the gate fails closed and names the group; NOT that the model still matches |
| `n2c-identity` job | only when repository variable `N2C_ENABLED=true` and a self-hosted runner labelled `n2c-model` exists | the model itself; **skipped (not green) otherwise** |
| saved-output tests (2 skips off-machine) | owner's machine, `ANYFEM_N2C_SAVED` (default `C:/ANYworkspaceWork/wt-identity/harness-5377460`) | the committed constants reproduce from the 2026-11-13 outputs |

Model availability: the model (`AIP-Gen2-full-tripod-no-tower.MODEL-N2c-rotated-arms.SIMPLIFIED-EXERCISE.anyfem`, sha256 969cc5f9...)
is a private local file and is **not in the repository** (not added; size/licence not reviewed for publication). The measurement child
also imports unpublished local harness modules. A GitHub-hosted runner therefore cannot run it. The job needs a runner holding the model
and a staged driver (variables `N2C_DRIVER`, `N2C_PY_SERIAL`, `N2C_PY_THREAD`; contract in the workflow header).

## Gate exit codes
0 pass; 1 FINDING (a digest, face count, NumPy version, or serial-vs-ON difference; a run that was not what it claims); 2 INPUT (missing/empty/
malformed file, bad constants: fail closed); 3 REFRESH REQUIRED (only non-time counters differ from the constants, digests unchanged,
serial == ON); 4 KERNEL (see below).

## BLAS kernel pin (S3 kernel-pin, after T7b section C)
The full model is **BLAS-kernel dependent** (NumPy 2.4.6, serial, full model): the frozen constants are the OpenBLAS **SkylakeX** kernel;
`OPENBLAS_CORETYPE=HASWELL` gives model 2aa9dd33a986 / plan 3a37c931e055 / counters 5a804ef5cdb6, and NEHALEM makes preparation refuse
(StructuralPreparationError, unresolved arrangement edge face:550). So:
- The driver environment pins `OPENBLAS_CORETYPE=SKYLAKEX` for **both** the serial and the thread-route run (workflow job env `n2c-identity`;
  any driver must pass it to the child unchanged and must not set another value). A runner CPU without AVX-512 cannot run it.
- The child writes `child-result.json["blas"]`: `requested_coretype` (the env value, or null) and `openblas_pools` (threadpoolctl
  `internal_api`/`architecture`/`num_threads` of every OpenBLAS pool). `tools/ci_blas_kernel.py` produces the same record and, with
  `--constants`, is the workflow's preflight under both interpreters (exit 4 before any model run).
- The gate exits **4 KERNEL** (named, distinct) when the record is missing or malformed, no OPENBLAS_CORETYPE was requested, the requested value is not
  the frozen one (exact spelling), the effective architecture is not the frozen one, or the effective architecture differs from the request
  (OpenBLAS may fall back silently, e.g. SKYLAKEX on a CPU without AVX-512: that is detected by the effective architecture, never passed).
  Runs recorded before the pin (2026-11-13, no `blas` field) are therefore refused; the saved-output tests add a field marked `added_by_test`.
- Constants are keyed to the kernel (`blas_kernel` in `tools/n2c_identity_constants.json`, schema 2). A refresh for another kernel is a **separate,
  independently reviewed constants file** (e.g. `n2c_identity_constants.haswell.json`, passed with `--constants`), never overwritten into this
  one; this file is only ever changed for SkylakeX. Nothing selects a constants file automatically.
- Open: the NEHALEM refusal is kernel-dependent robustness in ANYgeometry (not addressed here).

## Refresh policy (constants: `tools/n2c_identity_constants.json`)
- Never refreshed automatically; no workflow step writes the file. Changing it is a deliberate commit that is independently reviewed.
- A changed **prepared-model or plan digest is a finding**: investigate the code change; do not refresh until the change is explained as intended.
- A changed **counters sha with identical model and plan digests** (exit 3) is a *reviewed refresh*: state which counters changed and why
  (e.g. new work/box counters), show serial == ON, commit with the old and new values in the message.
- A re-freeze is for the same kernel only (see above).
- Re-freeze from a run pair that passes `serial == ON`; record the code SHAs, interpreters, NumPy version and model sha in `frozen_from`.
- NumPy is pinned at 2.4.6 (the gate fails if either run reports another version); a NumPy bump is a model/plan digest question first (T7).

## Limits
Masked digests, counters and face counts only; replay/face200/pair digests are not child outputs and are not gated. The child's
reported model sha is not in its output; the driver must assert it. Failure notice goes to whoever watches the workflow (open decision).
