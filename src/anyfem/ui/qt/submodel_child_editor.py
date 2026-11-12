"""A modeless native editor for the shell child of an existing submodel.

``open_shell_child_editor(app, definition_id)`` opens a second ``QtFemWindow`` on a
detached clone of ``definition.child``.  The user edits it with the ordinary
geometry, sections and loads tools; nothing reaches the parent until the explicit
"Apply to submodel" action, which validates the draft headlessly
(``anyfem.submodel_child_edit``) and replaces the stored child in one undoable
parent command.  The child window cannot save, open or replace its document, so
it can never become, or apply to, a different document.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMessageBox

from ...submodel_child_edit import (
    apply_child_edit,
    child_fingerprint,
    definition_fingerprint,
    detach_child,
)
from .app import QtFemWindow

_REGISTRY = "_submodel_child_editors"
_OUTDATED = "_submodel_child_outdated"
_FILTER = "_submodel_child_filter"


class ShellChildEditorWindow(QtFemWindow):
    """The child window; ``apply_to_submodel`` is the only way its draft reaches the parent."""

    _locked = False  # class defaults: QtFemWindow.__init__ may run before the instance is configured
    _forced = False
    _outdated = False

    def __init__(self, parent_app: Any, definition_id: str, *, viewer_backend: str = "auto") -> None:
        definition = parent_app.project.submodels.get(definition_id)
        if definition is None:
            raise ValueError("That submodel is not in the project")
        if getattr(definition, "child_kind", "shell") != "shell":
            raise ValueError("Only the child of a shell submodel is edited here")
        clone = detach_child(definition)
        base = definition_fingerprint(definition)
        QtFemWindow.__init__(self, project=clone, viewer_backend=viewer_backend)
        self.parent_app = parent_app
        self.definition_id = definition_id
        self._parent_session = parent_app.session
        self._parent_document = getattr(parent_app.project, "document_id", None)
        self._base_fingerprint = base
        self._draft_baseline = child_fingerprint(self.project)
        self._checked_revision = self._parent_revision()  # parent revision whose fingerprint was last compared
        self._checked_reason: Optional[str] = None
        self._outdated = False
        self.last_edit = None
        self.setWindowTitle(f"Edit submodel child — {definition.name}")
        self.apply_action = QAction("Apply to submodel", self)
        self.apply_action.setToolTip("Validate this draft and replace the submodel's child (undoable in the parent)")
        self.apply_action.triggered.connect(lambda _checked=False: self.apply_to_submodel())
        toolbar = self.addToolBar("Submodel")
        toolbar.setObjectName("SubmodelChildToolbar")
        toolbar.addAction(self.apply_action)
        # no polling: staleness is re-evaluated when the window is activated, after an apply and on Apply itself
        self._locked = True  # from here the document can no longer be replaced
        self.refresh_apply_state()

    # -- identity -------------------------------------------------------
    def _parent_revision(self) -> Any:
        return getattr(getattr(self.parent_app.session, "revision", None), "id", None)

    def stale_reason(self, *, full: bool = False) -> Optional[str]:
        """Why the draft can no longer be applied to the parent, or None.

        The (expensive) definition fingerprint is only recomputed when the parent's revision
        moved since the last comparison, or when ``full`` insists (Apply always does).
        """

        app = self.parent_app
        if getattr(app, "_closing", False):
            return "The parent window is closing."
        # the same session and document id mean the same document even when an undo/redo or an applied
        # command replaced the project object, so identity of the project object is deliberately not compared
        if app.session is not self._parent_session or getattr(app.project, "document_id", None) != self._parent_document:
            return "The parent document was switched."
        current = app.project.submodels.get(self.definition_id)
        if current is None:
            return "The submodel was deleted."
        revision = self._parent_revision()
        if full or revision is None or revision != self._checked_revision:
            self._checked_reason = None
            if definition_fingerprint(current) != self._base_fingerprint:
                self._checked_reason = "The submodel changed after this editor was opened (reopen to edit the current one)."
            self._checked_revision = revision
        if self._checked_reason is not None:
            return self._checked_reason
        if app.session.read_only:
            return "The parent document is read-only."
        return None

    def has_unapplied_changes(self) -> bool:
        return child_fingerprint(self.project) != self._draft_baseline

    def refresh_apply_state(self) -> None:
        reason = self.stale_reason()
        self.apply_action.setEnabled(reason is None)
        self.apply_action.setToolTip(reason or "Validate this draft and replace the submodel's child")

    def mark_outdated(self) -> None:
        """This draft was superseded by a newer editor; it stays open for review but cannot apply."""

        self._outdated = True
        self.setWindowTitle(self.windowTitle() + " (outdated draft)")
        self.refresh_apply_state()

    def changeEvent(self, event: Any) -> None:
        super().changeEvent(event)
        if self._locked and event.type() == QEvent.ActivationChange and self.isActiveWindow():
            self.refresh_apply_state()

    # -- no independent solving or mesh publication -----------------------------
    def _refuse_independent_run(self, *_args: Any, **_kwargs: Any) -> None:
        self.set_status("A submodel child is not meshed or solved here: edit it, apply it, then run the submodel "
                        "from the parent window. Sizing can be edited and is applied with the child.", error=True)

    def solve(self, *args: Any, **kwargs: Any) -> None:
        self._refuse_independent_run()

    def solve_async(self, *args: Any, **kwargs: Any) -> None:
        self._refuse_independent_run()

    def generate_mesh(self, *args: Any, **kwargs: Any) -> None:
        self._refuse_independent_run()

    def generate_mesh_async(self, *args: Any, **kwargs: Any) -> None:
        self._refuse_independent_run()

    # -- apply ------------------------------------------------------------
    def apply_to_submodel(self) -> Any:
        """Apply the draft; any refusal leaves the parent and the draft untouched (returns None)."""

        reason = "This draft was superseded by a newer editor." if self._outdated else self.stale_reason(full=True)
        if reason is not None:
            self.set_status(f"Not applied — {reason}", error=True)
            self.refresh_apply_state()
            return None
        try:
            outcome = apply_child_edit(self.parent_app.session, self.definition_id, self.project,
                                       base_fingerprint=self._base_fingerprint)
        except Exception as error:  # noqa: BLE001 - the refusal text is the product
            code = getattr(error, "code", type(error).__name__)
            self.set_status(f"Not applied — {code}: {error}", error=True)
            return None
        self.last_edit = outcome
        self._base_fingerprint = definition_fingerprint(outcome.definition)
        self._draft_baseline = child_fingerprint(self.project)
        self._checked_revision, self._checked_reason = None, None
        text = "Applied to submodel" if outcome.changed else "No changes to apply"
        self.set_status(text + ("; " + "; ".join(outcome.notes) if outcome.notes else ""))
        try:
            self.parent_app.set_status(f"Submodel {outcome.definition.name!r} child edited; earlier runs are kept")
        except Exception:  # noqa: BLE001 - the parent status line is only a courtesy
            pass
        self.refresh_apply_state()
        return outcome.definition

    # -- the document cannot change underneath the editor -----------------
    def _refuse_document_change(self, *_args: Any, **_kwargs: Any) -> None:
        self.set_status("This window edits a submodel child: use 'Apply to submodel'; "
                        "it cannot save, open or start a new document.", error=True)

    def save_project(self, *args: Any, **kwargs: Any) -> None:
        self._refuse_document_change()

    def open_project(self, *args: Any, **kwargs: Any) -> None:
        self._refuse_document_change()

    def new_project(self, *args: Any, **kwargs: Any) -> None:
        self._refuse_document_change()

    def _set_project(self, *args: Any, **kwargs: Any) -> Any:
        if self._locked:
            self._refuse_document_change()
            raise ValueError("The submodel editor's document cannot be replaced")
        return super()._set_project(*args, **kwargs)

    # -- closing -------------------------------------------------------------
    def _confirm_discard(self) -> bool:
        if self._forced or not self.has_unapplied_changes():
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Unapplied submodel edits")
        box.setText("The child has edits that were not applied to the submodel.")
        apply_button = box.addButton("Apply to submodel", QMessageBox.AcceptRole)
        box.addButton("Discard", QMessageBox.DestructiveRole)
        cancel = box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is cancel:
            return False
        if clicked is apply_button:
            return self.apply_to_submodel() is not None
        return True

    def close_editor(self, *, force: bool = False) -> bool:
        """Close; ``force`` drops an unapplied draft without asking."""

        self._forced = bool(force)
        try:
            return bool(self.close())
        finally:
            self._forced = False

    def closeEvent(self, event: Any) -> None:
        super().closeEvent(event)
        if event.isAccepted():
            editors = getattr(self.parent_app, _REGISTRY, {})
            if editors.get(self.definition_id) is self:
                del editors[self.definition_id]
            outdated = getattr(self.parent_app, _OUTDATED, None)
            if outdated is not None and self in outdated:
                outdated.remove(self)


class _ParentCloseFilter(QObject):
    """Closes the child editors with their parent window; a child that refuses vetoes the close."""

    def __init__(self, app: Any) -> None:
        QObject.__init__(self, app)
        self.app = app

    def eventFilter(self, watched: Any, event: Any) -> bool:
        if watched is self.app and event.type() == QEvent.Close:
            # only this window's own editors, and only asking where an unapplied draft would be lost
            for window in _own_windows(self.app):
                if not window.close_editor():
                    event.ignore()
                    return True
        return False


def child_editors(app: Any) -> Dict[str, ShellChildEditorWindow]:
    """The open child editors of a window, by definition id."""

    return dict(getattr(app, _REGISTRY, {}))


def close_child_editors(app: Any, *, force: bool = True) -> None:
    """Close every child editor of ``app`` (for the parent's shutdown)."""

    for window in _own_windows(app):
        window.close_editor(force=force)


def _own_windows(app: Any) -> list:
    """Current editors plus outdated drafts that were kept open for review."""

    return list(getattr(app, _REGISTRY, {}).values()) + list(getattr(app, _OUTDATED, []))


def open_shell_child_editor(app: Any, definition_id: str) -> ShellChildEditorWindow:
    """Open (or raise) the modeless editor of one shell submodel's child.

    An existing editor that has become stale is not handed back: it is kept open as an
    outdated draft (reviewable, closable, never applicable, nothing clobbered) and a fresh
    editor on the current definition is opened instead.
    """

    editors = getattr(app, _REGISTRY, None)
    if editors is None:
        editors = {}
        setattr(app, _REGISTRY, editors)
    existing = editors.get(definition_id)
    if existing is not None:
        if existing.stale_reason(full=True) is None:
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return existing
        if getattr(app, "_closing", False) or definition_id not in app.project.submodels:
            existing.show()
            existing.raise_()
            return existing  # nothing current to open instead
        del editors[definition_id]
        existing.mark_outdated()
        outdated = getattr(app, _OUTDATED, None)
        if outdated is None:
            outdated = []
            setattr(app, _OUTDATED, outdated)
        outdated.append(existing)
    if getattr(app, "_closing", False):
        raise ValueError("The window is closing")
    window = ShellChildEditorWindow(app, definition_id, viewer_backend=getattr(app, "_viewer_backend", "auto"))
    editors[definition_id] = window
    if getattr(app, _FILTER, None) is None:
        guard = _ParentCloseFilter(app)
        app.installEventFilter(guard)
        setattr(app, _FILTER, guard)
    window.show()
    return window
