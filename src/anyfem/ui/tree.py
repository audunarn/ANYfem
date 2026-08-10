"""The model tree: everything in the project, and a way to select it.

The tree is a view. Clicking an entity row drives the shared Selection, which
the viewport listens to, so the tree and the 3D view can never disagree about
what is selected.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Dict, Optional
from anygeometry.entities import EntityRef

from ..model.project import Project
from ..selection import Selection, entity_tag, parse_entity_tag

__all__ = ["ModelTree"]


class ModelTree(ttk.Frame):
    """A collapsible view of materials, sections, geometry and attributes."""

    def __init__(
        self, master: tk.Misc, project: Project, selection: Selection
    ) -> None:
        super().__init__(master)
        self.project = project
        self.selection = selection
        self._syncing = False
        self._open_state: Dict[str, bool] = {}
        self._row_refs: Dict[str, EntityRef] = {}
        self._scroll_fraction = 0.0

        self.tree = ttk.Treeview(self, show="tree", selectmode="extended")
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.selection.add_listener(self.sync_from_selection)

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """Rebuild the tree from the project, preserving what was expanded."""

        self._remember_open_state()
        self._syncing = True
        try:
            self._row_refs.clear()
            self.tree.delete(*self.tree.get_children())
            geometry = self.project.geometry

            materials = self._group("materials", "Materials")
            for name, material in sorted(self.project.materials.items()):
                if material.symmetry == "isotropic":
                    modulus = material.constants["elastic_modulus"] / 1e9
                    description = f"E={modulus:g} GPa"
                else:
                    description = "orthotropic"
                self._leaf(
                    materials,
                    f"material:{name}",
                    f"{name}   {description}",
                )

            sections = self._group("sections", "Sections")
            for name, plate in sorted(self.project.plate_sections.items()):
                self._leaf(
                    sections,
                    f"plate_section:{name}",
                    f"{name}   plate {plate.thickness * 1000:g} mm",
                )
            for name, beam in sorted(self.project.beam_sections.items()):
                self._leaf(
                    sections, f"beam_section:{name}", f"{name}   {beam.profile}"
                )

            points = self._group("points", f"Points ({len(geometry.vertices)})")
            for vertex_id in sorted(geometry.vertices):
                position = geometry.vertex_position(vertex_id)
                self._leaf(
                    points,
                    entity_tag(EntityRef("vertex", vertex_id)),
                    f"Point {vertex_id}   "
                    f"({position[0]:g}, {position[1]:g}, {position[2]:g})",
                )

            lines = self._group("lines", f"Lines ({len(geometry.edges)})")
            for edge_id in sorted(geometry.edges):
                edge = geometry.edges[edge_id]
                kind = type(edge.curve).__name__.lower()
                section = self.project.edge_sections.get(edge_id)
                suffix = f"   [{section}]" if section else ""
                self._leaf(
                    lines,
                    entity_tag(EntityRef("edge", edge_id)),
                    f"Line {edge_id}   {kind} {edge.start}→{edge.end}{suffix}",
                )

            plates = self._group("plates", f"Plates ({len(geometry.faces)})")
            for face_id in sorted(geometry.faces):
                section = self.project.face_sections.get(face_id)
                suffix = f"   [{section}]" if section else "   (no section)"
                self._leaf(
                    plates,
                    entity_tag(EntityRef("face", face_id)),
                    f"Plate {face_id}{suffix}",
                )

            supports = self._group(
                "supports", f"Supports ({len(self.project.supports)})"
            )
            for index, support in enumerate(self.project.supports):
                dofs = ",".join(sorted(support.constraints))
                self._leaf(
                    supports,
                    f"support:{index}",
                    f"{support.name}   {support.ref}   {dofs}",
                    ref=support.ref,
                )

            masses = self._group(
                "masses", f"Masses ({len(self.project.masses)})"
            )
            for index, mass in enumerate(self.project.masses):
                self._leaf(
                    masses,
                    f"mass:{index}",
                    f"{mass.name}   {mass.value:g} kg at {mass.ref}",
                    ref=mass.ref,
                )

            total_loads = sum(
                len(case.point_loads)
                + len(case.pressures)
                + len(case.line_loads)
                + len(case.surface_tractions)
                + int(case.gravity is not None)
                for case in self.project.load_cases.values()
            )
            loads = self._group("loads", f"Loads ({total_loads})")
            for name, case in sorted(self.project.load_cases.items()):
                case_count = (
                    len(case.point_loads)
                    + len(case.pressures)
                    + len(case.line_loads)
                    + len(case.surface_tractions)
                    + int(case.gravity is not None)
                )
                case_node = self._leaf(
                    loads, f"case:{name}", f"Case {name} ({case_count})"
                )
                for load in case.point_loads:
                    self._leaf(
                        case_node,
                        f"load:{name}:point:{id(load)}",
                        f"Point load at {load.ref}",
                        ref=load.ref,
                    )
                for load in case.pressures:
                    self._leaf(
                        case_node,
                        f"load:{name}:pressure:{id(load)}",
                        f"Pressure {load.value:g} Pa on {load.ref}",
                        ref=load.ref,
                    )
                for load in case.line_loads:
                    self._leaf(
                        case_node,
                        f"load:{name}:line:{id(load)}",
                        f"Line load on {load.ref}",
                        ref=load.ref,
                    )
                for load in case.surface_tractions:
                    self._leaf(
                        case_node,
                        f"load:{name}:traction:{id(load)}",
                        f"Surface traction on {load.ref}",
                        ref=load.ref,
                    )
                if case.gravity is not None:
                    self._leaf(
                        case_node, f"load:{name}:gravity", "Gravity / acceleration"
                    )
        finally:
            self._syncing = False
        self._restore_open_state()
        self.sync_from_selection()

    # ------------------------------------------------------------------
    def _group(self, key: str, label: str) -> str:
        return self.tree.insert("", "end", iid=key, text=label, open=True)

    def _leaf(
        self,
        parent: str,
        key: str,
        label: str,
        *,
        ref: Optional[EntityRef] = None,
    ) -> str:
        item = self.tree.insert(parent, "end", iid=key, text=label)
        if ref is not None:
            self._row_refs[key] = ref
        return item

    def _remember_open_state(self) -> None:
        view = self.tree.yview()
        if view:
            self._scroll_fraction = float(view[0])
        for key in self.tree.get_children():
            self._open_state[key] = bool(self.tree.item(key, "open"))

    def _restore_open_state(self) -> None:
        for key, is_open in self._open_state.items():
            if self.tree.exists(key):
                self.tree.item(key, open=is_open)
        self.tree.yview_moveto(self._scroll_fraction)

    # ------------------------------------------------------------------
    def _on_tree_select(self, _event: tk.Event) -> None:
        # Tk fires this while tearing the widget down, when the panels the
        # listeners refresh may already be gone.
        if self._syncing or not self.tree.winfo_exists():
            return
        refs = [
            ref
            for ref in (
                parse_entity_tag(key) or self._row_refs.get(key)
                for key in self.tree.selection()
            )
            if ref is not None
        ]
        if not refs or refs == self.selection.items:
            # Tk delivers <<TreeviewSelect>> asynchronously, so this can arrive
            # after our own sync has already applied it.  Echoing it back would
            # bounce between the tree and the selection forever.
            return
        # Follow the tree into whichever entity mode the user clicked on.
        kinds = {ref.kind for ref in refs}
        if len(kinds) == 1:
            self.selection.set_mode(kinds.pop())
        self.selection.select_many(refs)

    def sync_from_selection(self) -> None:
        """Mirror the shared selection into the tree without echoing back."""

        if self._syncing:
            return
        self._syncing = True
        try:
            keys = [
                tag for tag in self.selection.tags() if self.tree.exists(tag)
            ]
            if list(self.tree.selection()) == keys:
                return
            self.tree.selection_set(keys)
            if keys:
                self.tree.see(keys[0])
        finally:
            self._syncing = False

    def describe_selection(self) -> str:
        return self.selection.describe()
