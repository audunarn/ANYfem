"""Headless ANYfem adapter for the incremental native meshing runtime."""

from __future__ import annotations

from dataclasses import dataclass, replace
from threading import RLock
from typing import Any, Iterable, Mapping

from anygeometry import EntityHandle, EntityRef, extract_model_closure

from anymesher.hybrid import generate_hybrid_mesh_result
from anymesher.meshing_view import GeometryMeshingView
from anymesher.refinement import Refinement

from .native_meshing import (
    CertificationMode,
    CoalescedChangeSet,
    DirtyComponentResolution,
    MeshBackend,
    MeshGenerationRequest,
    NativeMeshCancelled,
    NativeMeshingCancellation,
    NativeMeshResult,
    NativeMeshSettings,
    NativeMeshingRuntime,
)
from .structural_preparation import (
    prepare_structural_connectivity,
    remap_mesh_to_source,
    source_work_mapping,
    source_edge_paths,
)

__all__ = [
    "GeometryComponentSnapshot",
    "NativeProjectMeshingSession",
    "create_native_meshing_session",
]


@dataclass(frozen=True, slots=True)
class GeometryComponentSnapshot:
    """Detached component closure plus the source revision that created it."""

    geometry: Any
    component: EntityHandle
    source_geometry: Any
    source_component: EntityHandle
    model_id: Any
    revision: int
    source_to_work: Mapping[EntityHandle, EntityHandle]
    work_to_source: Mapping[EntityHandle, EntityHandle]
    closure: Any

    def assert_current(self) -> None:
        if self.source_geometry.model_id != self.model_id:
            raise RuntimeError("geometry model identity changed during mesh generation")
        if int(self.source_geometry.revision) != int(self.revision):
            raise RuntimeError(
                "geometry changed during mesh generation; stale result rejected"
            )


def _key(value: Any) -> tuple[str, int] | None:
    if hasattr(value, "kind") and hasattr(value, "id"):
        return str(value.kind), int(value.id)
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return str(value[0]), int(value[1])
    return None


