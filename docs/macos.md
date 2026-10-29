# macOS developer installation

The first target is native Apple Silicon Python 3.11 or 3.14 on macOS 15.
Compatibility is pending the `macOS developer compatibility` CI results for the
exact candidate commit. Windows-hosted mocked tests do not establish Mac support.
Intel, older macOS versions and standalone `.app` distribution are not yet targets.

**Concurrent feature dependency:** the separate, unpublished ANYfem geometry-cleaner work uses
`anygeometry.cleaner`, which is untracked in the local ANYgeometry checkout and
absent from the pinned geometry commit. Before testing this combined candidate
from a clean hosted install, commit/consume that owner feature and update the
coordinated CI pins. The bootstrap reports this missing capability explicitly.
The isolated Mac CI branch starts from remote main and excludes that unpublished
feature. Diagnosis-specific tests are required when the feature is present in a
candidate; they are explicitly unavailable in a candidate without the feature.

Install Python, Git and Apple's Command Line Tools (`xcode-select --install`).
From the ANYfem checkout, use a **new directory outside all source trees**:

```sh
python3 tools/bootstrap_macos.py --work-dir "$HOME/anyfem-mac-build-1"
"$HOME/anyfem-mac-build-1/venv/bin/anyfem"
```

The command fetches the exact ecosystem commits in `tools/macos-ecosystem.json`,
builds local wheels, installs them with normal dependency resolution into a fresh
venv, runs `pip check`, and checks imports, the native mesher, project persistence
and HDF5 outside the source tree. It requires network access and a compiler.
All optional Python dependencies are included to satisfy the existing installed
licence gate; this does not qualify the separate floating-body solver runtime.
Only the ANY ecosystem source revisions are pinned. Third-party versions resolve
within their declared ranges in each job and are recorded in `install-report.json`
and the freeze log; cross-platform comparison currently spans those independently
resolved environments. Third-party packages must have binary wheels: missing
Apple Silicon/Python wheels cause an explicit installation failure.
ANYgeometry uses its production default backend; ANYmesher's compiled extension
is required. A failed build or resolver conflict is an error, not a fallback.
The standard GUI extra includes WebGPU; on Mac its intended backend is Metal.

The output directory retains exact pins, wheel hashes, resolved distributions,
compiler/SDK details, actual module paths, native/backend state and NumPy/SciPy
BLAS configuration. Each command has a bounded timeout, exit status, wall time and
full log. Failures are retained. Use a new work directory for another attempt.

## Verification

CI runs lifecycle tests with a required-test inventory, the existing full/scientific
regression profile, Qt software contracts and the Qt parallel mesh caller.
The acceptance runner uses `--installed-package` to suppress `src/`, reject source
shadowing and run those contracts against the installed ANYfem wheel. The checkout
remains available for test fixtures and tooling.
Existing numerical tolerances and exact-equality obligations remain binding;
ARM/BLAS/compiler differences must be investigated, not hidden by changed limits.
The same pinned wheel bootstrap also produces small Linux reference snapshots.
CI compares the existing normalized linear/quadratic/beam mesh oracle across both
systems and Python endpoints; a mismatch blocks the compatibility lane.
This reuses exact equality of the existing rounded signatures, rather than adding
a new tolerance or a bitwise cross-platform guarantee.
Native containment covers pre-dispatch refusal, cancellation, timeout, worker,
parent and guardian death, retirement inspection and unrelated-process safety.

The optional manual `metal_probe` workflow job enumerates the actual adapters and
attempts an offscreen Metal clear/readback. Missing hardware produces
`not_verified`. It does not validate the interactive Qt window, picking, Retina
scaling, docking, animations or packaged-app behaviour. Those remain pending
access to a Mac desktop. No signing/notarization credentials are needed here.

For implementation status, limitations and commands actually run, see
[the living task record](handoff/macos-20261029.md).
