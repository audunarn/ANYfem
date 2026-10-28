"""Mean-wet source and target preparation for the floating response (geometry-owner clipping + conservative overlap regions).

The posed hull (world = Q0 x_body + t) is clipped at z_world=0 by ``anygeometry.floating_overlap``.  The BEM source is an
independent, budgeted plate panelisation (``anymesher.floating_source_mesh``) that does NOT depend on the structural
mesh; the TARGET manifest is the structural wet cells.  Mapping keeps the actual overlap POLYGONS (source panel x target
cell); ``map_pressure_nodal`` integrates the element's own pressure virtual work over each physical region through the
solver owner (``anysolver.floating_pressure_regions``), so dry portions never load and force, moment and native S3 rotational
work follow the actual shape functions.  No averaging, no nearest panel, no global rescale.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

import numpy as np

from anygeometry import floating_overlap as ov


class FloatingSourceError(ValueError):
    """Source/target preparation refused (never repaired silently)."""


@dataclass(frozen=True)
class MeanPose:
    Q0: np.ndarray        # body -> world
    t: np.ndarray         # world position of the body origin

    def world(self, points):
        return np.asarray(points, dtype=float) @ self.Q0.T + self.t

    def body(self, points):
        return (np.asarray(points, dtype=float) - self.t) @ self.Q0


@dataclass(frozen=True)
class TargetCells:
    element_ids: tuple
    polygons_wet: tuple          # world, oriented into the fluid, clipped at z=0
    polygons_wet_body: tuple     # the same regions in the FE (body) frame
    area_wet: np.ndarray
    area_element: np.ndarray
    centroid_wet: np.ndarray
    sign: np.ndarray             # solver normal-signed pressure = sign * pressure magnitude
    label: tuple
    plate_polygons: tuple = ()   # (label, face_id, world polygon oriented into the fluid, wet clipped, element ids)

    def digest(self):
        h = hashlib.sha256(json.dumps([self.element_ids, list(self.label)]).encode())
        for a in (self.area_wet, self.area_element, self.centroid_wet, self.sign):
            h.update(np.ascontiguousarray(a).tobytes())
        return h.hexdigest()


@dataclass(frozen=True)
class SourceManifest:
    vertices: np.ndarray         # (nv,3) world, z<=0
    faces: np.ndarray            # (n,4) degenerate quads (a,b,c,c), outward
    surface_ids: tuple
    triangles: tuple             # (n) arrays (3,3)
    areas: np.ndarray
    centers: np.ndarray
    normals: np.ndarray
    provenance: dict

    def digest(self):
        h = hashlib.sha256(json.dumps(self.provenance, sort_keys=True, default=str).encode())
        for a in (self.vertices, self.faces):
            h.update(np.ascontiguousarray(a).tobytes())
        h.update(json.dumps(list(self.surface_ids)).encode())
        return h.hexdigest()

    @property
    def mode(self):
        return self.provenance.get('generator', 'source')


def _snap_waterline(poly):
    p = np.array(poly, dtype=float)
    scale = max(1.0, float(np.linalg.norm(p - p.mean(axis=0), axis=1).max()))
    p[np.abs(p[:, 2]) < 1e-12 * scale, 2] = 0.0
    return p


def prepare_target_cells(project, mesh, pose: MeanPose) -> TargetCells:
    """Structural wet element cells in world axes, clipped at z=0 (partial cells keep only the wet polygon)."""
    from anyloads.fluid import fluid_sign
    from ..solve.build import _wet_elements
    owner, by_face = _wet_elements(project, mesh, project.wet_surfaces)
    ids, polys, bodies, aw, ae, cw, sg, lab = [], [], [], [], [], [], [], []
    for eid in sorted(owner):
        surface = owner[eid]
        corners = np.array([mesh.nodes[n] for n in mesh.corners_of(eid)], dtype=float)
        if len(corners) == 3:
            nb = np.cross(corners[1] - corners[0], corners[2] - corners[0])
        else:
            nb = np.cross(corners[2] - corners[0], corners[3] - corners[1])
        nb = nb / np.linalg.norm(nb)
        s = fluid_sign(surface.fluid_side, surface.interior_point, corners.mean(axis=0), nb, label=surface.name)
        world = pose.world(corners)
        if s > 0:                                 # fluid on the negative-normal side: reverse so the normal points into fluid
            world = world[::-1]
        try:
            _, _, a_e = ov.polygon_plane(world)
        except Exception as error:
            raise FloatingSourceError(f'wet element {eid} is not a planar polygon: {error}') from error
        wet = ov.wet_polygon(world, 0.0)
        if wet is None:
            continue
        wet = _snap_waterline(wet)
        _, c_w, a_w = ov.polygon_plane(wet)
        ids.append(int(eid)); polys.append(wet); bodies.append(pose.body(wet)); aw.append(a_w); ae.append(a_e)
        cw.append(c_w); sg.append(float(s)); lab.append(surface.name)
    if not ids:
        raise FloatingSourceError('no wet structural elements at the mean pose (hull does not intersect z=0 water)')
    cell = {e: k for k, e in enumerate(ids)}
    plates = []
    for face_id, elements in sorted(by_face.items()):
        wet_elements = [e for e in elements if e in cell]
        if wet_elements:
            plates.append((lab[cell[wet_elements[0]]], int(face_id), tuple(wet_elements), tuple(int(e) for e in elements)))
    return TargetCells(tuple(ids), tuple(polys), tuple(bodies), np.array(aw), np.array(ae), np.array(cw), np.array(sg),
                       tuple(lab), tuple(plates))


def _convex(poly) -> bool:
    n, _, _ = ov.polygon_plane(poly)
    p = np.asarray(poly)
    for i in range(len(p)):
        if np.cross(p[(i + 1) % len(p)] - p[i], p[(i + 2) % len(p)] - p[(i + 1) % len(p)]) @ n < -1e-12:
            return False
    return True


def _bem_arrays(tris, labels, provenance):
    index, verts, faces = {}, [], []
    for t in tris:
        f = []
        for p in t:
            key = tuple(np.round(p / 1e-9).astype(np.int64))
            if key not in index:
                index[key] = len(verts); verts.append(p)
            f.append(index[key])
        faces.append([f[0], f[1], f[2], f[2]])
    v = np.array(verts); f = np.array(faces, dtype=np.int64)
    edges = {}
    for face in f:
        for a, b in ((face[0], face[1]), (face[1], face[2]), (face[2], face[0])):
            edges.setdefault(tuple(sorted((int(a), int(b)))), []).append((int(a), int(b)))
    for key, uses in edges.items():
        if len(uses) == 1:
            if not np.allclose(v[list(key), 2], 0.0, atol=1e-9, rtol=0.0):
                raise FloatingSourceError('source panels have an open or non-conforming seam below the waterline '
                                          '(plate junction subdivided differently); adjust panel_size')
        elif len(uses) != 2 or uses[0] != uses[1][::-1]:
            raise FloatingSourceError('source panels are non-manifold or inconsistently oriented')
    areas = np.array([ov.polygon_area(t) for t in tris])
    cen = np.array([t.mean(axis=0) for t in tris])
    nrm = np.array([ov.polygon_plane(t)[0] for t in tris])
    if float(np.sum(np.einsum('ij,ij->i', cen, nrm) * areas) / 3.0) <= 0.0:
        raise FloatingSourceError('source hull winding must enclose positive volume below z=0')
    return SourceManifest(v, f, tuple(labels), tuple(tris), areas, cen, nrm, provenance)


def prepare_source(project, mesh, pose: MeanPose, target: TargetCells, *, panel_size: float = 0.0,
                   max_panels: int = 600) -> SourceManifest:
    """Budgeted wet-hull BEM source from the project's GEOMETRY plates (never from the structural mesh)."""
    from anymesher.floating_source_mesh import build_wet_source_mesh, SourceMeshError
    if not 4 <= int(max_panels) <= 600:
        raise FloatingSourceError(f'max_panels={max_panels} is outside 4..600 (native BEM body limit)')
    cell = {int(e): k for k, e in enumerate(target.element_ids)}
    geom = project.geometry
    plates = []
    for label, face_id, wet_elements, all_elements in target.plate_polygons:
        corners = np.array([geom.vertex_position(v) for v in geom.face_corner_vertices(face_id)], dtype=float)
        poly = pose.world(corners)
        n_ref = ov.polygon_plane(target.polygons_wet[cell[wet_elements[0]]])[0]
        try:
            if ov.polygon_plane(poly)[0] @ n_ref < 0:
                poly = poly[::-1]
            plate_area = ov.polygon_area(poly)
            element_area = sum(ov.polygon_area(pose.world(np.array([mesh.nodes[n] for n in mesh.corners_of(e)])))
                               for e in all_elements)
            usable = _convex(poly) and abs(plate_area - element_area) <= 1e-6 * plate_area
        except Exception:
            usable = False
        if not usable:
            raise FloatingSourceError(f'geometry face {face_id} is not a planar convex plate exactly covered by its '
                                      'elements; the plate source cannot panelise it conformingly')
        wet = ov.wet_polygon(poly, 0.0)
        if wet is not None:
            plates.append(dict(label=label, polygon=_snap_waterline(wet)))
    try:
        built = build_wet_source_mesh(plates, size=panel_size, max_panels=max_panels, require_closed=True)   # complete hull
    except SourceMeshError as error:
        raise FloatingSourceError(str(error)) from error
    return _bem_arrays([_snap_waterline(t) for t in built['triangles']], built['labels'], built['provenance'])


