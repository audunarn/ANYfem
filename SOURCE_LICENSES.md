# Source, documentation and asset scope

Public source license: **GPL-3.0-only**. The authoritative complete license is
[`LICENSE`](LICENSE); copyright and warranty notices are in [`NOTICE`](NOTICE).

Unless a file carries its own applicable notice, this covers ANYfem-authored
Python, shell, configuration/build/CI code, tests, tools, executable examples and
code snippets (including code in documentation). Existing files without an SPDX
header inherit this repository notice; adding headers does not establish title.
New source files should use `SPDX-License-Identifier: GPL-3.0-only` where the file
format permits comments. Do not remove a third party's attribution or relabel
third-party files to satisfy a checker; record and review a specific exception.
`source-license-exceptions.json` is currently empty. A future exception must name
an exact relative path, normalized SHA-256, applicable license, original provenance
and review record; it stays excluded from alternative commercial licensing pending
separate clearance. Source checks bind the notice to that hash and refuse drift.
The current exception allowlist is deliberately limited; a new license family
or a source file outside delivered source paths needs a reviewed policy/packaging
change. Consistency checks recognize common notices, not every possible legal
formulation, and cannot detect copied code with an otherwise matching GPL header.
All commercial eligibility still requires a complete rights review.

Original narrative documentation remains **CC BY 4.0**, as described in
[`docs/LICENSE.md`](docs/LICENSE.md). The GPL license document itself and
third-party license texts retain their own copying conditions.

Scientific result JSON/logs, historical evidence and patch snapshots keep their
recorded provenance and existing rights. No new blanket data, model, image or
third-party rights are granted by this source notice. Python-authored model
examples follow the source license; imported customer geometry, datasets,
standards excerpts, figures and third-party assets require their own rights
review. Running ANYfem does not by itself make a user's input or engineering
results GPL-covered; output that reproduces protected code/artwork needs a
separate assessment.

Dependencies are separately licensed, not relicensed by ANYfem. See
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md). Historical published copies
retain their licenses; see [`docs/LICENSING_TRANSITION.md`](docs/LICENSING_TRANSITION.md).
An optional commercial contract can cover only specifically cleared rights:
[`docs/COMMERCIAL_LICENSING.md`](docs/COMMERCIAL_LICENSING.md). This file grants no
alternative commercial license.
