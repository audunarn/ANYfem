"""Minimal reproducers for the two owner hole routes that fail on HEAD (S0 findings).

1. Legacy butterfly decomposition (anymesher.punch_circular_hole, the ANYfem
   "ButterflyHoleDecomposition" command) in a model with more than one face:
   ANYfem structural preparation refuses the patches.
2. Neutral trimmed hole (anygeometry.operations.punch_hole, the ANYfem
   "NeutralTrimHole" command) on a single plain strip: the mesher's qualified-S3
   repair does not satisfy the admission contract, at every size tried.
3. Control: the same O-grid built by hand with an explicit Plane surface meshes when
   its arcs and spokes are pinned to equal divisions (without the pins the structured
   route rejects it on aspect ratio, which is the expected quality gate).
"""

from __future__ import annotations

import s0_env  # noqa: F401

import s0_lib as L
from anygeometry.operations import punch_hole
from anymesher import punch_circular_hole


def attempt(label, build, size, overrides=None):
    try:
        project = build()
        pins = overrides(project) if overrides else None
        m = project.generate_mesh(size, overrides=pins)
        result = f"ok: {len(m.quads)} quads, {len(m.tris)} triangles"
    except Exception as error:
        result = f"{type(error).__name__}: {error}"
    print(f"{label} (size {size}): {result}", flush=True)
    return {"label": label, "size": size, "result": result}


def butterfly_three_faces():
    p = L.new_project("butterfly")
    faces = L.rectangle_faces(p, [-1.0, -0.25, 0.25, 1.0], [-0.25, 0.25], assign=False)
    punch_circular_hole(p.geometry, faces[1, 0], (0.0, 0.0, 0.0), 0.05)
    for f in p.geometry.faces:
        p.assign_plate(f, "plate")
    return p


def neutral_strip():
    p = L.new_project("neutral")
    faces = L.rectangle_faces(p, [-1.0, 1.0], [-0.25, 0.25], assign=False)
    punch_hole(p.geometry, faces[0, 0], (0.0, 0.0, 0.0), 0.05)
    for f in p.geometry.faces:
        p.assign_plate(f, "plate")
    return p


def hand_ogrid():
    import e4_hole

    p = L.new_project("ogrid")
    e4_hole.ogrid(p, 0.25, 0.25, side_x=1.0)
    return p


def _pins(project):
    import numpy as np

    g = project.geometry
    pins = {}
    for e, edge in g.edges.items():
        ra = np.linalg.norm(np.asarray(g.vertex_position(edge.start))[:2])
        rb = np.linalg.norm(np.asarray(g.vertex_position(edge.end))[:2])
        if abs(ra - 0.05) < 1e-9 or abs(rb - 0.05) < 1e-9:
            pins[e] = 8
    return pins


def main():
    rows = [attempt("butterfly, 3 faces", butterfly_three_faces, 0.05)]
    for size in (0.125, 0.05, 0.02):
        rows.append(attempt("neutral trimmed hole, 1 face", neutral_strip, size))
    rows.append(attempt("hand-built O-grid on Plane, unpinned", hand_ogrid, 0.05))
    rows.append(attempt("hand-built O-grid on Plane, arcs+spokes pinned to 8", hand_ogrid, 0.5 / 8, _pins))
    print("saved", L.save("repro_hole_routes", {"rows": rows}))


if __name__ == "__main__":
    main()
