"""Transient fatigue picking and readout graphics on the displayed Results mesh."""

from dataclasses import dataclass
from contextlib import nullcontext

import numpy as np
from any3dview import MeshArrays, PickBinding, ProjectedPrimitive, ProjectedSelectionIndex, SelectionFilter
from anygeometry.entities import EntityRef
from PySide6.QtCore import QEvent, QObject, QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid

from ...application.fatigue import FatigueMeshEdgeRef
from ...selection import MeshEntityRef, owner_to_ref


_SHELL_FILTER = SelectionFilter(kinds=frozenset({"mesh.element"}))
_NO_TRIANGLES = np.empty((0, 3), dtype=np.uint32)


def _record_id(mesh):
    return getattr(mesh, "_anyfem_mesh_record_id", None)


def _same_mesh(first, second):
    if first is second:
        return True
    if first is None or second is None or _record_id(first) != _record_id(second):
        return False
    # Reopened or detached solutions may have separate native objects. Even
    # for legacy untagged meshes, validate geometry and the target ownership
    # maps; repeated node/element numbers alone do not identify a result.
    return (
        first.nodes.keys() == second.nodes.keys()
        and all(np.array_equal(xyz, second.nodes[node]) for node, xyz in first.nodes.items())
        and first.shells == second.shells
        and first.node_of_vertex == second.node_of_vertex
        and first.nodes_of_edge == second.nodes_of_edge
        and first.elements_of_face == second.elements_of_face
    )


@dataclass
class _Display:
    job: str
    document: str
    scene: object
    canvas: object
    mesh: object
    polygons: dict
    nodes: dict

    @classmethod
    def read(cls, app, requested):
        viewport = app.viewport
        job = app.active_job_id
        scene = getattr(viewport, "_scene", None)
        if (app._view_mode != "results" or not job
                or viewport.displayed_result_identity != (app.project.document_id, job)
                or scene is None or scene.result_mesh is None):
            raise ValueError("Display a contour from the fatigue result job first")
        # This resolver checks the submitted document/hash and handles detached
        # result sidecars. A cold load can return a second instance of the mesh.
        submitted = app.retained_result_mesh(job)
        if not _same_mesh(submitted, scene.result_mesh) or not _same_mesh(submitted, requested):
            raise ValueError("The displayed result and fatigue target use different meshes")
        polygons, nodes = {}, {}
        shells = submitted.shells
        for patch in scene.faces:
            if patch.polygon_node_ids is None:
                continue
            for index, (ids, points) in enumerate(zip(patch.polygon_node_ids, patch.polygons)):
                element = next((ref for ref in patch.owners_for_polygon(index)
                                if isinstance(ref, MeshEntityRef) and ref.kind == "element"), None)
                if element is None or element.id not in shells:
                    continue
                if element.mesh_id and element.mesh_id != _record_id(submitted):
                    raise ValueError("A displayed element belongs to a different mesh publication")
                if tuple(ids) != tuple(submitted.corners_of(element.id)):
                    raise ValueError("Displayed shell connectivity differs from the submitted mesh")
                polygons[element.id] = (tuple(ids), np.asarray(points))
                nodes.update(zip(ids, points))
        if not polygons:
            raise ValueError("The displayed result has no visible shell elements")
        return cls(job, app.project.document_id, scene, viewport.canvas, submitted, polygons, nodes)

    def current(self, app):
        viewport = app.viewport
        return (not viewport._closed and app._view_mode == "results" and app.active_job_id == self.job
                and viewport.displayed_result_identity == (self.document, self.job)
                and app.project.document_id == self.document
                and viewport.canvas is self.canvas and viewport._scene is self.scene)


def _lines(canvas, segments, color, *, width=3):
    if not len(segments):
        return
    points = np.asarray(segments, dtype=float).reshape(-1, 3)
    indices = np.arange(len(points), dtype=np.uint32).reshape(-1, 2)
    return canvas.add_mesh_arrays(
        MeshArrays(points, _NO_TRIANGLES, lines=indices), line_color=color, color=color,
        line_width=width, draw_overlay=True, layer=45, pickable=False,
    )


