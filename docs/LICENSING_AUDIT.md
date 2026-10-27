# Licensing and commercial-readiness audit

Snapshot: 2026-10-27, baseline `e243c3ebe888a7f09e1e8d80aa6c3f05910faf69`.
This is a bounded repository/distribution assessment, **not a legal opinion,
chain-of-title certification or approval to convey a proprietary build**.
Full source/registry snapshots and the private product-integration trace are
retained in the task evidence archive (`audit-snapshot.json`, `audit-summary.txt`,
`audit-context.md`, `pypi-anyfem.json`). The portable findings below do not depend
on workstation paths. This public summary names the requested product and public
ANYfem APIs; exact private checkout identities remain in the task evidence.
The downloaded public 0.4.1 wheel/sdist matched registry hashes and each declares
MPL-2.0 in its package metadata, independently confirming the published boundary.

## Ownership and provenance inventory

Baseline: 641 tracked files, including 556 Python, 56 Markdown, 12 JSON, 4 patch,
3 CI YAML, 2 log, one TOML/build-manifest/shell script each and 5 extensionless.
Git shortlog records 222 commits under Audun Arnesen Nyhus (210 gmail and 12 pm.me).
NOTICE identifies Audun Nyhus. There are 28 Claude Opus 5.5 and 10 Claude Sonnet 5.5
coauthor trailers in reachable history. These describe authorship/tool use, not
legal assignments or proof that all copyright vests in the named maintainer.

| Material | Observations / rights treatment | Remaining verification |
|---|---|---|
| Application, tests, tools, coded examples/models | Repository GPL-3.0-only scope; no additional human copyright holder detected in bounded keyword/history scan | Owner confirms employer/contractor claims, outside contributions, copied code and relevant AI-service contract history; author metadata is insufficient |
| Narrative docs | CC BY 4.0 notice retained; embedded source uses GPL | Quoted standards/figures/reference descriptions retain external rights |
| Result JSON/logs and historical patches | 12 JSON includes dependency inventory, mesher pin inventory, UI manifest and 9 submodelling result files; historical logs/patches retained | Preserve lineage; distinguish program-generated facts from copied protected content; no blanket new asset commercialization grant |
| Images/fonts/CAD/mesh/binary data | No tracked image/font/CAD/mesh binary file found in baseline extension inventory | Does not exclude external assets, embedded material or runtime-generated/customer assets |
| Published example dimensions / standards references | `src/anyfem/application/examples.py:34` cites a Damen Stan Pontoon product page; `src/anyfem/verification.py` names a DNV-RP-C208 yield-stress reference | Public dimensions, independently coded examples and external expressive material need different treatment; no permission to copy Damen drawings/images or DNV standards is inferred, and no endorsement is claimed |
| Portable copied control extracts | `tools/prepared_candidate_controls/reviewed_geometry_driver_extract.py` identifies original driver and SHA-256 `54149b83d7232e33a796a8ae19c18b201debc2ef7b32519be29ec17853387b40`; supervisor helper extract identifies reviewed in-repository helpers | Establish original source rights rather than treating extract comments as assignment; proposal helper also needs title review |
| Vendor/generated sources | No tracked vendor directory or separate third-party copyright notice identified by the bounded scan | This is not a plagiarism scan or evidence of absence; investigate uncertain provenance before an alternative grant |

Commercial eligibility is **conditional**: only audited files for which the
licensor has sufficient alternative-licensing rights. No exhaustive eligible-file
schedule has been signed off. Contractor/employer title, third-party source and
future contributions remain explicit legal clearance items. The public GPL route
does not itself supply those missing alternative rights. New GPL contributions
remain GPL unless separately cleared; policy is in [COMMERCIAL_LICENSING.md](COMMERCIAL_LICENSING.md).

## Dependencies, integration and distribution

`dependency-licenses.json` inventories 22 distinct direct build/dev/runtime/optional
packages and per-extra requirements. ANYfem's wheel installs dependencies
separately and does not vendor their implementations. Inspect actual shipment
contents before applying that statement to an installer or OEM bundle.

