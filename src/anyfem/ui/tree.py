"""The model tree: everything in the project, and a way to select it.

The tree is a view. Clicking an entity row drives the shared Selection, which
the viewport listens to, so the tree and the 3D view can never disagree about
what is selected.
"""

from __future__ import annotations

from itertools import islice
import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, Iterable, Optional
from anygeometry.entities import EntityRef

from ..model.project import Project
from ..model.regions import BooleanRegion, QueryRegion, RegionDomain, RegionStatus
from ..selection import Selection, entity_tag, parse_entity_tag

__all__ = ["ModelTree", "TREE_ENTITY_ROW_LIMIT", "bounded_entity_ids"]


TREE_ENTITY_ROW_LIMIT = 2000


def bounded_entity_ids(
    collection: Iterable[object],
    query: str = "",
    *,
    limit: int = TREE_ENTITY_ROW_LIMIT,
) -> list[int]:
    """Return only the entity IDs that the virtual model tree needs.

    Normal refreshes consume at most ``limit`` values.  A numeric search uses
    mapping membership directly, so jumping to an entity in a 50k-owner model
    does not first allocate or scan a 50k-item list.  The helper is deliberately
    Tk-free so the scalability contract remains enforceable in headless CI.
    """

    if limit < 0:
        raise ValueError("tree row limit cannot be negative")
    normalized = str(query).strip().casefold()
    digits = "".join(character for character in normalized if character.isdigit())
    if digits:
        wanted = int(digits)
        try:
            present = wanted in collection  # type: ignore[operator]
        except (TypeError, AttributeError):
            present = any(int(identifier) == wanted for identifier in collection)
        return [wanted] if present else []
    return [int(value) for value in islice(iter(collection), limit)]


