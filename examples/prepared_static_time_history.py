"""Experimental prepared static history: supported or balanced free-free beam.

Run with coordinated ANYfem/ANYsolver packages installed or on PYTHONPATH:
    python examples/prepared_static_time_history.py
    python examples/prepared_static_time_history.py --free-free
    python examples/prepared_static_time_history.py --stop-after 1
    python examples/prepared_static_time_history.py --cancel-after 1

Whole-model envelopes are reduced by the solver alongside selected histories.
The prepared route has unresolved stress/principal/envelope accuracy gates.
"""
from __future__ import annotations

import argparse
import sys

from anyfem import Project, StaticLoadRequest, fixed, steel
from anyfem.model import BeamSection, ProjectError
from anyfem.solve import prepare_linear_static_evaluator
from anysolver import (CancellationToken, PreparedStaticError, RecoveryConfig,
                       ResourceConfig, ResourcePolicyError, SolveCancelled)


TIMES = (0.0, 0.13, 0.31, 0.56, 0.92)


def build_example(free_free: bool):
    project = Project(name="experimental prepared static history")
    project.add_material(steel("S355", 0.01))
    project.add_beam_section(BeamSection(
        name="beam", profile="Flatbar", material="S355", flange_width=0.2,
        flange_thickness=0.02, web_direction=(0, 0, 1),
    ))
    points = project.geometry.add_points([(0, 0, 0), (1, 0, 0)])
    edge = project.geometry.add_line(*points)
    project.assign_beam(edge, "beam")
    if not free_free:
        project.add_support(fixed(project.point(points[0])))
    history = project.add_load_series("samples", list(TIMES))
    # Two independent spatial load patterns with different nonlinear time laws.
    # Both pairs are independently self-equilibrated for the free-free variant.
    for point, sign in ((points[0], -1.0), (points[1], 1.0)):
        history.add_point_load(project.point(point), force=(sign * 100, 0, 0),
                               envelope={"expression": "1 + t*t"})
        history.add_point_load(project.point(point), moment=(sign * 2, 0, 0),
                               envelope={"expression": "2 - 3*t + t*t*t"})
    # Two elements: only one enters per-case history; both enter the envelope.
    mesh = project.generate_mesh(0.5)
    selected_node = mesh.node_of_vertex[points[1]]
    selected_element = next(iter(mesh.beams))
    requests = tuple(
        StaticLoadRequest(f"sample-{index}", load_case=step.name, time=time)
        for index, (step, time) in enumerate(zip(project.series_children("samples"), TIMES))
    )
    return project, mesh, selected_node, selected_element, requests


def run(*, free_free=False, stop_after=None, cancel_after=None):
    project, mesh, node, element, requests = build_example(free_free)
    token = CancellationToken()
    envelope = None
    evaluator = None
    reason = "not started"
    if cancel_after == 0:
        token.cancel("example cancellation before preparation")
    try:
        with prepare_linear_static_evaluator(
            project, mesh=mesh, output_nodes=[node],
            recovery_config=RecoveryConfig(
                node_ids=[node], element_ids=[element],
                components=["axial_stress", "torsional_stress"],
                include_displacements=False, include_stresses=True,
                include_reactions=False, store_full_histories=False,
            ),
            envelope_recovery_config=RecoveryConfig(
                components=["axial_stress", "torsional_stress"],
                include_displacements=False, include_stresses=True,
                include_reactions=False,
            ),
            resource_config=ResourceConfig(
                solver_threads=1, assembly_threads=1, recovery_threads=1,
                memory_limit_bytes=64 * 1024 * 1024,
            ),
            constraint_mode="auto", allow_unbalanced_free_free=False,
            cancellation_token=token,
        ) as evaluator:
            stream = evaluator.iter_batches(requests, batch_size=2,
                                            cancellation_token=token)
            try:
                for block_number, block in enumerate(stream, start=1):
                    # Keep only current selected results and running extrema.
                    print(block.case_ids, "last selected node motion:",
                          block.node_displacement(node, len(block.case_ids) - 1))
                    if cancel_after is not None and block_number >= cancel_after:
                        token.cancel("example cancellation after a certified block")
                    if stop_after is not None and block_number >= stop_after:
                        reason = "caller stopped early"
                        break
                else:
                    # Only normal exhaustion certifies the complete history.
                    reason = "all intended samples processed"
            finally:
                stream.close()
    except SolveCancelled as error:
        reason = str(error)
    except (PreparedStaticError, ResourcePolicyError, ProjectError) as error:
        # Previously consumed blocks remain available, but never become a
        # complete envelope after refusal, numerical failure or cleanup error.
        print(f"Prepared route failed; envelope incomplete: {error}", file=sys.stderr)
        raise
    if evaluator is None:
        print(f"Envelope unavailable; preparation did not complete: {reason}")
        return None
    envelope = evaluator.envelope_result()
    print(f"Envelope complete={envelope.complete}; cases={envelope.cases_processed}; {reason}")
    for extrema in envelope.extrema:
        governing = extrema.absolute_maximum
        print(extrema.quantity, extrema.entity_id, extrema.component,
              governing.values, governing.case_ids, governing.input_indices,
              governing.times)
    return envelope


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--free-free", action="store_true")
    parser.add_argument("--stop-after", type=int, metavar="BLOCKS")
    parser.add_argument("--cancel-after", type=int, metavar="BLOCKS")
    args = parser.parse_args()
    if args.stop_after is not None and args.stop_after < 1:
        parser.error("--stop-after must be positive")
    if args.cancel_after is not None and args.cancel_after < 0:
        parser.error("--cancel-after must be nonnegative")
    print("EXPERIMENTAL: prepared stress/principal/envelope accuracy gates remain unaccepted.")
    envelope = run(free_free=args.free_free, stop_after=args.stop_after,
                   cancel_after=args.cancel_after)
    return 0 if envelope is not None and envelope.complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
