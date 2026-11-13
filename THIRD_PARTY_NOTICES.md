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
| ANYhydro | optional floating-body hydrodynamics | MPL-2.0 | https://github.com/audunarn/ANYhydro |
| ANYsection | optional beam-section library | MPL-2.0 | https://github.com/audunarn/ANYsection |
| ANYsolid | optional solid child | MPL-2.0 | https://github.com/audunarn/ANYsolid |
| ANYfeSolver | runtime | MPL-2.0 | https://github.com/audunarn/ANYfeSolver |
| ANYfeSolver-results | runtime | MPL-2.0 | https://github.com/audunarn/ANYfeSolver |
| ANYstability | optional floating-body equilibrium | MPL-2.0 | https://github.com/audunarn/ANYstability |
| ANYtk3D | optional legacy Tk extra | MPL-2.0 | https://github.com/audunarn/ANYtk3D |
| build | development | MIT | https://github.com/pypa/build |
| h5py | runtime | BSD-3-Clause | https://www.h5py.org/ |
| NumPy | runtime | BSD-3-Clause and bundled-component licenses | https://numpy.org/ |
| Pillow | optional GUI | HPND | https://python-pillow.github.io/ |
| PySide6 / Qt | optional GUI | LGPL-3.0-only route | https://doc.qt.io/qtforpython-6/commercial/index.html |
| platformdirs | runtime | MIT | https://github.com/tox-dev/platformdirs |
| pytest | development | MIT | https://pytest.org/ |
| SciPy | runtime | BSD-3-Clause and bundled-component licenses | https://scipy.org/ |
| setuptools | build | MIT | https://github.com/pypa/setuptools |
| twine | development | Apache-2.0 | https://twine.readthedocs.io/ |
| wheel | build | MIT | https://github.com/pypa/wheel |

The machine-readable companion inventory is
[`dependency-licenses.json`](dependency-licenses.json).

The Qt frontend uses dynamically installed PySide6 and the Qt Core, Gui,
Widgets and OpenGL modules under their available LGPL route. It does not bundle
Qt libraries in the ANYfem wheel. Preserve the installed PySide6, Shiboken and Qt
license notices and users' ability to replace these libraries. A standalone
installer must include the applicable notices/source access and replacement
instructions and undergo the existing installed-artifact release review. This
development dependency entry does not establish installer compliance. GPL-only
Qt modules are not authorized by this entry; commercial licensing is a separate
distribution choice.

Optional drawing exports use ezdxf (MIT) and Shapely (BSD-3-Clause), installed separately.

The optional fatigue workflow uses ANYfatigue (MPL-2.0), installed separately.

## Bundled free-threaded runtime (thread route, Windows x64)

The optional thread route runs plan computation in a child process on a bundled free-threaded CPython. The runtime folder
`runtime-314t` is assembled by `tools/build_runtime.py` from `tools/runtime.lock.json` (hash-pinned interpreter payload and
wheels) and contains: CPython 3.14.10 free-threaded (PSF-2.0, with the third-party components listed in its `LICENSE.txt`),
NumPy 2.4.6 and SciPy 1.17.1 (BSD-3-Clause, with bundled OpenBLAS/LAPACK and the GCC runtime library under
GPL-3.0-or-later WITH GCC-exception-3.1; their license files stay inside the wheel `*.dist-info` folders), Shapely 2.1.2
(BSD-3-Clause) and the Microsoft Visual C++ runtime DLLs shipped with those files. The machine-readable record is the
`bundled_runtimes` entry of [`dependency-licenses.json`](dependency-licenses.json).

GEOS (LGPL-2.1) is vendored in the Shapely wheel as `shapely.libs/geos*.dll`. The runtime folder consists of plain, unmodified
wheel contents and interpreter files, so any of these libraries can be replaced by the user: put a different build of the DLL in
place and the application will refuse the changed folder (the health check compares the folder to its lock and falls back to the
serial route); a replacement therefore needs a rebuilt lock (`tools/build_runtime.py`) or the developer override
`ANYGEOMETRY_THREAD_RUNTIME`. The corresponding GEOS source is available from https://libgeos.org/ and the licence text is in
`shapely-2.1.2.dist-info/licenses/LICENSE_GEOS` inside the runtime. Installer integration, notarization/signing and the
redistribution terms for the Visual C++ files are owner decisions and are not established by this entry.