def _markers(canvas, points, color, size, *, outline=""):
    if not len(points):
        return
    points = np.asarray(points, dtype=float)
    indices = np.arange(len(points), dtype=np.uint32)
    return canvas.add_mesh_arrays(
        MeshArrays(points, _NO_TRIANGLES, point_indices=indices), point_color=color,
        point_size=size, point_outline=outline, draw_overlay=True, layer=48,
        pickable=False,
    )


def _unit(vector):
    length = np.linalg.norm(vector)
    if not np.isfinite(length) or length == 0:
        raise ValueError("Cannot display a direction on a degenerate result facet")
    return vector / length


class _Patch:
    """Corresponding native and displayed triangles of the visible readout plate."""

    def __init__(self, display, elements):
        self.rims = []
        native, drawn = [], []
        self.first_triangle = {}
        for eid in elements:
            if eid not in display.polygons:
                raise ValueError(f"Shell element {eid} is absent from the displayed result")
            ids, polygon = display.polygons[eid]
            xyz = np.asarray([display.mesh.nodes[n] for n in ids], dtype=float)
            self.first_triangle[eid] = len(native)
            self.rims.extend(zip(polygon, np.roll(polygon, -1, axis=0)))
            for i in range(1, len(ids) - 1):
                idx = [0, i, i + 1]
                native.append(xyz[idx])
                drawn.append(polygon[idx])
        if not native:
            raise ValueError("Pick at least one visible shell element for the fatigue preview")
        self.native = np.asarray(native)
        self.drawn = np.asarray(drawn)
        self.basis = (self.native[:, 1:] - self.native[:, :1]).transpose(0, 2, 1)
        self.inverse = np.linalg.pinv(self.basis)

    def map(self, point):
        # Barycentric coordinates identify the display triangle containing a
        # native station or probe. The expected shell readout is on the patch.
        delta = np.asarray(point) - self.native[:, 0]
        uv = np.einsum("nij,nj->ni", self.inverse, delta)
        weights = np.column_stack((1 - uv.sum(axis=1), uv))
        within = np.all(weights >= -1e-7, axis=1)
        distance = np.linalg.norm(np.einsum("nij,nj->ni", self.basis, uv) - delta, axis=1)
        if not within.any():
            raise ValueError("The fatigue readout lies outside the displayed shell patch")
        index = int(np.argmin(np.where(within, distance, np.inf)))
        # Preserve any physical out-of-plane offset under pure translations.
        shift = weights[index] @ (self.drawn[index] - self.native[index])
        return np.asarray(point) + shift, index

    def normal(self, i, native_normal):
        nat_a, nat_b = self.basis[i].T
        draw_a, draw_b = self.drawn[i, 1:] - self.drawn[i, 0]
        sign = 1 if np.dot(np.cross(nat_a, nat_b), native_normal) >= 0 else -1
        return sign * _unit(np.cross(draw_a, draw_b))

    def direction(self, i, native_direction):
        draw_basis = (self.drawn[i, 1:] - self.drawn[i, 0]).T
        return _unit(draw_basis @ (self.inverse[i] @ native_direction))


def _arrow(segments, start, end, across):
    delta = end - start
    side = _unit(across) * (.12 * np.linalg.norm(delta))
    neck = end - .26 * delta
    segments.extend(((start, end), (end, neck + side), (end, neck - side)))