@dataclass(frozen=True)
class OverlapMap:
    """Actual overlap regions: per target element index, physical triangles (BODY frame) and their source panel index."""
    regions: dict          # target index -> (triangles (k,3,3) body frame, source index (k,))
    coverage: dict


def build_overlap_map(source: SourceManifest, target: TargetCells, pose: MeanPose, *,
                      coverage_tolerance: float = 1e-6) -> OverlapMap:
    found = ov.overlap_regions(source.triangles, target.polygons_wet)
    rows = np.array([f[0] for f in found], dtype=np.int64); cols = np.array([f[1] for f in found], dtype=np.int64)
    vals = np.array([ov.polygon_area(f[2]) for f in found])
    rep = ov.coverage_report(source.areas, target.area_wet, rows, cols, vals)
    if rep['source_uncovered_rel'] > coverage_tolerance or rep['target_unmatched_rel'] > coverage_tolerance:
        raise FloatingSourceError(f'source/target overlap does not cover the wet hull: {rep}')
    regions = {}
    for i, j, poly in found:
        tris = [pose.body(t) for t in ov.fan_triangles(poly)]
        entry = regions.setdefault(j, ([], []))
        entry[0].extend(tris); entry[1].extend([i] * len(tris))
    return OverlapMap({j: (np.array(t), np.array(s, dtype=np.int64)) for j, (t, s) in regions.items()}, rep)