class ModelTree(ttk.Frame):
    """A collapsible view of materials, sections, geometry and attributes."""

    def __init__(
        self,
        master: tk.Misc,
        project: Project,
        selection: Selection,
        *,
        job_is_stale: Optional[Callable[[object], bool]] = None,
        mesh_is_stale: Optional[Callable[[object], bool]] = None,
    ) -> None:
        super().__init__(master)
        self.project = project
        self.selection = selection
        self._syncing = False
        self._open_state: Dict[str, bool] = {}
        self._row_refs: Dict[str, EntityRef] = {}
        self._region_candidate_cache: dict[str, tuple[EntityRef, ...]] = {}
        self._scroll_fraction = 0.0
        self._action_handler: Optional[
            Callable[[str, tuple[str, ...]], None]
        ] = None
        self._filter_text = tk.StringVar(value="")
        self._job_is_stale = job_is_stale or (lambda _job: False)
        self._mesh_is_stale = mesh_is_stale or (lambda _mesh: False)

        search = ttk.Frame(self, padding=(4, 4))
        search.pack(fill="x")
        ttk.Label(search, text="Model").pack(side="left")
        entry = ttk.Entry(search, textvariable=self._filter_text, width=18)
        entry.pack(side="right", fill="x", expand=True, padx=(6, 0))
        self._filter_text.trace_add("write", lambda *_args: self.refresh())

        self.tree = ttk.Treeview(self, show="tree", selectmode="extended")
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>", lambda _event: self._invoke("edit"))
        self.tree.bind("<Button-3>", self._context_menu)
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

            settings = self._group("settings", "Project Settings")
            profile = self.project.units
            self._leaf(
                settings,
                "unit:profile",
                "Units   "
                f"{profile.name}   "
                f"[{profile.symbol('length')} / {profile.symbol('force')} / "
                f"{profile.symbol('pressure')}]",
            )

            feature_records = getattr(
                getattr(geometry, "features", None), "records", ()
            )
            features = self._group(
                "features",
                f"Geometry / Features ({len(feature_records)})",
            )
            for feature in feature_records:
                status = "suppressed" if feature.suppressed else feature.state
                self._leaf(
                    features,
                    f"feature:{feature.feature_id}",
                    f"{feature.name}   [{status}]",
                )

            coordinates = self._group(
                "coordinates",
                f"Coordinate Systems ({len(self.project.coordinate_systems)})",
            )
            for identifier, system in sorted(self.project.coordinate_systems.items()):
                self._leaf(
                    coordinates,
                    f"coordinate:{identifier}",
                    f"{system.name}   {system.kind}",
                )

            named_regions = tuple(
                region for region in self.project.regions if not region.hidden
            )
            self._region_candidate_cache: dict[str, tuple[EntityRef, ...]] = {}
            regions = self._group(
                "regions", f"Regions ({len(named_regions)})"
            )
            for region in named_regions:
                status = self._region_status(region)
                definition = region.definition
                definition_label = (
                    definition.operation
                    if isinstance(definition, BooleanRegion)
                    else type(definition).__name__.removesuffix("Region").lower()
                )
                self._leaf(
                    regions,
                    f"region:{region.id}",
                    f"{region.name}   {region.domain.value}/{region.entity_kind}"
                    f"   {definition_label}   [{status.value}]",
                )

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
            for vertex_id in self._visible_ids(geometry.vertices):
                position = geometry.vertex_position(vertex_id)
                self._leaf(
                    points,
                    entity_tag(EntityRef("vertex", vertex_id)),
                    f"Point {vertex_id}   "
                    f"({position[0]:g}, {position[1]:g}, {position[2]:g})",
                )

            lines = self._group("lines", f"Lines ({len(geometry.edges)})")
            for edge_id in self._visible_ids(geometry.edges):
                edge = geometry.edges[edge_id]
                kind = type(edge.curve).__name__.lower()
                section = self.project.edge_sections.get(edge_id)
                suffix = f"   [{section}]" if section else ""
                self._leaf(
                    lines,
                    entity_tag(EntityRef("edge", edge_id)),
                    f"Line {edge_id}   {kind} {edge.start}->{edge.end}{suffix}",
                )

            plates = self._group("plates", f"Plates ({len(geometry.faces)})")
            for face_id in self._visible_ids(geometry.faces):
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
                identifier = getattr(support, "id", f"legacy-{index}")
                self._leaf(
                    supports,
                    f"support:{identifier}",
                    f"{support.name}   {support.ref}   {dofs}",
                    ref=support.ref,
                )

            masses = self._group(
                "masses", f"Masses ({len(self.project.masses)})"
            )
            for index, mass in enumerate(self.project.masses):
                identifier = getattr(mass, "id", f"legacy-{index}")
                self._leaf(
                    masses,
                    f"mass:{identifier}",
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
                case_id = getattr(case, "id", name)
                case_node = self._leaf(
                    loads, f"case:{case_id}", f"Case {name} ({case_count})"
                )
                for load in case.point_loads:
                    self._leaf(
                        case_node,
                        f"load:point:{load.id}",
                        f"Point load at {load.ref}",
                        ref=load.ref,
                    )
                for load in case.pressures:
                    self._leaf(
                        case_node,
                        f"load:pressure:{load.id}",
                        f"Pressure {load.value:g} Pa on {load.ref}",
                        ref=load.ref,
                    )
                for load in case.line_loads:
                    self._leaf(
                        case_node,
                        f"load:line:{load.id}",
                        f"Line load on {load.ref}",
                        ref=load.ref,
                    )
                for load in case.surface_tractions:
                    self._leaf(
                        case_node,
                        f"load:traction:{load.id}",
                        f"Surface traction on {load.ref}",
                        ref=load.ref,
                    )
                if case.gravity is not None:
                    self._leaf(
                        case_node,
                        f"load:{case_id}:gravity",
                        "Gravity / acceleration",
                    )

            meshes = self._group(
                "meshes", f"Meshes ({len(self.project.mesh_records)})"
            )
            for mesh in self.project.mesh_records.values():
                self._leaf(
                    meshes,
                    f"mesh:{mesh.id}",
                    self._mesh_label(mesh),
                )

            output_requests = self._group(
                "output_requests",
                f"Output Requests ({len(self.project.output_requests)})",
            )
            for request in self.project.output_requests.values():
                status = self._output_request_status(request)
                quantities = ", ".join(request.quantity_keys)
                self._leaf(
                    output_requests,
                    f"output_request:{request.id}",
                    f"{request.label}   {quantities} / {request.location}"
                    f"   [{status}]",
                )

            analyses = self._group(
                "analyses", f"Analyses ({len(self.project.analyses)})"
            )
            for analysis in self.project.analyses.values():
                self._leaf(
                    analyses,
                    f"analysis:{analysis.id}",
                    f"{analysis.name}   {analysis.type.replace('_', ' ')}",
                )

            jobs = self._group("jobs", f"Jobs ({len(self.project.jobs)})")
            results = self._group(
                "results",
                "Results "
                f"({sum(job.result_artifact_id is not None for job in self.project.jobs.values())})",
            )
            for job in reversed(tuple(self.project.jobs.values())):
                stale = bool(self._job_is_stale(job))
                self._leaf(jobs, f"job:{job.id}", self._job_label(job, stale))
                if job.result_artifact_id is not None:
                    self._leaf(
                        results,
                        f"result:{job.result_artifact_id}",
                        self._result_label(job, stale),
                    )
        finally:
            self._syncing = False
        self._restore_open_state()
        self.sync_from_selection()

    def _region_status(self, region) -> RegionStatus:
        """Return a cheap, deterministic badge for one reusable scope."""

        if (
            region.domain is RegionDomain.MESH
            and region.mesh_id not in self.project.mesh_records
        ):
            return RegionStatus.STALE
        geometry = self.project.geometry
        candidates: tuple[EntityRef, ...] = ()
        if region.domain is RegionDomain.GEOMETRY:
            collections = {
                "vertex": geometry.vertices,
                "edge": geometry.edges,
                "face": geometry.faces,
            }
            collection = collections.get(region.entity_kind, ())
            if self._region_uses_query(region.id):
                # A query over tens of thousands of owners belongs in the
                # retained/background selection index, not in a tree rebuild.
                # Its AST and dependencies are already validated on insert;
                # report that structural validity without an eager full scan.
                if len(collection) > TREE_ENTITY_ROW_LIMIT:
                    return RegionStatus.VALID
                candidates = self._region_candidate_cache.setdefault(
                    region.entity_kind,
                    tuple(
                        EntityRef(region.entity_kind, int(identifier))
                        for identifier in collection
                    ),
                )
        try:
            return self.project.regions.status(
                region.id,
                geometry=geometry,
                mesh_id=region.mesh_id,
                candidates=candidates,
                feature_resolver=lambda anchor: geometry.features.resolve(
                    anchor, geometry
                ),
            )
        except (AttributeError, KeyError, ValueError):
            return RegionStatus.UNRESOLVED

    def _output_request_status(self, request) -> str:
        """Cheap tree badge without ever fabricating an available field."""

        try:
            region = self.project.regions[request.region.id]
        except (AttributeError, KeyError, ValueError):
            return "unresolved"
        status = self._region_status(region)
        if status is not RegionStatus.VALID:
            return status.value
        if (
            request.basis
            not in ("global", "local", "element", "material", "cylindrical")
            and request.basis not in self.project.coordinate_systems
        ):
            return "unresolved"
        for analysis in self.project.analyses.values():
            if request.id in analysis.output_request_ids and request.problems_for_analysis(
                analysis.type
            ):
                return "invalid"
        return "valid"

    def _region_uses_query(
        self, region_id: str, seen: frozenset[str] = frozenset()
    ) -> bool:
        if region_id in seen:
            return False
        definition = self.project.regions[region_id].definition
        if isinstance(definition, QueryRegion):
            return True
        if not isinstance(definition, BooleanRegion):
            return False
        visited = seen | {region_id}
        return any(
            self._region_uses_query(child, visited)
            for child in definition.region_ids
        )

    def refresh_job_states(self) -> None:
        """Synchronize the small job/result branches without a full rebuild."""

        if not self.tree.exists("jobs") or not self.tree.exists("results"):
            return
        records = tuple(self.project.jobs.values())
        self.tree.item("jobs", text=f"Jobs ({len(records)})")
        self.tree.item(
            "results",
            text="Results "
            f"({sum(job.result_artifact_id is not None for job in records)})",
        )

        for job in reversed(records):
            stale = bool(self._job_is_stale(job))
            job_key = f"job:{job.id}"
            if self.tree.exists(job_key):
                self.tree.item(job_key, text=self._job_label(job, stale))
            else:
                self.tree.insert(
                    "jobs", 0, iid=job_key, text=self._job_label(job, stale)
                )
            if job.result_artifact_id is not None:
                result_key = f"result:{job.result_artifact_id}"
                if self.tree.exists(result_key):
                    self.tree.item(
                        result_key, text=self._result_label(job, stale)
                    )
                else:
                    self.tree.insert(
                        "results",
                        0,
                        iid=result_key,
                        text=self._result_label(job, stale),
                    )
        result_index = 0
        for job_index, job in enumerate(reversed(records)):
            self.tree.move(f"job:{job.id}", "jobs", job_index)
            if job.result_artifact_id is not None:
                self.tree.move(
                    f"result:{job.result_artifact_id}",
                    "results",
                    result_index,
                )
                result_index += 1

    def refresh_mesh_states(self) -> None:
        """Refresh the small mesh branch after a revision without rebuilding."""

        if not self.tree.exists("meshes"):
            return
        records = tuple(self.project.mesh_records.values())
        self.tree.item("meshes", text=f"Meshes ({len(records)})")
        for index, mesh in enumerate(records):
            key = f"mesh:{mesh.id}"
            label = self._mesh_label(mesh)
            if self.tree.exists(key):
                self.tree.item(key, text=label)
            else:
                self.tree.insert("meshes", "end", iid=key, text=label)
            self.tree.move(key, "meshes", index)

    def _mesh_label(self, mesh: object) -> str:
        status = str(getattr(mesh, "status", "") or "completed")
        if self._mesh_is_stale(mesh):
            status = "stale"
        kind = str(getattr(mesh, "kind", "generated"))
        badge = status if status == kind else f"{kind}, {status}"
        return f"{getattr(mesh, 'name', 'Mesh')}   [{badge}]"

    @staticmethod
    def _job_label(job: object, stale: bool) -> str:
        status_value = getattr(job, "status", "unknown")
        status = getattr(status_value, "value", str(status_value))
        if stale:
            status = f"{status}, stale"
        return f"{getattr(job, 'name', 'Job')}   [{status}]"

    @staticmethod
    def _result_label(job: object, stale: bool) -> str:
        label = f"{getattr(job, 'name', 'Job')} results"
        return label + ("   [stale]" if stale else "")

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

    def _visible_ids(self, collection: object) -> list[int]:
        """Return a bounded, filtered set of entity IDs for the virtual tree.

        The viewport owns the full model.  The tree intentionally materialises
        only a useful window for very large models, while a numeric search can
        jump directly to an entity without creating tens of thousands of Tk
        rows.
        """

        query = self._filter_text.get().strip().casefold()
        return bounded_entity_ids(  # type: ignore[arg-type]
            collection,
            query,
            limit=TREE_ENTITY_ROW_LIMIT,
        )

    def set_action_handler(
        self, callback: Optional[Callable[[str, tuple[str, ...]], None]]
    ) -> None:
        """Receive context and double-click actions from the application."""

        self._action_handler = callback

    def _context_menu(self, event: tk.Event) -> None:
        row = self.tree.identify_row(event.y)
        if row and row not in self.tree.selection():
            self.tree.selection_set(row)
        menu = tk.Menu(self, tearoff=False)
        for label, action in (
            ("Edit", "edit"),
            ("Rename", "rename"),
            ("Suppress / Resume", "suppress"),
            ("Duplicate", "duplicate"),
            ("Isolate", "isolate"),
            ("Show Dependencies", "dependencies"),
            ("Delete", "delete"),
        ):
            menu.add_command(label=label, command=lambda value=action: self._invoke(value))
        menu.tk_popup(event.x_root, event.y_root)

    def _invoke(self, action: str) -> None:
        if self._action_handler is not None:
            self._action_handler(action, tuple(self.tree.selection()))

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
