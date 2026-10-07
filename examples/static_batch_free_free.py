"""Balanced free-free static loads, selected stresses and an explicit refusal."""

from anyfem import Project, StaticLoadRequest, iter_linear_static_batches, steel
from anyfem.model import BeamSection
from anysolver import RecoveryConfig, StaticBatchError
from anysolver.static_envelopes import EnvelopeReducer


def main():
    project = Project(name="free-free static history")
    project.add_material(steel("S355", 0.01))
    project.add_beam_section(BeamSection(
        name="beam", profile="Flatbar", material="S355", flange_width=0.2,
        flange_thickness=0.02, web_direction=(0, 0, 1),
    ))
    points = project.geometry.add_points([(0, 0, 0), (1, 0, 0)])
    edge = project.geometry.add_line(*points)
    project.assign_beam(edge, "beam")
    series = project.add_load_series("balanced", [0.0, 0.3, 0.8])
    envelope = {"expression": "1+t*t"}
    series.add_point_load(project.point(points[0]), force=(10, 0, 0), envelope=envelope)
    series.add_point_load(project.point(points[1]), force=(-10, 0, 0), envelope=envelope)
    bad = project.load_case("unbalanced")
    bad.add_point_load(project.point(points[0]), force=(10, 0, 0))
    mesh = project.generate_mesh(1.0)
    node = mesh.node_of_vertex[points[1]]
    element = next(iter(mesh.beams))
    requests = (StaticLoadRequest(step.id, load_case=step) for step in project.series_children("balanced"))
    envelopes = EnvelopeReducer()
    iterator = iter_linear_static_batches(
        project, mesh=mesh, load_cases=requests, output_nodes=[node], batch_size=2,
        recovery_config=RecoveryConfig(element_ids=[element], include_displacements=False),
    )
    try:
        for block in iterator:
            assert block.displacements.shape[0] == 6
            assert all(case["status"] == "converged" for case in block.case_results)
            envelopes.update_batch(block)
        envelopes.finish()
    finally:
        iterator.close()
    assert envelopes.complete and envelopes.case_count == 3
    print("Balanced free-free envelope complete:", envelopes.case_count, "cases")
    refused = iter_linear_static_batches(project, mesh=mesh, load_cases=["unbalanced"], output_nodes=[node])
    try:
        try:
            next(refused)
        except StaticBatchError as error:
            print("Expected unbalanced-load refusal:", error)
        else:
            raise AssertionError("an unbalanced free-free load must be refused")
    finally:
        refused.close()
    return envelopes


if __name__ == "__main__":
    main()