def hull_geometry_key(project, extra_face_ids=()):
    """Identity of the hull reference geometry (wet plates + closing faces): geometry vertex ids and exact positions."""
    geom = project.geometry
    wet = {int(s.ref.id): s for s in project.wet_surfaces}
    ids = sorted(set(wet) | {int(i) for i in extra_face_ids})

    def role(f):
        if f not in wet:
            return ['closing']
        s = wet[f]       # wet surface: its fluid side and interior reference decide the load direction, so they are bound too
        return ['wet', str(s.fluid_side),
                None if s.interior_point is None else [float(x).hex() for x in np.asarray(s.interior_point, dtype=float)]]

    record = [[f, role(f), [[int(v), *[float(x).hex() for x in geom.vertex_position(v)]] for v in geom.face_corner_vertices(f)]]
              for f in ids]
    return hashlib.sha256(json.dumps(record).encode()).hexdigest()


def _scatter(nodal, node_row, rv):
    for nid, vec in zip(rv.node_ids, rv.vectors):
        nodal[node_row[nid]] += vec


def map_pressure_nodal(fe_model, node_ids, target: TargetCells, omap: OverlapMap, source_pressure, kernel_proof=None):
    """Mapped source-panel pressure as consistent nodal (n,6) vectors: element pressure work over each overlap region.
    ``kernel_proof``: caller-owned (job-lifetime) dict for the owner element-kernel equivalence proof (see ANYsolver)."""
    from anysolver.floating_pressure_regions import region_nodal_vectors
    p = np.asarray(source_pressure)
    row = {int(n): i for i, n in enumerate(node_ids)}
    nodal = np.zeros((len(node_ids), 6), dtype=p.dtype)
    for j, (tris, src) in omap.regions.items():
        element = fe_model.mesh.elements[int(target.element_ids[j])]
        rv = region_nodal_vectors(element, fe_model.mesh, tris, lambda pts, ti, src=src: p[src][ti], sign=float(target.sign[j]),
                                  kernel_proof=kernel_proof)
        _scatter(nodal, row, rv)
    return nodal


def wet_region_nodal(fe_model, node_ids, target: TargetCells, pose: MeanPose, magnitude_world, kernel_proof=None):
    """Direct field on the mean WET region of each element: ``magnitude_world(points_world) -> (m,)`` (affine hydrostatics)."""
    from anysolver.floating_pressure_regions import region_nodal_vectors
    row = {int(n): i for i, n in enumerate(node_ids)}
    probe = np.asarray(magnitude_world(np.zeros((1, 3))))
    nodal = np.zeros((len(node_ids), 6), dtype=probe.dtype)
    for j, body_poly in enumerate(target.polygons_wet_body):
        tris = np.array([body_poly[[0, k, k + 1]] for k in range(1, len(body_poly) - 1)])
        element = fe_model.mesh.elements[int(target.element_ids[j])]
        rv = region_nodal_vectors(element, fe_model.mesh, tris, lambda pts, ti: magnitude_world(pose.world(pts)),
                                  sign=float(target.sign[j]), kernel_proof=kernel_proof)
        _scatter(nodal, row, rv)
    return nodal


def source_triangles_closed(source: SourceManifest):
    return source.vertices, source.faces[:, :3]
