"""Experimental periodic source responses with original-time canonical replay.

python examples/prepared_static_response.py [--stop-after 1] [--cancel-after 1]
Requires the coordinated experimental ANYsolver response API. No speed claim.
"""
import argparse
import math
from anyfem import Project, commands, fixed, steel
from anyfem.model import WetSurface
from anyfem.solve import PeriodicLoadSource, prepare_linear_static_response_evaluator
from anysolver import CancellationToken, RecoveryConfig, ResourceConfig, SolveCancelled


def build_example():
    project = Project(name="periodic source responses")
    project.add_material(steel("S355", .01))
    project.add_plate_section("plate", thickness=.01, material="S355")
    points = project.geometry.add_points([(0,0,0), (2,0,0), (2,1,0), (0,1,0)])
    edges = project.geometry.add_polyline(points, close=True)
    face = project.geometry.add_face(edges)
    project.assign_plate(face, "plate")
    project.add_support(fixed(project.edge(edges[3])))
    commands.CommandStack(project).run(commands.AddConnection(refs=(project.edge(edges[1]),), name="end"))
    project.add_wet_surface(WetSurface(project.face(face), fluid_side="negative_normal"))
    source = project.add_load_series("waves", [0., .5, 1., 1.5, 2., 2.5])
    source.parameters["omega"] = math.pi
    source.add_wet_pressure(expression="max(0, 2+sin(omega*t-x)-z)", envelope={"expression": "t/2"})
    source.add_connection_load(project.connection_for("end").id, moment=(0,3,0),
                               envelope={"expression": "1+t*t"})
    return project, project.generate_mesh(1.)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stop-after", type=int)
    parser.add_argument("--cancel-after", type=int)
    args = parser.parse_args()
    project, mesh = build_example()
    elements = list(mesh.quads or mesh.tris)
    selected = RecoveryConfig(element_ids=elements[:1], components=("membrane_xx", "bending_xx"),
                              include_reactions=True)
    whole = RecoveryConfig(element_ids=elements, components=("membrane_xx", "bending_xx"),
                           include_reactions=True)
    token = CancellationToken()
    with prepare_linear_static_response_evaluator(project, mesh=mesh,
            periodic_source=PeriodicLoadSource("waves", period=2., time_step=.5),
            recovery_config=selected, envelope_recovery_config=whole,
            resource_config=ResourceConfig(memory_limit_bytes=256*1024*1024),
            cancellation_token=token) as owner:
        stream = owner.iter_batches(batch_size=2, cancellation_token=token)
        try:
            for index, block in enumerate(stream, 1):
                print("certified", block.case_ids, "selected stress records", len(block.stresses(0)))
                if args.cancel_after == index:
                    token.cancel("example cancellation")
                if args.stop_after == index:
                    break
        except SolveCancelled:
            print("cancelled; preceding certified blocks remain available")
        finally:
            stream.close()
        summary = owner.envelope_result()
        print("complete", summary.complete, "cases", summary.cases_processed, "extrema", len(summary.extrema))
        print(owner.diagnostics()["response"])


if __name__ == "__main__":
    main()
