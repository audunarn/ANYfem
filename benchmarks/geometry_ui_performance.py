"""Bounded, headless ANYfem/ANYgeometry integration benchmark.

This benchmark targets the work done before a geometry viewport is submitted
to a renderer.  It deliberately does not construct Tk or open a window, so it
is safe to run during ordinary development.  Timings are observations rather
than machine-specific pass/fail thresholds.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys
import time
from typing import Callable, TypeVar


ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT / "src", ROOT.parent / "ANYgeometry" / "src"):
    if source.is_dir() and str(source) not in sys.path:
        sys.path.insert(0, str(source))

from anygeometry import feature_entity_owners  # noqa: E402
from anyfem import Project  # noqa: E402
from anyfem import commands  # noqa: E402
from anyfem.ui.scene import (  # noqa: E402
    build_geometry_scene,
    geometry_display_resolution,
)


T = TypeVar("T")


def measured(factory: Callable[[], T], repeats: int) -> tuple[T, dict[str, float]]:
    samples: list[float] = []
    value: T
    for _ in range(repeats):
        started = time.perf_counter()
        value = factory()
        samples.append(time.perf_counter() - started)
    return value, {
        "minimum_seconds": min(samples),
        "median_seconds": statistics.median(samples),
        "maximum_seconds": max(samples),
    }


def scene_counts(scene) -> dict[str, int]:
    return {
        "face_batches": len(scene.faces),
        "polygons": sum(len(patch.polygons) for patch in scene.faces),
        "lines": len(scene.lines),
        "points": len(scene.points),
    }


def cylinder_case(segments: int, repeats: int) -> dict[str, object]:
    project = Project(f"cylinder-{segments}")
    stack = commands.CommandStack(project)
    started = time.perf_counter()
    feature = stack.run(
        commands.AddCylinder(
            2.0,
            4.0,
            circumferential_segments=segments,
            longitudinal_spacing=0.5,
            ring_spacing=1.0,
        )
    )
    construction_seconds = time.perf_counter() - started

    owners, ownership = measured(
        lambda: feature_entity_owners(project.geometry), repeats
    )
    resolution = geometry_display_resolution(project.geometry)
    collapsed, collapsed_timing = measured(
        lambda: build_geometry_scene(
            project,
            divisions=resolution.divisions,
            curve_samples=resolution.curve_samples,
            entity_owners=owners,
        ),
        repeats,
    )
    interaction, interaction_timing = measured(
        lambda: build_geometry_scene(
            project,
            divisions=resolution.interaction_divisions,
            curve_samples=resolution.interaction_curve_samples,
            entity_owners=owners,
        ),
        repeats,
    )
    exploded, exploded_timing = measured(
        lambda: build_geometry_scene(
            project,
            divisions=resolution.divisions,
            curve_samples=resolution.curve_samples,
            entity_owners=owners,
            exposed_feature_ids=(feature.feature_id,),
        ),
        1,
    )
    _copies, cached_redraw_timing = measured(
        lambda: (collapsed.copy(), interaction.copy()),
        repeats,
    )
    return {
        "segments": segments,
        "construction_seconds": construction_seconds,
        "entities": {
            "vertices": len(project.geometry.vertices),
            "edges": len(project.geometry.edges),
            "faces": len(project.geometry.faces),
            "owned": len(owners),
        },
        "resolution": vars(resolution),
        "ownership": ownership,
        "collapsed_scene": {**scene_counts(collapsed), **collapsed_timing},
        "interaction_scene": {**scene_counts(interaction), **interaction_timing},
        "exploded_scene": {**scene_counts(exploded), **exploded_timing},
        "cached_redraw": cached_redraw_timing,
    }


def run(segments: list[int], repeats: int) -> dict[str, object]:
    return {
        "profile": "headless-anyfem-anygeometry",
        "repeats": repeats,
        "cases": [cylinder_case(value, repeats) for value in segments],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--segments", nargs="+", type=int, default=[32, 128, 512])
    parser.add_argument("--repeats", type=int, default=3)
    arguments = parser.parse_args()
    if arguments.repeats < 1:
        parser.error("--repeats must be positive")
    if any(value < 3 for value in arguments.segments):
        parser.error("every segment count must be at least 3")
    print(json.dumps(run(arguments.segments, arguments.repeats), indent=2))


if __name__ == "__main__":
    main()