class NativeProjectMeshingSession:
    """Own event hooks, component resolution, generation and publication."""

    def __init__(
        self,
        project: Any,
        settings: NativeMeshSettings | None = None,
        *,
        max_background_jobs: int = 2,
    ) -> None:
        self.project = project
        self.geometry = project.geometry
        self._native_backend = project.set_native_triangulation_backend(
            project.native_triangulation_backend
        )
        resolved = settings or project.native_mesh_settings
        if resolved is None:
            if project.target_size is None:
                raise ValueError(
                    "native meshing needs persisted settings or project.target_size"
                )
            resolved = NativeMeshSettings.create(
                float(project.target_size), element_order=project.element_order
            )
        # The persisted settings never carry the generator-derived controls;
        # the runtime sees them merged in (see ``_effective_settings``).
        project.set_native_mesh_settings(resolved)
        self._lock = RLock()
        self._audit_reports: dict[int, Any] = {}
        self.runtime = NativeMeshingRuntime(
            self._effective_settings(resolved),
            resolve_dirty_components=self.resolve_dirty_components,
            generate_component=self.generate_component,
            capture_component=self.capture_component,
            max_background_jobs=max_background_jobs,
        )
        self.geometry.add_change_hook(self.on_geometry_change)
        self._closed = False

    # -- generator-owned size controls ----------------------------------
    def _generator_controls(self) -> tuple[Any, ...]:
        """Scoped override controls mirroring the project's generator sizes.

        Stable ids (``generator:<feature>:mesh``) and scopes (the generator's
        own output faces/edges), derived from the live generator properties and
        never persisted.  A feature that no longer resolves contributes none.
        """

        from .generator_properties import generator_mesh_controls

        # Absent or suppressed generators contribute nothing (decided inside);
        # a malformed advertised size raises GeneratorControlError.
        return generator_mesh_controls(self.project)

    def _effective_settings(self, base: NativeMeshSettings) -> NativeMeshSettings:
        generated = self._generator_controls()
        if not generated:
            return base
        owned = {control.control_id for control in generated}
        kept = tuple(item for item in base.controls if item.control_id not in owned)
        return replace(base, controls=(*kept, *generated))

    def _components_of_controls(
        self, controls: Iterable[Any]
    ) -> tuple[EntityHandle, ...]:
        """Components a control scope can influence (empty scope: all of them)."""

        view = GeometryMeshingView(self.geometry)
        found: set[EntityHandle] = set()
        for control in controls:
            handles = tuple(control.scope.handles)
            if not handles:
                return self.all_components()
            for handle in handles:
                if handle.model_id != self.geometry.model_id:
                    raise ValueError("control scope belongs to another geometry model")
                found.update(self._components_for_key(view, handle.kind, int(handle.id)))
        return tuple(sorted(found, key=lambda item: (item.kind, item.id)))

    def _refresh_generator_controls(self) -> tuple[EntityHandle, ...]:
        """Re-derive the generator controls; return the components that changed.

        A change that is only in generator-owned controls goes through the
        runtime's scoped route: the control generation stays, the publications
        and jobs of every other component stay valid, and each affected
        component's old request can never publish the old size.  Any other
        difference (the persisted settings changed) is a global update.
        """

        base = self.project.native_mesh_settings
        if base is None:
            return ()
        new = self._effective_settings(base)
        old = self.runtime.settings
        if new == old:
            return ()

        def owned(settings: NativeMeshSettings) -> dict[str, Any]:
            return {
                item.control_id: item for item in settings.controls
                if item.control_id.startswith("generator:")
            }

        def rest(settings: NativeMeshSettings) -> NativeMeshSettings:
            return replace(settings, controls=tuple(
                item for item in settings.controls
                if not item.control_id.startswith("generator:")
            ))

        if rest(new) != rest(old):
            self.runtime.set_settings(new)
            return self.all_components()
        before, after = owned(old), owned(new)
        changed = [
            item for key in sorted(set(before) | set(after))
            for item in (before.get(key), after.get(key))
            if item is not None and before.get(key) != after.get(key)
        ]
        affected = self._components_of_controls(changed)
        return self.runtime.set_settings_scoped(new, affected)

    def refresh_generator_controls(self) -> int:
        """Re-derive the generator controls; returns the control generation.

        Public contract kept: the return value is the runtime's control
        generation.  Only the components a changed generator size can reach are
        remeshed (see :meth:`_refresh_generator_controls`).
        """

        self._refresh_generator_controls()
        return self.runtime.control_generation

    def _handle(self, kind: str, identifier: int) -> EntityHandle:
        return EntityHandle(self.geometry.model_id, str(kind), int(identifier))

    def all_components(self) -> tuple[EntityHandle, ...]:
        view = GeometryMeshingView(self.geometry)
        handles: list[EntityHandle] = []
        covered_faces: set[int] = set()
        for sheet_id in sorted(view.sheets):
            handles.append(self.geometry.handle("sheet", int(sheet_id)))
            covered_faces.update(view.faces_for_sheet(int(sheet_id)))
        for face_id in sorted(set(view.faces) - covered_faces):
            handles.append(self.geometry.handle("face", int(face_id)))
        for member_id in sorted(view.members):
            handles.append(self.geometry.handle("member", int(member_id)))
        return tuple(handles)

    def capture_component(self, component: EntityHandle) -> GeometryComponentSnapshot:
        if component.model_id != self.geometry.model_id:
            raise ValueError("component handle belongs to another geometry model")
        # Intersections are relationships between components.  Capturing only
        # the requested component cannot qualify a beam/shell or plate/plate
        # neighbour, so each immutable component job receives the complete
        # structural closure and still publishes only its requested component.
        closure = extract_model_closure(
            self.geometry,
            self.all_components(),
            include_structural_closure=True,
            include_features=False,
        )
        from .model.project import source_publication_required, source_publication_service
        if (self.runtime.settings.to_dict().get('parameters', {}).get('geometry_approximation_error') is not None
                and source_publication_required(self.geometry)):
            qualify_model_closure_source = source_publication_service(
                "anygeometry", "qualify_model_closure_source")
            qualify_model_closure_source(self.geometry, closure)
        return GeometryComponentSnapshot(
            geometry=closure.working_model,
            component=closure.source_to_work[component],
            source_geometry=self.geometry,
            source_component=component,
            model_id=closure.source_model_id,
            revision=closure.source_revision,
            source_to_work=closure.source_to_work,
            work_to_source=closure.work_to_source,
            closure=closure,
        )

    def _components_for_key(
        self, view: GeometryMeshingView, kind: str, identifier: int
    ) -> tuple[EntityHandle, ...]:
        result: set[EntityHandle] = set()
        if kind == "sheet" and identifier in view.sheets:
            result.add(self.geometry.handle(kind, identifier))
        elif kind == "member" and identifier in view.members:
            result.add(self.geometry.handle(kind, identifier))
        elif kind == "face" and identifier in view.faces:
            sheets = view.sheets_for_face(identifier)
            if sheets:
                result.update(self.geometry.handle("sheet", item) for item in sheets)
            else:
                result.add(self.geometry.handle("face", identifier))
        elif kind == "edge" and identifier in view.edges:
            result.update(
                self.geometry.handle("sheet", item)
                for item in view.sheets_using_edge(identifier)
            )
            result.update(
                self.geometry.handle("member", item)
                for item in view.members_using_edge(identifier)
            )
            for face_id in self.geometry.faces_using_edge(identifier):
                if not view.sheets_for_face(face_id):
                    result.add(self.geometry.handle("face", face_id))
        elif kind == "vertex" and identifier in view.vertices:
            for edge_id in self.geometry.edges_using_vertex(identifier):
                result.update(self._components_for_key(view, "edge", int(edge_id)))
        return tuple(sorted(result, key=lambda item: (item.kind, item.id)))

    def resolve_dirty_components(
        self, changes: CoalescedChangeSet
    ) -> DirtyComponentResolution:
        # Generator sizes edited since the last flush reach the runtime here.
        self.refresh_generator_controls()
        view = GeometryMeshingView(self.geometry)
        dirty: set[EntityHandle] = set()
        removed: set[EntityHandle] = set()
        conservative = bool(
            changes.document_settings_changed
            or changes.feature_history_changed
            or changes.ownership_changes
            or changes.member_changes
            or changes.attachment_changes
        )
        for value in changes.changed:
            parsed = _key(value)
            if parsed is None:
                conservative = True
                continue
            kind, identifier = parsed
            resolved = self._components_for_key(view, kind, identifier)
            if resolved:
                dirty.update(resolved)
            elif value not in changes.removed:
                conservative = True
        for value in changes.removed:
            parsed = _key(value)
            if parsed is not None and parsed[0] in {"sheet", "member", "face"}:
                removed.add(self._handle(*parsed))
        if conservative:
            dirty.update(self.all_components())
        dirty.difference_update(removed)
        order = lambda item: (item.kind, item.id)
        return DirtyComponentResolution(
            tuple(sorted(dirty, key=order)), tuple(sorted(removed, key=order))
        )

    def on_geometry_change(self, change_set: Any) -> int:
        report = self.geometry.audit_changed_region(change_set)
        with self._lock:
            self._audit_reports[int(change_set.revision_after)] = report
            if len(self._audit_reports) > 32:
                for revision in sorted(self._audit_reports)[:-32]:
                    del self._audit_reports[revision]
        return self.runtime.on_geometry_change(change_set)

    def _audit_diagnostics(
        self, changes: CoalescedChangeSet | None
    ) -> tuple[str, ...]:
        if changes is None:
            return ("interactive preflight; no ChangeSet audit requested",)
        with self._lock:
            reports = tuple(
                self._audit_reports[revision]
                for revision in sorted(self._audit_reports)
                if changes.revision_before < revision <= changes.revision_after
            )
        if not reports:
            return ("changed-region audit report unavailable; result is not certified",)
        return tuple(
            "changed-region audit "
            f"scope={getattr(getattr(report, 'scope', None), 'value', getattr(report, 'scope', 'unknown'))} "
            f"certifiable={bool(getattr(report, 'certifiable', False))}"
            for report in reports
        )

    def generate_component(self, request: MeshGenerationRequest) -> NativeMeshResult:
        request.cancellation.raise_if_cancelled("hybrid component start")
        base = self.project.native_mesh_settings
        current_generated = {
            item.control_id: item for item in self._generator_controls()
        }
        captured_generated = {
            item.control_id: item for item in request.settings.controls
            if item.control_id.startswith("generator:")
        }
        if base is not None and current_generated != captured_generated:
            # A generator size (or the persisted controls) changed after this
            # request captured its settings.  Publish nothing from the old
            # sizes: re-issue through the runtime (new control generation,
            # every known component remeshed) and drop this attempt.
            affected = self._refresh_generator_controls()
            if request.component in affected:
                # Re-issued by the runtime for this component; never publish
                # from the old size.  A component the change cannot reach
                # carries on: its own result does not depend on it.
                raise NativeMeshCancelled("mesh controls changed; request superseded")
        snapshot = request.snapshot
        if not isinstance(snapshot, GeometryComponentSnapshot):
            raise TypeError("native generator requires GeometryComponentSnapshot")
        snapshot.assert_current()
        geometry = snapshot.geometry
        qualification = None
        from .model.project import (ProjectError, source_publication_required,
                                    source_publication_service)
        if (request.settings.to_dict().get("parameters", {}).get("geometry_approximation_error") is not None
                and source_publication_required(snapshot.source_geometry)):
            qualify_model_closure_source_publication = source_publication_service(
                "anymesher", "qualify_model_closure_source_publication")
            qualification = qualify_model_closure_source_publication(
                snapshot.source_geometry, snapshot.closure,
                cancellation_check=request.cancellation.raise_if_cancelled)
            if qualification is None:
                raise ProjectError(
                    "curved source publication returned no owner qualification; "
                    "curved publication is refused")
        preparation = prepare_structural_connectivity(
            geometry,
            source_model_id=str(snapshot.model_id),
            source_revision=snapshot.revision,
            cancellation_check=request.cancellation.raise_if_cancelled,
        )
        preparation.source_edge_paths = source_edge_paths(snapshot.closure)
        preparation.source_to_working = source_work_mapping(snapshot.closure, edge_paths=preparation.source_edge_paths)
        view = GeometryMeshingView(geometry)
        component = snapshot.component
        face_ids: tuple[int, ...] = ()
        member_ids: tuple[int, ...] | None = ()
        if component.kind == "sheet":
            face_ids = view.faces_for_sheet(component.id)
        elif component.kind == "member":
            member_ids = (component.id,)
        elif component.kind == "face":
            current_faces = tuple(
                geometry.resolve_ref(EntityRef("face", component.id))
            )
            face_ids = tuple(item.id for item in current_faces) or (component.id,)
        else:
            raise ValueError(
                f"unsupported native mesh component kind {component.kind!r}"
            )

        target_size = float(request.settings.target_size)
        parameters: dict[str, Any] = request.settings.to_dict()["parameters"]
        if "native_backend" in parameters:
            raise ValueError(
                "native_backend is a project-level setting, not a mesh parameter"
            )
        # Entity overrides own the component's size (finer or coarser than the
        # global target); overlapping overrides resolve to the finest.  Refine
        # controls keep their established meaning and can still refine below
        # an override.  Controls with no target size never touch the size.
        ordered_controls = sorted(request.controls, key=lambda item: item.control_id)
        override_sizes: list[float] = []
        entity_refinements: list[Refinement] = []
        for control in ordered_controls:
            if (
                control.target_size is None
                or getattr(control, "size_mode", "refine") != "override"
            ):
                continue
            handles = tuple(control.scope.handles)
            if not handles:
                # Global control: every component's own size.
                override_sizes.append(float(control.target_size))
                continue
            for handle in handles:
                if handle == snapshot.source_component:
                    # Scoped to exactly this component.
                    override_sizes.append(float(control.target_size))
                elif handle.kind in ("face", "edge"):
                    # Plate/line scoped: that face or edge keeps its own size
                    # inside this component; the others stay at the base target.
                    working = snapshot.source_to_work.get(handle)
                    if working is not None and working.kind in ("face", "edge"):
                        entity_refinements.append(
                            Refinement(
                                size=float(control.target_size),
                                ref=EntityRef(working.kind, int(working.id)),
                                name=control.control_id,
                                mode="override",
                            )
                        )
                # A sheet/member handle of another component does not apply here.
        if override_sizes:
            target_size = min(override_sizes)
        for control in ordered_controls:
            if (
                control.target_size is not None
                and getattr(control, "size_mode", "refine") != "override"
            ):
                target_size = min(target_size, float(control.target_size))
            control_parameters = dict(control.parameters)
            if "native_backend" in control_parameters:
                raise ValueError(
                    "native_backend is a project-level setting, not a control "
                    f"parameter ({control.control_id!r})"
                )
            parameters.update(control_parameters)
        backend = getattr(request.backend, "value", request.backend)
        strategy = {
            "automatic": "auto",
            "mapped": "mapped",
            "native": "native",
            "quad_first": "native",
            "smart_mapped": "smart_mapped",
        }[str(backend)]
        quad_pipeline = str(backend) in ("quad_first", "smart_mapped")
        from anymesher.recovery import (select_curvilinear_recovery_policy,
                                         generate_automatic_mesh_result)
        recovery_policy, recovery_selection = select_curvilinear_recovery_policy(
            geometry, requested_strategy=backend, order=request.settings.element_order,
            certification_mode=request.certification_mode,
            geometry_approximation_error=parameters.get('geometry_approximation_error'),
            explicit_controls=(any(key.startswith('native_') for key in parameters)
                or 'quad_options' in parameters or 'layout_policy' in parameters
                or any(key.startswith('quad_t3_') for key in parameters)),
            face_ids=face_ids, cancellation_check=request.cancellation.raise_if_cancelled)
        automatic_selection = None
        if (
            recovery_selection is None
            and strategy == "auto"
            and parameters.get("quad_options") is None
            and not self._already_structured(geometry, face_ids)
        ):
            # One shared owner eligibility call (same helper as Project);
            # explicit native/quad-first/SmartMapped/mapped never reach here.
            from .mesh_controls import select_smart_mapped_for_automatic

            automatic_selection = select_smart_mapped_for_automatic(
                geometry,
                face_ids=face_ids,
                target_size=target_size,
                order=request.settings.element_order,
                member_ids=member_ids,
                refinements=entity_refinements,
            )
            if automatic_selection.eligible:
                strategy = "smart_mapped"
                quad_pipeline = True
                parameters["layout_policy"] = "adaptive"
        if quad_pipeline:
            from .quad_first import effective_quad_options

            quad_options = effective_quad_options(
                parameters.get("quad_options")
            )
            native_backend = "python"
        else:
            native_backend = self._native_backend
        # The bound reaches the mesher only on a qualified curved route.  A
        # bound supplied only by a control never passed the gate above, so a
        # curved model refuses here instead of losing it.
        if (parameters.get("geometry_approximation_error") is not None
                and qualification is None
                and source_publication_required(snapshot.source_geometry)):
            raise ProjectError(
                "curved source publication requires owner qualification of "
                "the component closure; curved publication is refused")
        supported = {
            key: value
            for key, value in parameters.items()
            if key in {"recombine", "overlap_policy"}
            or (key == "geometry_approximation_error" and qualification is not None)
        }
        if quad_pipeline:
            supported["quad_options"] = quad_options
            supported["layout_policy"] = parameters.get("layout_policy", "adaptive")
            supported["quad_t3_minimum_angle"] = parameters.get("quad_t3_minimum_angle", 15.0)
            supported["quad_t3_angle_action"] = parameters.get("quad_t3_angle_action", "error")
        strict = request.certification_mode is CertificationMode.STRICT
        generation_options = dict(
            target_size=target_size,
            strategy=strategy,
            face_ids=face_ids,
            member_ids=member_ids,
            refinements=tuple(entity_refinements),
            order=request.settings.element_order,
            certification_mode="strict" if strict else "none",
            cancellation_check=(request.cancellation.geometry_cancellation
                if type(request.cancellation) is NativeMeshingCancellation
                else request.cancellation.raise_if_cancelled),
            native_backend=native_backend,
            structural_preparation={
                "automatic_face_connections": False,
                "automatic_member_connections": False,
                "automatic_member_sheet_connections": False,
                "declare_missing_owners": True,
            },
            mutation_policy="working_copy",
            **supported,
        )
        def generate_checked(action, *args, **kwargs):
            try:
                return action(*args, **kwargs)
            except Exception:
                # Geometry's passive signal returns typed non-success. Keep
                # the application's established cancellation classification.
                request.cancellation.raise_if_cancelled('hybrid component cancellation')
                raise

        if recovery_selection is None:
            generated = generate_checked(generate_hybrid_mesh_result, geometry, **generation_options)
        else:
            from .mesh_controls import StructuredMeshControls
            effective_settings = NativeMeshSettings.from_dict({
                **request.settings.to_dict(), 'parameters': parameters})
            generation_options['_native_surface_options'] = (
                StructuredMeshControls.from_settings(effective_settings).owner_options(
                    preference=parameters.get('structure_preference', 'balanced'),
                    quality_policy={key.removeprefix('mesh_quality_'): value
                                    for key, value in parameters.items()
                                    if key.startswith('mesh_quality_')}))
            # This route already requires source S3 at final publication.
            # Apply the same admission during recovery candidate selection.
            generation_options["qualified_s3"] = True
            generated = generate_checked(generate_automatic_mesh_result,
                geometry, automation=recovery_policy,
                curvilinear_recovery_selection=recovery_selection,
                **generation_options).result
        request.cancellation.raise_if_cancelled("hybrid component completion")
        snapshot.assert_current()
        diagnostics = list(self._audit_diagnostics(request.changes))
        if all(
            hasattr(generated.mesh, name)
            for name in (
                "elements_of_face",
                "elements_of_edge",
                "nodes_of_edge",
                "offset_nodes_of_edge",
                "node_of_vertex",
                "grid_of_face",
                "thickness_of_face",
            )
        ):
            remap_mesh_to_source(generated.mesh, snapshot.closure, edge_paths=preparation.source_edge_paths,
                                 source_geometry=snapshot.source_geometry,
                                 cancellation_check=request.cancellation.raise_if_cancelled,
                                 **({'qualification': qualification} if qualification is not None else {}))
        else:
            # NativeMeshingRuntime deliberately permits lightweight backend
            # payloads for predictors/adapters.  Only a real Mesh carries
            # source associations that can be remapped.
            diagnostics.append(
                "structural association remap skipped for non-Mesh backend payload"
            )
        diagnostics.append(
            "structural preparation: "
            f"{preparation.created_count} declared connection(s), "
            f"working revision {preparation.working_revision}"
        )
        if automatic_selection is not None:
            record = automatic_selection.to_dict()
            diagnostics.append(
                f"automatic selection: {record['selected']} "
                f"({record['reason']}); owner eligibility evaluated once in "
                f"{record['selection_seconds']:.6f} s"
            )
        diagnostics.extend(
            f"face {face_id}: {route}"
            for face_id, route in sorted(generated.strategy_by_face.items())
        )
        return NativeMeshResult(
            generated.mesh,
            valid=True,
            certified=bool(generated.certifiable),
            diagnostics=tuple(diagnostics),
        )

    @staticmethod
    def _already_structured(geometry: Any, face_ids: Iterable[int]) -> bool:
        """Whether the component is already mappable (owner fast path/legacy)."""

        # check_mappable permits holes (ok=True), so holes are excluded here.
        from .mesh_controls import faces_are_untrimmed_mappable

        return faces_are_untrimmed_mappable(geometry, face_ids)

    def request_remesh(
        self, components: EntityHandle | Iterable[EntityHandle] | None = None
    ) -> tuple[EntityHandle, ...]:
        self.refresh_generator_controls()
        selected = self.all_components() if components is None else components
        return self.runtime.request_remesh(selected)

    def close(self, *, wait: bool = True) -> None:
        if self._closed:
            return
        self._closed = True
        self.geometry.remove_change_hook(self.on_geometry_change)
        self.runtime.shutdown(wait=wait)

    def __enter__(self) -> "NativeProjectMeshingSession":
        return self

    def __exit__(self, *_exc_info: Any) -> None:
        self.close()


def create_native_meshing_session(
    project: Any,
    settings: NativeMeshSettings | None = None,
    *,
    max_background_jobs: int = 2,
) -> NativeProjectMeshingSession:
    return NativeProjectMeshingSession(
        project, settings, max_background_jobs=max_background_jobs
    )
