# SPDX-License-Identifier: GPL-3.0-only
"""Acceptance/refusal tests for source, public notices and distribution licensing."""
from email.message import Message
from io import BytesIO
import json
from pathlib import Path
import shutil
import tarfile
import zipfile

import pytest

from tools import check_licenses as licenses
from tools import check_release_artifacts as artifacts


@pytest.fixture
def source(tmp_path, monkeypatch):
    root = licenses.ROOT
    for relative in {*licenses.EXPECTED_LICENSE_FILES, "README.md", "pyproject.toml"}:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / relative, target)
    monkeypatch.setattr(licenses, "ROOT", tmp_path)
    monkeypatch.setattr(licenses, "INVENTORY_PATH", tmp_path / "dependency-licenses.json")
    return tmp_path


def _metadata():
    msg = Message()
    msg["Metadata-Version"] = "2.4"
    msg["Name"] = "ANYfem"
    msg["Version"] = artifacts.VERSION
    msg["License-Expression"] = "GPL-3.0-only"
    for relative in licenses.EXPECTED_LICENSE_FILES:
        msg["License-File"] = relative
    return msg


def _wheel(path, *, edit=None):
    root = f"anyfem-{artifacts.VERSION}.dist-info"
    content = {root + "/METADATA": _metadata().as_bytes(),
               "anyfem/__init__.py": b"# SPDX-License-Identifier: GPL-3.0-only\n"}
    for relative in licenses.EXPECTED_LICENSE_FILES:
        content[root + "/licenses/" + relative] = (licenses.ROOT / relative).read_bytes()
    if edit:
        edit(content, root)
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in content.items():
            archive.writestr(name, data)
    return path


def _sdist(path, *, edit=None):
    root = "anyfem-" + artifacts.VERSION
    content = {root + "/PKG-INFO": _metadata().as_bytes()}
    for relative in (*licenses.EXPECTED_LICENSE_FILES, "CHANGELOG.md", "pyproject.toml"):
        content[root + "/" + relative] = (licenses.ROOT / relative).read_bytes()
    content[root + "/src/anyfem/__init__.py"] = b"# SPDX-License-Identifier: GPL-3.0-only\n"
    if edit:
        edit(content, root)
    with tarfile.open(path, "w:gz") as archive:
        for name, data in content.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, BytesIO(data))
    return path


def test_source_checkout_and_no_git_sdist_scope_pass(source):
    (source / "run_gui.py").write_text("# SPDX-License-Identifier: GPL-3.0-only\n", encoding="utf-8")
    licenses.check()


