# Third-party notices

ANYfem uses the following direct dependencies. They are installed separately
and are not copied into ANYfem source or binary distributions. Each dependency
remains under its own license; transitive dependencies and binary-wheel notices
remain governed by the notices shipped by their distributors.

| Dependency | Scope | License | Upstream |
| --- | --- | --- | --- |
| ANY3dView | optional GUI | MPL-2.0 | https://github.com/audunarn/ANY3dView |
| ANYfileio | runtime | MPL-2.0 | https://github.com/audunarn/ANYfileIO |
| ANYgeometry | runtime | MPL-2.0 | https://github.com/audunarn/ANYgeometry |
| ANYloads | runtime and optional Excel extra | MPL-2.0 | https://github.com/audunarn/ANYloads |
| ANYmaterial | runtime | MPL-2.0 | https://github.com/audunarn/ANYmaterial |
| ANYmesher | runtime | MPL-2.0 | https://github.com/audunarn/ANYmesh |
| ANYpost | runtime and optional results extra | MPL-2.0 | https://github.com/audunarn/ANYpost |
| ANYsection | optional beam-section library | MPL-2.0 | https://github.com/audunarn/ANYsection |
| ANYsolver | runtime | MPL-2.0 | https://github.com/audunarn/ANYsolver |
| ANYsolver-results | runtime | MPL-2.0 | https://github.com/audunarn/ANYsolver |
| ANYtk3D | optional legacy Tk extra | MPL-2.0 | https://github.com/audunarn/ANYtk3D |
| build | development | MIT | https://github.com/pypa/build |
| h5py | runtime | BSD-3-Clause | https://www.h5py.org/ |
| NumPy | runtime | BSD-3-Clause and bundled-component licenses | https://numpy.org/ |
| Pillow | optional GUI | HPND legacy / MIT-CMU in newer wheels; version-specific | https://python-pillow.github.io/ |
| PySide6 / Qt | optional GUI | LGPL-3.0-only route | https://doc.qt.io/qtforpython-6/commercial/index.html |
| platformdirs | runtime | MIT | https://github.com/tox-dev/platformdirs |
| pytest | development | MIT | https://pytest.org/ |
| SciPy | runtime | BSD-3-Clause and bundled-component licenses | https://scipy.org/ |
| setuptools | build | MIT | https://github.com/pypa/setuptools |
| twine | development | Apache-2.0 | https://twine.readthedocs.io/ |
| wheel | build | MIT | https://github.com/pypa/wheel |

The machine-readable companion inventory is
[`dependency-licenses.json`](dependency-licenses.json).
The generated per-extra index is [`DEPENDENCY_NOTICES.md`](DEPENDENCY_NOTICES.md).
Neither inventory substitutes for complete notices of actual shipped binaries.
ANYfem's optional commercial contracts cover only eligible ANYfem rights; they
do not relicense these dependencies. See the version-bounded findings and open
installer/native-runtime obligations in [`docs/LICENSING_AUDIT.md`](docs/LICENSING_AUDIT.md).

The Qt frontend uses dynamically installed PySide6 and the Qt Core, Gui,
Widgets and OpenGL modules under their available LGPL route. It does not bundle
Qt libraries in the ANYfem wheel. Preserve the installed PySide6, Shiboken and Qt
license notices and users' ability to replace these libraries. A standalone
installer must include the applicable notices/source access and replacement
instructions and undergo the existing installed-artifact release review. This
development dependency entry does not establish installer compliance. GPL-only
Qt modules are not authorized by this entry; commercial licensing is a separate
distribution choice.