class _Highlights(QWidget):
    """Paint frequently changing feedback without invalidating the renderer pick index."""

    def __init__(self, picker):
        super().__init__(picker._display.canvas.event_widget)
        self.picker = picker
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()

    def paintEvent(self, event):
        picker = self.picker
        display = picker._display
        if display is None or not display.current(picker.app):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        ratio = self.devicePixelRatioF()
        clip = display.canvas._effective_section
        if isinstance(picker._preview_group, _SoftwarePreview):
            picker._preview_group.paint(painter, ratio)
        for refs, color, width in ((picker._selected, "#ffad25", 5),
                                   ((picker._hover_ref,), "#11eeff", 2.5)):
            for ref in refs:
                nodes = picker._nodes_for(ref)
                if not nodes:
                    continue
                world = [display.nodes[n] for n in nodes]
                if len(world) == 1:
                    if clip is not None and not clip.contains(world[0]):
                        continue
                    projected = display.canvas.project_points(world)
                else:
                    pairs = zip(world, world[1:])
                    if clip is not None:
                        pairs = (line for a, b in pairs if (line := clip.clip_segment(a, b)) is not None)
                    projected = display.canvas.project_points(p for line in pairs for p in line)
                points = [None if p is None else QPointF(p[0] / ratio, p[1] / ratio) for p in projected]
                for ink, size in (("#182431", width + 2), (color, width)):
                    painter.setPen(QPen(QColor(ink), size, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                    if len(points) == 1 and points[0] is not None:
                        painter.drawEllipse(points[0], width + 3, width + 3)
                    else:
                        for start, end in zip(points[::2], points[1::2]):
                            if start is not None and end is not None:
                                painter.drawLine(start, end)
        painter.end()


@dataclass
class _PreviewMark:
    mesh: MeshArrays
    appearance: dict
    removed: bool = False

    def remove(self):
        self.removed = True


class _SoftwarePreview:
    """Transient marks painted over the cached software contour image."""

    def __init__(self, canvas):
        self.viewer = canvas
        self.handles = []
        self.removed = False
        self._projection_key = None
        self._image = None

    def add_mesh_arrays(self, mesh, **appearance):
        mark = _PreviewMark(mesh, appearance)
        self.handles.append(mark)
        return mark

    def remove(self):
        self.removed = True
        for mark in self.handles:
            mark.remove()
        self.handles.clear()

    def paint(self, painter, ratio):
        key = _camera_key(self.viewer), ratio
        if self._projection_key != key:
            image = QPixmap(*self.viewer.viewport_size)
            image.setDevicePixelRatio(ratio)
            image.fill(Qt.transparent)
            marks = QPainter(image)
            try:
                marks.setRenderHint(QPainter.Antialiasing)
                self._draw(marks, ratio)
            finally:
                marks.end()
            self._projection_key = key
            self._image = image
        painter.drawPixmap(0, 0, self._image)

    def _draw(self, painter, ratio):
        canvas = self.viewer
        clip = canvas._effective_section
        for mark in self.handles:
            if mark.removed:
                continue
            mesh, style = mark.mesh, mark.appearance
            if mesh.lines is not None and len(mesh.lines):
                painter.setPen(QPen(QColor(style["line_color"]), style["line_width"]))
                painter.setBrush(Qt.NoBrush)
                segments = mesh.positions[mesh.lines]
                if clip is not None:
                    segments = [line for a, b in segments if (line := clip.clip_segment(a, b)) is not None]
                projected = canvas.project_points(p for segment in segments for p in segment)
                for a, b in zip(projected[::2], projected[1::2]):
                    if a is not None and b is not None:
                        painter.drawLine(QPointF(a[0] / ratio, a[1] / ratio), QPointF(b[0] / ratio, b[1] / ratio))
            else:
                outline = style["point_outline"]
                painter.setPen(QPen(QColor(outline), 1) if outline else Qt.NoPen)
                painter.setBrush(QColor(style["point_color"]))
                radius = style["point_size"] / 2
                points = mesh.positions[mesh.point_indices]
                if clip is not None:
                    points = [p for p in points if clip.contains(p)]
                for p in canvas.project_points(points):
                    if p is not None:
                        painter.drawEllipse(QPointF(p[0] / ratio, p[1] / ratio), radius, radius)


def _camera_key(canvas):
    camera = canvas.camera
    return (tuple(canvas.viewport_size), camera.fov, camera.near, camera.far,
            *((p.x, p.y, p.z) for p in (camera.position, camera.target, camera.world_up)),
            repr(getattr(canvas, "section_plane", None)), repr(getattr(canvas, "section_box", None)))


class FatiguePicking(QObject):
    """A scoped Results selection tool with optional persistent, additive capture."""

    def __init__(self, app, on_pick, on_cancel, *, on_selection=None, on_miss=None):
        super().__init__(app.viewport.widget)
        self.app = app
        self.on_pick = on_pick
        self.on_cancel = on_cancel
        self.on_selection = on_selection
        self.on_miss = on_miss
        self._display = None
        self._kind = None
        self._software_query = None
        self._preview_group = None
        self._preview_inputs = None
        self._highlights = None
        self._hover_ref = None
        self._selected = ()
        self._targets = []
        self._camera = None
        self._index = None
        self._tracking = False
        self._reference = False
        self._pressed = False
        self._command_window = None
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._refresh)

    @property
    def active(self):
        return self._kind is not None

    def _bind(self, mesh):
        self._check_display()
        if self._display is None or not _same_mesh(mesh, self._display.mesh):
            self.clear()
            self._display = _Display.read(self.app, mesh)
        self._timer.start()
        return self._display

    def _check_display(self):
        if self._display is not None and not self._display.current(self.app):
            self.clear()

    def _refresh(self):
        self._check_display()
        if self._display is None or self.app.viewport._interaction_scene_active:
            return
        # The software camera can temporarily replace the full scene. Reinstall
        # only our vanished annotations after its original owner map is restored.
        def missing(group):
            return group is not None and any(handle.removed for handle in group.handles)
        if missing(self._preview_group) and self._preview_inputs is not None:
            elements, stations, governing = self._preview_inputs
            self.preview(self._display.mesh, elements, stations, governing_index=governing)
        self._update_camera()

    def _update_camera(self):
        key = _camera_key(self._display.canvas)
        if key != self._camera:
            self._camera = key
            self._index = None
            self._set_hover(None)
            if self._highlights is not None:
                self._highlights.setGeometry(self._display.canvas.event_widget.rect())
                self._highlights.update()

    def _nodes_for(self, ref):
        if ref is None:
            return ()
        display = self._display
        mesh = display.mesh
        if getattr(ref, "mesh_id", None) not in (None, _record_id(mesh)):
            return ()
        if ref.kind == "element":
            nodes = display.polygons.get(ref.id, ((), ()))[0]
            return nodes + nodes[:1]
        if ref.kind == "mesh_edge":
            nodes = ref.node_ids
        elif ref.kind == "edge":
            nodes = tuple(n for n in mesh.nodes_of_edge.get(ref.id, ()) if n in display.nodes)
        elif ref.kind == "vertex":
            nodes = (mesh.node_of_vertex.get(ref.id),)
        elif ref.kind == "node":
            nodes = (ref.id,)
        else:
            return ()
        return nodes if all(n in display.nodes for n in nodes) else ()

    def set_selected(self, refs):
        """Synchronize persistent highlights on the currently bound result."""
        self._check_display()
        refs = tuple(dict.fromkeys(refs))
        self._selected = tuple(ref for ref in refs if self._display and self._nodes_for(ref))
        self._update_highlights()
        self._release_if_idle()

    def _set_hover(self, ref):
        if ref != self._hover_ref:
            self._hover_ref = ref
            self._update_highlights()

    def _update_highlights(self):
        if self._display is not None and (self._selected or self._hover_ref is not None
                                          or isinstance(self._preview_group, _SoftwarePreview)):
            if self._highlights is None:
                self._highlights = _Highlights(self)
            self._highlights.update()
        elif self._highlights is not None:
            if isValid(self._highlights):
                self._highlights.hide()
                self._highlights.deleteLater()
            self._highlights = None

    def start(self, kind, mesh, *, element_ids=(), selected=(), through_node=None):
        if kind not in ("element", "point", "line"):
            raise ValueError(f"Unknown fatigue pick kind {kind!r}")
        self.cancel()
        display = self._bind(mesh)
        self._kind = kind
        self._reference = through_node is not None
        self._command_window = QApplication.activeWindow()
        surface = display.canvas.event_widget
        self._tracking = surface.hasMouseTracking()
        try:
            self._targets = self._make_targets(display, kind, element_ids, through_node)
            self._camera = None
            self._update_camera()
            self.set_selected(selected)
            surface.setMouseTracking(True)
            QApplication.instance().installEventFilter(self)
        except Exception:
            self.cancel()
            raise

    def _make_targets(self, display, kind, element_ids, through_node):
        if kind == "element":
            return []
        mesh = display.mesh
        allowed = set(element_ids) if element_ids else set(display.polygons)
        shells = mesh.shells
        members = {n for eid in allowed if eid in shells for n in shells[eid]}
        # The area of interest is defined by the submitted receiving plate. Only
        # displayed corner chords (including those of Q8s) can be pointed at.
        corners = {}
        sides = {}
        for eid in allowed.intersection(display.polygons):
            ids = display.polygons[eid][0]
            for a, b in zip(ids, ids[1:] + ids[:1]):
                corners.setdefault(a, set()).add(eid)
                sides.setdefault(tuple(sorted((a, b))), set()).add(eid)
        record = _record_id(mesh)
        if kind == "point":
            vertices = {}
            for vertex, node in mesh.node_of_vertex.items():
                vertices[node] = vertex if node not in vertices else None
            return [(EntityRef("vertex", vertices[n]) if vertices.get(n) is not None
                     else MeshEntityRef("node", n, record), (n,), incident)
                    for n, incident in corners.items()]

        if through_node is None:
            required_nodes = set()
        elif isinstance(through_node, (int, np.integer)):
            required_nodes = {int(through_node)}
        else:
            required_nodes = set(through_node)
        targets = []
        represented = set()
        # Keep entire CAD lines only. A chain leaving this plate or crossing a
        # hidden corner cannot be shortened and returned as a different weld.
        native_corners = {n for eid in allowed if eid in shells for n in mesh.corners_of(eid)}
        for edge, chain in mesh.nodes_of_edge.items():
            if not chain or not members.issuperset(chain):
                continue
            chain = tuple(n for n in chain if n in native_corners)
            pairs = [tuple(sorted(pair)) for pair in zip(chain, chain[1:])]
            if not pairs or any(pair not in sides for pair in pairs):
                continue
            represented.update(pairs)
            if required_nodes.issubset(chain):
                ref = EntityRef("edge", edge)
                targets.extend((ref, pair, sides[pair]) for pair in pairs)
        targets.extend((FatigueMeshEdgeRef(pair, record), pair, incident)
                       for pair, incident in sides.items()
                       if pair not in represented and required_nodes.issubset(pair))
        return targets

    def _project_targets(self):
        display = self._display
        nodes = dict.fromkeys(n for _, pair, _ in self._targets for n in pair)
        projected = dict(zip(nodes, display.canvas.project_points(display.nodes[n] for n in nodes)))
        primitives = []
        clip = display.canvas._effective_section
        self._projected = {}
        for i, (_, pair, _) in enumerate(self._targets):
            points = [projected[n] for n in pair]
            if clip is not None:
                if len(pair) == 1:
                    if not clip.contains(display.nodes[pair[0]]):
                        continue
                else:
                    line = clip.clip_segment(*(display.nodes[n] for n in pair))
                    if line is None:
                        continue
                    points = display.canvas.project_points(line)
            if all(p is not None for p in points):
                self._projected[pair] = points
                primitives.append(ProjectedPrimitive(
                    i, "point" if len(pair) == 1 else "segment",
                    tuple((p[0], p[1]) for p in points), tuple(p[2] for p in points),
                    PickBinding.one(str(i))))
        self._index = ProjectedSelectionIndex(primitives, *display.canvas.viewport_size)

    def _front_shell(self, x, y):
        canvas = self._display.canvas
        if canvas.backend_name == "software" and self._software_query is None:
            # This CPU service culls screen regions before applying ANY3dView's
            # canonical retained clipping, visibility and owner resolution.
            # Older viewer packages only expose the full-scene point query.
            try:
                from any3dview.webgpu.picking import PointQuery
            except ImportError:
                self._software_query = False
            else:
                self._software_query = PointQuery(canvas)
        if self._software_query:
            hits = self._software_query.query(round(x), round(y), _SHELL_FILTER, 0)
        else:
            hits = canvas.query_point(round(x), round(y), selection_filter=_SHELL_FILTER, radius=0)
        for hit in hits:
            ref = owner_to_ref(hit.owner)
            if hit.visible and isinstance(ref, MeshEntityRef) and ref.id in self._display.polygons:
                if ref.mesh_id in (None, _record_id(self._display.mesh)):
                    return ref.id
        return None

    def _exposed(self, pair, incident, x, y):
        points = self._projected[pair]
        a = points[0]
        px, py = a[:2]
        if len(pair) == 2:
            b = points[1]
            dx, dy = b[0] - px, b[1] - py
            length2 = dx * dx + dy * dy
            if length2:
                t = max(0., min(1., ((x - px) * dx + (y - py) * dy) / length2))
                px, py = px + t * dx, py + t * dy
        front = self._front_shell(px, py)
        if front in incident:
            return True
        if front is not None:
            # At a welded junction either visible sheet can win the depth tie.
            # Require the very same submitted node or side on the front shell.
            ids = self._display.polygons[front][0]
            if len(pair) == 1:
                return pair[0] in ids
            return any((a, b) == pair or (b, a) == pair for a, b in zip(ids, ids[1:] + ids[:1]))
        # The projected boundary can round one pixel outside its face.
        for eid in incident:
            center = self._display.canvas.project_point(self._display.polygons[eid][1].mean(axis=0))
            if center is not None:
                dx, dy = center[0] - px, center[1] - py
                length = max(1., np.hypot(dx, dy))
                if self._front_shell(px + 2 * dx / length, py + 2 * dy / length) in incident:
                    return True
        return False

    def _pick(self, position, surface):
        ratio = surface.devicePixelRatioF()
        x, y = position.x() * ratio, position.y() * ratio
        if self._kind == "element":
            eid = self._front_shell(x, y)
            return MeshEntityRef("element", eid, _record_id(self._display.mesh)) if eid is not None else None
        self._update_camera()
        if self._index is None:
            self._project_targets()
        radius = (14 if self._kind == "point" else 11) * ratio
        hits = self._index.point_hits(x, y, SelectionFilter(), radius=radius)
        for hit in sorted(hits, key=lambda h: (h.screen_distance, h.depth, h.primitive)):
            ref, pair, incident = self._targets[hit.primitive]
            if self._exposed(pair, incident, x, y):
                return ref
        return None

    def eventFilter(self, target, event):
        if self._kind is None:
            return False
        if not self._display.current(self.app):
            viewport = self.app.viewport
            consume = (not viewport._closed and target is viewport.canvas.event_widget
                       and event.type() in {QEvent.MouseButtonPress, QEvent.MouseButtonRelease})
            self.clear()
            return consume
        surface = self.app.viewport.canvas.event_widget
        if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            if target is surface or (hasattr(target, "window")
                                     and target.window() in (self.app, self._command_window)):
                self.cancel()
                self.on_cancel()
                return True
        if target is not surface:
            return False
        if event.type() in (QEvent.Leave, QEvent.Wheel, QEvent.Hide):
            self._set_hover(None)
        if event.type() == QEvent.MouseMove:
            if event.buttons() or self.app.viewport._interaction_scene_active:
                self._set_hover(None)
                return False
            self._set_hover(self._pick(event.position(), surface))
            return True
        if (event.type() not in {QEvent.MouseButtonPress, QEvent.MouseButtonDblClick,
                                QEvent.MouseButtonRelease} or event.button() != Qt.LeftButton):
            return False
        if event.type() != QEvent.MouseButtonRelease:
            self.app.viewport._cancel_interaction_restore()
            self.app.viewport._restore_full_scene()
            self._refresh()
            self._pressed = event.type() == QEvent.MouseButtonPress
            return True
        if self._pressed:
            self._pressed = False
            try:
                ref = self._pick(event.position(), surface)
            except (ValueError, RuntimeError) as error:
                self._miss(str(error))
                return True
            if ref is None:
                prompt = {"element": "Choose a visible shell element on the result.",
                          "point": "Move closer to a visible corner on the receiving plate.",
                          "line": "Choose a visible line or shell side on the receiving plate."}
                self._miss("Choose a visible line on the receiving plate through all selected toe points."
                           if self._reference else prompt[self._kind])
            elif self.on_selection is not None:
                additive = bool(event.modifiers() & Qt.ShiftModifier)
                self.on_selection(ref, additive)
            else:
                self.cancel()
                self.on_pick(ref)
        return True

    def _miss(self, message):
        if self.on_miss is not None:
            self.on_miss(message)
        else:
            self.app.set_status(message)

    def cancel(self):
        """Cancel the click command; the retained readout preview stays visible."""
        if self._kind is not None:
            QApplication.instance().removeEventFilter(self)
            surface = self._display.canvas.event_widget
            if isValid(surface):
                surface.setMouseTracking(self._tracking)
        self._kind = None
        self._command_window = None
        self._pressed = False
        self._set_hover(None)
        self._targets = []
        self._index = None
        self._projected = {}
        self._release_if_idle()

    def _remove_group(self, name):
        group = getattr(self, name)
        setattr(self, name, None)
        if group is not None and not group.removed:
            group.remove()
            if isinstance(group, _SoftwarePreview):
                self._update_highlights()

    def _release_if_idle(self):
        if self._kind is None and self._preview_group is None and not self._selected:
            self._timer.stop()
            self._display = None
            if self._software_query:
                self._software_query.close()
            self._software_query = None

    def preview(self, mesh, element_ids, stations, *, governing_index=None):
        """Draw the shell patch, station normals, readout directions and probe positions."""
        display = self._bind(mesh)
        elements, stations = tuple(element_ids), tuple(stations)
        self._preview_inputs = None
        self._remove_group("_preview_group")
        try:
            if governing_index is not None and (
                not isinstance(governing_index, (int, np.integer))
                or not 0 <= governing_index < len(stations)
            ):
                raise ValueError("The governing station index is outside the fatigue preview")
            patch = _Patch(display, elements)
            origins, probes, normals, directions = [], [], [], []
            direct = len(stations) == len(elements) and all(s.direction is None for s in stations)
            for j, station in enumerate(stations):
                if direct:
                    # Direct stations are the selected element centroids, in
                    # selection order. This also handles warped quadrilaterals.
                    eid = elements[j]
                    origin = display.polygons[eid][1].mean(axis=0)
                    i = patch.first_triangle[eid]
                else:
                    origin, i = patch.map(station.xyz)
                origins.append(origin)
                triangle = patch.drawn[i]
                size = .25 * np.linalg.norm(triangle[1] - triangle[0])
                normal = patch.normal(i, station.normal)
                across = _unit(triangle[1] - triangle[0])
                _arrow(normals, origin, origin + size * normal, across)
                if station.direction is not None:
                    direction = patch.direction(i, station.direction)
                    positions = [patch.map(probe)[0] for probe in station.probes]
                    probes.extend(positions)
                    length = max([size] + [np.linalg.norm(p - origin) for p in positions])
                    _arrow(directions, origin, origin + length * direction, np.cross(normal, direction))
            canvas = display.canvas
            software = canvas.backend_name == "software"
            self._preview_group = _SoftwarePreview(canvas) if software else canvas.annotation_group()
            target = self._preview_group if software else canvas
            with nullcontext() if software else self._preview_group:
                _lines(target, patch.rims, "#f0a202", width=2)
                _lines(target, normals, "#0072b2")
                _lines(target, directions, "#d55e00")
                ordinary = [origin for j, origin in enumerate(origins) if j != governing_index]
                _markers(target, ordinary, "#f0a202", 9)
                _markers(target, probes, "#cc79a7", 8)
                if governing_index is not None:
                    _markers(target, [origins[governing_index]], "#97003c", 16, outline="#ffffff")
            self._preview_inputs = elements, stations, governing_index
            if software:
                self._update_highlights()
            else:
                canvas.redraw()
        except Exception:
            self._remove_group("_preview_group")
            self._release_if_idle()
            raise

    def clear_preview(self):
        self._preview_inputs = None
        self._remove_group("_preview_group")
        self._release_if_idle()

    def clear(self):
        self._selected = ()
        self._set_hover(None)
        self._update_highlights()
        self.clear_preview()
        self.cancel()