@pytest.mark.parametrize("expression", ["MPL-2.0", "GPL-3.0-or-later", "GPL-3.0-only OR LicenseRef-Commercial"])
def test_conflicting_explicit_source_header_is_refused(source, expression):
    (source / "run_gui.py").write_text(f"# SPDX-License-Identifier: {expression}\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="conflicting source SPDX"):
        licenses.check()


@pytest.mark.parametrize("relative, replacement, error", [
    ("LICENSE", "changed license\n", "unmodified GPL"),
    ("DEPENDENCY_NOTICES.md", "stale notice\n", "notices drifted"),
    ("docs/drafts/COMMERCIAL_LICENSE_TERM_SHEET.md", "# Operative contract\n", "non-operative warning"),
    ("docs/COMMERCIAL_LICENSING.md", "# Commercial rights automatically granted\n", "must not itself grant rights"),
    ("docs/LICENSE.md", "# Documentation GPL only\n", "CC BY 4.0"),
])
def test_changed_legal_notices_are_refused(source, relative, replacement, error):
    (source / relative).write_text(replacement, encoding="utf-8")
    with pytest.raises(SystemExit, match=error):
        licenses.check()


def test_historical_narrative_mpl_mentions_are_preserved(source):
    (source / "docs/history.md").write_text("Earlier source was MPL-2.0.\n", encoding="utf-8")
    licenses.check()


def test_metadata_duplicate_expression_is_refused():
    msg = _metadata()
    msg["License-Expression"] = "MPL-2.0"
    with pytest.raises(SystemExit, match="duplicate License-Expression"):
        licenses.check_distribution_metadata(msg, label="test")


def test_metadata_missing_notice_is_refused():
    msg = _metadata()
    del msg["License-File"]
    with pytest.raises(SystemExit, match="License-File"):
        licenses.check_distribution_metadata(msg, label="test")


def test_correct_wheel_and_sdist_pass(tmp_path):
    artifacts._check_wheel(_wheel(tmp_path / "candidate.whl"))
    artifacts._check_sdist(_sdist(tmp_path / "candidate.tar.gz"))


def test_wheel_altered_license_is_refused(tmp_path):
    def alter(content, root):
        content[root + "/licenses/LICENSE"] = b"not the GPL"
    with pytest.raises(SystemExit, match="notice differs"):
        artifacts._check_wheel(_wheel(tmp_path / "bad.whl", edit=alter))


def test_wheel_missing_commercial_policy_is_refused(tmp_path):
    def alter(content, root):
        del content[root + "/licenses/docs/COMMERCIAL_LICENSING.md"]
    with pytest.raises(SystemExit, match="missing"):
        artifacts._check_wheel(_wheel(tmp_path / "bad.whl", edit=alter))


def test_wheel_conflicting_source_header_is_refused(tmp_path):
    def alter(content, root):
        content["anyfem/module.py"] = b"# SPDX-License-Identifier: MPL-2.0\n"
    with pytest.raises(SystemExit, match="conflicting source SPDX"):
        artifacts._check_wheel(_wheel(tmp_path / "bad.whl", edit=alter))


def test_sdist_wrong_pkg_info_is_refused(tmp_path):
    def alter(content, root):
        content[root + "/PKG-INFO"] = content[root + "/PKG-INFO"].replace(b"GPL-3.0-only", b"MPL-2.0")
    with pytest.raises(SystemExit, match="License-Expression"):
        artifacts._check_sdist(_sdist(tmp_path / "bad.tar.gz", edit=alter))


def test_sdist_wrong_pyproject_license_is_refused(tmp_path):
    def alter(content, root):
        content[root + "/pyproject.toml"] = content[root + "/pyproject.toml"].replace(b'license = "GPL-3.0-only"', b'license = "MPL-2.0"')
    with pytest.raises(SystemExit, match="inconsistent pyproject"):
        artifacts._check_sdist(_sdist(tmp_path / "bad.tar.gz", edit=alter))


def test_sdist_conflicting_source_header_is_refused(tmp_path):
    def alter(content, root):
        content[root + "/src/anyfem/module.py"] = b"# SPDX-License-Identifier: GPL-3.0-or-later\n"
    with pytest.raises(SystemExit, match="conflicting source SPDX"):
        artifacts._check_sdist(_sdist(tmp_path / "bad.tar.gz", edit=alter))


def test_installed_distribution_notices_and_mutation(tmp_path, monkeypatch):
    folder = tmp_path / f"anyfem-{artifacts.VERSION}.dist-info/licenses"
    files = []
    for relative in licenses.EXPECTED_LICENSE_FILES:
        target = folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(licenses.ROOT / relative, target)
        files.append(target.relative_to(tmp_path))
    class Installed:
        metadata = _metadata()
        version = artifacts.VERSION
        def locate_file(self, name):
            return tmp_path / name
    installed = Installed()
    installed.files = files
    monkeypatch.setattr(licenses.importlib_metadata, "distribution", lambda name: installed)
    licenses.check_installed_distribution()
    (folder / "LICENSE").write_text("mutated", encoding="utf-8")
    with pytest.raises(SystemExit, match="installed notice differs"):
        licenses.check_installed_distribution()
