# Licensing transition and historical boundary

Snapshot: 2026-10-27. Public source license on this branch: **GPL-3.0-only**.
No commercial contract is granted, no release is published by this work, and
the package version remains 0.4.2. The fully functional application is retained;
this task changes licensing, compliance tooling and documentation only.

## Exact chronology

| Boundary | Observed identity | Applicable source license |
|---|---|---|
| Original tagged 0.3.0 | `v0.3.0`, package metadata | GPL-3.0-or-later (not the new GPL-only variant) |
| MPL introduction | `65fea8b3b9eaa2d38abc0963ad292c60c1fbc10f` | MPL-2.0 |
| 0.4.0 tag | `276d26a087e23fd0250af31b9c01cbbdbaa51c4f` | MPL-2.0 |
| Last published MPL version observed on PyPI | 0.4.1, tag `6d5fd9975f940495d6f8124c8a5d727f5f156605` | MPL-2.0 |
| Last MPL main source immediately before this transition | `80d18140bb075e26b239f5243c8da4cd09b4ea2c`, version 0.4.2 | MPL-2.0; source commit, not a published 0.4.2 release |
| GPL transition on main | `e243c3ebe888a7f09e1e8d80aa6c3f05910faf69` | GPL-3.0-only |

Public [PyPI metadata](https://pypi.org/pypi/ANYfem/json), read 2026-10-27,
lists 0.3.0, 0.4.0 and 0.4.1, latest `License-Expression: MPL-2.0`. The 0.4.1
artifacts were uploaded on 2026-09-17 UTC:

- `anyfem-0.4.1-py3-none-any.whl`, SHA-256
  `734fa27c683682eaa151ee7a9d8bd4cd64c8ec054ec1c717ff73bb27ee293d45`.
- `anyfem-0.4.1.tar.gz`, SHA-256
  `42744e32d4813a9876bd3d49ae1d62b339bbd81ba86ac6f647fb327a115cdad5`.

Both artifacts were independently downloaded, their hashes matched the registry,
and wheel METADATA / sdist PKG-INFO each declare 0.4.1 and MPL-2.0.
Refreshed main at `e243c3e` consistently declares GPL-3.0-only. Earlier local
working changes were preserved through a dedicated isolated licensing branch.

## Earlier rights survive

New notices do not revoke or retroactively replace earlier GPL/MPL grants.
Recipients may continue using, modifying and distributing legitimately obtained
MPL versions under their original terms. GPL-only applies to the new development
line and new grants as described here, not to all historical copies. A competitor
can maintain a compliant legacy MPL fork; later GPL-only additions are not thereby
available under MPL. Confirm the exact source/commit and provenance of backports.
Commercial rights likewise cannot revoke earlier public grants.

Narrative documentation stays CC BY 4.0. Code, examples and snippets follow source
terms; data/evidence, third-party material and dependencies retain their rights.
See [source scope](../SOURCE_LICENSES.md), [audit](LICENSING_AUDIT.md) and
[commercial policy](COMMERCIAL_LICENSING.md). A change in metadata is not copyright
title verification or clearance of a particular binary shipment.

## Compliance and future release procedure

Run `python tools/check_licenses.py` for source declarations, source SPDX conflicts,
generated dependency notices and the exact direct-dependency inventory. Source
headers are optional under the repository scope notice, but a conflicting source
header without a hash-bound reviewed exception is refused. The Git-mode scan
uses tracked source only, excluding independently checked-out dependency repos.
Historical narrative/evidence isn't rewritten to eliminate old
MPL mentions. New exceptions require a reviewed notice rather than blanket relabeling.

Run `python -m pytest tests/test_license_policy.py tests/test_licensing_artifacts.py`
for focused acceptance/refusal checks, `python -m build`,
`python tools/check_release_artifacts.py dist`, and `python -m twine check dist/*`.
The artifact gate compares source notices and metadata to built wheel/sdist,
checks packaged source declarations. Wheels contain licensing notices, not the
audit, strategy or draft term sheet as `License-File` metadata. Those documents
remain narrative source/sdist documentation; source checks verify the policy's
no-grant statement and the draft warning. No automated wording check substitutes
for legal review of a contract.
Build from the sdist too; do not only validate the checkout-built wheel.

Install the resulting wheel into a fresh environment, then run
`python tools/check_licenses.py --distribution-only` from the checkout to inspect
installed metadata/notices without requiring optional GUI dependencies. This is
a licensing check, not a runtime/GUI test. Existing full installed-dependency,
engine/GUI, supported-platform and scientific acceptance gates remain required
for a supported release. There is no publication authorization in this task.

Before an installer/OEM shipment, resolve the exact dependency graph and native
components, preserve complete notices and corresponding-source access, and review
Qt replacement/relinking/install-information obligations as applicable. A wheel
that contains only ANYfem source does not clear a future frozen GUI executable.
Publication uses `python tools/check_licenses.py --for-publication`, which refuses
the unchanged 0.4.2 version: both MPL source and GPL development currently identify
as 0.4.2. Select a new version at least 0.4.3 under the governing patch-only policy
before any release. It also refuses the existing direct-URL ANYgeometry `gui`
requirement, which is accepted by development pip installations but unsuitable
for public PyPI package metadata. Replace that pin only through an authorized,
compatibility-verified release decision. Neither hold blocks source development
or grants permission to publish; `twine check` alone does not clear these holds.
