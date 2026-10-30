"""Display the project's shipped licensing documents in the Help menu."""
from importlib import metadata
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTabWidget, QTextBrowser, QVBoxLayout


def _document(name):
    root=Path(__file__).resolve().parents[4]
    if (root/'pyproject.toml').is_file() and (root/name).is_file():
        return (root/name).read_text(encoding='utf-8')
    try:
        distribution=metadata.distribution('ANYfem')
    except metadata.PackageNotFoundError:
        return None
    for entry in distribution.files or ():
        if '.dist-info/licenses/' in entry.as_posix() and entry.as_posix().endswith('/licenses/'+name):
            return Path(distribution.locate_file(entry)).read_text(encoding='utf-8')
    return None


def show_licensing(window):
    existing=window.findChild(QDialog,'LicensingDialog')
    if existing is not None:
        existing.show();existing.raise_();existing.activateWindow()
        return existing
    dialog=QDialog(window)
    dialog.setObjectName('LicensingDialog')
    dialog.setWindowTitle('ANYfem — Licensing')
    dialog.setAttribute(Qt.WA_DeleteOnClose)
    dialog.resize(720,560)
    layout=QVBoxLayout(dialog)
    tabs=QTabWidget(dialog);layout.addWidget(tabs)
    overview=QTextBrowser()
    overview.setHtml('''<h2>ANYfem licensing</h2>
        <p>Copyright © Audun Nyhus</p>
        <p>ANYfem is licensed under the <b>GNU General Public License,
        version 3 only (GPL-3.0-only)</b>.</p>
        <p>You may redistribute and modify ANYfem under the terms of that license.
        ANYfem is provided without warranty; see the full license for details.</p>
        <p>Third-party components retain their own licenses. Earlier published
        ANYfem versions retain the license terms under which they were distributed.</p>
        <p>Original narrative documentation is licensed under CC BY 4.0 unless
        stated otherwise; embedded code follows the source-code license.</p>
        <p>The following tabs contain the license and notices supplied with ANYfem.</p>''')
    tabs.addTab(overview,'Overview')
    for name,title in [('NOTICE','Notice'),('LICENSE','Full license'),('THIRD_PARTY_NOTICES.md','Third-party notices'),('docs/LICENSE.md','Documentation')]:
        browser=QTextBrowser()
        browser.setPlainText(_document(name) or 'This licensing document is unavailable in this installation.\nLicense: https://www.gnu.org/licenses/gpl-3.0.html\nNotices: https://github.com/audunarn/ANYfem/blob/main/'+name)
        tabs.addTab(browser,title)
    buttons=QDialogButtonBox(QDialogButtonBox.Close)
    buttons.rejected.connect(dialog.close);layout.addWidget(buttons)
    dialog.show()
    return dialog
