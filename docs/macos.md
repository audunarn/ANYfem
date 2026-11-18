# macOS developer support

Target: native Apple Silicon (arm64) macOS 15, one Python (3.14 in CI). Intel,
older macOS and standalone `.app` distribution are not targets.

**Status: not yet verified on macOS.** The Darwin containment code, its tests and
the CI job were written and reviewed on Windows only. Nothing here establishes
macOS support until the `macOS developer contracts` workflow has passed on the
exact commit (see below). Mocked tests on other platforms prove only that the
code is inert there and refuses cleanly.

## Install for development

Install Python, Git and Apple's Command Line Tools (`xcode-select --install`).
From the ANYfem checkout, use a **new directory outside all source trees**:

```sh
python3 tools/bootstrap_macos.py --work-dir "$HOME/anyfem-mac-build-1"
"$HOME/anyfem-mac-build-1/venv/bin/anyfem"
```

The command fetches the owner commits pinned in
`.github/workflows/application-lane.yml` (the only pin list), builds local wheels,
installs them with normal dependency resolution into a fresh venv, runs
`pip check` and an isolated import check. It needs network access and a compiler.
Third-party packages must have Apple Silicon wheels; a missing wheel or a resolver
conflict is an error, not a fallback.

## Process containment

Contained worker trees (parallel meshing, geometry diagnosis) have no fork and no
parent-death signal on macOS. The worker leads its own session; a fresh-interpreter
guardian (`src/anyfem/_darwin_guardian.py`, stdlib only) and a worker-side watch
kill the owned group if the application dies. Retirement is verified with
`psutil` (`src/anyfem/_darwin_process.py`) over the whole owned session.
`psutil` is a macOS-only dependency. If `psutil` or the guardian script is
missing, containment is refused (`ContainmentUnavailable`) before any worker is
spawned. Windows and Linux paths are unchanged.

## CI

`.github/workflows/macos.yml` runs one `macos-15` / Python 3.14 job through the
shared application lane, on manual dispatch and a weekly schedule only, never on
push or pull request. `tools/macos_required_tests.py` lists the lifecycle tests
that must report *passed* (a skip, xfail or missing test fails the run via
`tools/test_anyfem.py --require-passed`).
