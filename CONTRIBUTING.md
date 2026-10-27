# Contributions and licensing

Propose original source, tests, tools and executable examples under GPL-3.0-only;
original narrative documentation remains CC BY 4.0. Preserve existing notices
and clearly identify external source, copied/generated material, datasets and
the authority under which you may submit them. Do not copy customer models or
restricted standards material into public issues or patches.

New comment-capable source should carry `SPDX-License-Identifier: GPL-3.0-only`.
Existing headerless source is covered by [SOURCE_LICENSES.md](SOURCE_LICENSES.md).
A legitimate differently licensed source file requires a reviewed, hash-bound
entry in `source-license-exceptions.json` and its applicable original notices;
do not change an upstream license merely to pass CI.

Submitting a patch transfers no copyright and supplies no automatic commercial
relicensing permission or signed CLA. A public contribution may be accepted
under the applicable public license without joining a commercial program.
Alternative licensing requires a separate executed agreement from the relevant
rights holder. Until then, third-party contributions stay excluded from any
alternative rights schedule; a future OEM release must explicitly inventory
eligible files/versions rather than assume all contributions are cleared.

See [commercial and contribution policy](docs/COMMERCIAL_LICENSING.md),
[licensing transition](docs/LICENSING_TRANSITION.md) and existing repository
engineering instructions. Licensing changes do not waive tests, numerical
qualification or release requirements. Run `python tools/check_licenses.py` and
the focused licensing tests after changing notices or packaging.
