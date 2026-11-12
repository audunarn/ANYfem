"""A3 finite-width membrane strip and S0's recovered gross-section stress factor."""

import numpy as np

from anyfem import Project, steel, support
from anyfem.mesh_jobs import MeshSettings
from anyfem.native_meshing import NativeMeshSettings
from anygeometry.curves import Arc, Straight
from anygeometry.surfaces import Plane
from anyfesolver.results import recover_nodal_stresses

WIDTH = 0.5
DIAMETER = 0.1
RADIUS = DIAMETER / 2
THICKNESS = 0.01
NOMINAL_STRESS = 100e6
HOWLAND_KT = 3.14  # Howland, Table XII, d/W = 0.2 (gross section).


def build_ring(half):
    """Author four analytic sectors starting their face loops on the outer square."""
    project = Project("Finite-width holed strip")
    project.add_material(steel("S355", THICKNESS))
    project.add_plate_section("plate", thickness=THICKNESS, material="S355")
    geom = project.geometry
    plane = Plane(origin=(0., 0., 0.), u_vector=(1., 0., 0.), v_vector=(0., 1., 0.))
    corners = [(-half, -half), (half, -half), (half, half), (-half, half)]
    outside = [geom.add_point(x, y, 0.) for x, y in corners]
    inside = [geom.add_point(RADIUS * x / np.hypot(x, y), RADIUS * y / np.hypot(x, y), 0.)
              for x, y in corners]
    via = [geom.add_point(x, y, 0.) for x, y in
           ((0., -RADIUS), (RADIUS, 0.), (0., RADIUS), (-RADIUS, 0.))]
    arcs = [geom.add_arc(inside[k], via[k], inside[(k + 1) % 4]) for k in range(4)]
    spokes = [geom.add_line(outside[k], inside[k]) for k in range(4)]
    sides = [geom.add_line(outside[k], outside[(k + 1) % 4]) for k in range(4)]
    for k in range(4):
        face = geom.add_face([sides[k], spokes[(k + 1) % 4], arcs[k], spokes[k]], surface=plane)
        project.assign_plate(face, "plate")
        project.add_support(support(project.face(face), uz=0., rx=0., ry=0.))
    return project, arcs, spokes, sides, outside, plane


def holed_strip(*, half_length=1.0):
    """Six faces, four circular hole arcs, analytic planes and the S0 end loading."""
    half = WIDTH / 2
    project, arcs, spokes, sides, outside, plane = build_ring(half)
    geom = project.geometry
    if half_length <= half:
        raise ValueError("the strip ends must extend past the ring")
    far = [geom.add_point(x, y, 0.) for x, y in
           ((-half_length, -half), (half_length, -half), (half_length, half), (-half_length, half))]
    left = geom.add_line(far[3], far[0])
    right = geom.add_line(far[1], far[2])
    left_panel = geom.add_face([geom.add_line(far[0], outside[0]), sides[3], geom.add_line(outside[3], far[3]),
                                left], surface=plane)
    right_panel = geom.add_face([geom.add_line(outside[1], far[1]), right, geom.add_line(far[2], outside[2]),
                                 sides[1]], surface=plane)
    for face in (left_panel, right_panel):
        project.assign_plate(face, "plate")
        project.add_support(support(project.face(face), uz=0., rx=0., ry=0.))
    project.add_support(support(project.edge(left), ux=0.))
    project.add_support(support(project.point(far[0]), uy=0.))
    project.load_case().add_line_load(project.edge(right), (NOMINAL_STRESS * THICKNESS, 0., 0.))
    return project, tuple(arcs), tuple(spokes)


def mesh_settings(arcs, spokes, divisions, *, size):
    return MeshSettings.create(size, element_order="linear", strategy="mapped",
                               overrides={edge: divisions for edge in (*arcs, *spokes)})


