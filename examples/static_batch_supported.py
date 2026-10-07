"""Selected physical results and envelopes for an arbitrary static history.

Run with the coordinated ANYfem and ANYsolver packages on PYTHONPATH.
The Python pressure code is authored here and trusted by add_wet_pressure.
"""

from anyfem import Project, StaticLoadRequest, commands, fixed, iter_linear_static_batches, steel
from anyfem.model import WetSurface
from anysolver import RecoveryConfig
from anysolver.static_envelopes import EnvelopeReducer


def main():
    project = Project(name="supported static history")
    project.add_material(steel("S355", 0.01))
    project.add_plate_section("plate", thickness=0.01, material="S355")
    geometry = project.geometry
    points = geometry.add_points([(0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0)])
    edges = geometry.add_polyline(points, close=True)
    face = geometry.add_face(edges)
    project.assign_plate(face, "plate")
    project.add_support(fixed(project.edge(edges[3])))
    commands.CommandStack(project).run(
        commands.AddConnection(refs=(project.edge(edges[1]),), name="end")
    )
    project.add_wet_surface(WetSurface(project.face(face), fluid_side="negative_normal"))
    times = (0.0, 0.3, 0.8)
    series = project.add_load_series("history", times)
    series.add_point_load(project.point(points[1]), force=(2, 0, 0),
                          envelope={"expression": "t*t"})
    series.add_pressure(project.face(face), 11, envelope={"start": 2, "expression": "1+t"})
    series.add_wet_pressure(code="def pressure(t, x):\n    return 17*(x+t) if x>t else 0\n")
    series.add_connection_load(project.connection_for("end").id, force=(0, 7, 0),
                               envelope={"expression": "1+t*t*t"})
    series.add_line_load(project.edge(edges[0]), (0, 3, 0))
    series.add_surface_traction(project.face(face), (1, 0, 2))
    series.set_acceleration(0, 0, -0.4)
    mesh = project.generate_mesh(1.0)
    tip_node = mesh.node_of_vertex[points[1]]
    element = next(iter(mesh.quads or mesh.tris))
    requests = (
        StaticLoadRequest(f"sample-{index}", load_case=step.name, time=time)
        for index, (step, time) in enumerate(zip(project.series_children("history"), times))
    )
    envelopes = EnvelopeReducer()
    iterator = iter_linear_static_batches(
        project, mesh=mesh, load_cases=requests, batch_size=2,
        output_nodes=[tip_node], output_connections=["end"],
        recovery_config=RecoveryConfig(element_ids=[element], components=["von_mises"],
                                       include_displacements=False, include_reactions=True),
    )
    try:
        for block in iterator:
            assert block.displacements.shape[0] == 12
            assert all(case["status"] == "converged" for case in block.case_results)
            envelopes.update_batch(block)
            for column, case_id in enumerate(block.case_ids):
                print(case_id, block.times[column], block.node_displacement(tip_node, column))
        envelopes.finish()
    finally:
        iterator.close()
    assert envelopes.complete and envelopes.case_count == len(times)
    print("Complete envelope:", envelopes.case_count, "cases;", len(envelopes.extrema), "quantities")
    return envelopes


if __name__ == "__main__":
    main()
