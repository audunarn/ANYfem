"""Leased full-versus-dirty native component remeshing qualification."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from anyfem.model.project import Project
from anyfem.native_meshing import NativeMeshSettings
from anyfem.native_meshing_backend import NativeProjectMeshingSession
from anymesher.serialize import mesh_to_dict


def _project(component_count: int) -> tuple[Project, tuple[Any, ...], dict[Any, int]]:
    project = Project("native incremental performance")
    columns = max(1, int(math.ceil(math.sqrt(component_count))))
    handles = []
    movable_vertices = {}
    for index in range(component_count):
        column = index % columns
        row = index // columns
        x = 1.5 * column
        y = 1.5 * row
        vertices = project.geometry.add_points(
            (
                (x, y, 0.0),
                (x + 1.0, y, 0.0),
                (x + 1.0, y + 1.0, 0.0),
                (x, y + 1.0, 0.0),
            )
        )
        face_id = project.geometry.add_plate(vertices)
        sheet_id = project.geometry.add_sheet((face_id,), name=f"sheet-{index + 1}")
        handle = project.geometry.handle("sheet", sheet_id)
        handles.append(handle)
        movable_vertices[handle] = int(vertices[1])
    return project, tuple(handles), movable_vertices


def _canonical_mesh_hash(mesh: Any) -> str:
    payload = json.dumps(
        mesh_to_dict(mesh), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _publications(session: NativeProjectMeshingSession, handles) -> dict[Any, Any]:
    result = {handle: session.runtime.publication(handle) for handle in handles}
    if any(value is None for value in result.values()):
        raise RuntimeError("native runtime did not publish every requested component")
    return result


def run(
    *,
    component_count: int,
    target_size: float,
    max_background_jobs: int,
    timeout: float,
    minimum_speedup: float,
    maximum_dirty_fraction: float,
) -> dict[str, Any]:
    project, handles, movable_vertices = _project(component_count)
    settings = NativeMeshSettings(
        target_size=target_size,
        backend="native",
        certification_mode="interactive",
    )
    with NativeProjectMeshingSession(
        project, settings, max_background_jobs=max_background_jobs
    ) as session:
        started = time.perf_counter()
        session.request_remesh(handles)
        if not session.runtime.wait_for_idle(timeout):
            raise TimeoutError("initial full component generation did not finish")
        full_seconds = time.perf_counter() - started
        before = _publications(session, handles)
        before_hashes = {
            handle: _canonical_mesh_hash(publication.mesh)
            for handle, publication in before.items()
        }

        changed = handles[0]
        vertex_id = movable_vertices[changed]
        position = project.geometry.vertices[vertex_id].position
        started = time.perf_counter()
        project.geometry.move_point(
            vertex_id,
            float(position[0]) + 0.01,
            float(position[1]),
            float(position[2]),
        )
        resolution = session.runtime.flush_changes()
        if not session.runtime.wait_for_idle(timeout):
            raise TimeoutError("dirty component generation did not finish")
        dirty_seconds = time.perf_counter() - started
        after = _publications(session, handles)
        after_hashes = {
            handle: _canonical_mesh_hash(publication.mesh)
            for handle, publication in after.items()
        }

    dirty = tuple(resolution.dirty)
    removed = tuple(resolution.removed)
    clean = tuple(handle for handle in handles if handle not in set(dirty))
    clean_publications_reused = all(after[handle] is before[handle] for handle in clean)
    clean_hashes_unchanged = all(
        after_hashes[handle] == before_hashes[handle] for handle in clean
    )
    dirty_publications_replaced = all(after[handle] is not before[handle] for handle in dirty)
    dirty_fraction = len(dirty) / len(handles)
    speedup = full_seconds / dirty_seconds
    total_elements = sum(int(item.mesh.num_elements) for item in before.values())
    dirty_elements = sum(int(after[item].mesh.num_elements) for item in dirty)
    accepted = (
        not removed
        and dirty_fraction <= maximum_dirty_fraction
        and speedup >= minimum_speedup
        and clean_publications_reused
        and clean_hashes_unchanged
        and dirty_publications_replaced
    )
    return {
        "schema": "anyfem.native_incremental_performance.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "component_count": len(handles),
        "target_size": target_size,
        "max_background_jobs": max_background_jobs,
        "total_elements_before_edit": total_elements,
        "dirty_elements_after_edit": dirty_elements,
        "dirty_component_count": len(dirty),
        "dirty_fraction": dirty_fraction,
        "removed_component_count": len(removed),
        "full_generation_seconds": full_seconds,
        "dirty_response_seconds": dirty_seconds,
        "full_over_dirty_speedup": speedup,
        "clean_publications_reused": clean_publications_reused,
        "clean_hashes_unchanged": clean_hashes_unchanged,
        "dirty_publications_replaced": dirty_publications_replaced,
        "acceptance": {
            "minimum_speedup": minimum_speedup,
            "maximum_dirty_fraction": maximum_dirty_fraction,
            "passed": accepted,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--components", type=int, default=20)
    parser.add_argument("--target-size", type=float, default=0.1)
    parser.add_argument("--max-background-jobs", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--minimum-speedup", type=float, default=5.0)
    parser.add_argument("--maximum-dirty-fraction", type=float, default=0.2)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.components <= 0:
        parser.error("--components must be positive")
    if args.target_size <= 0.0:
        parser.error("--target-size must be positive")
    result = run(
        component_count=args.components,
        target_size=args.target_size,
        max_background_jobs=args.max_background_jobs,
        timeout=args.timeout,
        minimum_speedup=args.minimum_speedup,
        maximum_dirty_fraction=args.maximum_dirty_fraction,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["acceptance"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
