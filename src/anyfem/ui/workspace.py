"""Persistent Details workspace, selection strip and job summary widgets."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any

from ..selection import SELECTION_MODES, mode_label

__all__ = ["DetailsWorkspace", "JobStatusView", "SelectionStrip"]


class DetailsWorkspace(ttk.Frame):
    """A commercial-style task/details host replacing stage notebook tabs."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        self._panels: dict[str, ttk.Frame] = {}
        self._buttons: dict[str, ttk.Button] = {}
        self._current: str | None = None

        header = ttk.Frame(self, padding=(8, 6))
        header.pack(fill="x")
        self._title = ttk.Label(header, text="Details", font=("TkDefaultFont", 11, "bold"))
        self._title.pack(side="left")
        self._hint = ttk.Label(header, text="", foreground="#666666")
        self._hint.pack(side="right")

        self._navigation = ttk.Frame(self, padding=(6, 0, 6, 6))
        self._navigation.pack(fill="x")
        self._content = ttk.Frame(self)
        self._content.pack(fill="both", expand=True)

    def add(self, panel: ttk.Frame, *, text: str) -> None:
        if text in self._panels:
            raise ValueError(f"details page {text!r} already exists")
        self._panels[text] = panel
        button = ttk.Button(
            self._navigation,
            text=text,
            command=lambda name=text: self.select(name),
        )
        button.pack(side="left", padx=1, pady=1)
        self._buttons[text] = button
        if self._current is None:
            self.select(text)

    def select(self, page: str | ttk.Frame) -> None:
        if not isinstance(page, str):
            name = next(
                (key for key, candidate in self._panels.items() if candidate is page),
                None,
            )
            if name is None:
                raise KeyError("panel is not registered in the Details workspace")
        else:
            name = page
        if name not in self._panels:
            raise KeyError(f"unknown Details page {name!r}")
        for key, panel in self._panels.items():
            if key == name:
                panel.pack(fill="both", expand=True)
                self._buttons[key].state(["disabled"])
            else:
                panel.pack_forget()
                self._buttons[key].state(["!disabled"])
        self._current = name
        self._title.configure(text=name)

    def current(self) -> str | None:
        return self._current

    def set_hint(self, text: str) -> None:
        self._hint.configure(text=text)


class SelectionStrip(ttk.Frame):
    """Always-visible selection domain/filter/tool/depth controls."""

    def __init__(self, master: tk.Misc, app) -> None:
        super().__init__(master, padding=(6, 3))
        self.app = app
        ttk.Label(self, text="Selection:").pack(side="left")

        self.domain = tk.StringVar(value="Geometry")
        domain_box = ttk.Combobox(
            self,
            textvariable=self.domain,
            values=("Geometry", "Mesh"),
            state="readonly",
            width=9,
        )
        domain_box.pack(side="left", padx=(4, 2))
        domain_box.bind("<<ComboboxSelected>>", self._set_domain)

        self.filter = tk.StringVar(value=mode_label(app.selection.mode))
        self._filter_box = ttk.Combobox(
            self,
            textvariable=self.filter,
            values=("Point", "Line", "Plate", "Node", "Element", "Element face"),
            state="readonly",
            width=12,
        )
        self._filter_box.pack(side="left", padx=2)
        self._filter_box.bind("<<ComboboxSelected>>", self._set_filter)

        self.tool = tk.StringVar(value="Box")
        tool_box = ttk.Combobox(
            self,
            textvariable=self.tool,
            values=("Single", "Box", "Lasso"),
            state="readonly",
            width=7,
        )
        tool_box.pack(side="left", padx=2)
        tool_box.bind("<<ComboboxSelected>>", lambda _event: self._apply_canvas())

        self.depth = tk.StringVar(value="Visible")
        depth_box = ttk.Combobox(
            self,
            textvariable=self.depth,
            values=("Visible", "Through"),
            state="readonly",
            width=8,
        )
        depth_box.pack(side="left", padx=2)
        depth_box.bind("<<ComboboxSelected>>", lambda _event: self._apply_canvas())

        self.operation = tk.StringVar(value="Replace")
        operation_box = ttk.Combobox(
            self,
            textvariable=self.operation,
            values=("Replace", "Add", "Remove", "Toggle"),
            state="readonly",
            width=8,
        )
        operation_box.pack(side="left", padx=2)
        operation_box.bind(
            "<<ComboboxSelected>>", lambda _event: self._apply_canvas()
        )

        self._count = ttk.Label(self, text="0 selected")
        self._count.pack(side="right")
        self._hint = ttk.Label(
            self,
            text="LMB select/drag  |  MMB pan  |  RMB orbit",
            foreground="#666666",
        )
        self._hint.pack(side="right", padx=12)
        app.selection.add_listener(self.refresh)

    def refresh(self) -> None:
        domain = getattr(self.app.selection.domain, "value", "geometry")
        self.domain.set("Mesh" if domain == "mesh" else "Geometry")
        self.filter.set(mode_label(self.app.selection.mode))
        count = len(self.app.selection)
        self._count.configure(text=f"{count} selected")

    def set_context(self, kind: str, hint: str = "") -> None:
        if kind in SELECTION_MODES:
            self.app.selection.set_mode(kind)
            self.domain.set("Geometry")
        if hint:
            self._hint.configure(text=hint)
        self.refresh()
        self._apply_canvas()

    def _set_filter(self, _event=None) -> None:
        mapping = {
            "Point": "vertex", "Line": "edge", "Plate": "face",
            "Node": "node", "Element": "element",
            "Element face": "element_face",
        }
        kind = mapping.get(self.filter.get())
        if kind is not None:
            self.domain.set(
                "Geometry" if kind in ("vertex", "edge", "face") else "Mesh"
            )
            self.app.selection.set_mode(kind)
        self._apply_canvas()

    def _set_domain(self, _event=None) -> None:
        kind = "face" if self.domain.get() == "Geometry" else "element"
        self.app.selection.set_mode(kind)
        self.filter.set(mode_label(kind))
        self._apply_canvas()

    def _apply_canvas(self) -> None:
        viewport = getattr(self.app, "viewport", None)
        if viewport is not None and hasattr(viewport, "configure_selection"):
            viewport.configure_selection(
                tool=self.tool.get().lower(),
                depth=self.depth.get().lower(),
                operation=self.operation.get().lower(),
            )


class JobStatusView(ttk.Frame):
    """Compact queued/running/completed job area below the viewport."""

    def __init__(self, master: tk.Misc, app) -> None:
        super().__init__(master, padding=(4, 2))
        self.app = app
        ttk.Label(self, text="Jobs", font=("TkDefaultFont", 9, "bold")).pack(side="left")
        self._text = ttk.Label(self, text="No jobs", foreground="#555555")
        self._text.pack(side="left", padx=8)
        ttk.Button(self, text="Open", width=6, command=lambda: app.details.select("Solve")).pack(side="right")

    def refresh(self) -> None:
        manager = getattr(self.app, "job_manager", None)
        if manager is None:
            return
        tree = getattr(self.app, "tree", None)
        if tree is not None and hasattr(tree, "refresh_job_states"):
            tree.refresh_job_states()
        active = manager.active_job_id
        queued = len(manager.queued)
        if active:
            text = f"Running {active[:8]}  |  {queued} queued"
        elif queued:
            text = f"{queued} queued"
        elif getattr(self.app.project, "jobs", None):
            records = list(self.app.project.jobs.values())
            text = f"{len(records)} retained  |  {records[-1].status.value}"
        else:
            text = "No jobs"
        self._text.configure(text=text)