| Component/route | Observed license / compatibility | Required treatment |
|---|---|---|
| ANY3dView, ANYfileio, ANYgeometry, ANYloads, ANYmaterial, ANYmesher, ANYpost, ANYsection, ANYsolver, ANYsolver-results, ANYtk3D | MPL-2.0 direct package declarations | Generally compatible with GPL distribution via MPL 2.0 rules; preserve applicable MPL source/notices and check secondary-license exclusions at exact source revisions. A proprietary larger work may use the MPL route with obligations; ANYfem commercial permission does not replace it |
| PySide6, Essentials/Addons, Shiboken6 and actual Qt modules | LGPL-3.0/GPL alternatives in sampled metadata | Review per module/version; dynamic install is the intended LGPL route. Preserve notices, source access and required replacement/relinking rights. GPL-only modules need a compatible public route or separate rights for proprietary conveyance; an ANYfem contract cannot sell Qt rights |
| NumPy/SciPy | BSD plus bundled native license sets | Include OpenBLAS, compiler/native runtime and other notices for exact wheels. GCC GPL-with-runtime-exception is not a blanket GPL-only prohibition; preserve the exception and its conditions |
| h5py/HDF5 and geometry/rendering chain | h5py BSD plus bundled HDF5/compression; Shapely BSD plus GEOS LGPL; ModernGL/glcontext MIT | Metadata of wrapper alone does not clear bundled native libraries; retain actual distributors' notices and any source/replacement obligations |
| Pillow/platformdirs and dev/build tools | Installed Pillow MIT-CMU reviewed alias; inventory HPND; platformdirs MIT; build/pytest/setuptools/wheel MIT, twine Apache-2.0 | Preserve actual version-specific terms; do not call every installed version representative of the release |
| New WebGPU route | Optional declarations in metadata, older installed viewer sample | Resolve and inspect actual `ANY3dView[webgpu]` graph and binary notices before shipping; sampled old metadata cannot establish this closure |

The local Python 3.14 metadata traversal found 23 installed reachable packages
across non-dev extras, with ANYpost/ANYloads/ANYsection absent. Several versions
do not meet declared ranges: viewer 0.5.5 vs >=0.5.6, mesher 0.5.0 vs >=0.5.1,
solver 0.4.6 vs >=0.4.7 and PySide6 6.12.0 vs <6.11. It is a **partial transitive
sample, not a qualified resolved environment**. No new blanket dependency
allowlist or gate relaxation follows. Existing installed audit can fail here
and remains mandatory in the intended release environment. No GPL-only blocker
was identified in the bounded reviewed sample; that is not full binary clearance.

Public wheels, sdist and their installed metadata/notices receive automated
licensing checks. GUI packaging uses optional Qt/Tk routes; no tracked frozen
installer recipe was established in this inspection. Installer-specific SBOM,
source offer/access, Qt install/replacement/relinking instructions, signing,
binary-component review and supported-platform testing remain shipment gates.
Generated project/result archives are engineering data, not an automatic source
code or third-party license bundle. Exported reports may include protected
content and need their applicable notices where relevant.

## ANYworkbenchAI assessment (no sibling changes)

The inspected ANYworkbenchAI engine pins an earlier ANYfem source commit whose
metadata is version 0.4.2, **MPL-2.0**, not GPL main. The exact pin, private
checkout identity and import locations are recorded in the task evidence.
Prior grants on that pin survive this migration.

The engine directly imports ANYfem `DocumentSession`, `Project`,
`WorkbenchController`, `AnalysisDefinition` and `JobManager`, plus public model,
IO and command APIs. The frontend communicates with that Python engine over IPC. Thus a
process boundary exists at UI/engine, **not between the engine and ANYfem**.

For an upgrade to new GPL ANYfem in a proprietary engine, evaluate the combined
work and distribution with counsel. The intended alternative route is an executed
license for the exact eligible ANYfem code, keeping mechanics/meshing/material
owners in their packages and calling stable versioned public APIs. Maintain a
dependency/source/notice schedule and preserve MPL file obligations and Qt/native
component duties. Alternative rights for uncleared contributors or GPL-only
dependencies must be obtained from their holders, or those components excluded.
The owner cannot waive third-party rights by licensing ANYfem.

Alternatives are keeping the old MPL pin (with its fixed feature/bug baseline),
or a separately distributed GPL engine with a proprietary frontend where counsel
finds that combination permissible. IPC alone does not decide GPL scope; a
custom tightly coupled protocol is not an automatic exemption. No boundary change,
mechanics copy, sibling license change or proprietary implementation is made here.

## Commercial clearance and strategic decision

Proceed with paid onboarding/support for public GPL use first; prepare an OEM
contract only for an identified integration customer. See the A/B/C comparison
in [commercial policy](COMMERCIAL_LICENSING.md). Before an operative OEM grant:

1. Rights holder signs off a version/file-specific eligible-source register and
   clears contributions, employer/contractor claims and copied-source lineage.
2. Resolve the real deployment graph, audit third-party/native binary rights and
   notices, and decide a compliant Qt route and proprietary integration boundary.
3. Counsel reviews the non-operative term sheet and drafts the actual agreement;
   define update/support and engineering-responsibility boundaries.
4. Re-run the unchanged release/installed-platform gates for the intended artifact
   and obtain separate publication/commercial authority.

Sources: [GPLv3 text](https://www.gnu.org/licenses/gpl-3.0.html),
[GNU FAQ](https://www.gnu.org/licenses/gpl-faq.en.html),
[MPL FAQ](https://www.mozilla.org/en-US/MPL/2.0/FAQ/),
[Qt licensing](https://doc.qt.io/qt-6/licensing.html),
[Qt for Python terms](https://doc.qt.io/qtforpython-6/commercial/index.html).
These explain governing concepts, not legal clearance of this product.
