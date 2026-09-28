"""Read-only per-cell normal audit for a preserved RA1 operation."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np

from anyfem.io.project_file import project_from_dict
from anymesher.serialize import mesh_from_dict
from anymesher.quad.high_order import evaluate_mapping
from benchmarks.ra1.operation import _fixture
from benchmarks.sg1.measure import samples


def main(folder: Path) -> None:
    spec = json.loads((folder / "input.json").read_text(encoding="utf-8"))
    frozen = json.loads(Path(spec["fixture"]).read_text(encoding="utf-8"))
    project = project_from_dict(frozen["project"])
    mesh = mesh_from_dict(json.loads((folder / "mesh.json").read_text(encoding="utf-8")))
    fixture = _fixture(project, frozen["center"], spec["case"])
    rows = []
    for face, patch in fixture.patches.items():
        for eid in mesh.elements_of_face.get(face, ()):
            body = mesh.quads.get(eid, mesh.tris.get(eid))
            nc = 4 if eid in mesh.quads else 3
            family = ("Q" if nc == 4 else "T") + str(len(body))
            xyz = np.asarray([mesh.nodes[node] for node in body])
            uv_corners = np.asarray([patch.uv(point) for point in xyz[:nc]])
            reference = patch.normal(*np.mean(uv_corners, axis=0))
            ev = evaluate_mapping(xyz, family, samples(nc == 3), reference_normal=reference)
            nvec = np.asarray(ev.jacobian_vector)
            nvec /= np.linalg.norm(nvec, axis=1)[:, None]
            uv_scalar = np.asarray([patch.uv(point) for point in ev.points])
            uv_batch = project.geometry.face_local_uv_many(face, ev.points)
            normal_scalar = np.asarray([patch.normal(*uv) for uv in uv_scalar])
            normal_batch = project.geometry.face_normal_many(face, uv_batch)
            error_scalar = np.degrees(np.arccos(np.clip(np.einsum("ij,ij->i", nvec, normal_scalar), -1, 1)))
            error_batch = np.degrees(np.arccos(np.clip(np.einsum("ij,ij->i", nvec, normal_batch), -1, 1)))
            rows.append({
                "face": face, "element": eid, "family": family,
                "scalar_normal_degrees": float(np.max(error_scalar)),
                "batch_normal_degrees": float(np.max(error_batch)),
                "uv_difference": float(np.max(np.abs(uv_scalar-uv_batch))),
                "nodes": tuple(body),
            })
    rows.sort(key=lambda row: row["scalar_normal_degrees"], reverse=True)
    destination = folder / "normal-diagnostic.json"
    destination.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    for row in rows[:8]:
        print({key: row[key] for key in ("face", "element", "family", "scalar_normal_degrees",
                                          "batch_normal_degrees", "uv_difference")})
    print(f"full diagnostic: {destination}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
