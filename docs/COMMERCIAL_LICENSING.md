# Public GPL and optional commercial licensing policy

**Public license: GPL-3.0-only. No commercial rights are granted by this policy.**
ANYfem remains the fully functional public engineering application. There is no
license-key restriction or deliberately disabled solver/GUI feature in this
transition. Ordinary commercial engineering use of the GPL version does not
require payment. The GPL permits commercial activity and private use/modification;
conveying covered software creates obligations under the actual GPL. See
[GNU's FAQ](https://www.gnu.org/licenses/gpl-faq.en.html#DoesTheGPLAllowMoney).

## Optional contractual offer

Audun Arnesen Nyhus, or a future verified rights holder, may negotiate a separate
written license for **specifically identified code for which that licensor holds
sufficient rights**. A notice, Git author name or AI coauthor trailer is not proof
of those rights. No alternative license is effective until eligibility is
cleared and the appropriate parties execute an agreement. Public package SPDX
metadata remains `GPL-3.0-only`, not `GPL OR Commercial` or an invented SPDX term.
Earlier public grants are unaffected. Enquiries, payment discussions and a draft
term sheet grant nothing.

Candidates include proprietary embedding through ANYfem public APIs, OEM
redistribution of a modified application and integration into a proprietary
engineering workflow. A negotiated grant could authorize reproduction,
modification, integration and redistribution of an agreed source subset or
binary, including specified downstream/OEM rights, under terms unavailable
through the public GPL. Each agreement must define products, recipients,
deployment model, source access, permitted modifications, sublicensing and
redistribution boundaries. Commercial licensing does not waive third-party terms.

## Eligibility and exclusions

Before quoting an OEM license, build a file/commit/version schedule, establish
copyright title (including employer/contractor and AI-assisted source provenance),
identify contributions with alternative-licensing permission, and review all
bundled, linked/imported and transitive components for the actual shipment.
Exclude unapproved third-party or contributor material or obtain sufficient
separate permission. A rights gap is a blocker to licensing that material, not a
reason to imply the GPL provides proprietary redistribution rights.

MPL ANY packages, PySide6/Qt/Shiboken, NumPy/SciPy native runtimes, geometry,
rendering and file libraries retain their own obligations. Qt commercial rights
must come from Qt's authorized licensor if the LGPL route cannot be satisfied.
Original narrative documentation remains CC BY 4.0; customer datasets, imported
models, trademarks, third-party images, standards and reference data are excluded
unless expressly scheduled with adequate rights. A license to ANYfem does not
certify the safety, accuracy or suitability of an engineering analysis.

## Version, support and maintenance boundaries

An executed agreement must identify commit/tag, version range and included files,
and distinguish existing code from updates, bug fixes, supported platforms and
optional paid maintenance. Future contributions and third-party upgrades are not
automatically covered. Define support channels, response targets, supported
environments and end-of-support terms separately. No service level, warranty,
indemnity, fitness assurance, exclusive right or price is promised here.

The [sample term sheet](drafts/COMMERCIAL_LICENSE_TERM_SHEET.md) is
**NON-OPERATIVE / LEGAL REVIEW REQUIRED**. It is a negotiation outline, not a
license offer that can be accepted by using or downloading the repository.

## Contributions and rights retention

Public contributions are proposed under GPL-3.0-only for original source and
CC BY 4.0 for original narrative documentation, subject to file-specific rights.
Submitting a contribution does **not** transfer copyright or grant alternative
commercial licensing rights. Contributors should state original/third-party
provenance and authority to submit; maintainers must preserve notices. Public GPL
contributions can be accepted without joining a commercial program. The release
inventory must track whether any third-party contribution is eligible for a
commercial build. Obtain a separately executed, counsel-reviewed assignment or
sufficient non-exclusive alternative-licensing permission if needed; otherwise
exclude it from the alternative grant or retain applicable open-source terms.
No CLA is deemed executed by opening an issue or pull request.

## Enquiry workflow

Contact the maintainer listed in `pyproject.toml` (`audunarn@gmail.com`) or open a
non-confidential [licensing enquiry](https://github.com/audunarn/ANYfem/issues).
Provide organization/contact, intended integration and distribution, target
version, deployment scale/platforms, required modifications and support needs.
Keep proprietary models and confidential contracts out of public issues.
Maintainer steps: confirm scope and authority; assemble source/dependency rights
schedule; obtain legal review; propose commercial terms and maintenance scope;
execute agreement; deliver the identified artifact and compliance materials.
Until execution, only the existing public license applies.

## Strategy and first credible offering

| Route | Revenue mechanism | Protection and adoption | Cost / practical limits |
|---|---|---|---|
| A: GPL plus support/services | Paid onboarding, integration, training, troubleshooting, sponsored fixes | Low procurement barrier for internal engineering; source and GPL forks remain available | Revenue depends on demonstrable service value; avoid unbounded support or scientific assurance promises |
| B: GPL plus optional OEM agreement | Negotiated alternative rights and separately scoped maintenance | Useful to an identified customer that must distribute a proprietary combination; reciprocity discourages closed forks of new GPL code | Requires title clearance, shipment inventory, contract/enforcement work and an actual customer; legacy MPL forks remain possible |
| C: GPL core plus proprietary workflow modules | Paid separately eligible workflow/integration modules | Potential product differentiation in proprietary services/workflows | Module/import boundary can create a GPL combined-work issue; naming something a plugin is no exemption. Requires separate rights or counsel-reviewed separation; more engineering and support overhead |

Start with **one bounded paid onboarding/integration-support engagement for the
unchanged GPL application**: a named customer workflow, fixed deliverables and
supported environment, acceptance examples and an explicit support limit.
Validate willingness to pay before building a commercial edition. Prepare route
B only when a concrete OEM customer needs alternative rights and can fund title
and shipment review. Defer C until demand and a defensible boundary exist.

GPL provides a compliance basis for distributed covered derivatives; it is not
a ban on competition, commercial use, private modifications or old MPL forks.
GPLv3 also does not generally trigger corresponding-source obligations merely
because someone runs a modified service over a network without conveying copies.
An AGPL change would be a separate licensing decision, not part of this task.
Detection and enforcement require rights evidence, actual distribution evidence,
technical/legal assessment and proportionate counsel-led action. Do not forecast
income from license choice alone. Enterprise adoption depends on stability,
credible evidence, support and procurement clarity as much as license terms.
