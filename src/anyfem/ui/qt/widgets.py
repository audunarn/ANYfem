"""Small presentation widgets shared by the Qt workspaces.

They only arrange or label existing controls; values and command routes are unchanged.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QFrame, QLabel, QSizePolicy, QToolButton,
    QVBoxLayout, QWidget)


class FormHost(QWidget):
    """Holds one form widget at its natural height.

    It keeps the ``widget``/``setWidget``/``takeWidget`` contract of the former inner
    scroll area, so the workspace's single outer scroll area does all scrolling.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._widget = None

    def widget(self):
        return self._widget

    def setWidget(self, widget):
        self.takeWidget()
        self._widget = widget
        self._layout.addWidget(widget)

    def takeWidget(self):
        widget, self._widget = self._widget, None
        if widget is not None:
            self._layout.removeWidget(widget)
            widget.setParent(None)
        return widget


class Collapsible(QWidget):
    """A titled section whose body can be expanded on request.

    Collapsed sections keep their controls alive, so values and buttons keep working.
    """

    def __init__(self, title, parent=None, *, expanded=False, hint=""):
        super().__init__(parent)
        self.setObjectName("CollapsibleSection")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(4)
        self.toggle = QToolButton(self)
        self.toggle.setObjectName("SectionToggle")
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.toggle.setAccessibleName(title)
        if hint:
            self.toggle.setToolTip(hint)
        layout.addWidget(self.toggle)
        self.body = QFrame(self)
        self.body.setObjectName("SectionBody")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(6, 2, 0, 4)
        self.body_layout.setSpacing(6)
        layout.addWidget(self.body)
        self.toggle.toggled.connect(self.set_expanded)
        self.toggle.setChecked(expanded)
        self.set_expanded(expanded)

    def addWidget(self, widget):
        self.body_layout.addWidget(widget)
        return widget

    def addLayout(self, layout):
        self.body_layout.addLayout(layout)
        return layout

    def is_expanded(self):
        return self.toggle.isChecked()

    def set_expanded(self, expanded):
        expanded = bool(expanded)
        if self.toggle.isChecked() != expanded:
            self.toggle.setChecked(expanded)
            return
        self.toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.body.setVisible(expanded)


def section_label(text, parent=None):
    """A small uppercase heading used to group controls inside a workspace."""
    label = QLabel(text.upper(), parent)
    label.setObjectName("SectionHeading")
    return label


def hint_label(text, parent=None):
    """Secondary explanatory text."""
    label = QLabel(text, parent)
    label.setObjectName("Hint")
    label.setWordWrap(True)
    return label


class ValueCombo(QComboBox):
    """A combo that shows readable labels while storing the internal value."""

    def __init__(self, choices, parent=None):
        super().__init__(parent)
        for value, label in choices:
            self.addItem(label, value)

    def value(self):
        return self.currentData()

    def set_value(self, value):
        index = self.findData(value)
        if index < 0:
            raise ValueError(f"Unknown choice {value!r}")
        self.setCurrentIndex(index)


class ChoiceEdit(QComboBox):
    """A drop-down that reads and writes like the text field it replaces.

    ``text()`` returns the internal value, so command parsing is unchanged. Unknown
    values (for example from a loaded record) are kept as an extra choice, never
    silently replaced.
    """

    def __init__(self, choices, default=None, parent=None):
        super().__init__(parent)
        for value, label in choices:
            self.addItem(label, value)
        if default is not None:
            self.setText(default)

    def set_choices(self, choices):
        """Replace the offered values, keeping the current value selected."""
        choices = tuple(choices)
        if choices == tuple((self.itemData(i), self.itemText(i)) for i in range(self.count())):
            return
        current = self.text()
        self.blockSignals(True)
        try:
            self.clear()
            for value, label in choices:
                self.addItem(label, value)
            if current:
                self.setText(current)
        finally:
            self.blockSignals(False)

    def text(self):
        value = self.currentData()
        return "" if value is None else str(value)

    def setText(self, value):
        value = str(value)
        index = self.findData(value)
        if index < 0:
            self.addItem(value, value)
            index = self.count() - 1
        self.setCurrentIndex(index)

    def setPlaceholderText(self, text):
        super().setPlaceholderText(text)

    def setReadOnly(self, read_only):
        self.setEnabled(not read_only)
