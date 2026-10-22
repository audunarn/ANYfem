"""Generator mesh defaults reach the production meshing route."""
import numpy as np

from anyfem import commands
from anyfem.application.controller import default_project


def test_generator_local_size_controls_beams_with_coarser_global_size():
    project = default_project()
    commands.AddStiffenedPanel(
        4, 3, 1, beam_section="stiffener", plate_thickness=.02,
        material="S355", mesh_size=.4,
    ).do(project)
    mesh = project.generate_mesh(2.)
    lengths = [np.linalg.norm(np.asarray(mesh.nodes[nodes[1]]) - mesh.nodes[nodes[0]])
               for nodes in mesh.beams.values()]
    coarse = project.generate_mesh(2., refinements=[])
    coarse_lengths = [np.linalg.norm(np.asarray(coarse.nodes[nodes[1]]) - coarse.nodes[nodes[0]])
                      for nodes in coarse.beams.values()]
    # A target size is approximate. Compare the same production route with
    # the local controls explicitly disabled, rather than imposing a hard cap.
    assert len(lengths) >= 4 * len(coarse_lengths)
    assert max(lengths) < .5 * max(coarse_lengths)
    assert all(mesh.elements_of_face[face] for face in project.face_sections)