def configure_cut_mesh(child, *, divisions, size, radial_divisions=None, core_half=None):
    """Choose mapped sides on the real clipped sectors and seed the retained hole arcs."""
    geometry = child.geometry
    ring = []
    composite_seeds = {}
    for face_id, face in geometry.faces.items():
        edges = [geometry.edges[use.edge] for use in face.loop]
        arc_positions = [i for i, edge in enumerate(edges) if type(edge.curve) is Arc]
        if len(arc_positions) > 1 or any(type(edge.curve) not in (Straight, Arc) for edge in edges):
            raise ValueError(f"cut face {face_id} is not a sector or an end panel of the holed strip")
        if arc_positions:
            k = arc_positions[0]
            ring.append(face.loop[k].edge)
            # The two sides incident to the retained hole arc are radial.
            # All opposite box-boundary segments form one composite mapped side.
            corners = sorted({(k + offset) % len(edges) for offset in (-1, 0, 1, 2)})
            if radial_divisions is not None:
                for index in (k-1, k+1):
                    composite_seeds[face.loop[index % len(edges)].edge] = radial_divisions
            if len(edges) > 4:
                opposite = [face.loop[(k + j) % len(edges)].edge for j in range(2, len(edges)-1)]
                if divisions < len(opposite):
                    raise ValueError(f"face {face_id} needs at least one division per opposite segment")
                lengths = [geometry.edge_length(e) for e in opposite]
                # The mapped side opposite the circular edge must have precisely the same division count.
                # Mirror the two short wing counts and minimize their step-size jump to the central edge.
                if len(opposite) != 3 or not np.isclose(lengths[0], lengths[2], rtol=1.e-10, atol=1.e-12):
                    raise ValueError(f"face {face_id} does not have a symmetric three-segment opposite side")
                candidates = ((w, divisions-2*w, w) for w in range(1, (divisions-1)//2+1))
                counts = min(candidates, key=lambda c: np.ptp(np.log(np.asarray(lengths) / c)))
                composite_seeds.update(zip(opposite, counts))
        elif len(edges) == 4:
            corners = (0, 1, 2, 3)
            if radial_divisions is not None:
                half = WIDTH / 2 if core_half is None else core_half
                positions = np.asarray([geometry.vertex_position(e.start) for e in edges])
                centre = (positions.max(axis=0)+positions.min(axis=0)) / 2
                axis = int(np.argmax(np.abs(centre[:2])))
                # Continue the O-grid's radial spacing across each rectangular end panel.
                for use, edge in zip(face.loop, edges):
                    a, b = geometry.vertex_position(edge.start), geometry.vertex_position(edge.end)
                    if abs(a[1-axis]-b[1-axis]) < 1.e-10:
                        length = abs(a[axis]-b[axis])
                        composite_seeds[use.edge] = max(1, round(length * radial_divisions / (half-RADIUS)))
        else:
            raise ValueError(f"end panel {face_id} has {len(edges)} edges; four mapped sides are required")
        geometry.set_face_corners(face_id, corners)
    if len(ring) != 4:
        raise ValueError(f"the cut retained {len(ring)} of four source hole arcs")
    child.set_native_mesh_settings(NativeMeshSettings(target_size=size, backend="mapped"))
    child.seeding_overrides.update(dict.fromkeys(ring, divisions))
    child.seeding_overrides.update(composite_seeds)


def stress_factor(solution):
    """Average top/bottom surfaces at both +/-90-degree sensors, then divide by gross stress."""
    recovered = recover_nodal_stresses(solution.built.fe_model, solution.displacements)
    assert not recovered["skipped_element_ids"]
    mesh = solution.built.mesh
    values = []
    for y in (RADIUS, -RADIUS):
        point = np.array([0., y, 0.])
        hits = [n for n, xyz in mesh.nodes.items() if np.linalg.norm(xyz - point) < 1.e-9]
        assert len(hits) == 1, f"stress sensor at {point} must be a node of the admitted mesh"
        stress = recovered["nodal"][hits[0]]
        values.append((stress["global_xx_top"] + stress["global_xx_bot"]) / (2 * NOMINAL_STRESS))
    return tuple(map(float, values))
